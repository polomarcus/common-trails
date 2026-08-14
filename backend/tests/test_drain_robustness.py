"""Drain robustness — MVP-audit gaps 1+2 between a REAL Strava archive and the map.

Prod evidence (2026-07, archive ``f1ed34db``, 817 MB / 2813 activities):

  1. The zip-bomb caps counted media members (thousands of ``media/*.jpg``
     that are NEVER read): after the junk fix the archive would trip the
     member cap again on media, then the 500 MB total-uncompressed cap.
     → caps now count INGESTIBLE members only (.gpx/.fit ± .gz).
  2. A timeout-killed drain job stranded rows in ``processing`` forever
     (Cloud Run reports SUCCESS on a timeout kill), and an ops env mistake
     (missing UPLOADS_BUCKET) burned a user's row as ``failed``.
     → stale-processing reclaim + admin requeue + config-error clarity.

All tests drive the REAL functions/endpoints (no inline mirrors) and fail
on the pre-fix code.
"""
import io
import uuid
import zipfile
from unittest.mock import MagicMock, patch

import gpxpy
import gpxpy.gpx
import pytest
from sqlalchemy import text as sa_text


def _gpx_xml(n: int = 8) -> str:
    gpx = gpxpy.gpx.GPX()
    trk = gpxpy.gpx.GPXTrack(name="ride")
    gpx.tracks.append(trk)
    seg = gpxpy.gpx.GPXTrackSegment()
    trk.segments.append(seg)
    for i in range(n):
        seg.points.append(gpxpy.gpx.GPXTrackPoint(
            latitude=44.0 + i * 0.001, longitude=4.0 + i * 0.001, elevation=100 + i,
        ))
    return gpx.to_xml()


def _zip_with_media(gpx_count: int, media_count: int, media_size: int = 64) -> bytes:
    """A realistic Strava-export shape: activities/*.gpx + media/*.jpg
    (photos are listed in the zip but NEVER read by any drain)."""
    gpx_xml = _gpx_xml()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(gpx_count):
            zf.writestr(f"activities/{i}.gpx", gpx_xml)
        for i in range(media_count):
            zf.writestr(f"media/{i}.jpg", b"\xff\xd8" + b"x" * media_size)
    return buf.getvalue()


def _iter_members(zip_bytes: bytes) -> list[tuple]:
    from app.services import archive_intake
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return list(archive_intake.iter_zip_members(zf))


# ── Gap 1: caps count only INGESTIBLE members ────────────────────────────────


class TestMemberCapIngestibleOnly:
    """Old code: 30 gpx + 5001 media = 5031 members > 5000 → whole-archive
    reject, although the 5001 media are never ``zf.read()``."""

    def test_media_members_dont_count_iter_zip_members(self):
        members = _iter_members(_zip_with_media(gpx_count=30, media_count=5001))
        assert len(members) == 30
        assert all(n.endswith(".gpx") for n, _r, _s in members)

    def test_media_members_dont_count_parse_zip_of_gpx(self):
        from app.services import gpx as gpx_service
        results = gpx_service.parse_zip_of_gpx(_zip_with_media(30, 5001))
        assert len([r for r in results if "error" not in r]) == 30

    def test_ingestible_count_over_cap_still_raises(self, monkeypatch):
        from app.services import archive_intake  # noqa: F401 — real path under test
        from app.services import gpx as gpx_service
        monkeypatch.setattr(gpx_service, "MAX_ZIP_MEMBERS", 40)
        # Error message reports the INGESTIBLE count (45), not raw 145.
        with pytest.raises(gpx_service.ZipBombError, match=r"\(45 > 40\)"):
            _iter_members(_zip_with_media(gpx_count=45, media_count=100))
        with pytest.raises(gpx_service.ZipBombError, match=r"\(45 > 40\)"):
            gpx_service.parse_zip_of_gpx(_zip_with_media(45, 100))


