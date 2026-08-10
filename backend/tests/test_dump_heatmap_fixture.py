"""Tests for ``app.cli.dump_heatmap_fixture``.

The dump CLI is the upstream half of the Layer-3 fixture loop. We test:

1. Round-trip: ingest a chain in a known bbox, dump, assert the fixture
   schema + row count + ordering invariant.
2. Empty bbox: dump returns an empty payload with the right shape.
3. Determinism: dump twice, payloads are byte-identical.
4. Ordering: rows are sorted by edge_key (heat_edges) and
   (tile_key, osm_way_id, segment_idx) (osm_road_edges).

We ingest into an off-grid Iceland bbox to avoid colliding with prod or
other tests' data.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text as sa_text

from app.cli.dump_heatmap_fixture import FIXTURE_VERSION, dump_fixture
from app.db.session import SessionLocal
from app.services.ingest import _update_heat_edges

# Bbox distinct from `test_discover_test_corridor` (further north
# again so concurrent runs don't fight). 0.5° × 0.2° around 66.6N.
_SPORT = "gravel"
_BBOX = (-22.5, 66.6, -22.0, 66.8)


def _wipe_bbox() -> None:
    min_lon, min_lat, max_lon, max_lat = _BBOX
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
def _clear_bbox():
    _wipe_bbox()
    yield
    _wipe_bbox()


def _geojson(coords: list) -> str:
    return json.dumps({"type": "LineString", "coordinates": coords})


def _make_activity_id(token: str) -> str:
    h = abs(hash(token)) % (16 ** 12)
    return f"00000000-0000-0000-0000-{h:012x}"


def _ingest_chain() -> None:
    """Ingest 5 distinct users along a 5-vertex chain so user_count >= 2
    is met on each interior segment.
    """
    base_lon = -22.25
    base_lat = 66.70
    step = 0.0008
    chain = [[base_lon + i * step, base_lat + i * step * 0.5] for i in range(5)]
    for u_idx, user in enumerate(("a", "b", "c", "d", "e")):
        _update_heat_edges(
            user,
            _SPORT,
            _geojson(chain),
            activity_id=_make_activity_id(f"dump-{user}-{u_idx}"),
        )


def _insert_osm_row(tile_key: int, osm_way_id: int, segment_idx: int, lon: float, lat: float) -> None:
    """Insert an osm_road_edges row whose geometry lies inside the test bbox."""
    db = SessionLocal()
    try:
        coords = [[lon, lat], [lon + 0.0005, lat + 0.0005]]
        db.execute(
            sa_text(
                """
                INSERT INTO osm_road_edges (
                    tile_key, osm_way_id, segment_idx, surface, highway, geometry
                ) VALUES (
                    :tile_key, :osm_way_id, :segment_idx, 'asphalt', 'secondary',
                    ST_GeomFromGeoJSON(:geom)
                )
                """
            ),
            {
                "tile_key": tile_key,
                "osm_way_id": osm_way_id,
                "segment_idx": segment_idx,
                "geom": _geojson(coords),
            },
        )
        db.commit()
    finally:
        db.close()


# ── Tests ────────────────────────────────────────────────────────────────────


def test_dump_empty_bbox_returns_empty_payload():
    fixture = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=1)
    assert fixture["version"] == FIXTURE_VERSION
    assert fixture["bbox"] == list(_BBOX)
    assert fixture["sport"] == _SPORT
    assert fixture["min_user_count"] == 1
    assert fixture["heat_edges"] == []
    assert fixture["osm_road_edges"] == []
    assert fixture["osm_ways"] == []


def test_dump_includes_heat_edges_for_chain():
    _ingest_chain()
    fixture = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=2)
    # Each segment of the 5-vertex chain is ingested by 5 users → user_count >= 2.
    assert len(fixture["heat_edges"]) >= 1
    for edge in fixture["heat_edges"]:
        assert "edge_key" in edge
        assert edge["user_count"] >= 2
        assert edge["geometry"]["type"] == "LineString"
        assert len(edge["geometry"]["coordinates"]) >= 2


def test_dump_min_user_count_filters_rows_out():
    _ingest_chain()
    # Only 5 contributing users → none meet user_count >= 100.
    fixture = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=100)
    assert fixture["heat_edges"] == []


def test_dump_heat_edges_sorted_by_edge_key():
    _ingest_chain()
    fixture = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=1)
    keys = [e["edge_key"] for e in fixture["heat_edges"]]
    assert keys == sorted(keys), "heat_edges must be sorted by edge_key for determinism"


def test_dump_osm_road_edges_included_and_sorted():
    # Insert two osm rows out of order so the SQL ORDER BY proves itself.
    _insert_osm_row(900009000, 555, 1, -22.25, 66.70)
    _insert_osm_row(900009000, 555, 0, -22.25, 66.70)
    _insert_osm_row(900009000, 111, 0, -22.25, 66.70)
    fixture = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=1)
    osm_rows = fixture["osm_road_edges"]
    assert len(osm_rows) == 3
    # Sorted by (tile_key, osm_way_id, segment_idx)
    sort_keys = [(r["tile_key"], r["osm_way_id"], r["segment_idx"]) for r in osm_rows]
    assert sort_keys == sorted(sort_keys), (
        "osm_road_edges must be sorted by (tile_key, osm_way_id, segment_idx)"
    )


def test_dump_is_deterministic():
    _ingest_chain()
    _insert_osm_row(900009000, 999, 0, -22.25, 66.70)
    a = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=1)
    b = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=1)
    # `dataclasses.asdict` returns plain dicts; identical inputs → identical JSON.
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_dump_uses_sport_partition():
    """Heat_edges of a different sport in the same bbox must NOT leak
    into a dump scoped to the test sport.
    """
    _ingest_chain()
    # Same chain, different sport (road) — also under same bbox.
    base_lon = -22.25
    base_lat = 66.70
    step = 0.0008
    chain = [[base_lon + i * step, base_lat + i * step * 0.5] for i in range(5)]
    for u_idx, user in enumerate(("a", "b", "c")):
        _update_heat_edges(
            user,
            "road",
            _geojson(chain),
            activity_id=_make_activity_id(f"road-{user}-{u_idx}"),
        )

    fixture = dump_fixture(bbox=_BBOX, sport=_SPORT, min_user_count=1)
    # All rows in the gravel dump must be gravel — partition filter holds.
    # We can't check `sport` field per-row (we don't dump sport per row;
    # it's at the top level), but we can check the dump's heat_edges
    # count didn't blow up by 2x.
    fixture_road = dump_fixture(bbox=_BBOX, sport="road", min_user_count=1)
    # Both should have heat_edges of comparable count (one chain each).
    assert len(fixture["heat_edges"]) > 0
    assert len(fixture_road["heat_edges"]) > 0
    # Distinct edge_keys per sport (the sport is baked into the edge_key
    # via `_edge_key` in services/ingest.py).
    gravel_keys = {e["edge_key"] for e in fixture["heat_edges"]}
    road_keys = {e["edge_key"] for e in fixture_road["heat_edges"]}
    assert gravel_keys.isdisjoint(road_keys), (
        f"edge_keys must not overlap across sport partitions; "
        f"gravel={gravel_keys & road_keys}"
    )
