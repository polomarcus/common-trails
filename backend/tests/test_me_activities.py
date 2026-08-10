"""Tests for GET /me/activities — personal GeoJSON route feed."""
import pathlib

import gpxpy
import gpxpy.gpx

FIXTURES = pathlib.Path(__file__).parent.parent.parent.parent / "e2e" / "fixtures" / "gpx"


def _make_gpx_bytes(name: str = "Test", n_points: int = 10) -> bytes:
    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(track)
    seg = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(seg)
    for i in range(n_points):
        seg.points.append(
            gpxpy.gpx.GPXTrackPoint(
                latitude=43.62 + i * 0.001,
                longitude=3.87 + i * 0.001,
                elevation=50 + i * 5,
            )
        )
    return gpx.to_xml().encode()


def _upload_gpx(client, headers, gpx_bytes: bytes, sport: str = "mtb", name: str = "ride.gpx"):
    return client.post(
        "/gpx/upload",
        headers=headers,
        files={"file": (name, gpx_bytes, "application/gpx+xml")},
        data={"sport": sport, "contribute_heatmap": "false"},
    )


def _decode_user_id(auth_headers: dict) -> str:
    """Pull the user_id (JWT ``sub``) out of a bearer header — no conftest dep."""
    import base64
    import json as _json
    token = auth_headers["Authorization"].split(" ", 1)[1]
    payload_b64 = token.split(".")[1]
    pad = "=" * (-len(payload_b64) % 4)
    return _json.loads(base64.urlsafe_b64decode(payload_b64 + pad))["sub"]