class TestTotalUncompressedCapIngestibleOnly:
    """Old code summed ALL members' uncompressed sizes — a media-heavy
    legitimate export tripped the cap although photos are never read."""

    def test_media_bytes_dont_count(self, monkeypatch):
        from app.services import gpx as gpx_service
        monkeypatch.setattr(gpx_service, "MAX_ZIP_TOTAL_UNCOMPRESSED", 50_000)
        # 40 media × 2000 B ≈ 80 KB > 50 KB cap; 5 gpx stay well under it.
        z = _zip_with_media(gpx_count=5, media_count=40, media_size=2000)
        assert len(_iter_members(z)) == 5
        results = gpx_service.parse_zip_of_gpx(z)
        assert len([r for r in results if "error" not in r]) == 5

    def test_ingestible_total_over_cap_still_raises(self, monkeypatch):
        from app.services import gpx as gpx_service
        monkeypatch.setattr(gpx_service, "MAX_ZIP_TOTAL_UNCOMPRESSED", 5_000)
        z = _zip_with_media(gpx_count=30, media_count=0)  # 30 × ~1.5 KB gpx > 5 KB
        with pytest.raises(gpx_service.ZipBombError, match="possible zip-bomb"):
            _iter_members(z)
        with pytest.raises(gpx_service.ZipBombError, match="possible zip-bomb"):
            gpx_service.parse_zip_of_gpx(z)

    def test_defaults_are_env_tunable_and_total_matches_upload_cap(self):
        # The advertised MAX_ARCHIVE_UPLOAD_BYTES admits a 2 GB *compressed*
        # archive; the decompressed-total default must sit above it.
        from app.services import archive_intake
        from app.services import gpx as gpx_service
        assert gpx_service.MAX_ZIP_TOTAL_UNCOMPRESSED >= archive_intake.MAX_ARCHIVE_BYTES
        # The per-member cap tracks the single-file cap (both from MAX_GPX_SIZE_BYTES,
        # 25 MB default — raised from 10 MB so real long Garmin rides aren't 413'd).
        assert gpx_service.MAX_ZIP_MEMBER_UNCOMPRESSED == gpx_service.MAX_GPX_SIZE


# ── Gap 2: recovery paths ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_archive_tables():
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        db.execute(sa_text(
            "TRUNCATE pending_archive_files, pending_archives, contribution_consents"
        ))
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture(autouse=True)
def _local_intake_dir(tmp_path, monkeypatch):
    from app.services import archive_intake
    monkeypatch.setattr(archive_intake, "ARCHIVE_INTAKE_DIR", str(tmp_path / "intake"))
    monkeypatch.setattr(archive_intake, "UPLOADS_BUCKET", "")


def _mk_archive(
    status: str,
    *,
    attempts: int = 1,
    storage_backend: str = "local",
    last_error: str | None = None,
    user_id: str | None = None,
) -> str:
    from app.db.models import PendingArchive
    from app.db.session import SessionLocal
    uid = user_id or str(uuid.uuid4())
    db = SessionLocal()
    try:
        row = PendingArchive(
            user_id=uid,
            storage_backend=storage_backend,
            bucket_key=f"archive-intake/{uid}/{uuid.uuid4()}.zip",
            fallback_sport="road",
            contribute_heatmap=True,
            status=status,
            attempts=attempts,
            last_error=last_error,
        )
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def _backdate(archive_id: str, hours: int) -> None:
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        db.execute(sa_text(
            "UPDATE pending_archives "
            "SET updated_at = now() - make_interval(hours => :h) WHERE id = :id"
        ), {"h": hours, "id": archive_id})
        db.commit()
    finally:
        db.close()


def _row(archive_id: str) -> dict:
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        r = db.execute(sa_text(
            "SELECT status, attempts, last_error FROM pending_archives WHERE id = :id"
        ), {"id": archive_id}).mappings().one()
        return dict(r)
    finally:
        db.close()


class TestClaimReclaimsStaleProcessing:
    """A timeout-killed drain leaves claimed rows stranded in ``processing``;
    the OLD claim query (status='uploaded' only) never picked them up again."""

    def test_stale_processing_reclaimed_fresh_and_exhausted_not(self):
        from app.db.session import SessionLocal
        from app.jobs.ingest_pending_archives import _claim_archives

        stale = _mk_archive("processing", attempts=1)
        _backdate(stale, 4)
        fresh = _mk_archive("processing", attempts=1)  # updated_at = now()
        exhausted = _mk_archive("processing", attempts=5)
        _backdate(exhausted, 4)
        uploaded = _mk_archive("uploaded", attempts=0)

        db = SessionLocal()
        try:
            # Raw-SQL rows surface uuid.UUID; the model id is its string form.
            claimed = {str(r["id"]) for r in _claim_archives(db, 10)}
        finally:
            db.close()

        assert stale in claimed        # FAILS on old code
        assert uploaded in claimed     # unchanged behaviour
        assert fresh not in claimed    # actively being worked on
        assert exhausted not in claimed  # poison archive — attempts >= 5

        assert _row(stale)["status"] == "processing"
        assert _row(stale)["attempts"] == 2  # claim bumps attempts


