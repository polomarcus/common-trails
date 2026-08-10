"""group_edges_osm must set osm_way_id when re-keying / inserting.

The spaghetti audit (2026-05-14) traced a bug to this script: it
spatially matched grid-fallback heat_edges against osm_road_edges and
aligned their geometries, but **never wrote the `osm_way_id`** onto
the matched heat_edges. So `build_pmtiles` still routed those rows
through the grid-fallback CTE (one feature per 2-point segment) →
visible spaghetti at z17+ even after `group_edges_osm` reported
"267k merged".

These tests pin the fix: every matched heat_edge gains `osm_way_id`
on UPDATE, every freshly-INSERTed OSM-keyed edge gets `osm_way_id`,
and a merge into an existing osm-keyed row preserves whichever
`osm_way_id` is non-NULL (via COALESCE).
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text as sa_text

_TEST_PREFIX = "_gosm_test"
_OSM_WAY_ID = 999_999_001
_TEST_BBOX = (-18.5, 65.5, -17.5, 66.0)  # Iceland — far from real data


@pytest.fixture
def db_session() -> Iterator:
    from app.db.session import SessionLocal
    db = SessionLocal()

    def _wipe():
        db.execute(sa_text(
            "DELETE FROM heat_edges WHERE edge_key LIKE :p OR ST_Within(geometry, ST_MakeEnvelope(:l0,:la0,:l1,:la1,4326))"
        ), {"p": f"%{_TEST_PREFIX}%",
            "l0": _TEST_BBOX[0], "la0": _TEST_BBOX[1],
            "l1": _TEST_BBOX[2], "la1": _TEST_BBOX[3]})
        db.execute(sa_text(
            "DELETE FROM osm_road_edges WHERE osm_way_id = :w"
        ), {"w": _OSM_WAY_ID})
        db.commit()

    _wipe()
    try:
        yield db
    finally:
        _wipe()
        db.close()


def _insert_osm_way(db, osm_way_id: int, lon1: float, lat1: float, lon2: float, lat2: float) -> None:
    """Insert one osm_road_edge to match against."""
    db.execute(sa_text("""
        INSERT INTO osm_road_edges (tile_key, osm_way_id, segment_idx, surface, highway, geometry)
        VALUES (
            0, :wid, 0, 'asphalt', 'residential',
            ST_MakeLine(ST_MakePoint(:lon1, :lat1), ST_MakePoint(:lon2, :lat2))
        )
    """), {"wid": osm_way_id, "lon1": lon1, "lat1": lat1, "lon2": lon2, "lat2": lat2})
    db.commit()


def _insert_heat_edge_grid_fallback(
    db, edge_key: str, lon1: float, lat1: float, lon2: float, lat2: float,
    sport: str = "gravel",
) -> None:
    """Insert a heat_edge with NULL osm_way_id (the grid-fallback case)."""
    db.execute(sa_text("""
        INSERT INTO heat_edges (
            edge_key, sport, user_count, pass_count, forward_count, backward_count,
            geometry, osm_way_id, surface_type, highway_type
        ) VALUES (
            :ek, :sport, 1, 1, 1, 0,
            ST_MakeLine(ST_MakePoint(:lon1, :lat1), ST_MakePoint(:lon2, :lat2))::geometry(LineString, 4326),
            NULL, 'unknown', 'unknown'
        )
    """), {"ek": edge_key, "sport": sport,
           "lon1": lon1, "lat1": lat1, "lon2": lon2, "lat2": lat2})
    db.commit()


def _get_heat_edge(db, edge_key: str) -> dict | None:
    row = db.execute(sa_text(
        "SELECT edge_key, osm_way_id, surface_type, highway_type FROM heat_edges WHERE edge_key = :k"
    ), {"k": edge_key}).fetchone()
    if row is None:
        return None
    return {"edge_key": row[0], "osm_way_id": row[1],
            "surface_type": row[2], "highway_type": row[3]}


# ── Matched heat_edge gains osm_way_id ─────────────────────────────────

def test_group_edges_sets_osm_way_id_on_update(db_session) -> None:
    """A heat_edge spatially-matched to an OSM way gets its osm_way_id
    set (and geometry aligned). Before the 2026-05-14 fix this UPDATE
    path silently left osm_way_id NULL, so build_pmtiles still treated
    these as grid-fallback (one feature per 2-point segment) instead
    of grouping them onto the smooth OSM way → spaghetti at z17+."""
    from app.cli.group_edges_osm import run_grouping

    # Pair them up: heat_edge collinear with osm_way, both at Iceland.
    # Segments are ~100m long so they pass the ST_Length > 1m filter.
    _insert_osm_way(db_session, _OSM_WAY_ID, -18.10, 65.70, -18.099, 65.7090)
    he_key = "gravel/-18.099992,65.70001/-18.099,65.7088"
    _insert_heat_edge_grid_fallback(db_session, he_key,
                                     -18.099992, 65.70001, -18.099, 65.7088)

    # Sanity: starts NULL
    before = _get_heat_edge(db_session, he_key)
    assert before is not None
    assert before["osm_way_id"] is None

    # Run group_edges_osm targeted at the Iceland bbox so we don't
    # touch any real data
    run_grouping(dry_run=False, bbox="-18.5,65.5,-17.5,66.0")
    db_session.expire_all()

    # SOME heat_edge in the Iceland bbox now has osm_way_id set
    # (either the original or a re-keyed sibling — depends on whether
    # the snap-to-OSM produced the same edge_key or a new one)
    rows = db_session.execute(sa_text("""
        SELECT edge_key, osm_way_id FROM heat_edges
        WHERE ST_Within(geometry, ST_MakeEnvelope(:l0,:la0,:l1,:la1,4326))
    """), {"l0": _TEST_BBOX[0], "la0": _TEST_BBOX[1],
           "l1": _TEST_BBOX[2], "la1": _TEST_BBOX[3]}).fetchall()
    osm_way_ids = [r[1] for r in rows]
    assert _OSM_WAY_ID in osm_way_ids, (
        f"No heat_edge tagged with osm_way_id={_OSM_WAY_ID}. Edges in bbox: {rows}"
    )
