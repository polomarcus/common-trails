"""Compact display geometry for "Ma heatmap" (GET /me/activities payload fix).

Non-regression for the /map payload: /me/activities?limit=5000 shipped every
activity's full-resolution geometry_geojson (1.48 MB raw / 577 KB gzipped for
one real 38k-point ride). The list now serves a precomputed compact geometry
(activities.display_geojson): gap-split on raw coords → Douglas-Peucker 2 m →
5-decimal coords. Tests drive the real functions / handler / ingest / backfill.
"""

import gzip
import json
import math

import gpxpy
import gpxpy.gpx

from app.services.display_geometry import (
    DISPLAY_SIMPLIFY_TOLERANCE_M,
    _simplify_dp,
    compact_display_geojson,
    compact_display_geometry,
)

LAT0 = 43.62
_KY = 6_371_000.0 * math.pi / 180.0
_KX = _KY * math.cos(math.radians(LAT0))


def _dense_wiggly(n: int = 5000, start_lon: float = 3.87) -> list[list[float]]:
    """A 1 Hz-like ride: ~4.8 m spacing, a gentle 30 m-amplitude meander,
    plus sub-metre GPS jitter and full float precision (what a device emits)."""
    out = []
    for i in range(n):
        lon = start_lon + i * 0.00006
        lat = LAT0 + 0.00027 * math.sin(i / 150.0) + 0.000003 * math.sin(i * 1.7)
        out.append([lon, lat, 50.0 + 20.0 * math.sin(i / 400.0)])
    return out


def _xy(c):
    return c[0] * _KX, c[1] * _KY


def _dist_point_to_polyline_m(p, line) -> float:
    px, py = _xy(p)
    best = float("inf")
    for a, b in zip(line, line[1:], strict=False):
        ax, ay = _xy(a)
        bx, by = _xy(b)
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
        best = min(best, math.hypot(px - ax - t * dx, py - ay - t * dy))
    return best


def _n_points(geom: dict) -> int:
    if geom["type"] == "LineString":
        return len(geom["coordinates"])
    return sum(len(p) for p in geom["coordinates"])


def _decimals(x: float) -> int:
    s = repr(x)
    return len(s.split(".")[1]) if "." in s and "e" not in s else 0


# ── Pure function ────────────────────────────────────────────────────────────


