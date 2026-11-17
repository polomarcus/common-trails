"""The heat-compute hop is gone: uploads request the community rebuild directly.

* ``/gpx/upload`` (prod path, TEST_MODE off) calls
  ``cloud_tasks.request_community_rebuild`` — the windowed ``artefact-rebuild``
  Cloud Task — and never creates a task for ``/internal/ingest/heat``.
* ``/imports/files`` asks for ONE rebuild per batch, the batch-end job
  trigger, and never enqueues that task on top (it would launch a second full
  build: the task's handler runs the same job).
* ``/internal/ingest/heat`` (kept one release as a 200 no-op) is removed.
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
    """Record every rebuild request made by the API modules — the Cloud Task
    (``"rebuild"``) and the direct job trigger (``"job"``); nothing real."""
    calls: list[str] = []

    def _fake() -> str:
        calls.append("rebuild")
        return "queued"

    def _fake_job() -> bool:
        calls.append("job")
        return True

    monkeypatch.setattr("app.api.gpx_upload.request_community_rebuild", _fake)
    monkeypatch.setattr("app.services.cloud_tasks.request_community_rebuild", _fake)
    monkeypatch.setattr("app.services.run_jobs.trigger_build_pmtiles_job", _fake_job)
    monkeypatch.setattr("app.services.run_jobs.build_pmtiles_debounce_ok", lambda *a, **k: True)
    return calls


@pytest.fixture
def prod_upload_path(monkeypatch):
    """Take the PRODUCTION branch of the upload handlers (they read
    ``app.config.TEST_MODE`` at request time) without leaving TEST_MODE for
    the rest of the app (auth, Cloud Tasks gates stay in test mode)."""
    monkeypatch.setattr("app.config.TEST_MODE", False)


class TestUploadsRequestRebuildDirectly:
    def test_cloud_tasks_has_no_heat_compute_enqueue(self):
        from app.services import cloud_tasks

        assert not hasattr(cloud_tasks, "enqueue_heat_compute")

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
        # ONE rebuild: the batch-end job trigger — never the Cloud Task on top
        # (its handler runs the same job: two full builds per contribution).
        assert rebuild_requests == ["job"]
        assert resp.json()["heatmap_status"] == "queued"

    def test_imports_files_non_final_file_defers_to_the_batch_end(
        self, client, auth_headers, rebuild_requests, prod_upload_path,
    ):
        resp = client.post(
            "/imports/files",
            headers=auth_headers,
            files={"file": ("imp2.gpx", _make_gpx_bytes("deferred rebuild imports"), "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "true", "rebuild_display": "false", **_CONSENT},
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["imported"] == 1
        assert rebuild_requests == []
        assert resp.json()["heatmap_status"] == "deferred"


class TestRequestCommunityRebuild:
    def test_noop_inline_when_cloud_tasks_unconfigured(self):
        from app.services.cloud_tasks import request_community_rebuild

        # TEST_MODE: nothing is enqueued.
        assert request_community_rebuild() == "inline"

    def test_enqueues_the_debounced_artefact_task_not_a_heat_task(self, monkeypatch):
        """Prod wiring: ONE constant-named task on the artefact queue, pointed
        at /internal/artefacts/rebuild — never at /internal/ingest/heat."""
        from app.services import cloud_tasks

        seen: dict = {}

        def _fake_enqueue(**kwargs):
            seen.update(kwargs)
            return "deduped"

        monkeypatch.setattr(cloud_tasks, "_enqueue_with_const_name", _fake_enqueue)

        # A deduped enqueue means a rebuild is already pending → "queued".
        assert cloud_tasks.request_community_rebuild() == "queued"
        assert seen["queue_env"] == "CLOUD_TASKS_ARTEFACT_QUEUE"
        assert seen["handler_url_env"] == "INTERNAL_ARTEFACT_HANDLER_URL"
        assert seen["task_name"].startswith("artefact-rebuild-")
        assert 0 < seen["schedule_seconds"] <= 305  # fires when its window closes

    def test_task_name_is_per_window(self, monkeypatch):
        """Uploads of one window share ONE task; a later upload gets its own.
        A constant name could not: Cloud Tasks keeps a run task's name
        reserved (~1 h), so a 2nd upload 7 min after the 1st was swallowed."""
        from app.services import cloud_tasks

        monkeypatch.delenv("ARTEFACT_REBUILD_SCHEDULE_S", raising=False)
        t0 = 1_800_000_000.0  # a window boundary (multiple of 300 s)
        n_a, d_a = cloud_tasks.artefact_rebuild_task(t0 + 10)
        n_b, d_b = cloud_tasks.artefact_rebuild_task(t0 + 290)
        n_c, _ = cloud_tasks.artefact_rebuild_task(t0 + 7 * 60)
        assert n_a == n_b  # same burst → one task
        assert n_c != n_a  # 7 min later → a new task, never deduped
        assert (d_a, d_b) == (295, 15)  # both fire 5 s after the window closes

    def test_never_raises(self, monkeypatch):
        from app.services import cloud_tasks

        def _boom():
            raise RuntimeError("queue not terraformed")

        monkeypatch.setattr(cloud_tasks, "enqueue_artefact_rebuild", _boom)
        assert cloud_tasks.request_community_rebuild() == "failed"


class TestRetiredInternalIngestHeat:
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
