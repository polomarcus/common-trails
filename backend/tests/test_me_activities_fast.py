"""GET /me/activities fast path: verbatim splice of the cached display geometry,
~256 KiB streamed chunks, gzip, and ETag / 304 revalidation.

All tests drive the REAL handler (TestClient or the handler coroutine) against
the test DB; rows belong to the fixture's freshly-registered user.
"""
import asyncio
import datetime as dt
import gzip
import json
import uuid

from sqlalchemy import update

from app.api import me_activities as ma
from app.api.auth import AuthenticatedUser
from app.db.models import Activity
from app.db.session import SessionLocal
from app.services.display_geometry import compact_display_geojson
from app.services.ingest import iter_user_map_activities
from tests.test_me_activities import _decode_user_id, _make_gpx_bytes, _upload_gpx

BASE = dt.datetime(2026, 3, 1, 8, 0, tzinfo=dt.UTC)


def _line(n: int, lon0: float = 3.87, lat0: float = 43.62, step: float = 0.0001) -> list:
    return [[round(lon0 + i * step, 7), round(lat0 + i * step * 0.7, 7), 50.0 + (i % 17)] for i in range(n)]


def _gap_line() -> list:
    # Two drawable parts > DISPLAY_GAP_BREAK_M apart → MultiLineString.
    return _line(20) + _line(20, lon0=3.99, lat0=43.70)


def _seed(user_id: str, specs: list[dict]) -> list[str]:
    """Insert rows directly. ``cache``: True → display_geojson computed like
    ingest does, False → NULL (pre-backfill fallback), str → that raw text."""
    ids = []
    db = SessionLocal()
    try:
        for i, spec in enumerate(specs):
            geom = json.dumps({"type": "LineString", "coordinates": spec.get("coords") or _line(30 + i)})
            cache = spec.get("cache", True)
            display = compact_display_geojson(geom) if cache is True else (None if cache is False else cache)
            aid = str(uuid.uuid4())
            db.add(Activity(
                id=aid,
                user_id=user_id,
                provider=spec.get("provider", "file"),
                provider_activity_id=f"fast-{uuid.uuid4().hex[:12]}",
                sport=spec.get("sport", "road"),
                name=spec.get("name", f"ride-{i}"),
                geometry_geojson=geom,
                display_geojson=display,
                distance_m=1000.0 + i,
                elevation_gain_m=12.5 * i,
                moving_time=600 + i,
                total_photo_count=spec.get("photos", 0),
                activity_date=spec.get("activity_date", BASE + dt.timedelta(days=i)),
            ))
            ids.append(aid)
        db.commit()
    finally:
        db.close()
    return ids


def _update(activity_id: str, **values) -> None:
    db = SessionLocal()
    try:
        db.execute(update(Activity).where(Activity.id == activity_id).values(**values))
        db.commit()
    finally:
        db.close()