class TestAdminRequeue:
    def _admin_headers(self, client) -> dict[str, str]:
        from app.db.session import SessionLocal
        email = f"admin_{uuid.uuid4().hex[:8]}@example.com"
        resp = client.post("/auth/register", json={
            "email": email, "password": "testpass123",
            "username": f"adm_{uuid.uuid4().hex[:6]}",
        })
        assert resp.status_code == 201, resp.text
        token = resp.json()["access_token"]
        db = SessionLocal()
        try:
            db.execute(sa_text("UPDATE users SET is_admin = true WHERE email = :e"),
                       {"e": email})
            db.commit()
        finally:
            db.close()
        return {"Authorization": f"Bearer {token}"}

    def test_requires_auth_and_admin(self, client, auth_headers):
        rid = _mk_archive("failed", last_error="boom")
        assert client.post(f"/admin/archives/{rid}/requeue").status_code == 401
        assert client.post(
            f"/admin/archives/{rid}/requeue", headers=auth_headers
        ).status_code == 403
        assert _row(rid)["status"] == "failed"  # untouched

    def test_unknown_and_malformed_id_404(self, client):
        headers = self._admin_headers(client)
        assert client.post(
            f"/admin/archives/{uuid.uuid4()}/requeue", headers=headers
        ).status_code == 404
        assert client.post(
            "/admin/archives/not-a-uuid/requeue", headers=headers
        ).status_code == 404

    def test_failed_row_requeued_and_job_triggered(self, client):
        from app.services import run_jobs
        headers = self._admin_headers(client)
        rid = _mk_archive("failed", last_error="ZIP has too many members")
        with patch.object(run_jobs, "trigger_ingest_archives_job",
                          MagicMock(return_value=True)) as trig:
            resp = client.post(f"/admin/archives/{rid}/requeue", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"archive_id": rid, "status": "uploaded", "enqueue": "triggered"}
        trig.assert_called_once()
        row = _row(rid)
        assert row["status"] == "uploaded"
        assert row["last_error"] is None

    def test_trigger_failure_does_not_fail_requeue(self, client):
        from app.services import run_jobs
        headers = self._admin_headers(client)
        rid = _mk_archive("failed", last_error="boom")
        with patch.object(run_jobs, "trigger_ingest_archives_job",
                          MagicMock(side_effect=RuntimeError("gcp down"))):
            resp = client.post(f"/admin/archives/{rid}/requeue", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["enqueue"] == "scheduled"
        assert _row(rid)["status"] == "uploaded"

    @pytest.mark.parametrize("status", ["done", "awaiting_upload", "uploaded"])
    def test_terminal_or_pristine_statuses_409(self, client, status):
        headers = self._admin_headers(client)
        rid = _mk_archive(status)
        resp = client.post(f"/admin/archives/{rid}/requeue", headers=headers)
        assert resp.status_code == 409, resp.text
        assert _row(rid)["status"] == status

    def test_fresh_processing_409_stale_processing_ok(self, client):
        headers = self._admin_headers(client)
        fresh = _mk_archive("processing")
        assert client.post(
            f"/admin/archives/{fresh}/requeue", headers=headers
        ).status_code == 409
        stale = _mk_archive("processing")
        _backdate(stale, 4)
        resp = client.post(f"/admin/archives/{stale}/requeue", headers=headers)
        assert resp.status_code == 200, resp.text
        assert _row(stale)["status"] == "uploaded"


class TestStorageConfigError:
    def test_archive_size_raises_clear_error_when_bucket_unset(self):
        # _local_intake_dir already monkeypatched UPLOADS_BUCKET to "".
        from app.services import archive_intake
        with pytest.raises(RuntimeError, match="UPLOADS_BUCKET not configured"):
            archive_intake.archive_size("gcs", "archive-intake/u/x.zip")

    def test_drain_leaves_row_uploaded_on_config_error(self):
        """Old code: the crash fell into the generic except → the USER's row
        was marked ``failed`` for an OPS env mistake. New code leaves it
        ``uploaded`` so it drains after the fix."""
        from app.jobs.ingest_pending_archives import drain_pending_archives
        rid = _mk_archive("uploaded", storage_backend="gcs")

        summary = drain_pending_archives(limit=5, pace_seconds=0.0,
                                         skip_heat_computation=True)
        assert summary["archives_failed"] == 1
        row = _row(rid)
        assert row["status"] == "uploaded"  # FAILS on old code ('failed')
        assert row["last_error"] is None
