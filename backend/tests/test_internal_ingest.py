"""Tests for /internal/ingest/heat (Cloud Tasks handler) and the
``cloud_tasks.enqueue_heat_compute`` inline-fallback path used in dev.
"""
import gpxpy
import gpxpy.gpx


def _make_gpx_bytes(name: str = "Test", n_points: int = 10) -> bytes:
    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(track)
    segment = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(segment)
    for i in range(n_points):
        segment.points.append(
            gpxpy.gpx.GPXTrackPoint(
                latitude=43.60 + i * 0.001,
                longitude=3.88 + i * 0.001,
                elevation=80 + i,
            )
        )
    return gpx.to_xml().encode("utf-8")


class TestInternalIngestHeat:
    def test_internal_endpoint_processes_existing_activity(self, client, auth_headers):
        """In TEST_MODE the upload runs heat sync, so re-running the
        internal handler is a no-op idempotent call (same edges UPSERT,
        contributors PK collapses → user_count unchanged)."""
        gpx_bytes = _make_gpx_bytes("Internal handler test", n_points=12)
        upload = client.post(
            "/gpx/upload",
            headers=auth_headers,
            files={"file": ("internal.gpx", gpx_bytes, "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "true",
                  "consent_version": "v-internal-seed", "consent_text": "ODbL ok", "locale": "fr"},
        )
        assert upload.status_code == 202, upload.text
        body = upload.json()
        activity_id = body["activity_id"]
        assert body["status"] == "created"

        # Now call the internal endpoint directly. TEST_MODE disables OIDC
        # verification, so an unauth POST is accepted.
        from app.api.auth import _decode_token
        token = auth_headers["Authorization"].split(" ")[1]
        user_id = _decode_token(token)["sub"]

        resp = client.post(
            "/internal/ingest/heat",
            json={"activity_id": activity_id, "user_id": user_id},
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["activity_id"] == activity_id
        assert result["status"] == "ok"

    def test_internal_endpoint_missing_activity_returns_200(self, client):
        """A stale Cloud Task pointing at a deleted activity should not
        loop forever — we respond 200 with status=missing so the queue
        stops retrying."""
        resp = client.post(
            "/internal/ingest/heat",
            json={
                "activity_id": "00000000-0000-0000-0000-000000000000",
                "user_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "missing"

    def test_internal_endpoint_validates_payload(self, client):
        resp = client.post("/internal/ingest/heat", json={"activity_id": "abc"})
        # Missing user_id → 422 Unprocessable
        assert resp.status_code == 422


class TestCloudTasksClient:
    def test_inline_fallback_in_test_mode(self, client, auth_headers):
        """In TEST_MODE, ``enqueue_heat_compute`` runs the work in-process
        via ``process_heat_compute`` — verify the call returns ``inline``.
        """
        from app.api.auth import _decode_token
        from app.services.cloud_tasks import enqueue_heat_compute

        # Create a real activity first so the heat compute has something to do.
        gpx_bytes = _make_gpx_bytes("Cloud tasks fallback", n_points=8)
        upload = client.post(
            "/gpx/upload",
            headers=auth_headers,
            files={"file": ("ctfb.gpx", gpx_bytes, "application/gpx+xml")},
            data={"sport": "road", "contribute_heatmap": "true",
                  "consent_version": "v-internal-seed", "consent_text": "ODbL ok", "locale": "fr"},
        )
        assert upload.status_code == 202
        activity_id = upload.json()["activity_id"]
        token = auth_headers["Authorization"].split(" ")[1]
        user_id = _decode_token(token)["sub"]

        outcome = enqueue_heat_compute(activity_id=activity_id, user_id=user_id)
        # TEST_MODE always returns "inline" because Cloud Tasks is disabled
        assert outcome == "inline"