class TestSpliceEquivalence:
    def test_spliced_feature_identical_to_parsed_feature(self, client, auth_headers):
        """For every row shape (cached, cached MultiLineString, NULL cache,
        NULL-cache gap split, undated, null name, photos) the spliced text is
        byte-identical to — and parses equal to — the former
        ``json.dumps(_activity_to_feature(act, compact=True))``."""
        user_id = _decode_user_id(auth_headers)
        _seed(user_id, [
            {"name": "cached"},
            {"name": "cached-gap", "coords": _gap_line()},
            {"name": "fallback", "cache": False},
            {"name": "fallback-gap", "cache": False, "coords": _gap_line()},
            {"name": "undated", "activity_date": None},
            {"name": None, "photos": 3, "sport": "gravel"},
        ])
        acts = list(iter_user_map_activities(user_id, limit=5000))
        assert len(acts) == 6
        fallbacks = 0
        for act in acts:
            old = json.dumps(ma._activity_to_feature(act, compact=True), separators=(",", ":"))
            new, fell_back = ma._compact_feature_json(act)
            fallbacks += fell_back
            assert json.loads(new) == json.loads(old)
            assert new == old
        assert fallbacks == 2

    def test_endpoint_body_matches_parsed_reference(self, client, auth_headers):
        """End-to-end: the streamed body equals what the old serialiser builds
        from the same rows (cached + fallback rows, newest last)."""
        user_id = _decode_user_id(auth_headers)
        _seed(user_id, [
            {"name": "a"},
            {"name": "b", "cache": False, "coords": _gap_line()},
            {"name": "c", "coords": _gap_line()},
        ])
        resp = client.get("/me/activities?limit=5000", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        expected = [
            ma._activity_to_feature(a, compact=True)
            for a in iter_user_map_activities(user_id, limit=5000)
        ]
        assert body == {"type": "FeatureCollection", "total": 3, "features": expected}
        assert [f["geometry"]["type"] for f in body["features"]] == [
            "LineString", "MultiLineString", "MultiLineString",
        ]

    def test_malformed_cache_never_breaks_the_body(self, client, auth_headers):
        """A cache value that is not a JSON object (truncated / garbage) fails
        the O(1) guard → parsed path → the row is skipped; the body stays
        valid JSON."""
        user_id = _decode_user_id(auth_headers)
        _seed(user_id, [
            {"name": "ok"},
            {"name": "truncated", "cache": '{"type":"LineString","coordinates":[[3.8'},
            {"name": "garbage", "cache": "not json"},
        ])
        resp = client.get("/me/activities?limit=5000", headers=auth_headers)
        assert resp.status_code == 200
        names = [f["properties"]["name"] for f in resp.json()["features"]]
        assert names == ["ok"]


class TestChunkingAndGzip:
    def test_chunks_are_large_and_stream_reassembles(self, client, auth_headers, monkeypatch):
        """With a small chunk threshold the body is split into several chunks,
        each >= the threshold except the last, and still reassembles into the
        exact FeatureCollection."""
        monkeypatch.setattr(ma, "STREAM_CHUNK_BYTES", 2048)
        user_id = _decode_user_id(auth_headers)
        _seed(user_id, [{"name": f"r{i}", "coords": _line(120)} for i in range(12)])
        user = AuthenticatedUser(user_id=user_id, email="c@example.com", username="c")

        async def _run():
            resp = await ma.me_activities(
                current_user=user, sport=None, limit=5000, if_none_match=None
            )
            return [c async for c in resp.body_iterator]

        chunks = asyncio.run(_run())
        assert len(chunks) > 2
        assert all(len(c) >= 2048 for c in chunks[:-1])
        data = json.loads(b"".join(chunks))
        assert data["total"] == 12 == len(data["features"])

    def test_default_chunk_size_is_256_kib(self):
        assert ma.STREAM_CHUNK_BYTES == 256 * 1024

    def test_gzip_applied_level_6(self, client, auth_headers):
        user_id = _decode_user_id(auth_headers)
        _seed(user_id, [{"name": f"g{i}", "coords": _line(200)} for i in range(5)])
        resp = client.get(
            "/me/activities?limit=5000",
            headers={**auth_headers, "Accept-Encoding": "gzip"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("content-encoding") == "gzip"
        assert resp.json()["total"] == 5  # httpx decodes transparently

        from starlette.middleware.gzip import GZipMiddleware

        from app.main import app

        gz = [m for m in app.user_middleware if m.cls is GZipMiddleware]
        assert gz and gz[0].kwargs.get("compresslevel") == 6
        assert gz[0].kwargs.get("minimum_size") == 1000

    def test_gzip_payload_budget(self, client, auth_headers):
        """Cheap size budget: the gzipped body of compact rows is a small
        fraction of the raw body (guards against shipping full geometry)."""
        user_id = _decode_user_id(auth_headers)
        _seed(user_id, [{"name": f"b{i}", "coords": _line(400)} for i in range(10)])
        raw = client.get("/me/activities?limit=5000", headers={**auth_headers, "Accept-Encoding": "identity"})
        assert raw.headers.get("content-encoding") is None
        assert len(gzip.compress(raw.content, 6)) < len(raw.content) / 2


class TestEtag:
    def _get(self, client, headers, etag=None, qs="limit=5000"):
        h = dict(headers)
        if etag:
            h["If-None-Match"] = etag
        return client.get(f"/me/activities?{qs}", headers=h)

    def test_etag_and_304(self, client, auth_headers):
        user_id = _decode_user_id(auth_headers)
        _seed(user_id, [{"name": "e1"}, {"name": "e2"}])
        r1 = self._get(client, auth_headers)
        assert r1.status_code == 200
        etag = r1.headers["etag"]
        assert etag.startswith('W/"') and etag.endswith('"')
        assert r1.headers["cache-control"] == "private, no-cache"

        r2 = self._get(client, auth_headers, etag)
        assert r2.status_code == 304
        assert r2.content == b""
        assert r2.headers["etag"] == etag
        assert r2.headers["cache-control"] == "private, no-cache"

        # Strong form / list form also match (weak comparison).
        assert self._get(client, auth_headers, etag.removeprefix("W/")).status_code == 304
        assert self._get(client, auth_headers, f'"nope", {etag}').status_code == 304
        assert self._get(client, auth_headers, '"nope"').status_code == 200

        # Query params are part of the validator.
        assert self._get(client, auth_headers, etag, qs="limit=1").status_code == 200
        assert self._get(client, auth_headers, etag, qs="limit=5000&sport=road").status_code == 200

    def test_etag_changes_on_every_mutation(self, client, auth_headers):
        user_id = _decode_user_id(auth_headers)
        ids = _seed(user_id, [{"name": "m1"}, {"name": "m2"}, {"name": "m3"}])
        seen = set()

        def current() -> str:
            r = self._get(client, auth_headers)
            assert r.status_code == 200
            return r.headers["etag"]

        def changed(label: str, previous: str) -> str:
            now = current()
            assert now != previous, f"ETag unchanged after {label}"
            assert self._get(client, auth_headers, previous).status_code == 200, label
            assert now not in seen, label
            seen.add(now)
            return now

        etag = current()
        seen.add(etag)
        assert current() == etag  # stable without changes

        # Sport edit through the real API.
        r = client.patch(f"/me/activities/{ids[0]}", json={"sport": "gravel"}, headers=auth_headers)
        assert r.status_code == 200 and r.json()["changed"] is True
        etag = changed("sport edit", etag)

        # Rename (e.g. a Strava re-sync), date change, contribute flag,
        # geometry promotion / cache backfill, photo count.
        _update(ids[1], name="renamed")
        etag = changed("rename", etag)
        _update(ids[1], activity_date=BASE - dt.timedelta(days=30))
        etag = changed("date change", etag)
        _update(ids[2], contribute_heatmap=False)
        etag = changed("contribute change", etag)
        new_geom = json.dumps({"type": "LineString", "coordinates": _line(31)})
        _update(ids[2], geometry_geojson=new_geom, display_geojson=compact_display_geojson(new_geom))
        etag = changed("geometry upgrade", etag)
        _update(ids[2], display_geojson=None)
        etag = changed("cache cleared", etag)
        _update(ids[2], total_photo_count=4)
        etag = changed("photo count", etag)

        # New import (real upload path).
        up = _upload_gpx(client, auth_headers, _make_gpx_bytes("new", 8), sport="road", name="etag_new.gpx")
        assert up.status_code == 202
        etag = changed("new activity", etag)

        # Delete through the real API.
        r = client.delete(f"/me/activities/{ids[0]}", headers=auth_headers)
        assert r.status_code == 204
        changed("delete", etag)

    def test_etag_is_per_user(self, client, auth_headers):
        """Two users with no activities get different validators (the user is
        hashed in), so a shared browser cache never serves one user's copy to
        another."""
        r1 = self._get(client, auth_headers)
        other = client.post(
            "/auth/register",
            json={
                "email": f"etag_{uuid.uuid4().hex[:8]}@example.com",
                "password": "testpass123",
                "username": f"user_{uuid.uuid4().hex[:6]}",
            },
        )
        assert other.status_code == 201, other.text
        client.cookies.clear()  # register set user 2's session cookie
        h2 = {"Authorization": f"Bearer {other.json()['access_token']}"}
        r2 = self._get(client, h2)
        assert r1.headers["etag"] != r2.headers["etag"]
        assert self._get(client, h2, r1.headers["etag"]).status_code == 200

    def test_etag_unit_includes_format_version_and_user(self, monkeypatch):
        a = ma._me_activities_etag("u1", None, 5000, 3, "d")
        assert a != ma._me_activities_etag("u2", None, 5000, 3, "d")
        monkeypatch.setattr(ma, "ME_ACTIVITIES_FORMAT_VERSION", "999")
        assert a != ma._me_activities_etag("u1", None, 5000, 3, "d")

    def test_etag_unit_changes_with_deploy_revision(self, monkeypatch):
        # A forgotten FORMAT_VERSION bump must not serve stale bodies forever:
        # each Cloud Run revision (K_REVISION) yields a different ETag.
        monkeypatch.setattr(ma, "_DEPLOY_REVISION", "web-00041-abc")
        a = ma._me_activities_etag("u1", None, 5000, 3, "d")
        monkeypatch.setattr(ma, "_DEPLOY_REVISION", "web-00042-def")
        assert a != ma._me_activities_etag("u1", None, 5000, 3, "d")

    def test_logs_one_info_line(self, client, auth_headers, caplog):
        import logging

        user_id = _decode_user_id(auth_headers)
        _seed(user_id, [{"name": "l1"}, {"name": "l2", "cache": False}])
        with caplog.at_level(logging.INFO, logger="app.api.me_activities"):
            r = self._get(client, auth_headers)
            assert r.status_code == 200
        lines = [rec.getMessage() for rec in caplog.records if rec.getMessage().startswith("me_activities:")]
        assert len(lines) == 1, lines
        assert "rows=2 fallbacks=1 " in lines[0] and "bytes=" in lines[0] and "elapsed_ms=" in lines[0]
        assert user_id not in lines[0]