class TestCompactDisplayGeometry:
    def test_dense_straight_line_collapses_to_endpoints(self):
        coords = [[3.87 + i * 0.00005, LAT0] for i in range(1000)]
        geom = compact_display_geometry({"type": "LineString", "coordinates": coords})
        assert geom == {"type": "LineString", "coordinates": [[3.87, LAT0], [round(coords[-1][0], 5), LAT0]]}

    def test_point_count_reduced_and_deviation_within_tolerance(self):
        coords = _dense_wiggly(3000)
        geom = compact_display_geometry({"type": "LineString", "coordinates": coords})
        assert geom["type"] == "LineString"
        simplified = geom["coordinates"]
        assert len(simplified) < len(coords) / 5
        # Every original fix lies within DP tolerance (+ ~1 m of 5-decimal
        # rounding) of the drawn line.
        worst = max(_dist_point_to_polyline_m(p, simplified) for p in coords[::7])
        assert worst <= DISPLAY_SIMPLIFY_TOLERANCE_M + 1.0, worst

    def test_dp_alone_respects_tolerance_exactly(self):
        coords = _dense_wiggly(2000)
        kept = _simplify_dp(coords, 2.0)
        assert kept[0] is coords[0] and kept[-1] is coords[-1]
        assert max(_dist_point_to_polyline_m(p, kept) for p in coords) <= 2.0 + 1e-6

    def test_endpoints_kept(self):
        coords = _dense_wiggly(1500)
        out = compact_display_geometry({"type": "LineString", "coordinates": coords})["coordinates"]
        assert out[0][:2] == [round(coords[0][0], 5), round(coords[0][1], 5)]
        assert out[-1][:2] == [round(coords[-1][0], 5), round(coords[-1][1], 5)]

    def test_out_and_back_far_end_kept(self):
        """Point-to-SEGMENT distance: the turnaround of an out-and-back must
        survive (a point-to-infinite-line DP would drop it)."""
        out_leg = [[3.87 + i * 0.0001, LAT0] for i in range(100)]
        coords = out_leg + out_leg[-2::-1]
        simplified = compact_display_geometry({"type": "LineString", "coordinates": coords})["coordinates"]
        assert [round(out_leg[-1][0], 5), LAT0] in simplified

    def test_gap_above_threshold_still_split(self):
        run_a = _dense_wiggly(400, start_lon=3.69)
        run_b = _dense_wiggly(400, start_lon=3.69 + 0.0004 * 60 + 0.062)  # ~5 km jump
        geom = compact_display_geometry({"type": "LineString", "coordinates": run_a + run_b})
        assert geom["type"] == "MultiLineString"
        assert len(geom["coordinates"]) == 2

    def test_long_straight_dense_segment_not_split(self):
        """Split BEFORE simplify: a 4 km dense straight road collapses to ONE
        4 km segment, which must not be mistaken for a > 2500 m dropout."""
        coords = [[3.87 + i * 0.00006, LAT0] for i in range(int(4000 / 4.8))]
        span = (coords[-1][0] - coords[0][0]) * _KX
        assert span > 2500
        geom = compact_display_geometry({"type": "LineString", "coordinates": coords})
        assert geom["type"] == "LineString"
        assert len(geom["coordinates"]) == 2

    def test_coords_have_at_most_5_decimals_and_elevation_kept(self):
        geom = compact_display_geometry({"type": "LineString", "coordinates": _dense_wiggly(800)})
        for c in geom["coordinates"]:
            assert _decimals(c[0]) <= 5 and _decimals(c[1]) <= 5
            assert len(c) == 3 and _decimals(c[2]) <= 1

    def test_degenerate_inputs_safe(self):
        assert compact_display_geometry({"type": "LineString", "coordinates": []}) == {
            "type": "LineString",
            "coordinates": [],
        }
        one = {"type": "LineString", "coordinates": [[3.87, 43.62]]}
        assert compact_display_geometry(one) == one
        two = {"type": "LineString", "coordinates": [[3.871234567, 43.621234567], [3.88, 43.63]]}
        assert compact_display_geometry(two) == {
            "type": "LineString",
            "coordinates": [[3.87123, 43.62123], [3.88, 43.63]],
        }
        point = {"type": "Point", "coordinates": [3.87, 43.62]}
        assert compact_display_geometry(point) == point
        assert compact_display_geojson(None) is None
        assert compact_display_geojson("") is None
        assert compact_display_geojson("{not json") is None
        assert compact_display_geojson("[1, 2]") is None

    def test_multilinestring_parts_simplified_not_resplit(self):
        a = [[3.87 + i * 0.00005, LAT0] for i in range(200)]
        b = [[3.95 + i * 0.00005, LAT0] for i in range(200)]
        geom = compact_display_geometry({"type": "MultiLineString", "coordinates": [a, b]})
        assert geom["type"] == "MultiLineString"
        assert [len(p) for p in geom["coordinates"]] == [2, 2]

    def test_input_not_mutated(self):
        coords = _dense_wiggly(300)
        snapshot = json.dumps(coords)
        compact_display_geometry({"type": "LineString", "coordinates": coords})
        assert json.dumps(coords) == snapshot

    def test_payload_at_least_5x_smaller(self):
        """Generated dense 1 Hz trace: compact JSON ≥ 5× smaller than the raw
        geometry_geojson, raw AND gzipped (GZip middleware is on in prod)."""
        raw = json.dumps({"type": "LineString", "coordinates": _dense_wiggly(20000)})
        compact = compact_display_geojson(raw)
        assert compact is not None
        assert len(raw) / len(compact) >= 5
        assert len(gzip.compress(raw.encode())) / len(gzip.compress(compact.encode())) >= 5


# ── Ingest + API (real upload, real streamed handler) ────────────────────────


def _gpx_bytes(name: str, coords: list) -> bytes:
    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(track)
    seg = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(seg)
    for lon, lat, ele in coords:
        seg.points.append(gpxpy.gpx.GPXTrackPoint(latitude=lat, longitude=lon, elevation=ele))
    return gpx.to_xml().encode()


