"""perf(drain): one archive ingestion must never DoS the site (2026-07-20 incident).

A single 2,813-activity archive drain pegged the shared db-f1-micro for ~7 h
(per-activity heat_edges_agg recompute × dozens of repeats on the same OSM
ways + no statement budget) until Cloud Run returned 429 — site DOWN.

These tests drive the REAL handlers (drain_pending_archives / drain_pending /
ingest_activity / the /imports/files endpoint) — only the geometric heavy
lifting (``_update_heat_edges``) is stubbed where the test needs deterministic
touched-way sets. Pinned contracts:

  (a) a multi-activity archive drain calls ``recompute_heat_agg_for_ways``
      ONCE with the deduplicated union of touched ways — never per-activity;
  (b) the small interactive ``/imports/files`` path KEEPS the per-activity
      incremental ``_maintain_heat_agg`` (near-live map updates);
  (c) an archive FAILURE mid-way still recomputes the partial set (the
      heat_edges rows of already-ingested members are committed — the
      aggregate must not drift);
  (d) the drain applies a Postgres ``statement_timeout`` to its DB
      connections (env ``DRAIN_STATEMENT_TIMEOUT_MS``);
  (e) ``/readyz`` still answers 200 on a healthy DB after the sync-def +
      short-statement-timeout hardening.
"""
import io
import zipfile
from unittest.mock import MagicMock, patch

import gpxpy
import gpxpy.gpx
import pytest
from sqlalchemy import text as sa_text

from app.jobs import ingest_pending_archives as drain_mod
from app.services import ingest as ingest_service


