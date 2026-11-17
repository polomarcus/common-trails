"""Uploads request the community rebuild from the build-pmtiles JOB directly.

* ``/gpx/upload`` and ``/imports/files`` (prod path, TEST_MODE off) call
  ``run_jobs.request_community_rebuild`` — a debounced ``jobs:run`` on the
  build-pmtiles Cloud Run Job — and never create a Cloud Task for the community
  map.
* ``/internal/ingest/heat`` (kept one release as a 200 no-op) is removed.
* ``/internal/artefacts/rebuild`` (the full build run inside the 2 GiB worker,
  fed by the ``artefact-rebuild`` Cloud Task) is removed too.
"""
import zlib
from datetime import UTC, datetime, timedelta

import gpxpy
import gpxpy.gpx
import pytest

_T0 = datetime(2024, 1, 1, 7, 0, tzinfo=UTC)
_CONSENT = {"consent_version": "v-internal-seed", "consent_text": "ODbL ok", "locale": "fr"}


def _make_gpx_bytes(name: str = "Test", n_points: int = 10, *, dated: bool = True) -> bytes:
    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(track)
    segment = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(segment)
    # Per-name offsets (time + position) so each test gets its own activity.
    h = zlib.crc32(name.encode())
    start = _T0 + timedelta(minutes=h % 500_000)
    lat0 = 43.60 + (h % 1000) * 0.0001
    for i in range(n_points):
        segment.points.append(
            gpxpy.gpx.GPXTrackPoint(
                latitude=lat0 + i * 0.001,
                longitude=3.88 + i * 0.001,
                elevation=80 + i,
                time=start + timedelta(seconds=10 * i) if dated else None,
            )
        )
    return gpx.to_xml().encode("utf-8")


@pytest.fixture
def rebuild_requests(monkeypatch):
    """Record every rebuild request made by the API modules; no Cloud Tasks."""
    calls: list[str] = []

    def _fake() -> str:
        calls.append("rebuild")
        return "queued"

    monkeypatch.setattr("app.api.gpx_upload.request_community_rebuild", _fake)
    monkeypatch.setattr("app.api.imports.request_community_rebuild", _fake)
    monkeypatch.setattr("app.services.run_jobs.request_community_rebuild", _fake)
    return calls


@pytest.fixture
def clean_debounce():
    """The build-pmtiles debounce is process-global: start and end clean."""
    from app.services.run_jobs import reset_build_pmtiles_debounce

    reset_build_pmtiles_debounce()
    yield
    reset_build_pmtiles_debounce()


@pytest.fixture
def prod_upload_path(monkeypatch):
    """Take the PRODUCTION branch of the upload handlers (they read
    ``app.config.TEST_MODE`` at request time) without leaving TEST_MODE for
    the rest of the app (auth, Cloud Tasks gates stay in test mode)."""
    monkeypatch.setattr("app.config.TEST_MODE", False)


