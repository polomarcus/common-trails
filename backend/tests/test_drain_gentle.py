"""perf(drain): one archive ingestion must never DoS the site (2026-07-20 incident).

A single 2,813-activity archive drain pegged the shared db-f1-micro for ~7 h
until Cloud Run returned 429 — site DOWN. (The heavy per-activity work of that
era — the matched pipeline — is gone; the DB-gentleness knobs remain.)

Pinned contracts:

  (0) an archive drain only writes ``activities`` + ``activity_cells`` — no
      statement ever reaches a matched-pipeline table;
  (a) the drain applies a Postgres ``statement_timeout`` to its DB
      connections (env ``DRAIN_STATEMENT_TIMEOUT_MS``);
  (b) inter-member pacing: adaptive by default, fixed when pinned;
  (c) ``/readyz`` still answers 200 on a healthy DB after the sync-def +
      short-statement-timeout hardening.
"""
import io
import zipfile
import zlib
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import gpxpy
import gpxpy.gpx
import pytest
from sqlalchemy import text as sa_text

from app.jobs import ingest_pending_archives as drain_mod

_T0 = datetime(2024, 1, 1, 7, 0, tzinfo=UTC)

def _gpx(name: str, lat0: float, lon0: float, n: int = 8) -> str:
    gpx = gpxpy.gpx.GPX()
    trk = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(trk)
    seg = gpxpy.gpx.GPXTrackSegment()
    trk.segments.append(seg)
    # Timestamped: an undated trace never feeds the public heatmap (provenance SSOT).
    _start = _T0 + timedelta(minutes=zlib.crc32(f"{name}{lon0}{lat0}".encode()) % 500_000)
    for i in range(n):
        seg.points.append(gpxpy.gpx.GPXTrackPoint(
            latitude=lat0 + i * 0.001, longitude=lon0 + i * 0.001, elevation=100 + i,
            time=_start + timedelta(seconds=10 * i),
        ))
    return gpx.to_xml()


