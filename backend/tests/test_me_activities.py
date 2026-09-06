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


class TestMeActivitiesSqlSideLimit:
    """Pins the SQL-side filter/order/limit fix (memory follow-up to the
    32 MiB streaming one).

    The old ``get_user_activities`` SELECTed EVERY column of the user's WHOLE
    corpus (geometry_geojson included) into web RAM, then Python-sliced
    ``[-limit:]`` — an OOM for a 14k-activity user on a 512Mi instance. The
    fix pushes sport/geometry filters, ordering and LIMIT into Postgres with
    column projection. These tests pin:

    1. the response CONTRACT is unchanged — same set (the ``limit`` most
       recent by effective date), same order (ascending, newest LAST — what
       ``[-limit:]`` on the insertion-ordered list returned);
    2. the emitted row SELECT actually carries a SQL LIMIT (assertion-level
       guard against a regression to fetch-all-then-slice).
    """

    @staticmethod
    def _seed_activities(user_id: str, specs: list[dict]) -> None:
        """Insert Activity rows directly (fast — no GPX upload round-trip).

        ``specs``: dicts with name/sport/activity_date/geometry_geojson
        overrides. Rows belong to the fixture's freshly-registered user, so
        the SHARED test DB is never truncated or cross-polluted."""
        import json as _json_mod
        import uuid as uuid_mod

        from app.db.models import Activity
        from app.db.session import SessionLocal

        default_geom = _json_mod.dumps({
            "type": "LineString",
            "coordinates": [[3.87, 43.62], [3.88, 43.63]],
        })
        db = SessionLocal()
        try:
            for i, spec in enumerate(specs):
                db.add(Activity(
                    user_id=user_id,
                    provider="file",
                    provider_activity_id=f"meacts-test-{uuid_mod.uuid4().hex[:12]}-{i}",
                    sport=spec.get("sport", "road"),
                    name=spec["name"],
                    geometry_geojson=spec.get("geometry_geojson", default_geom),
                    distance_m=1000.0 + i,
                    activity_date=spec.get("activity_date"),
                ))
            db.commit()
        finally:
            db.close()

    def test_limit_returns_most_recent_ascending_with_filters(self, client, auth_headers):
        """Seed MORE than ``limit`` activities and pin the exact pre-fix
        contract: sport+geometry filters apply BEFORE the limit, the response
        holds the ``limit`` most recent rows, ascending, newest LAST."""
        import datetime as _dt

        user_id = _decode_user_id(auth_headers)
        base = _dt.datetime(2026, 1, 1, 12, 0, tzinfo=_dt.UTC)
        specs = []
        # 15 road rides, one per day — road-4 has NO geometry (must be
        # excluded BEFORE the limit, like the old Python-side filter).
        for i in range(15):
            specs.append({
                "name": f"road-{i}",
                "sport": "road",
                "activity_date": base + _dt.timedelta(days=i),
                **({"geometry_geojson": None} if i == 4 else {}),
            })
        # 5 interleaved MTB rides — excluded by ?sport=road BEFORE the limit.
        for i in range(5):
            specs.append({
                "name": f"mtb-{i}",
                "sport": "mtb",
                "activity_date": base + _dt.timedelta(days=i, hours=6),
            })
        self._seed_activities(user_id, specs)

        resp = client.get("/me/activities?sport=road&limit=10", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert data["total"] == 10
        assert data["total"] == len(data["features"])
        names = [f["properties"]["name"] for f in data["features"]]
        # 14 road rides have geometry (road-4 dropped); the 10 most recent of
        # those are road-5..road-14, streamed OLDEST-FIRST (newest LAST).
        assert names == [f"road-{i}" for i in range(5, 15)], names

        # Unfiltered with a limit: the 10 most recent across sports. All mtb
        # rows sit at day 0-4 (+6h) so the 10 newest overall are still
        # road-5..road-14, ascending.
        resp_all = client.get("/me/activities?limit=10", headers=auth_headers)
        data_all = resp_all.json()
        assert data_all["total"] == 10
        names_all = [f["properties"]["name"] for f in data_all["features"]]
        assert names_all == [f"road-{i}" for i in range(5, 15)], names_all

        # Other sport + small limit: 3 newest mtb rides, ascending.
        resp_mtb = client.get("/me/activities?sport=mtb&limit=3", headers=auth_headers)
        data_mtb = resp_mtb.json()
        assert data_mtb["total"] == 3
        assert [f["properties"]["name"] for f in data_mtb["features"]] == [
            "mtb-2", "mtb-3", "mtb-4",
        ]

    def test_activities_row_select_carries_sql_limit(self, client, auth_headers):
        """Assertion-level guard: the SELECT that fetches activity rows
        (the one projecting geometry_geojson) must carry a SQL LIMIT — the
        old code fetched the whole corpus and sliced in Python."""
        from sqlalchemy import event

        from app.db.session import engine

        user_id = _decode_user_id(auth_headers)
        import datetime as _dt
        base = _dt.datetime(2026, 2, 1, tzinfo=_dt.UTC)
        self._seed_activities(user_id, [
            {"name": f"lim-{i}", "activity_date": base + _dt.timedelta(days=i)}
            for i in range(8)
        ])

        recorded: list[str] = []

        def _record(conn, cursor, statement, parameters, context, executemany):
            recorded.append(statement)

        event.listen(engine, "before_cursor_execute", _record)
        try:
            resp = client.get("/me/activities?limit=5", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()  # fully consume the stream → row query runs
        finally:
            event.remove(engine, "before_cursor_execute", _record)

        assert data["total"] == 5
        assert [f["properties"]["name"] for f in data["features"]] == [
            f"lim-{i}" for i in range(3, 8)
        ]

        row_selects = [
            s for s in recorded
            # The row query PROJECTS geometry_geojson; the head-of-body COUNT
            # only references it in its WHERE — exclude COUNTs.
            if "FROM activities" in s and "geometry_geojson" in s
            and "count(" not in s.lower()
        ]
        assert row_selects, "expected a SELECT on activities projecting geometry_geojson"
        for s in row_selects:
            assert "LIMIT" in s.upper(), (
                f"the activities row SELECT must carry a SQL LIMIT "
                f"(fetch-all-then-Python-slice regression): {s}"
            )
            # Column projection guard: the memory fix also stops SELECTing
            # unserialized heavy/irrelevant columns (display_coords blob).
            assert "display_coords" not in s, (
                f"row SELECT must project only serialized columns: {s}"
            )


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
