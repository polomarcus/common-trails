"""Ingestion hygiene — 2026-09 audit fixes. Every test drives the REAL
functions and fails on the pre-fix code.

  1. Abandoned ``awaiting_upload`` archives are reaped after the TTL (they
     permanently consumed a MAX_OPEN_ARCHIVES_PER_USER slot and 409'd the
     admin requeue) — WITHOUT emailing the owner (no upload ever happened).
  2. Migration 0066 indexes the drain's per-member (user_id, file_hash)
     dedup probe.
  3. The legacy per-member queue's spatial pre-sort (a GCS double-read of
     every member) only runs in legacy MATCHED display mode — under the
     raw-trace pivot there is no OSM segment cache to keep warm.
  4. HUSK GUARD: a member that parses but yields no geometry must be counted
     ``skipped`` (reason ``no_geometry``) and must NOT create an activity row
     (2026-08 Garmin recovery: 14,460 "imported", 13,882 empty husks).

Shared-DB discipline: NO TRUNCATE — every fixture row uses a unique
``hygiene-test-*`` user id / bucket key and is deleted in ``finally``.
"""
import io
import uuid
import zipfile
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text as sa_text

from app.jobs import ingest_pending_archives as drain_mod


def _db_available() -> bool:
    from app.db.session import SessionLocal
    try:
        db = SessionLocal()
        try:
            db.execute(sa_text("SELECT 1 FROM pending_archives LIMIT 1"))
            return True
        finally:
            db.close()
    except Exception:
        return False


_HAS_DB = _db_available()
needs_db = pytest.mark.skipif(not _HAS_DB, reason="needs the PostGIS DB")


_GOOD_GPX = (
    '<?xml version="1.0"?><gpx version="1.1" creator="test"><trk><name>ride'
    "</name><trkseg>"
    + "".join(
        f'<trkpt lat="{43.6 + i * 0.001}" lon="{3.87 + i * 0.001}">'
        f"<ele>{100 + i}</ele></trkpt>"
        for i in range(8)
    )
    + "</trkseg></trk></gpx>"
).encode()

# Parses fine, yields ZERO coordinates → the husk shape (a Garmin export is
# full of these: trackless .gpx / .fit files with no GPS records).
_TRACKLESS_GPX = (
    b'<?xml version="1.0"?><gpx version="1.1" creator="test">'
    b"<trk><name>husk</name><trkseg></trkseg></trk></gpx>"
)


def _mk_archive_row(
    db,
    *,
    user_id: str,
    status: str,
    attempts: int = 0,
    age_hours: float = 0,
    bucket_key: str | None = None,
) -> str:
    aid = str(uuid.uuid4())
    db.execute(sa_text("""
        INSERT INTO pending_archives (id, user_id, storage_backend, bucket_key,
            fallback_sport, contribute_heatmap, status, attempts, updated_at)
        VALUES (:id, :uid, 'local', :bk, 'road', true, :st, :att,
                now() - make_interval(hours => :age))
    """), {
        "id": aid, "uid": user_id,
        "bk": bucket_key or f"archive-intake/{user_id}/{aid}.zip",
        "st": status, "att": attempts, "age": age_hours,
    })
    return aid


def _archive_row(db, aid: str) -> dict:
    return dict(db.execute(sa_text(
        "SELECT status, last_error, imported, skipped FROM pending_archives "
        "WHERE id = :i"), {"i": aid}).mappings().one())


def _cleanup(db, *, user_ids: list[str] = (), archive_ids: list[str] = ()) -> None:
    for uid in user_ids:
        db.execute(sa_text("DELETE FROM activity_cells WHERE user_id = :u"), {"u": uid})
        db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": uid})
    for aid in archive_ids:
        db.execute(sa_text("DELETE FROM pending_archives WHERE id = :i"), {"i": aid})
    db.commit()


# ── 1. awaiting_upload reap ──────────────────────────────────────────────────