def _upload(client, headers, name: str, coords: list) -> str:
    resp = client.post(
        "/gpx/upload",
        headers=headers,
        files={"file": (f"{name}.gpx", _gpx_bytes(name, coords), "application/gpx+xml")},
        data={"sport": "road", "contribute_heatmap": "false"},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["activity_id"]


def _list_feature(client, headers, activity_id: str) -> dict:
    feats = client.get("/me/activities?limit=5000", headers=headers).json()["features"]
    return next(f for f in feats if f["properties"]["id"] == activity_id)


def _row(activity_id: str):
    from app.db.models import Activity
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        a = db.query(Activity).filter(Activity.id == activity_id).one()
        return a.geometry_geojson, a.display_geojson
    finally:
        db.close()


def _set_display_null(activity_id: str) -> None:
    from sqlalchemy import text as sa_text

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(sa_text("UPDATE activities SET display_geojson = NULL WHERE id = :id"), {"id": activity_id})
        db.commit()
    finally:
        db.close()


class TestMeActivitiesCompactPayload:
    def test_ingest_writes_display_geojson_and_keeps_source_untouched(self, client, auth_headers):
        coords = _dense_wiggly(1200)
        aid = _upload(client, auth_headers, "compact-ingest", coords)
        geometry_geojson, display_geojson = _row(aid)
        assert display_geojson is not None
        assert display_geojson == compact_display_geojson(geometry_geojson)
        # Trace integrity: the stored source keeps every fix.
        assert len(json.loads(geometry_geojson)["coordinates"]) == len(coords)

    def test_list_serves_compact_geometry_and_falls_back_when_null(self, client, auth_headers):
        coords = _dense_wiggly(1500)
        aid = _upload(client, auth_headers, "compact-list", coords)
        cached = _list_feature(client, auth_headers, aid)["geometry"]
        assert _n_points(cached) < len(coords) / 5
        assert cached == json.loads(_row(aid)[1])

        _set_display_null(aid)
        fallback = _list_feature(client, auth_headers, aid)["geometry"]
        assert fallback == cached  # on-the-fly == precomputed, byte for byte

        # Detail endpoint: full precision, unchanged.
        detail = client.get(f"/me/activities/{aid}", headers=auth_headers).json()["geometry"]
        assert _n_points(detail) == len(coords)

    def test_list_does_not_ship_full_geometry_when_cached(self, client, auth_headers):
        """The DB projection must not stream the heavy TEXT for cached rows."""
        from app.services.ingest import iter_user_map_activities

        aid = _upload(client, auth_headers, "compact-projection", _dense_wiggly(600))
        user_id = client.get("/auth/me", headers=auth_headers).json()["user_id"]
        row = next(r for r in iter_user_map_activities(user_id) if r["id"] == aid)
        assert row["display_geojson"] is not None
        assert row["geometry_geojson"] is None

        _set_display_null(aid)
        row = next(r for r in iter_user_map_activities(user_id) if r["id"] == aid)
        assert row["display_geojson"] is None
        assert row["geometry_geojson"]

    def test_backfill_fills_missing_cache_idempotently(self, client, auth_headers):
        from app.cli.backfill_display_geojson import backfill

        aid = _upload(client, auth_headers, "compact-backfill", _dense_wiggly(700))
        user_id = client.get("/auth/me", headers=auth_headers).json()["user_id"]
        expected = _row(aid)[1]
        _set_display_null(aid)

        first = backfill(user_id=user_id)
        assert first["backfilled"] >= 1
        assert _row(aid)[1] == expected
        second = backfill(user_id=user_id)
        assert second["backfilled"] == 0


def test_strava_stream_upgrade_refreshes_display_geojson(client, auth_headers):
    """The Strava GPS-stream upgrade REPLACES geometry_geojson: the derived
    cache must follow, or "Ma heatmap" keeps drawing the old polyline."""
    from app.api.integrations_strava import _gps_apply_batch

    aid = _upload(client, auth_headers, "compact-stream-upgrade", _dense_wiggly(300))
    upgraded = json.dumps({"type": "LineString", "coordinates": _dense_wiggly(900, start_lon=3.9)})
    _gps_apply_batch("no-such-job", [(aid, upgraded)], 0)
    geometry_geojson, display_geojson = _row(aid)
    assert geometry_geojson == upgraded
    assert display_geojson == compact_display_geojson(upgraded)
