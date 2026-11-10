"""Sharing "Ma heatmap" through a token link (docs/ma-heatmap-fichier-prive.md §10.4).

Pinned here:

- the token: >= 128 bits (192), URL-safe, unique, never derived from the user
  id, only its SHA-256 stored;
- revocation → 404 at once on the 4 public URLs; regeneration → the old token
  is dead;
- the shared artefact is endpoint-masked (no point within TRACE_MASK_METERS of
  a trace's ends), carries ``sport`` only, excludes undated and Strava-API
  traces;
- the private object is never reachable through ``/s/…`` and ``/me/heatmap/…``
  does not answer to a token;
- CORS ``*`` (no credentials) on ``/s/*`` only;
- GDPR delete / account merge / orphan sweep → ``shares/{id}/`` emptied and
  the row gone;
- the PMTiles reader / MVT decoder used to serve tiles.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import math
import re
import uuid
from datetime import UTC, datetime

import pytest
from PIL import Image
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services import mvt_raster, pmtiles_reader
from app.services import my_heatmap_files as mhf
from app.services import my_heatmap_share as share_svc
from app.services.geo import haversine_m
from tests.test_my_heatmap_files import (  # noqa: F401 — fixtures
    _mk_activity,
    _mk_user,
    _start,
    _triggers_on,
    _user_id_of,
    mhf_env,
    users,
)

MASK_M = 200.0
_DATE = datetime(2025, 5, 4, 8, 0, tzinfo=UTC)


@pytest.fixture
def share_env(mhf_env, monkeypatch):  # noqa: F811
    monkeypatch.setenv("TRACE_MASK_METERS", str(MASK_M))
    monkeypatch.delenv("MY_HEATMAP_SHARE_BASE_URL", raising=False)
    pmtiles_reader.clear_cache()
    yield mhf_env


def _ride(lon0: float, lat0: float, n: int = 400) -> list[list[float]]:
    """A ~6 km ride leaving "home" (lon0, lat0) with 15 m steps and turns, and
    coming back home: both ends at home, mid-ride far from it."""
    pts = []
    lon, lat = lon0, lat0
    dlon = 15 / (111_320 * math.cos(math.radians(lat0)))
    dlat = 15 / 110_540
    for i in range(n):
        leg = (i // 50) % 4
        if i < n // 2:
            lon += dlon * (1 if leg in (0, 1) else 0.3)
            lat += dlat * (0.3 if leg in (0, 2) else 1)
        else:
            lon -= dlon * (1 if leg in (0, 1) else 0.3)
            lat -= dlat * (0.3 if leg in (0, 2) else 1)
        pts.append([round(lon, 6), round(lat, 6), 100.0])
    return [[lon0, lat0, 100.0], *pts]


def _geojson(coords) -> str:
    return json.dumps({"type": "LineString", "coordinates": coords})


def _share_user(client, auth_headers, users, *, extra: bool = True):  # noqa: F811
    """The signed-in user with one shareable ride (+ an undated one and a
    Strava-API one that must NOT be shared). Returns (uid, ride coords)."""
    uid = _user_id_of(client, auth_headers)
    users.append(uid)
    lon0, lat0 = _start()
    coords = _ride(lon0, lat0)
    db = SessionLocal()
    try:
        _mk_activity(db, uid, geometry_geojson=_geojson(coords), name="Secret name")
        if extra:
            _mk_activity(db, uid, geometry_geojson=_geojson(_ride(lon0 + 0.3, lat0)), activity_date=None)
            _mk_activity(
                db, uid, geometry_geojson=_geojson(_ride(lon0 - 0.3, lat0)),
                provider="strava", source="strava_api",
            )
        db.commit()
    finally:
        db.close()
    return uid, coords


def _token(urls: dict) -> str:
    return re.search(r"/s/([^/]+)/style\.json$", urls["style"]).group(1)


def _tile_xy(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 1 << z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def _public_urls(urls: dict, coords) -> dict:
    mid = coords[len(coords) // 4]
    x, y = _tile_xy(mid[0], mid[1], 14)
    return {
        "pmtiles": urls["pmtiles"],
        "style": urls["style"],
        "pbf": urls["vector"].format(z=14, x=x, y=y),
        "png": urls["raster"].format(z=14, x=x, y=y),
    }


def _get(client, url):
    return client.get(url, headers={"Range": "bytes=0-16383"} if url.endswith(".pmtiles") else {})


# ── token ────────────────────────────────────────────────────────────────────


def test_token_entropy_alphabet_and_uniqueness():
    assert share_svc.TOKEN_BYTES * 8 >= 128
    tokens = {share_svc.new_token() for _ in range(2000)}
    assert len(tokens) == 2000
    for t in list(tokens)[:200]:
        assert share_svc.TOKEN_RE.match(t)
        assert re.fullmatch(r"[A-Za-z0-9_-]{32}", t)  # 32 × 6 bits = 192 bits


def test_token_not_derived_from_user_and_only_hash_stored(share_env, client, auth_headers, users):  # noqa: F811
    uid, _ = _share_user(client, auth_headers, users, extra=False)
    r1 = client.post("/me/heatmap/share", headers=auth_headers)
    assert r1.status_code == 200
    assert r1.headers["cache-control"] == "private, no-store"
    t1 = _token(r1.json()["urls"])
    t2 = _token(client.post("/me/heatmap/share", headers=auth_headers).json()["urls"])
    assert t1 != t2
    for t in (t1, t2):
        assert uid.replace("-", "") not in t and uid not in t
        assert hashlib.sha256(uid.encode()).hexdigest()[:8] not in t
    db = SessionLocal()
    try:
        row = db.execute(
            sa_text("SELECT token_sha256 FROM my_heatmap_shares WHERE user_id::text = :u"), {"u": uid}
        ).one()
    finally:
        db.close()
    assert row[0] == hashlib.sha256(t2.encode()).hexdigest()
    # The status endpoint never returns the link.
    st = client.get("/me/heatmap/share", headers=auth_headers).json()
    assert st["active"] is True and st["created_at"]
    assert t2 not in json.dumps(st)


# ── end to end: build, 4 URLs, revoke, regenerate ────────────────────────────


def test_share_roundtrip_revoke_and_regenerate(share_env, client, auth_headers, users, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    uid, coords = _share_user(client, auth_headers, users)
    assert client.get("/me/heatmap/share", headers=auth_headers).json() == {"available": True, "active": False}
    resp = client.post("/me/heatmap/share", headers=auth_headers).json()
    urls = resp["urls"]
    assert urls["style"].startswith("http://testserver/s/")
    token = _token(urls)

    # Before the build: the link resolves, but there is nothing to serve yet.
    assert client.get(urls["style"]).status_code == 200
    res = mhf.build_for_user(uid)
    assert res["status"] == "published"
    assert res["activity_count"] == 3
    # Only the dated, non-Strava-API ride is shared.
    assert res["share_activity_count"] == 1
    st = client.get("/me/heatmap/share", headers=auth_headers).json()
    assert st["ready"] is True and st["activity_count"] == 1

    pub = _public_urls(urls, coords)
    head = _get(client, pub["pmtiles"])
    assert head.status_code == 206 and head.content[:7] == b"PMTiles"
    assert head.headers["cache-control"] == "public, max-age=300"
    style = client.get(pub["style"])
    assert style.status_code == 200
    body = style.json()
    assert body["version"] == 8
    assert body["sources"]["ma-heatmap"]["tiles"] == [urls["vector"]]
    pbf = client.get(pub["pbf"])
    assert pbf.status_code == 200, pbf.status_code
    assert pbf.headers["content-type"] == "application/vnd.mapbox-vector-tile"
    assert pbf.headers["cache-control"] == "public, max-age=300"
    png = client.get(pub["png"])
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(png.content))
    assert img.size == (256, 256) and img.mode == "RGBA"
    assert img.getchannel("A").getextrema()[1] > 0, "the ride is drawn"
    # Over-zoom past the archive's max zoom renders from the z14 tile.
    mid = coords[len(coords) // 4]
    x16, y16 = _tile_xy(mid[0], mid[1], 16)
    assert client.get(urls["raster"].format(z=16, x=x16, y=y16)).status_code == 200
    # Out-of-range tile → 404, empty tile → transparent PNG / 204.
    assert client.get(urls["raster"].format(z=3, x=99, y=0)).status_code == 404
    assert client.get(urls["vector"].format(z=14, x=0, y=0)).status_code == 204

    # Regenerate → the old token is dead on all 4 URLs, the new one works.
    urls2 = client.post("/me/heatmap/share", headers=auth_headers).json()["urls"]
    for u in pub.values():
        assert _get(client, u).status_code == 404, u
    pub2 = _public_urls(urls2, coords)
    for u in pub2.values():
        assert _get(client, u).status_code in (200, 206), u

    # Revoke → 404 at once everywhere, shared objects gone, private file intact.
    assert client.delete("/me/heatmap/share", headers=auth_headers).status_code == 204
    for u in pub2.values():
        assert _get(client, u).status_code == 404, u
    store = mhf.get_store()
    assert store.list_keys(f"shares/{uid}/") == []
    assert store.list_keys(f"users/{uid}/")
    assert client.get("/me/heatmap/manifest", headers=auth_headers).json()["status"] == "ready"
    assert client.get("/me/heatmap/share", headers=auth_headers).json()["active"] is False

    # Never a token in the logs.
    assert token not in caplog.text
    assert _token(urls2) not in caplog.text


def test_share_requires_auth_and_flag(share_env, client, auth_headers, monkeypatch, users):  # noqa: F811
    uid, coords = _share_user(client, auth_headers, users, extra=False)
    assert client.post("/me/heatmap/share").status_code == 401
    assert client.get("/me/heatmap/share").status_code == 401
    assert client.delete("/me/heatmap/share").status_code == 401
    urls = client.post("/me/heatmap/share", headers=auth_headers).json()["urls"]
    mhf.build_for_user(uid)
    pub = _public_urls(urls, coords)
    assert client.get(pub["style"]).status_code == 200
    # Flag off → every share link is dead, creation refused, revocation allowed.
    monkeypatch.setenv("MY_HEATMAP_FILES_ENABLED", "false")
    for u in pub.values():
        assert _get(client, u).status_code == 404
    assert client.post("/me/heatmap/share", headers=auth_headers).status_code == 404
    assert client.get("/me/heatmap/share", headers=auth_headers).json()["available"] is False
    assert client.delete("/me/heatmap/share", headers=auth_headers).status_code == 204


def test_malformed_and_unknown_tokens_404(share_env, client):
    for t in ("x", "a" * 32, "../../users", "%2e%2e", "A" * 31 + "!"):
        assert client.get(f"/s/{t}/style.json").status_code == 404
        assert client.get(f"/s/{t}/14/1/1.png").status_code == 404


# ── privacy of the shared artefact ───────────────────────────────────────────


def _features(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_shared_artefact_is_masked_and_carries_sport_only(share_env, users, tmp_path):  # noqa: F811
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        lon0, lat0 = _start()
        coords = _ride(lon0, lat0)
        _mk_activity(db, uid, geometry_geojson=_geojson(coords), name="Secret name", sport="gravel")
        db.commit()
    finally:
        db.close()
    out = str(tmp_path / "shared.ndjson")
    assert mhf._write_share_inputs(uid, out) == 1
    feats = _features(out)
    assert len(feats) == 1
    f = feats[0]
    assert f["properties"] == {"sport": "gravel"}
    assert "id" not in f
    start, end = coords[0], coords[-1]
    n = 0
    for run in f["geometry"]["coordinates"]:
        for p in run:
            n += 1
            # 5-decimal rounding ≈ 1 m.
            assert haversine_m(start[0], start[1], p[0], p[1]) >= MASK_M - 1.5
            assert haversine_m(end[0], end[1], p[0], p[1]) >= MASK_M - 1.5
    assert n >= 4, "the ride outside the discs is kept"
    assert "Secret" not in json.dumps(feats) and uid not in json.dumps(feats)


def test_published_tiles_are_masked(share_env, users):  # noqa: F811
    """Decode every z14 tile of the published shared PMTiles: no vertex within
    the mask radius of the ride's ends, and the layer has the key ``sport``
    only."""
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        db.execute(
            sa_text("INSERT INTO my_heatmap_shares (user_id, token_sha256) VALUES (CAST(:u AS uuid), :h)"),
            {"u": uid, "h": hashlib.sha256(uuid.uuid4().bytes).hexdigest()},
        )
        lon0, lat0 = _start()
        coords = _ride(lon0, lat0)
        _mk_activity(db, uid, geometry_geojson=_geojson(coords))
        db.commit()
    finally:
        db.close()
    assert mhf.build_for_user(uid)["status"] == "published"
    db = SessionLocal()
    try:
        state = mhf.read_state(db, uid)
    finally:
        db.close()
    key = mhf.share_pmtiles_key(uid, state.share_version)
    assert key.startswith(f"shares/{uid}/")
    store = mhf.get_store()

    def read(s, e):
        return store.read(key, s, e)

    xs = [_tile_xy(p[0], p[1], 14) for p in coords]
    seen = 0
    for tx in range(min(x for x, _ in xs) - 1, max(x for x, _ in xs) + 2):
        for ty in range(min(y for _, y in xs) - 1, max(y for _, y in xs) + 2):
            _h, data = pmtiles_reader.get_tile(key, read, 14, tx, ty)
            if not data:
                continue
            raw = gzip.decompress(data)
            for field, _wt, layer in mvt_raster._fields(raw):
                if field != 3:
                    continue
                keys = [v.decode() for f2, _w, v in mvt_raster._fields(layer) if f2 == 3]
                assert keys == ["sport"]
                ids = [
                    ff for f2, _w, feat in mvt_raster._fields(layer) if f2 == 2
                    for ff, _w2, _v in mvt_raster._fields(feat) if ff == 1
                ]
                assert ids == [], "no feature id in the shared tiles"
            for extent, lines in mvt_raster.decode_lines(raw):
                for line in lines:
                    for px, py in line:
                        if not (0 <= px <= extent and 0 <= py <= extent):
                            continue  # tile buffer: checked in its own tile
                        n = 1 << 14
                        lon = (tx + px / extent) / n * 360 - 180
                        lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty + py / extent) / n))))
                        seen += 1
                        # z14 quantisation (4096 px / tile ≈ 2.4 m) + rounding.
                        assert haversine_m(coords[0][0], coords[0][1], lon, lat) >= MASK_M - 4
    assert seen > 10


def test_masked_runs_matches_public_mask():
    """Same polyline, same function as the public raw map: the shared
    geometry is a subset of ``mask_endpoints`` of the raw fixes."""
    from app.services.trace_privacy import mask_endpoints

    coords = _ride(3.0, 43.6)
    runs = mhf.masked_runs(coords, MASK_M)
    ref = mask_endpoints(coords, MASK_M)
    assert len(runs) == len(ref)
    for a, b in zip(runs, ref, strict=True):
        assert a[0] == [round(b[0][0], 5), round(b[0][1], 5)]
        assert a[-1] == [round(b[-1][0], 5), round(b[-1][1], 5)]


def test_fully_masked_ride_is_not_shared(share_env, users, tmp_path):  # noqa: F811
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        lon0, lat0 = _start()
        _mk_activity(db, uid, geometry_geojson=_geojson(_ride(lon0, lat0, n=10)))
        db.commit()
    finally:
        db.close()
    assert mhf._write_share_inputs(uid, str(tmp_path / "s.ndjson")) == 0


def test_private_object_never_reachable_by_token(share_env, client, auth_headers, users):  # noqa: F811
    uid, coords = _share_user(client, auth_headers, users, extra=False)
    urls = client.post("/me/heatmap/share", headers=auth_headers).json()["urls"]
    token = _token(urls)
    mhf.build_for_user(uid)
    store = mhf.get_store()
    # Remove the shared object only: the private one must NOT be served instead.
    for k in store.list_keys(f"shares/{uid}/"):
        store.delete(k)
    pmtiles_reader.clear_cache()
    assert _get(client, urls["pmtiles"]).status_code == 404
    pub = _public_urls(urls, coords)
    assert client.get(pub["pbf"]).status_code == 404
    assert client.get(pub["png"]).status_code == 404
    # The private proxy does not answer to a token (no cookie, token as version).
    assert client.get("/me/heatmap/manifest", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.get(f"/me/heatmap/{token}.pmtiles", headers={"Range": "bytes=0-9"}).status_code == 401
    assert client.get(
        f"/me/heatmap/{token}.pmtiles", headers={**auth_headers, "Range": "bytes=0-9"}
    ).status_code == 404
    # The resolved key is always under shares/.
    db = SessionLocal()
    try:
        res = share_svc.resolve(db, token)
    finally:
        db.close()
    assert res is not None and res.key.startswith(f"shares/{uid}/")


def test_revoke_racing_a_build_leaves_no_shared_object(share_env, users, monkeypatch):  # noqa: F811
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        share_svc.create_or_regenerate(db, uid)
        _mk_activity(db, uid, geometry_geojson=_geojson(_ride(*_start())))
        db.commit()
    finally:
        db.close()
    real = mhf._publish_cas

    def revoke_then_publish(*a, **kw):
        s = SessionLocal()
        try:
            share_svc.revoke(s, uid)
        finally:
            s.close()
        return real(*a, **kw)

    monkeypatch.setattr(mhf, "_publish_cas", revoke_then_publish)
    assert mhf.build_for_user(uid)["status"] == "published"
    db = SessionLocal()
    try:
        assert mhf.read_state(db, uid).share_version is None
    finally:
        db.close()
    assert mhf.get_store().list_keys(f"shares/{uid}/") == []


def test_enabling_share_on_a_fresh_file_rebuilds(share_env, users):  # noqa: F811
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        _mk_activity(db, uid, geometry_geojson=_geojson(_ride(*_start())))
        db.commit()
    finally:
        db.close()
    assert mhf.build_for_user(uid)["status"] == "published"
    assert mhf.build_for_user(uid)["status"] == "fresh"
    db = SessionLocal()
    try:
        share_svc.create_or_regenerate(db, uid)
        assert any(u == uid for u, _ in share_env), "creating a share schedules the build"
        assert mhf.manifest(db, uid)["status"] == "ready"
    finally:
        db.close()
    res = mhf.build_for_user(uid)
    assert res["status"] == "published" and res["share_activity_count"] == 1
    assert mhf.build_for_user(uid)["status"] == "fresh"


def test_source_change_marks_file_stale(users):  # noqa: F811
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        aid = _mk_activity(db, uid, source="strava_api")
        db.commit()
        rev = db.execute(sa_text("SELECT data_rev FROM my_heatmap_files WHERE user_id = :u"), {"u": uid}).scalar()
        db.execute(sa_text("UPDATE activities SET source = 'manual_upload' WHERE id = :a"), {"a": aid})
        db.commit()
        assert db.execute(
            sa_text("SELECT data_rev FROM my_heatmap_files WHERE user_id = :u"), {"u": uid}
        ).scalar() == rev + 1
    finally:
        db.close()


# ── CORS ─────────────────────────────────────────────────────────────────────


def test_cors_star_on_share_routes_only(share_env, client, auth_headers, users):  # noqa: F811
    uid, coords = _share_user(client, auth_headers, users, extra=False)
    urls = client.post("/me/heatmap/share", headers=auth_headers).json()["urls"]
    mhf.build_for_user(uid)
    origin = {"Origin": "https://gpx.studio"}
    r = client.get(urls["style"], headers=origin)
    assert r.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in r.headers
    pre = client.options(
        urls["pmtiles"],
        headers={**origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "range"},
    )
    assert pre.status_code == 204
    assert pre.headers["access-control-allow-origin"] == "*"
    assert "range" in pre.headers["access-control-allow-headers"].lower()
    # Even our own origin (credentialed site-wide policy) gets `*`, no credentials.
    r = client.get(urls["style"], headers={"Origin": "http://localhost:3787"})
    assert r.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in r.headers
    # The private routes: no CORS for a foreign origin, never `*`.
    m = client.get("/me/heatmap/manifest", headers={**auth_headers, **origin})
    assert "access-control-allow-origin" not in m.headers
    s = client.get("/me/heatmap/share", headers={**auth_headers, **origin})
    assert "access-control-allow-origin" not in s.headers
    pre = client.options(
        "/me/heatmap/manifest", headers={**origin, "Access-Control-Request-Method": "GET"}
    )
    assert pre.headers.get("access-control-allow-origin") != "*"


def test_share_routes_ignore_cookies(share_env, client, auth_headers, users):  # noqa: F811
    """No ambient authority: /s/ never sees the session cookie."""
    uid, _ = _share_user(client, auth_headers, users, extra=False)
    urls = client.post("/me/heatmap/share", headers=auth_headers).json()["urls"]
    token = auth_headers["Authorization"].split(" ", 1)[1]
    r = client.get(urls["style"], headers={"Cookie": f"auth_token={token}"})
    assert r.status_code == 200
    assert "set-cookie" not in r.headers


# ── deletion paths ───────────────────────────────────────────────────────────


def _share_row_count(uid: str) -> int:
    db = SessionLocal()
    try:
        return int(db.execute(
            sa_text("SELECT count(*) FROM my_heatmap_shares WHERE user_id::text = :u"), {"u": uid}
        ).scalar())
    finally:
        db.close()


def _built_share(uid: str) -> None:
    db = SessionLocal()
    try:
        share_svc.create_or_regenerate(db, uid)
        _mk_activity(db, uid, geometry_geojson=_geojson(_ride(*_start())))
        db.commit()
    finally:
        db.close()
    assert mhf.build_for_user(uid)["share_activity_count"] == 1
    assert mhf.get_store().list_keys(f"shares/{uid}/")


def test_gdpr_delete_removes_share(share_env, users):  # noqa: F811
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        db.commit()
    finally:
        db.close()
    _built_share(uid)
    assert mhf.delete_for_user(uid) >= 3  # private pmtiles + summary + shared
    assert mhf.get_store().list_keys(f"shares/{uid}/") == []
    assert mhf.get_store().list_keys(f"users/{uid}/") == []
    assert _share_row_count(uid) == 0


def test_account_merge_removes_absorbed_share(share_env, users):  # noqa: F811
    from app.services.account_merge import merge_synthetic_account

    athlete = uuid.uuid4().hex[:10]
    db = SessionLocal()
    try:
        survivor = _mk_user(db, email=f"real_{athlete}@example.com")
        synth = _mk_user(db, email=f"strava_{athlete}@strava.local", password=None)
        users += [survivor, synth]
        db.commit()
    finally:
        db.close()
    _built_share(synth)
    db = SessionLocal()
    try:
        assert merge_synthetic_account(db, survivor, synth, commit=True).merged
    finally:
        db.close()
    assert _share_row_count(synth) == 0
    assert mhf.get_store().list_keys(f"shares/{synth}/") == []
    # The survivor did not inherit the absorbed account's link.
    assert _share_row_count(survivor) == 0


def test_user_row_delete_cascades_share(share_env, users):  # noqa: F811
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        share_svc.create_or_regenerate(db, uid)
        db.execute(sa_text("DELETE FROM users WHERE id = :u"), {"u": uid})
        db.commit()
    finally:
        db.close()
    assert _share_row_count(uid) == 0


def test_sweep_removes_shared_objects_without_share(share_env, users):  # noqa: F811
    db = SessionLocal()
    try:
        uid = _mk_user(db)
        users.append(uid)
        db.commit()
    finally:
        db.close()
    _built_share(uid)
    # Revocation whose object deletion failed: row gone, objects left behind.
    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM my_heatmap_shares WHERE user_id::text = :u"), {"u": uid})
        db.commit()
    finally:
        db.close()
    assert uid in mhf.sweep_orphans(dry_run=True)
    assert mhf.get_store().list_keys(f"shares/{uid}/")
    mhf.sweep_orphans()
    assert mhf.get_store().list_keys(f"shares/{uid}/") == []
    assert mhf.get_store().list_keys(f"users/{uid}/"), "the private file is not an orphan"


# ── Sentry, base URL, reader ─────────────────────────────────────────────────


def test_sentry_scrub_redacts_share_tokens():
    from app.sentry_scrub import before_send

    t = share_svc.new_token()
    ev = {
        "request": {"url": f"https://example.org/s/{t}/14/1/2.png"},
        "transaction": f"/s/{t}/style.json",
        "breadcrumbs": {"values": [{"message": f"GET /s/{t}/heatmap.pmtiles", "data": {"url": f"/s/{t}/x"}}]},
    }
    assert t not in json.dumps(before_send(ev, {}))


def test_public_base_url_override(share_env, client, auth_headers, users, monkeypatch):  # noqa: F811
    _share_user(client, auth_headers, users, extra=False)
    monkeypatch.setenv("MY_HEATMAP_SHARE_BASE_URL", "https://example.org/")
    urls = client.post("/me/heatmap/share", headers=auth_headers).json()["urls"]
    assert urls["style"].startswith("https://example.org/s/")
    monkeypatch.delenv("MY_HEATMAP_SHARE_BASE_URL")
    urls = client.post(
        "/me/heatmap/share", headers={**auth_headers, "X-Forwarded-Proto": "https"}
    ).json()["urls"]
    assert urls["style"].startswith("https://testserver/s/")


def test_hilbert_tile_ids():
    # PMTiles spec reference values.
    assert pmtiles_reader.zxy_to_tile_id(0, 0, 0) == 0
    assert pmtiles_reader.zxy_to_tile_id(1, 0, 0) == 1
    assert pmtiles_reader.zxy_to_tile_id(1, 0, 1) == 2
    assert pmtiles_reader.zxy_to_tile_id(1, 1, 1) == 3
    assert pmtiles_reader.zxy_to_tile_id(1, 1, 0) == 4
    assert pmtiles_reader.zxy_to_tile_id(2, 0, 0) == 5
    assert pmtiles_reader.zxy_to_tile_id(20, 0, 0) == 366503875925
    with pytest.raises(pmtiles_reader.PMTilesError):
        pmtiles_reader.zxy_to_tile_id(2, 4, 0)