@needs_db
class TestAwaitingUploadReap:
    def test_abandoned_awaiting_upload_failed_but_never_returned_for_email(
        self, tmp_path, monkeypatch
    ):
        """Pre-fix: an /init'd-then-abandoned archive stayed 'awaiting_upload'
        FOREVER — camping on the 5-slot quota and 409ing the admin requeue.
        The reaper must fail it, free its (maybe half-uploaded) object, and
        keep it OUT of the returned list (= the caller's email loop)."""
        from app.db.session import SessionLocal
        from app.services import archive_intake

        monkeypatch.setattr(archive_intake, "ARCHIVE_INTAKE_DIR", str(tmp_path))
        uid = f"hygiene-test-await-{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        ids: list[str] = []
        try:
            old_await = _mk_archive_row(db, user_id=uid, status="awaiting_upload",
                                        age_hours=50)
            fresh_await = _mk_archive_row(db, user_id=uid, status="awaiting_upload",
                                          age_hours=1)
            old_processing = _mk_archive_row(
                db, user_id=uid, status="processing",
                attempts=drain_mod._MAX_DRAIN_ATTEMPTS, age_hours=4,
            )
            ids = [old_await, fresh_await, old_processing]
            db.commit()
            # A PUT that landed without /complete: the object must be freed.
            bk = db.execute(sa_text(
                "SELECT bucket_key FROM pending_archives WHERE id = :i"
            ), {"i": old_await}).scalar()
            archive_intake.store_archive_local(bk, b"partial upload")
            stored = archive_intake.local_archive_path(bk)

            reaped = drain_mod._reap_stuck_archives(db)

            # Only the stuck-processing id is returned (→ terminal email).
            assert old_processing in reaped
            assert old_await not in reaped, "no upload happened → no email"
            assert fresh_await not in reaped

            row = _archive_row(db, old_await)
            assert row["status"] == "failed"  # FAILS pre-fix: 'awaiting_upload'
            assert "upload never completed" in (row["last_error"] or "")
            assert _archive_row(db, fresh_await)["status"] == "awaiting_upload"
            import os
            assert not os.path.exists(stored), "abandoned object must be deleted"
        finally:
            _cleanup(db, archive_ids=ids)
            db.close()

    def test_drain_caller_emails_only_processing_reaps(self, monkeypatch):
        """Drive the REAL drain entrypoint: _notify_archive_terminal must fire
        for the stuck-'processing' reap (an upload existed) and NOT for the
        abandoned 'awaiting_upload' reap."""
        from app.db.session import SessionLocal

        uid = f"hygiene-test-notif-{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        ids: list[str] = []
        try:
            old_await = _mk_archive_row(db, user_id=uid, status="awaiting_upload",
                                        age_hours=50)
            old_processing = _mk_archive_row(
                db, user_id=uid, status="processing",
                attempts=drain_mod._MAX_DRAIN_ATTEMPTS, age_hours=4,
            )
            ids = [old_await, old_processing]
            db.commit()

            notified: list[str] = []
            monkeypatch.setattr(
                drain_mod, "_notify_archive_terminal",
                lambda aid, *a, **k: notified.append(aid),
            )
            # Claim nothing — this run only exercises the reap path.
            monkeypatch.setattr(drain_mod, "_claim_archives", lambda d, lim: [])

            drain_mod.drain_pending_archives(limit=1, pace_seconds=0.0,
                                             skip_heat_computation=True)

            assert old_processing in notified
            assert old_await not in notified  # FAILS if awaiting reaps are emailed
            assert _archive_row(db, old_await)["status"] == "failed"
            assert _archive_row(db, old_processing)["status"] == "failed"
        finally:
            _cleanup(db, archive_ids=ids)
            db.close()


# ── 2. migration 0066: (user_id, file_hash) partial index ────────────────────


@needs_db
class TestDedupIndexMigration:
    def test_partial_dedup_index_exists(self):
        """`ingest_activity`'s per-member idempotency probe
        (WHERE user_id = :u AND file_hash = :h) needs 0066's partial index —
        without it every member of a drain scans the user's whole activity
        set. Fails until `alembic upgrade head` applies 0066."""
        from app.db.session import SessionLocal
        db = SessionLocal()
        try:
            idxdef = db.execute(sa_text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'activities' "
                "AND indexname = 'ix_activities_user_file_hash'"
            )).scalar()
        finally:
            db.close()
        assert idxdef, "ix_activities_user_file_hash missing — run alembic upgrade head"
        assert "user_id" in idxdef and "file_hash" in idxdef
        assert "file_hash IS NOT NULL" in idxdef  # partial: Strava rows excluded


# ── 3. raw mode skips the vestigial spatial pre-sort ─────────────────────────


class TestRawModeSkipsSpatialPresort:
    """The pre-sort probe loads EVERY member's bytes from storage a second
    time, purely to warm an OSM segment cache that does not exist under the
    raw-trace pivot. DB-free: same seam style as
    test_drain_spatial_locality_and_cache."""

    def _run_drain(self, monkeypatch) -> int:
        """Run the REAL drain_pending over 2 fake claimed members; return how
        many times member bytes were loaded from storage."""
        from app.services import archive_intake

        rows = [
            {"id": f"m{i}", "user_id": "u1", "storage_backend": "local",
             "storage_key": f"k{i}", "original_filename": f"r{i}.gpx",
             "sport": "road", "contribute_heatmap": True,
             "source": "manual_upload"}
            for i in range(2)
        ]
        loads = []
        monkeypatch.setattr(
            archive_intake, "load_member",
            lambda backend, key: loads.append(key) or _GOOD_GPX,
        )
        monkeypatch.setattr(drain_mod, "_claim_batch", lambda db, lim: rows)
        monkeypatch.setattr(drain_mod, "_finish", lambda *a, **k: None)
        monkeypatch.setattr(drain_mod, "_apply_drain_statement_timeout", lambda: None)
        monkeypatch.setattr(drain_mod, "_recompute_heat_agg_batched", lambda w: None)
        monkeypatch.setattr(drain_mod, "_ping_pmtiles_rebuild", lambda i, s: None)
        monkeypatch.setattr(
            drain_mod, "_ingest_member_bytes",
            lambda **kw: ("imported", "act-id", None),
        )
        monkeypatch.setattr("app.db.session.SessionLocal", MagicMock())

        summary = drain_mod.drain_pending(limit=2, pace_seconds=0.0)
        assert summary["imported"] == 2, summary
        return len(loads)

    def test_raw_mode_loads_each_member_once(self, monkeypatch):
        monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")
        # FAILS pre-fix: the probe double-read → 4 loads for 2 members.
        assert self._run_drain(monkeypatch) == 2

    def test_matched_mode_still_presorts(self, monkeypatch):
        monkeypatch.delenv("HEATMAP_DISPLAY_SOURCE", raising=False)
        # Legacy matched mode keeps the locality sort: probe + ingest loads.
        assert self._run_drain(monkeypatch) == 4