def _archive_zip() -> bytes:
    """Two distinct in-scope members (different coords → different file_hash)."""
    csv = (
        "Activity ID,Activity Date,Activity Name,Activity Type,Filename\n"
        "1,2024-01-01,VTT session,MountainBikeRide,activities/mtb1.gpx\n"
        "2,2024-01-02,Morning spin,Ride,activities/road1.gpx\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("activities.csv", csv)
        zf.writestr("activities/mtb1.gpx", _gpx("VTT session", 44.10, 3.60))
        zf.writestr("activities/road1.gpx", _gpx("Morning spin", 45.75, 4.83))
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolate_archive_tables():
    """The drains claim pending rows GLOBALLY — sibling leftovers must not leak."""
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


@pytest.fixture
def _no_engine_mutation(monkeypatch):
    """Replace the engine-level statement-timeout registration with a spy so
    drain tests don't mutate the shared test-process engine; the real
    registration is exercised in TestStatementTimeout."""
    spy = MagicMock()
    monkeypatch.setattr(drain_mod, "_apply_drain_statement_timeout", spy)
    return spy


def _init_put_complete(client, auth_headers, payload: bytes) -> dict:
    body = client.post(
        "/imports/strava-archive/init",
        headers=auth_headers,
        json={
            "sport": "road",
            "consent": True,
            "consent_version": "strava-archive-2026-07-v2",
            "consent_text": "Je consens à contribuer mes traces (ODbL).",
            "locale": "fr",
            "filename": "export.zip",
        },
    ).json()
    put = client.put(body["upload_url"], headers=auth_headers, content=payload)
    assert put.status_code == 200, put.text
    comp = client.post(
        "/imports/strava-archive/complete",
        headers=auth_headers,
        json={"archive_id": body["archive_id"], "key": body["bucket_key"]},
    )
    assert comp.status_code == 202, comp.text
    return body


class TestArchiveDrainIsLight:
    def test_archive_drain_writes_activities_only(
        self, client, auth_headers, _no_engine_mutation,
    ):
        """(0) The REAL whole-archive drain imports both members and its SQL
        never names a table of the removed matched pipeline."""
        import re

        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        _init_put_complete(client, auth_headers, _archive_zip())
        statements: list[str] = []

        def _record(_conn, _cursor, statement, _params, _context, _executemany):
            statements.append(statement)

        event.listen(Engine, "before_cursor_execute", _record)
        try:
            summary = drain_mod.drain_pending_archives(limit=5, pace_seconds=0.0)
        finally:
            event.remove(Engine, "before_cursor_execute", _record)

        assert summary["archives"] == 1 and summary["archives_failed"] == 0, summary
        assert summary["imported"] == 2 and summary["failed"] == 0, summary
        _no_engine_mutation.assert_called_once()
        matched = re.compile(
            r"\b(heat_edges\w*|heat_edge_contributors|heat_cells|heat_cell_contributors|"
            r"osm_road_edges|osm_ways|edge_version_state|trail_edges)\b"
        )
        assert [s for s in statements if matched.search(s)] == []
        inserted = {m.group(1) for s in statements
                    if (m := re.match(r"\s*INSERT INTO (\w+)", s, re.I))}
        assert {"activities", "activity_cells"} <= inserted


class TestStatementTimeout:
    def test_drain_statement_timeout_is_applied_to_sessions(self, monkeypatch):
        """(d) DRAIN_STATEMENT_TIMEOUT_MS lands as a real Postgres
        ``statement_timeout`` on connections opened after the drain armed it."""
        from sqlalchemy import event

        from app.db.session import SessionLocal, engine

        monkeypatch.setenv("DRAIN_STATEMENT_TIMEOUT_MS", "120000")
        # Reset the once-per-process guard (an earlier test may have armed it
        # via the spy fixture — never for real).
        monkeypatch.setattr(drain_mod, "_statement_timeout_registered", False)
        monkeypatch.setattr(drain_mod, "_statement_timeout_listener", None)

        drain_mod._apply_drain_statement_timeout()
        assert drain_mod._statement_timeout_registered is True
        listener = drain_mod._statement_timeout_listener
        assert listener is not None
        try:
            db = SessionLocal()
            try:
                value = db.execute(sa_text("SHOW statement_timeout")).scalar()
            finally:
                db.close()
            assert value == "2min", value  # 120000 ms
        finally:
            # Un-arm so the rest of the suite runs on default connections.
            event.remove(engine, "connect", listener)
            engine.dispose()

    def test_zero_disables(self, monkeypatch):
        monkeypatch.setenv("DRAIN_STATEMENT_TIMEOUT_MS", "0")
        monkeypatch.setattr(drain_mod, "_statement_timeout_registered", False)
        monkeypatch.setattr(drain_mod, "_statement_timeout_listener", None)
        drain_mod._apply_drain_statement_timeout()
        assert drain_mod._statement_timeout_registered is False
        assert drain_mod._statement_timeout_listener is None


class TestPacing:
    """Legacy FIXED pacing helper (still the fallback when adaptive is off)."""
    def test_pace_default_comes_from_env(self, monkeypatch):
        monkeypatch.setenv("DRAIN_PACE_SECONDS", "2.5")
        assert drain_mod._drain_pace_seconds() == 2.5

    def test_pace_default_without_env(self, monkeypatch):
        monkeypatch.delenv("DRAIN_PACE_SECONDS", raising=False)
        assert drain_mod._drain_pace_seconds() == 0.25

    def test_garbage_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("DRAIN_PACE_SECONDS", "not-a-float")
        assert drain_mod._drain_pace_seconds() == 0.25


class TestAdaptivePacing:
    """Adaptive pacing: mode resolution + the per-member sleep computation."""

    def test_default_is_adaptive(self, monkeypatch):
        monkeypatch.delenv("DRAIN_PACE_SECONDS", raising=False)
        mode, value = drain_mod._resolve_pace_mode(None)
        assert mode == "adaptive"
        assert value == drain_mod._DEFAULT_PACE_RATIO  # 1.0

    def test_explicit_pace_arg_pins_fixed(self):
        assert drain_mod._resolve_pace_mode(0.0) == ("fixed", 0.0)
        assert drain_mod._resolve_pace_mode(0.5) == ("fixed", 0.5)

    def test_negative_pace_arg_clamped_to_zero(self):
        assert drain_mod._resolve_pace_mode(-3.0) == ("fixed", 0.0)

    def test_env_pace_seconds_pins_fixed(self, monkeypatch):
        monkeypatch.setenv("DRAIN_PACE_SECONDS", "2.5")
        assert drain_mod._resolve_pace_mode(None) == ("fixed", 2.5)

    def test_blank_env_is_adaptive(self, monkeypatch):
        monkeypatch.setenv("DRAIN_PACE_SECONDS", "  ")
        assert drain_mod._resolve_pace_mode(None)[0] == "adaptive"

    def test_garbage_env_falls_back_to_adaptive(self, monkeypatch):
        monkeypatch.setenv("DRAIN_PACE_SECONDS", "not-a-float")
        assert drain_mod._resolve_pace_mode(None)[0] == "adaptive"

    def test_ratio_env_tunable(self, monkeypatch):
        monkeypatch.setenv("DRAIN_PACE_RATIO", "2.0")
        assert drain_mod._pace_ratio() == 2.0
        monkeypatch.setenv("DRAIN_PACE_RATIO", "junk")
        assert drain_mod._pace_ratio() == drain_mod._DEFAULT_PACE_RATIO

    def test_cap_env_tunable(self, monkeypatch):
        monkeypatch.setenv("DRAIN_PACE_MAX_SECONDS", "0.5")
        assert drain_mod._pace_cap_seconds() == 0.5

    def test_fixed_next_pace_ignores_work(self):
        # fixed mode → always the pinned value, regardless of last_work_s / cap.
        assert drain_mod._next_pace("fixed", 0.3, None, 1.0) == 0.3
        assert drain_mod._next_pace("fixed", 0.3, 99.0, 0.1) == 0.3

    def test_adaptive_first_member_no_sleep(self):
        # No prior measurement → the first member never pre-sleeps.
        assert drain_mod._next_pace("adaptive", 1.0, None, 1.0) == 0.0

    def test_adaptive_scales_with_work_and_ratio(self):
        # sleep = last_work × ratio.
        assert drain_mod._next_pace("adaptive", 1.0, 0.01, 1.0) == 0.01
        assert drain_mod._next_pace("adaptive", 2.0, 0.01, 1.0) == 0.02

    def test_adaptive_capped(self):
        # A slow/stalled ingest can't translate into an unbounded sleep.
        assert drain_mod._next_pace("adaptive", 1.0, 5.0, 1.0) == 1.0


class TestReadyzStillHealthy:
    def test_readyz_200_on_healthy_db(self, client):
        """(e) the sync-def + short-statement-timeout hardening must not break
        the happy path."""
        resp = client.get("/readyz")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] in ("ok", "warming")
        assert "version" in body
