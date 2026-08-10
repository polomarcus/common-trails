"""Tests for ``app.cli.load_heatmap_fixture``.

Coverage:
1. **Round-trip**: dump → load → dump-again, second dump matches the
   first (modulo column defaults that get normalised through ST_GeomFromGeoJSON).
2. **Idempotence**: loading the same fixture twice produces the same
   final state (no row duplication, no count drift).
3. **Bbox scoping**: loading a fixture only wipes data within the
   fixture's bbox + sport — adjacent regions / other sports are untouched.
4. **Version rejection**: loader refuses unknown fixture versions.
5. **Schema validation**: malformed payloads are rejected before any
   DB writes.

Same off-grid Iceland bbox approach as `test_dump_heatmap_fixture.py`.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text as sa_text

from app.cli.dump_heatmap_fixture import dump_fixture
from app.cli.load_heatmap_fixture import load_fixture
from app.db.session import SessionLocal
from app.services.ingest import _update_heat_edges

_SPORT = "gravel"
_BBOX = (-22.5, 66.8, -22.0, 67.0)
_NEIGHBOR_BBOX = (-22.5, 67.0, -22.0, 67.2)


def _wipe_bbox(bbox: tuple[float, float, float, float]) -> None:
    min_lon, min_lat, max_lon, max_lat = bbox
    db = SessionLocal()
    try:
        db.execute(
            sa_text(
                """
                DELETE FROM heat_edge_contributors WHERE edge_key IN (
                    SELECT edge_key FROM heat_edges
                    WHERE ST_Intersects(
                        geometry,
                        ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
                    )
                )
                """
            ),
            {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat},
        )
        db.execute(
            sa_text(
                """
                DELETE FROM heat_edges
                WHERE ST_Intersects(
                    geometry,
                    ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
                )
                """
            ),
            {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat},
        )
        db.execute(
            sa_text(
                """
                DELETE FROM osm_road_edges
                WHERE ST_Intersects(
                    geometry,
                    ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
                )
                """
            ),
            {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat},
        )
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clear_bboxes():
    _wipe_bbox(_BBOX)
    _wipe_bbox(_NEIGHBOR_BBOX)
    yield
    _wipe_bbox(_BBOX)
    _wipe_bbox(_NEIGHBOR_BBOX)


def _geojson(coords: list) -> str:
    return json.dumps({"type": "LineString", "coordinates": coords})


def _make_activity_id(token: str) -> str:
    h = abs(hash(token)) % (16 ** 12)
    return f"00000000-0000-0000-0000-{h:012x}"


def _ingest_chain_in_bbox(bbox: tuple[float, float, float, float], n_users: int = 3) -> None:
    min_lon, min_lat, max_lon, max_lat = bbox
    base_lon = (min_lon + max_lon) / 2 - 0.002
    base_lat = (min_lat + max_lat) / 2
    step = 0.0008
    chain = [[base_lon + i * step, base_lat + i * step * 0.5] for i in range(5)]
    for u_idx in range(n_users):
        _update_heat_edges(
            f"user{u_idx}",
            _SPORT,
            _geojson(chain),
            activity_id=_make_activity_id(f"load-{min_lat}-{u_idx}"),
        )


def _count_heat_edges(bbox: tuple[float, float, float, float], sport: str) -> int:
    min_lon, min_lat, max_lon, max_lat = bbox
    db = SessionLocal()
    try:
        return db.execute(
            sa_text(
                """
                SELECT count(*) FROM heat_edges
                WHERE sport = :sport
                  AND ST_Intersects(
                      geometry,
                      ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
                  )
                """
            ),
            {
                "sport": sport,
                "min_lon": min_lon,
                "min_lat": min_lat,
                "max_lon": max_lon,
                "max_lat": max_lat,
            },
        ).scalar() or 0
    finally:
        db.close()


def _count_osm_edges(bbox: tuple[float, float, float, float]) -> int:
    min_lon, min_lat, max_lon, max_lat = bbox
    db = SessionLocal()
    try:
        return db.execute(
            sa_text(
                """
                SELECT count(*) FROM osm_road_edges
                WHERE ST_Intersects(
                    geometry,
                    ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
                )
                """
            ),
            {
                "min_lon": min_lon,
                "min_lat": min_lat,
                "max_lon": max_lon,
                "max_lat": max_lat,
            },
        ).scalar() or 0
    finally:
        db.close()


# ── Tests ────────────────────────────────────────────────────────────────────


def test_load_round_trip_preserves_row_count():
    _ingest_chain_in_bbox(_BBOX)
    fixture = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=1)
    pre_count = len(fixture["heat_edges"])
    assert pre_count > 0

    # Wipe + load → row count matches the fixture.
    counts = load_fixture(fixture)
    assert counts["heat_edges_deleted"] == pre_count
    assert counts["heat_edges_inserted"] == pre_count

    re_dump = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=1)
    assert len(re_dump["heat_edges"]) == pre_count

    # edge_keys must match exactly.
    pre_keys = {e["edge_key"] for e in fixture["heat_edges"]}
    post_keys = {e["edge_key"] for e in re_dump["heat_edges"]}
    assert pre_keys == post_keys


def test_load_is_idempotent():
    _ingest_chain_in_bbox(_BBOX)
    fixture = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=1)

    counts_1 = load_fixture(fixture)
    counts_2 = load_fixture(fixture)
    # Second load deletes EXACTLY what the first load inserted.
    assert counts_2["heat_edges_deleted"] == counts_1["heat_edges_inserted"]
    assert counts_2["heat_edges_inserted"] == counts_1["heat_edges_inserted"]

    # And the final row count is stable.
    assert _count_heat_edges(_BBOX, _SPORT) == counts_1["heat_edges_inserted"]


def test_load_only_touches_fixture_bbox():
    """Loading the BBOX fixture must NOT delete data in NEIGHBOR_BBOX."""
    _ingest_chain_in_bbox(_BBOX)
    _ingest_chain_in_bbox(_NEIGHBOR_BBOX)

    neighbor_count_before = _count_heat_edges(_NEIGHBOR_BBOX, _SPORT)
    assert neighbor_count_before > 0

    fixture = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=1)
    load_fixture(fixture)

    neighbor_count_after = _count_heat_edges(_NEIGHBOR_BBOX, _SPORT)
    assert neighbor_count_after == neighbor_count_before, (
        f"loading a bbox-scoped fixture must not affect neighboring rows "
        f"(before={neighbor_count_before} after={neighbor_count_after})"
    )


def test_load_only_touches_fixture_sport():
    """Loading a `gravel` fixture must NOT touch `road` rows in the
    same bbox."""
    _ingest_chain_in_bbox(_BBOX)
    # Add some road traces in the same bbox.
    base_lon = (_BBOX[0] + _BBOX[2]) / 2 - 0.002
    base_lat = (_BBOX[1] + _BBOX[3]) / 2 + 0.005
    step = 0.0008
    chain = [[base_lon + i * step, base_lat + i * step * 0.5] for i in range(5)]
    for u_idx in range(3):
        _update_heat_edges(
            f"road_user{u_idx}",
            "road",
            _geojson(chain),
            activity_id=_make_activity_id(f"road-{u_idx}"),
        )

    road_count_before = _count_heat_edges(_BBOX, "road")
    assert road_count_before > 0

    fixture = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=1)
    load_fixture(fixture)

    road_count_after = _count_heat_edges(_BBOX, "road")
    assert road_count_after == road_count_before, (
        f"loading a gravel fixture must not touch road rows in the same bbox "
        f"(before={road_count_before} after={road_count_after})"
    )


def test_load_rejects_unknown_version():
    fixture = {
        "version": 999,
        "bbox": list(_BBOX),
        "sport": _SPORT,
        "heat_edges": [],
        "osm_road_edges": [],
    }
    with pytest.raises(ValueError, match="unsupported"):
        load_fixture(fixture)


def test_load_rejects_malformed_bbox():
    fixture = {
        "version": 1,
        "bbox": [1.0, 2.0],  # too short
        "sport": _SPORT,
        "heat_edges": [],
        "osm_road_edges": [],
    }
    with pytest.raises(ValueError, match="bbox"):
        load_fixture(fixture)


def test_load_rejects_missing_arrays():
    fixture = {
        "version": 1,
        "bbox": list(_BBOX),
        "sport": _SPORT,
        # heat_edges missing — should fail validation.
        "osm_road_edges": [],
    }
    with pytest.raises(ValueError, match="heat_edges"):
        load_fixture(fixture)


def test_load_inserts_osm_road_edges():
    """Round-trip osm_road_edges geometry through the loader."""
    db = SessionLocal()
    try:
        coords = [
            [(_BBOX[0] + _BBOX[2]) / 2, (_BBOX[1] + _BBOX[3]) / 2],
            [(_BBOX[0] + _BBOX[2]) / 2 + 0.0005, (_BBOX[1] + _BBOX[3]) / 2 + 0.0005],
        ]
        db.execute(
            sa_text(
                """
                INSERT INTO osm_road_edges (
                    tile_key, osm_way_id, segment_idx, surface, highway, geometry
                ) VALUES (
                    900019001, 4242, 0, 'asphalt', 'tertiary',
                    ST_GeomFromGeoJSON(:geom)
                )
                """
            ),
            {"geom": _geojson(coords)},
        )
        db.commit()
    finally:
        db.close()

    fixture = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=1)
    assert len(fixture["osm_road_edges"]) == 1

    counts = load_fixture(fixture)
    assert counts["osm_road_edges_deleted"] == 1
    assert counts["osm_road_edges_inserted"] == 1
    assert _count_osm_edges(_BBOX) == 1


def test_load_accepts_legacy_v1_fixture():
    """Pre-0056 fixtures (version 1: TEXT ``"14/x/y"`` tile_key + per-row
    ``way_geometry``) must still load — tile_key re-encoded to BIGINT, the
    way polyline upserted into the osm_ways side-table."""
    mid = [(_BBOX[0] + _BBOX[2]) / 2, (_BBOX[1] + _BBOX[3]) / 2]
    seg = {"type": "LineString",
           "coordinates": [mid, [mid[0] + 0.0005, mid[1] + 0.0005]]}
    way = {"type": "LineString",
           "coordinates": [mid, [mid[0] + 0.0002, mid[1] + 0.0004],
                           [mid[0] + 0.0005, mid[1] + 0.0005]]}
    fixture = {
        "version": 1,
        "bbox": list(_BBOX),
        "sport": _SPORT,
        "heat_edges": [],
        "osm_road_edges": [{
            "tile_key": "14/9001/9001",
            "osm_way_id": 4243,
            "segment_idx": 0,
            "surface": "asphalt",
            "highway": "tertiary",
            "geometry": seg,
            "way_geometry": way,
        }],
    }
    counts = load_fixture(fixture)
    assert counts["osm_road_edges_inserted"] == 1
    assert counts["osm_ways_upserted"] == 1

    db = SessionLocal()
    try:
        tk = db.execute(sa_text(
            "SELECT tile_key FROM osm_road_edges WHERE osm_way_id = 4243"
        )).scalar()
        assert tk == 9001 * 100_000 + 9001, f"legacy tile_key must re-encode, got {tk}"
        npoints = db.execute(sa_text(
            "SELECT ST_NPoints(way_geometry) FROM osm_ways WHERE osm_way_id = 4243"
        )).scalar()
        assert npoints == 3, "legacy per-row way_geometry must land in osm_ways"
        db.execute(sa_text("DELETE FROM osm_road_edges WHERE osm_way_id = 4243"))
        db.execute(sa_text("DELETE FROM osm_ways WHERE osm_way_id = 4243"))
        db.commit()
    finally:
        db.close()


def test_load_then_dump_byte_stable():
    """After load_fixture, a re-dump of the same bbox must match the
    fixture's heat_edges + osm_road_edges sets exactly. This is the
    determinism guarantee the snapshot workflow relies on.
    """
    _ingest_chain_in_bbox(_BBOX)
    fixture_a = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=1)
    load_fixture(fixture_a)
    fixture_b = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=1)

    # Compare edge_keys + per-row counters that the dump captures.
    keys_a = sorted(e["edge_key"] for e in fixture_a["heat_edges"])
    keys_b = sorted(e["edge_key"] for e in fixture_b["heat_edges"])
    assert keys_a == keys_b

    by_key_a = {e["edge_key"]: e for e in fixture_a["heat_edges"]}
    by_key_b = {e["edge_key"]: e for e in fixture_b["heat_edges"]}
    for k in keys_a:
        assert by_key_a[k]["user_count"] == by_key_b[k]["user_count"]
        assert by_key_a[k]["pass_count"] == by_key_b[k]["pass_count"]