# ── 4. husk guard: no-geometry member → skipped, never a row ─────────────────


@needs_db
class TestHuskGuard:
    def test_trackless_member_is_skipped_and_creates_no_row(self):
        from app.db.session import SessionLocal

        uid = f"hygiene-test-husk-{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        try:
            outcome, activity_id, reason = drain_mod._ingest_member_bytes(
                user_id=uid,
                filename="empty_ride.gpx",
                raw=_TRACKLESS_GPX,
                resolved_sport="road",
                contribute_heatmap=True,
                source="manual_upload",
                skip_heat_computation=True,
            )
            # FAILS pre-fix: ("imported", <uuid>, None) + a husk row.
            assert outcome == "skipped"
            assert activity_id is None
            assert reason == "no_geometry"
            n = db.execute(sa_text(
                "SELECT count(*) FROM activities WHERE user_id = :u"), {"u": uid}
            ).scalar()
            assert n == 0, "a no-geometry member must never create an activity row"
        finally:
            _cleanup(db, user_ids=[uid])
            db.close()

    def test_real_member_still_imports(self):
        from app.db.session import SessionLocal

        uid = f"hygiene-test-good-{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        try:
            outcome, activity_id, reason = drain_mod._ingest_member_bytes(
                user_id=uid,
                filename="real_ride.gpx",
                raw=_GOOD_GPX,
                resolved_sport="road",
                contribute_heatmap=True,
                source="manual_upload",
                skip_heat_computation=True,
            )
            assert (outcome, reason) == ("imported", None)
            geom = db.execute(sa_text(
                "SELECT geometry_geojson FROM activities WHERE id = :i"
            ), {"i": activity_id}).scalar()
            assert geom, "the imported row must carry its geometry"
        finally:
            _cleanup(db, user_ids=[uid])
            db.close()

    def test_archive_drain_counts_husk_as_skipped(self, tmp_path, monkeypatch):
        """End-to-end through the REAL whole-archive drain: a zip with one
        real ride + one trackless husk must finish 'done' with imported=1,
        skipped=1 (pre-fix: imported=2 and an empty husk row — the 2026-08
        recovery-report bug) and exactly ONE activity row."""
        from app.db.session import SessionLocal
        from app.services import archive_intake

        monkeypatch.setattr(archive_intake, "ARCHIVE_INTAKE_DIR", str(tmp_path))
        uid = f"hygiene-test-drain-{uuid.uuid4().hex[:8]}"
        db = SessionLocal()
        ids: list[str] = []
        try:
            aid = _mk_archive_row(db, user_id=uid, status="uploaded")
            ids = [aid]
            db.commit()
            arch = dict(db.execute(sa_text(
                "SELECT id, user_id, storage_backend, bucket_key, fallback_sport,"
                "       contribute_heatmap, source "
                "FROM pending_archives WHERE id = :i"), {"i": aid}).mappings().one())
            arch["id"] = str(arch["id"])

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("activities/real.gpx", _GOOD_GPX)
                zf.writestr("activities/husk.gpx", _TRACKLESS_GPX)
            archive_intake.store_archive_local(arch["bucket_key"], buf.getvalue())

            # Claim ONLY our row (never steal a concurrent session's queue),
            # and don't email — everything else is the real drain.
            monkeypatch.setattr(drain_mod, "_claim_archives", lambda d, lim: [arch])
            monkeypatch.setattr(drain_mod, "_notify_archive_terminal",
                                lambda *a, **k: None)

            summary = drain_mod.drain_pending_archives(
                limit=1, pace_seconds=0.0, skip_heat_computation=True)

            assert summary["imported"] == 1, summary   # FAILS pre-fix (2)
            assert summary["skipped"] == 1, summary
            assert summary["failed"] == 0, summary
            row = _archive_row(db, aid)
            assert row["status"] == "done"
            assert (row["imported"], row["skipped"]) == (1, 1)
            n = db.execute(sa_text(
                "SELECT count(*) FROM activities WHERE user_id = :u"), {"u": uid}
            ).scalar()
            assert n == 1, "the husk must not have created a second row"
        finally:
            _cleanup(db, user_ids=[uid], archive_ids=ids)
            db.close()