class TestUploadsRequestRebuildDirectly:
    def test_cloud_tasks_has_no_community_rebuild_enqueue(self):
        """Neither the heat-compute task nor the artefact-rebuild task (the
        in-worker full build) can be enqueued any more."""
        from app.services import cloud_tasks

        assert not hasattr(cloud_tasks, "enqueue_heat_compute")
        assert not hasattr(cloud_tasks, "enqueue_artefact_rebuild")
        assert not hasattr(cloud_tasks, "request_community_rebuild")

    def test_gpx_upload_requests_rebuild(
        self, client, auth_headers, rebuild_requests, prod_upload_path,
    ):
        resp = client.post(
            "/gpx/upload",
            headers=auth_headers,
            files={"file": ("direct.gpx", _make_gpx_bytes("direct rebuild gpx"), "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "true", **_CONSENT},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert rebuild_requests == ["rebuild"]
        assert body["heatmap_status"] == "queued"
        # A pending rebuild is surfaced as "processing", as before.
        assert body["status"] == "processing"

    def test_gpx_upload_opt_out_requests_nothing(
        self, client, auth_headers, rebuild_requests, prod_upload_path,
    ):
        resp = client.post(
            "/gpx/upload",
            headers=auth_headers,
            files={"file": ("optout.gpx", _make_gpx_bytes("opt out gpx"), "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "false"},
        )
        assert resp.status_code == 202, resp.text
        assert rebuild_requests == []
        assert resp.json()["heatmap_status"] == "skipped"

    def test_gpx_upload_undated_requests_nothing(
        self, client, auth_headers, rebuild_requests, prod_upload_path,
    ):
        """An undated trace is personal-only (provenance SSOT) — the old heat
        task no-oped on it, so no rebuild is requested either."""
        resp = client.post(
            "/gpx/upload",
            headers=auth_headers,
            files={"file": ("undated.gpx", _make_gpx_bytes("undated gpx", dated=False), "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "true", **_CONSENT},
        )
        assert resp.status_code == 202, resp.text
        assert rebuild_requests == []
        assert resp.json()["heatmap_status"] == "skipped"

    def test_imports_files_requests_one_rebuild(
        self, client, auth_headers, rebuild_requests, prod_upload_path,
    ):
        resp = client.post(
            "/imports/files",
            headers=auth_headers,
            files={"file": ("imp.gpx", _make_gpx_bytes("direct rebuild imports"), "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "true", **_CONSENT},
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["imported"] == 1
        assert rebuild_requests == ["rebuild"]
        assert resp.json()["heatmap_status"] == "queued"

    def test_imports_files_non_final_file_defers_to_batch_end(
        self, client, auth_headers, rebuild_requests, prod_upload_path,
    ):
        """A non-final file of a batch (rebuild_display=false) asks for nothing:
        the batch's last request does, so a 20-file drop is ONE job run."""
        resp = client.post(
            "/imports/files",
            headers=auth_headers,
            files={"file": ("mid.gpx", _make_gpx_bytes("batch middle file"), "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "true", "rebuild_display": "false", **_CONSENT},
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["imported"] == 1
        assert rebuild_requests == []
        assert resp.json()["heatmap_status"] == "deferred"


class TestRequestCommunityRebuild:
    """``run_jobs.request_community_rebuild``: the debounced build-pmtiles job
    trigger behind the upload endpoints."""

    def test_inline_when_job_trigger_unconfigured(self, clean_debounce):
        from app.services.run_jobs import request_community_rebuild

        # TEST_MODE: the job trigger is gated off, nothing is requested.
        assert request_community_rebuild() == "inline"

    def test_triggers_the_build_job_then_debounces(self, monkeypatch, clean_debounce):
        """Prod wiring: one jobs:run on the build-pmtiles JOB; a second request
        inside the cooldown is suppressed (no second job run)."""
        from app.services import run_jobs

        calls: list[str] = []

        def _fake_trigger() -> bool:
            calls.append("jobs:run")
            return True

        monkeypatch.setattr(run_jobs, "trigger_build_pmtiles_job", _fake_trigger)

        assert run_jobs.request_community_rebuild() == "queued"
        assert run_jobs.request_community_rebuild() == "debounced"
        assert calls == ["jobs:run"]

    def test_failed_when_configured_trigger_fails(self, monkeypatch, clean_debounce):
        from app.services import run_jobs

        monkeypatch.setattr(run_jobs, "trigger_build_pmtiles_job", lambda: False)
        monkeypatch.setattr(run_jobs, "_is_enabled", lambda _env: True)
        assert run_jobs.request_community_rebuild() == "failed"

    def test_never_raises(self, monkeypatch, clean_debounce):
        from app.services import run_jobs

        def _boom() -> bool:
            raise RuntimeError("metadata server unreachable")

        monkeypatch.setattr(run_jobs, "trigger_build_pmtiles_job", _boom)
        assert run_jobs.request_community_rebuild() == "failed"


class TestRetiredInternalRoutes:
    def test_route_is_gone(self, client, rebuild_requests):
        """The no-op kept for one release has been removed with the queue's
        last producer: the path no longer exists and requests nothing."""
        resp = client.post(
            "/internal/ingest/heat",
            json={
                "activity_id": "00000000-0000-0000-0000-000000000000",
                "user_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        assert resp.status_code in (404, 405), resp.text
        assert rebuild_requests == []

    def test_artefacts_rebuild_route_is_gone(self, client, rebuild_requests):
        """The worker no longer runs the full community build in-process: the
        community map is rebuilt only by the build-pmtiles Cloud Run Job."""
        resp = client.post("/internal/artefacts/rebuild", json={})
        assert resp.status_code in (404, 405), resp.text
        assert rebuild_requests == []
        from app.main import app

        assert "/internal/artefacts/rebuild" not in {
            getattr(r, "path", None) for r in app.routes
        }