def _gpx(name: str, lat0: float, lon0: float, n: int = 8) -> str:
    gpx = gpxpy.gpx.GPX()
    trk = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(trk)
    seg = gpxpy.gpx.GPXTrackSegment()
    trk.segments.append(seg)
    for i in range(n):
        seg.points.append(gpxpy.gpx.GPXTrackPoint(
            latitude=lat0 + i * 0.001, longitude=lon0 + i * 0.001, elevation=100 + i,
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


def _fake_update_heat_edges(way_sets: list[set[int]]):
    """A ``_update_heat_edges`` stand-in that feeds deterministic touched-way
    sets into the ``collect_touched_ways`` collector the REAL ``ingest_activity``
    threads through (or fails the test if the collector wasn't passed)."""
    remaining = list(way_sets)

    def fake(user_id, sport, geojson_str, activity_date=None, activity_id=None,
             collect_touched_ways=None):
        assert collect_touched_ways is not None, (
            "drain path must pass collect_touched_ways down to _update_heat_edges"
        )
        collect_touched_ways.update(remaining.pop(0) if remaining else set())
        return 1, set()

    return fake


class TestArchiveDrainBatchesAggRecompute:
    def test_one_recompute_with_deduplicated_union(
        self, client, auth_headers, _no_engine_mutation
    ):
        """(a) 2-activity archive → recompute_heat_agg_for_ways called ONCE
        with the deduplicated, sorted union of both activities' ways."""
        from app.db.models import PendingArchive
        from app.db.session import SessionLocal

        body = _init_put_complete(client, auth_headers, _archive_zip())

        recompute_spy = MagicMock(return_value=(0, 3))
        maintain_spy = MagicMock()
        # Overlapping way sets: 202 touched by BOTH activities → must dedup.
        fake_uhe = _fake_update_heat_edges([{101, 202}, {202, 303}])

        with (
            patch.object(ingest_service, "_update_heat_edges", fake_uhe),
            patch.object(ingest_service, "_maintain_heat_agg", maintain_spy),
            patch("app.jobs.rebuild_heat_agg.recompute_heat_agg_for_ways", recompute_spy),
        ):
            summary = drain_mod.drain_pending_archives(
                limit=5, pace_seconds=0.0, skip_heat_computation=False,
            )

        assert summary["archives"] == 1, summary
        assert summary["imported"] == 2, summary
        assert summary["failed"] == 0, summary

        # ONE batched recompute — not per-activity — with the dedup'd union.
        assert recompute_spy.call_count == 1, recompute_spy.call_args_list
        _db_arg, ways_arg = recompute_spy.call_args[0]
        assert ways_arg == [101, 202, 303]
        # The per-activity incremental hook never fired during the drain.
        maintain_spy.assert_not_called()
        # The drain armed the statement budget.
        _no_engine_mutation.assert_called()

        db = SessionLocal()
        try:
            assert db.get(PendingArchive, body["archive_id"]).status == "done"
        finally:
            db.close()

    def test_recompute_chunked_over_500_ways(
        self, client, auth_headers, _no_engine_mutation
    ):
        """A huge union is recomputed in <=500-way chunks (still one batch,
        never one giant statement)."""
        _init_put_complete(client, auth_headers, _archive_zip())

        recompute_spy = MagicMock(return_value=(0, 1))
        big = set(range(1, 1101))  # 1100 ways → 3 chunks of <=500
        fake_uhe = _fake_update_heat_edges([big, set()])

        with (
            patch.object(ingest_service, "_update_heat_edges", fake_uhe),
            patch("app.jobs.rebuild_heat_agg.recompute_heat_agg_for_ways", recompute_spy),
        ):
            drain_mod.drain_pending_archives(
                limit=5, pace_seconds=0.0, skip_heat_computation=False,
            )

        assert recompute_spy.call_count == 3
        seen: list[int] = []
        for call in recompute_spy.call_args_list:
            chunk = call[0][1]
            assert len(chunk) <= drain_mod.HEAT_AGG_WAYS_PER_CHUNK
            seen.extend(chunk)
        assert seen == sorted(big)

    def test_archive_failure_midway_still_recomputes_partial_set(
        self, client, auth_headers, monkeypatch, _no_engine_mutation
    ):
        """(c) member 1 ingests, then the drain blows up mid-run → the archive
        is marked failed AND the ways touched so far are still recomputed (the
        heat_edges rows exist — the aggregate must not drift).

        Post spatial-ordering (2026-07-20) ingest happens in a SECOND pass, so
        the explosion is injected via the inter-member pacing sleep (which lives
        OUTSIDE the per-member error isolation) — it reaches the archive-level
        handler after the first member has already committed."""
        from app.db.models import PendingArchive
        from app.db.session import SessionLocal

        body = _init_put_complete(client, auth_headers, _archive_zip())

        def exploding_sleep(_seconds):
            raise RuntimeError("drain exploded between members")

        monkeypatch.setattr(drain_mod.time, "sleep", exploding_sleep)

        recompute_spy = MagicMock(return_value=(0, 2))
        fake_uhe = _fake_update_heat_edges([{7, 8}])

        with (
            patch.object(ingest_service, "_update_heat_edges", fake_uhe),
            patch("app.jobs.rebuild_heat_agg.recompute_heat_agg_for_ways", recompute_spy),
        ):
            summary = drain_mod.drain_pending_archives(
                limit=5, pace_seconds=0.01, skip_heat_computation=False,
            )

        assert summary["archives_failed"] == 1, summary
        assert recompute_spy.call_count == 1
        assert recompute_spy.call_args[0][1] == [7, 8]

        db = SessionLocal()
        try:
            arch = db.get(PendingArchive, body["archive_id"])
            assert arch.status == "failed"
            assert arch.imported == 1
        finally:
            db.close()


class TestPerMemberQueueDrainBatchesToo:
    def test_drain_pending_batches_once_per_run(
        self, client, auth_headers, _no_engine_mutation
    ):
        """The legacy per-member queue drain also defers to ONE end-of-batch
        recompute."""
        # Enqueue via the REAL small-zip endpoint (per-member queue, 0059).
        resp = client.post(
            "/imports/strava-archive",
            headers=auth_headers,
            files={"file": ("export.zip", _archive_zip(), "application/zip")},
            data={
                "sport": "road",
                "consent": "true",
                "consent_version": "v1",
                "consent_text": "I consent",
            },
        )
        assert resp.status_code == 202, resp.text

        recompute_spy = MagicMock(return_value=(0, 2))
        maintain_spy = MagicMock()
        fake_uhe = _fake_update_heat_edges([{11, 22}, {22, 33}])

        with (
            patch.object(ingest_service, "_update_heat_edges", fake_uhe),
            patch.object(ingest_service, "_maintain_heat_agg", maintain_spy),
            patch("app.jobs.rebuild_heat_agg.recompute_heat_agg_for_ways", recompute_spy),
        ):
            summary = drain_mod.drain_pending(
                limit=50, pace_seconds=0.0, skip_heat_computation=False,
            )

        assert summary["imported"] == 2, summary
        assert recompute_spy.call_count == 1
        assert recompute_spy.call_args[0][1] == [11, 22, 33]
        maintain_spy.assert_not_called()


class TestInteractivePathKeepsIncremental:
    def test_imports_files_calls_maintain_heat_agg_per_activity(
        self, client, auth_headers
    ):
        """(b) the small interactive /imports/files path is UNCHANGED: the
        per-activity incremental ``_maintain_heat_agg`` fires for each
        ingested activity (near-live map updates)."""
        maintain_spy = MagicMock()
        with patch.object(ingest_service, "_maintain_heat_agg", maintain_spy):
            resp = client.post(
                "/imports/files",
                headers=auth_headers,
                files={"file": ("batch.zip", _archive_zip(), "application/zip")},
                data={"sport": "road", "contribute_heatmap": "true",
                      "consent_version": "v-drain-seed", "consent_text": "ODbL ok", "locale": "fr"},
            )
        assert resp.status_code == 202, resp.text
        assert resp.json()["imported"] == 2, resp.json()
        # One incremental call per activity — the interactive contract.
        assert maintain_spy.call_count == 2, maintain_spy.call_args_list


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
    def test_pace_default_comes_from_env(self, monkeypatch):
        monkeypatch.setenv("DRAIN_PACE_SECONDS", "2.5")
        assert drain_mod._drain_pace_seconds() == 2.5

    def test_pace_default_without_env(self, monkeypatch):
        monkeypatch.delenv("DRAIN_PACE_SECONDS", raising=False)
        assert drain_mod._drain_pace_seconds() == 0.25

    def test_garbage_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("DRAIN_PACE_SECONDS", "not-a-float")
        assert drain_mod._drain_pace_seconds() == 0.25


class TestReadyzStillHealthy:
    def test_readyz_200_on_healthy_db(self, client):
        """(e) the sync-def + short-statement-timeout hardening must not break
        the happy path."""
        resp = client.get("/readyz")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] in ("ok", "warming")
        assert "heat_edges" in body