class TestMeActivitiesStreaming:
    """Pins the /me/activities streaming fix (Cloud Run 32 MiB non-streaming cap).

    The endpoint must return a ``StreamingResponse`` (not a single materialized
    ``ActivitiesResponse`` body), while keeping the exact FeatureCollection JSON
    shape the frontend parses with ``resp.json()``.
    """

    def test_endpoint_returns_streaming_response(self, client, auth_headers):
        """Drive the REAL handler coroutine and assert it returns a
        ``StreamingResponse``. FAILS on the old code (returned an
        ``ActivitiesResponse`` model) — this is the non-regression pin."""
        import asyncio

        from fastapi.responses import StreamingResponse

        from app.api.auth import AuthenticatedUser
        from app.api.me_activities import me_activities

        gpx = _make_gpx_bytes("Stream Me", n_points=6)
        _upload_gpx(client, auth_headers, gpx, sport="mtb", name="stream_me.gpx")
        user_id = _decode_user_id(auth_headers)

        user = AuthenticatedUser(
            user_id=user_id, email="stream@example.com", username="streamer"
        )

        async def _run():
            resp = await me_activities(current_user=user, sport=None, limit=5000)
            assert isinstance(resp, StreamingResponse)
            # body_iterator is an async iterator (starlette wraps our sync gen).
            chunks = [chunk async for chunk in resp.body_iterator]
            return resp, b"".join(
                c if isinstance(c, bytes) else c.encode() for c in chunks
            )

        result, body = asyncio.run(_run())
        assert isinstance(result, StreamingResponse)
        assert result.media_type == "application/json"

        # The streamed body must be well-formed JSON of the same shape.
        import json as _json
        parsed = _json.loads(body)
        assert parsed["type"] == "FeatureCollection"
        assert parsed["total"] == len(parsed["features"])
        assert parsed["total"] >= 1

    def test_streamed_body_shape_and_geometry(self, client, auth_headers):
        """End-to-end through the ASGI streaming path: 200 + json content-type +
        a FeatureCollection whose total matches the feature count, each feature a
        LineString."""
        for i in range(3):
            gpx = _make_gpx_bytes(f"Streamed {i}", n_points=5 + i)
            up = _upload_gpx(client, auth_headers, gpx, sport="road", name=f"s{i}.gpx")
            assert up.status_code == 202

        resp = client.get("/me/activities?sport=road&limit=5000", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")

        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert data["total"] == len(data["features"])
        assert data["total"] >= 3
        for feat in data["features"]:
            assert feat["type"] == "Feature"
            assert feat["geometry"]["type"] == "LineString"
            assert len(feat["geometry"]["coordinates"]) >= 2
            assert feat["properties"]["sport"] == "road"


class TestMeActivities:
    def test_empty_returns_feature_collection(self, client, auth_headers):
        resp = client.get("/me/activities", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert data["features"] == []
        assert data["total"] == 0

    def test_requires_auth(self, client):
        resp = client.get("/me/activities")
        assert resp.status_code == 401

    def test_by_id_returns_single_feature(self, client, auth_headers):
        """GET /me/activities/{id} should return the single activity
        without paginating through the full list."""
        gpx = _make_gpx_bytes("Solo VTT", n_points=8)
        up = _upload_gpx(client, auth_headers, gpx, sport="mtb", name="solo.gpx")
        assert up.status_code == 202
        list_resp = client.get("/me/activities", headers=auth_headers)
        activity_id = list_resp.json()["features"][-1]["properties"]["id"]

        resp = client.get(f"/me/activities/{activity_id}", headers=auth_headers)
        assert resp.status_code == 200
        feat = resp.json()
        assert feat["type"] == "Feature"
        assert feat["properties"]["id"] == activity_id
        assert feat["properties"]["sport"] == "mtb"
        assert feat["geometry"]["type"] == "LineString"
        assert len(feat["geometry"]["coordinates"]) >= 2

    def test_by_id_404_when_missing(self, client, auth_headers):
        resp = client.get("/me/activities/00000000-0000-0000-0000-000000000000", headers=auth_headers)
        assert resp.status_code == 404

    def test_by_id_no_cross_user_leakage(self, client, auth_headers):
        """An activity owned by user A must 404 (not 200, not 403) when
        user B asks for it — same opacity as a missing row."""
        import uuid
        gpx = _make_gpx_bytes("User A VTT", n_points=5)
        _upload_gpx(client, auth_headers, gpx, sport="mtb", name="cross_user.gpx")
        list_resp = client.get("/me/activities", headers=auth_headers)
        activity_id = list_resp.json()["features"][-1]["properties"]["id"]

        # Register user B inline (same pattern as test_no_cross_user_leakage above)
        email2 = f"u_byid_{uuid.uuid4().hex[:8]}@example.com"
        reg = client.post(
            "/auth/register",
            json={"email": email2, "password": "password1234", "username": f"u_byid_{uuid.uuid4().hex[:6]}"},
        )
        token2 = reg.json()["access_token"]
        headers2 = {"Authorization": f"Bearer {token2}"}

        resp = client.get(f"/me/activities/{activity_id}", headers=headers2)
        assert resp.status_code == 404

    def test_by_id_requires_auth(self, client):
        resp = client.get("/me/activities/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 401

    def test_returns_geojson_after_mtb_upload(self, client, auth_headers):
        gpx = _make_gpx_bytes("Montpellier VTT", n_points=20)
        up = _upload_gpx(client, auth_headers, gpx, sport="mtb")
        assert up.status_code == 202

        resp = client.get("/me/activities", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

        feat = data["features"][-1]
        assert feat["type"] == "Feature"
        assert feat["geometry"]["type"] == "LineString"
        assert len(feat["geometry"]["coordinates"]) >= 2
        assert feat["properties"]["sport"] == "mtb"
        assert feat["properties"]["name"] == "Montpellier VTT"
        assert feat["properties"]["distance_m"] is not None

    def test_sport_filter(self, client, auth_headers):
        road = _make_gpx_bytes("Road Ride", n_points=5)
        mtb = _make_gpx_bytes("MTB Trail", n_points=5)
        _upload_gpx(client, auth_headers, road, sport="road", name="road.gpx")
        _upload_gpx(client, auth_headers, mtb, sport="mtb", name="mtb_filter.gpx")

        resp = client.get("/me/activities?sport=mtb", headers=auth_headers)
        data = resp.json()
        for f in data["features"]:
            assert f["properties"]["sport"] == "mtb"

    def test_no_cross_user_leakage(self, client, auth_headers):
        """A second user must not see the first user's activities."""
        import uuid
        gpx = _make_gpx_bytes("Private Route", n_points=5)
        _upload_gpx(client, auth_headers, gpx, sport="road", name="private.gpx")

        # Register a second user
        email2 = f"u2_{uuid.uuid4().hex[:8]}@example.com"
        reg = client.post(
            "/auth/register",
            json={"email": email2, "password": "password1234", "username": f"u2_{uuid.uuid4().hex[:6]}"},
        )
        token2 = reg.json()["access_token"]
        headers2 = {"Authorization": f"Bearer {token2}"}

        resp = client.get("/me/activities", headers=headers2)
        data = resp.json()
        # User 2 has uploaded nothing — must see 0 features
        assert data["total"] == 0

    def test_photos_empty_when_none_imported(self, client, auth_headers):
        """An activity with no imported photos returns an empty list (200)."""
        gpx = _make_gpx_bytes("Photo Test", n_points=5)
        _upload_gpx(client, auth_headers, gpx, sport="mtb", name="photos_empty.gpx")
        list_resp = client.get("/me/activities", headers=auth_headers)
        activity_id = list_resp.json()["features"][-1]["properties"]["id"]

        resp = client.get(f"/me/activities/{activity_id}/photos", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_photos_returns_owner_photos(self, client, auth_headers):
        """A 200 with the expected photo shape when ActivityPhoto rows exist."""
        from app.db.models import ActivityPhoto
        from app.db.session import SessionLocal

        gpx = _make_gpx_bytes("With Photos", n_points=5)
        _upload_gpx(client, auth_headers, gpx, sport="mtb", name="with_photos.gpx")
        list_resp = client.get("/me/activities", headers=auth_headers)
        feat = list_resp.json()["features"][-1]
        activity_id = feat["properties"]["id"]
        # total_photo_count should be exposed in the feature properties
        assert "total_photo_count" in feat["properties"]

        # Decode user_id from the JWT (sub claim) — kept inline so the test
        # doesn't depend on a new conftest fixture.
        import base64
        import json as _json
        token = auth_headers["Authorization"].split(" ", 1)[1]
        # jwt is base64url(header).base64url(payload).signature
        pad = "=" * (-len(token.split(".")[1]) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(token.split(".")[1] + pad))
        user_id = payload["sub"]

        db = SessionLocal()
        try:
            db.add(ActivityPhoto(
                activity_id=activity_id,
                user_id=user_id,
                strava_photo_id="test_photo_1",
                url_thumb="https://cdn.example.com/thumb1.jpg",
                url_medium="https://cdn.example.com/med1.jpg",
                lat=43.62, lon=3.87, caption="Test caption",
            ))
            db.commit()
        finally:
            db.close()

        resp = client.get(f"/me/activities/{activity_id}/photos", headers=auth_headers)
        assert resp.status_code == 200
        photos = resp.json()
        assert len(photos) == 1
        p = photos[0]
        assert p["thumbnail_url"] == "https://cdn.example.com/thumb1.jpg"
        assert p["full_url"] == "https://cdn.example.com/med1.jpg"
        assert p["caption"] == "Test caption"
        assert p["lat"] == 43.62
        assert p["lon"] == 3.87
        assert p["strava_photo_id"] == "test_photo_1"
        assert "photo_id" in p

    def test_photos_404_when_activity_missing(self, client, auth_headers):
        resp = client.get(
            "/me/activities/00000000-0000-0000-0000-000000000000/photos",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_photos_404_when_not_owner(self, client, auth_headers):
        """User B asking for user A's photos must get 404, never the photos."""
        import uuid

        gpx = _make_gpx_bytes("Owner Only", n_points=5)
        _upload_gpx(client, auth_headers, gpx, sport="road", name="owner_only.gpx")
        list_resp = client.get("/me/activities", headers=auth_headers)
        activity_id = list_resp.json()["features"][-1]["properties"]["id"]

        # Even when photos exist on this activity, user B must not see them
        import base64
        import json as _json

        from app.db.models import ActivityPhoto
        from app.db.session import SessionLocal
        token = auth_headers["Authorization"].split(" ", 1)[1]
        pad = "=" * (-len(token.split(".")[1]) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(token.split(".")[1] + pad))
        user_a_id = payload["sub"]
        db = SessionLocal()
        try:
            db.add(ActivityPhoto(
                activity_id=activity_id,
                user_id=user_a_id,
                strava_photo_id="leak_test_1",
                url_thumb="https://cdn.example.com/leak_thumb.jpg",
                url_medium="https://cdn.example.com/leak_med.jpg",
            ))
            db.commit()
        finally:
            db.close()

        email2 = f"u_photos_{uuid.uuid4().hex[:8]}@example.com"
        reg = client.post(
            "/auth/register",
            json={"email": email2, "password": "password1234", "username": f"u_photos_{uuid.uuid4().hex[:6]}"},
        )
        token2 = reg.json()["access_token"]
        headers2 = {"Authorization": f"Bearer {token2}"}

        resp = client.get(f"/me/activities/{activity_id}/photos", headers=headers2)
        assert resp.status_code == 404

    def test_photos_requires_auth(self, client):
        resp = client.get("/me/activities/00000000-0000-0000-0000-000000000000/photos")
        assert resp.status_code == 401

    def test_real_montpellier_mtb_fixture(self, client, auth_headers):
        """Upload the real Montpellier VTT GPX fixture and verify geometry."""
        gpx_path = FIXTURES / "montpellier_mtb.gpx"
        if not gpx_path.exists():
            import pytest
            pytest.skip("montpellier_mtb.gpx fixture not found")

        gpx_bytes = gpx_path.read_bytes()
        up = _upload_gpx(client, auth_headers, gpx_bytes, sport="mtb", name="montpellier_mtb.gpx")
        assert up.status_code == 202
        up_data = up.json()
        assert up_data["status"] in ("created", "already_exists")
        # Verify distance is roughly 34 km
        assert up_data["distance_m"] is not None
        assert 30_000 < up_data["distance_m"] < 40_000

        resp = client.get("/me/activities?sport=mtb", headers=auth_headers)
        data = resp.json()
        assert data["total"] >= 1
        feat = next(
            f for f in data["features"]
            if f["properties"].get("name") == "Montpellier VTT"
        )
        coords = feat["geometry"]["coordinates"]
        # Verify coordinates are in Montpellier area (lon≈3.87, lat≈43.62)
        assert 3.5 < coords[0][0] < 4.2, "longitude outside Montpellier area"
        assert 43.4 < coords[0][1] < 43.8, "latitude outside Montpellier area"
