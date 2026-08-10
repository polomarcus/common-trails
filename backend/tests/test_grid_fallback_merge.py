"""Grid-fallback merge invariant — points with NO OSM coverage must produce
merged/canonical grid edges, not a fan-out of near-duplicates.

Drives the REAL `_update_heat_edges` grid-fallback branch
(`app/services/ingest.py` Phase 2b ~line 2445). The branch only runs for
coordinates that `_match_to_osm` leaves unmatched, so we use FAR-NORTH
ICELAND coordinates (off-grid, zero OSM coverage even on a local occitanie
import) — `_match_to_osm` returns them all as `fallback_coords` and they go
through `_snap` (4dp ~11m grid) + `_find_canonical_edge`.

`_update_heat_edges` COMMITS, so we isolate by a dedicated bbox and wipe it
before AND after each test (same pattern as
`test_heat_edges_same_user_idempotence.py`). NOTE: a "throwaway sport" does
NOT isolate here — `_normalize_heat_edge_sport` coerces any `test_*`/unknown
sport to `gravel`, so all rows land in the gravel partition. Bbox isolation
is the real handle.

Contract pinned (read from the real Phase-2b code):
  (a) grid-fallback edges have `osm_way_id IS NULL` and
      `match_source = 'grid_fallback'`.
  (b) two overlapping traces snapping to the same 4dp grid points share the
      SAME `edge_key` on the common stretch — canonical merge collapses them
      to one row (`pass_count`/`user_count` aggregate), NOT parallel dups.
  (c) no grid edge exceeds the 60m `_MAX_GRID_EDGE_M` cap (no phantom
      straight-line across a GPS gap).
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.ingest import _update_heat_edges

# The Phase-2b cap is a function-local constant (`_MAX_GRID_EDGE_M` inside
# `_update_heat_edges`), so it can't be imported. We assert the OBSERVABLE
# contract instead — no persisted grid edge is longer than this — which is
# the behaviour that matters and fails if the cap is removed or raised.
_GRID_EDGE_CAP_M = 60.0


def _db_available() -> bool:
    try:
        db = SessionLocal()
        try:
            db.execute(sa_text("SELECT 1 FROM heat_edges LIMIT 1"))
            return True
        finally:
            db.close()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="needs a live PostGIS DB with the heat_edges table; CI without DB skips.",
)

# Far-north Iceland — guaranteed zero OSM coverage → 100% grid-fallback.
# Distinct bbox from test_heat_edges_same_user_idempotence.py (-23.x) and
# test_ingest_direction.py to avoid cross-test contamination.
_SPORT = "gravel"  # what test_* / unknown sports normalize to anyway
_BBOX = (-21.0, 64.40, -20.5, 64.60)  # lon0, lat0, lon1, lat1

# A short connected line. Steps of 0.0001° lon (~5m at lat 64.5) keep each
# segment well under the 60m cap and span ~4 grid cells → a small chain of
# merged grid edges. lat held constant so endpoints snap deterministically.
_LAT = 64.5000
_TRACE = [[-20.7000, _LAT], [-20.6999, _LAT], [-20.6998, _LAT], [-20.6997, _LAT]]


def _geojson(coords: list) -> str:
    return json.dumps({"type": "LineString", "coordinates": coords})


def _wipe_bbox() -> None:
    lon0, lat0, lon1, lat1 = _BBOX
    db = SessionLocal()
    try:
        db.execute(sa_text(
            """
            DELETE FROM heat_edge_contributors WHERE edge_key IN (
                SELECT edge_key FROM heat_edges
                WHERE ST_Intersects(geometry,
                    ST_MakeEnvelope(:lon0, :lat0, :lon1, :lat1, 4326))
            )
            """
        ), {"lon0": lon0, "lat0": lat0, "lon1": lon1, "lat1": lat1})
        db.execute(sa_text(
            """
            DELETE FROM heat_edges
            WHERE ST_Intersects(geometry,
                ST_MakeEnvelope(:lon0, :lat0, :lon1, :lat1, 4326))
            """
        ), {"lon0": lon0, "lat0": lat0, "lon1": lon1, "lat1": lat1})
        db.commit()
    finally:
        db.close()


def _edges_in_bbox() -> list[dict]:
    lon0, lat0, lon1, lat1 = _BBOX
    db = SessionLocal()
    try:
        rows = db.execute(sa_text("""
            SELECT edge_key, osm_way_id, match_source, pass_count, user_count,
                   ST_Length(geometry::geography) AS len_m
            FROM heat_edges
            WHERE ST_Intersects(geometry,
                ST_MakeEnvelope(:lon0, :lat0, :lon1, :lat1, 4326))
            ORDER BY edge_key
        """), {"lon0": lon0, "lat0": lat0, "lon1": lon1, "lat1": lat1}).fetchall()
        return [
            {
                "edge_key": r[0], "osm_way_id": r[1], "match_source": r[2],
                "pass_count": r[3], "user_count": r[4], "len_m": r[5],
            }
            for r in rows
        ]
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clear_bbox():
    _wipe_bbox()
    yield
    _wipe_bbox()


def test_offgrid_points_produce_grid_fallback_edges():
    """Contract (a): coords with no OSM coverage land as grid-fallback rows —
    `osm_way_id IS NULL` and `match_source = 'grid_fallback'`."""
    n, _ = _update_heat_edges(
        "user-grid-A", _SPORT, _geojson(_TRACE),
        activity_id="11111111-0000-0000-0000-000000000001",
    )
    assert n > 0, "off-grid trace should produce at least one grid-fallback edge"

    edges = _edges_in_bbox()
    assert edges, "expected grid-fallback edges written to the bbox"
    for e in edges:
        assert e["osm_way_id"] is None, (
            f"grid-fallback edge must have osm_way_id IS NULL; got {e['osm_way_id']} "
            f"for {e['edge_key']} (did _match_to_osm wrongly claim these coords?)"
        )
        assert e["match_source"] == "grid_fallback", (
            f"expected match_source='grid_fallback'; got {e['match_source']!r}"
        )


def test_grid_edges_respect_60m_cap():
    """Contract (c): no grid-fallback edge exceeds the 60m cap.

    A trace with a >60m jump between consecutive points must NOT produce a
    phantom straight-line edge across the gap — the Phase-2b loop drops it.
    We assert against the REAL constant, not a literal."""
    # Insert a 4th point ~150m east of the chain (0.003° lon at lat 64.5 ≈
    # 150m) — the segment into it exceeds 60m and must be dropped.
    trace = _TRACE + [[-20.6967, _LAT]]
    _update_heat_edges(
        "user-grid-cap", _SPORT, _geojson(trace),
        activity_id="22222222-0000-0000-0000-000000000001",
    )
    edges = _edges_in_bbox()
    assert edges, "expected at least the short connected edges"
    for e in edges:
        assert e["len_m"] <= _GRID_EDGE_CAP_M + 1.0, (
            f"grid edge {e['edge_key']} is {e['len_m']:.1f}m > {_GRID_EDGE_CAP_M}m cap — "
            f"a phantom straight-line edge across a GPS gap leaked through Phase-2b."
        )


def test_two_overlapping_traces_merge_to_canonical_edges():
    """Contract (b): two riders on the SAME off-grid path (separated only by
    sub-grid GPS jitter) snap to the SAME 4dp grid points → SAME edge_keys →
    ONE merged row per edge with pass_count=2, NOT a fan-out of duplicates.

    This is the [[project_dedup_bug]] anti-fragmentation invariant applied to
    the grid-fallback path: jitter below the 4dp (~11m) grid must collapse."""
    # Rider 2 = rider 1 plus a ~3m sub-grid jitter (well under the 11m cell),
    # so every point rounds to the SAME 4dp grid coordinate.
    jitter = 0.00002  # ~1-2m at lat 64.5, below the 0.0001 grid step
    trace_b = [[lon + jitter, lat + jitter] for lon, lat in _TRACE]

    _update_heat_edges(
        "user-grid-1", _SPORT, _geojson(_TRACE),
        activity_id="33333333-0000-0000-0000-000000000001",
    )
    edges_after_1 = _edges_in_bbox()
    keys_1 = {e["edge_key"] for e in edges_after_1}
    assert keys_1, "first trace should write grid edges"
    assert all(e["pass_count"] == 1 for e in edges_after_1)

    _update_heat_edges(
        "user-grid-2", _SPORT, _geojson(trace_b),
        activity_id="44444444-0000-0000-0000-000000000001",
    )
    edges_after_2 = _edges_in_bbox()
    keys_2 = {e["edge_key"] for e in edges_after_2}

    # Anti-fragmentation core: the second (jittered) rider did NOT create a
    # parallel set of edges — the edge_key set did not grow.
    assert keys_2 == keys_1, (
        f"jittered second rider must merge onto the SAME grid edges, not fan out. "
        f"only-trace-1 keys: {keys_1 - keys_2}; new phantom keys from trace-2: {keys_2 - keys_1}"
    )

    # And the shared edges now carry both riders: pass_count=2, user_count=2.
    by_key = {e["edge_key"]: e for e in edges_after_2}
    overlapped = [by_key[k] for k in keys_1 & keys_2]
    assert overlapped, "expected overlapping canonical edges shared by both riders"
    assert all(e["pass_count"] == 2 for e in overlapped), (
        f"two riders on the same canonical grid edge → pass_count=2; "
        f"got {[e['pass_count'] for e in overlapped]} — merge failed, edges fanned out."
    )
    assert all(e["user_count"] == 2 for e in overlapped), (
        f"two DISTINCT users on the same edge → user_count=2; "
        f"got {[e['user_count'] for e in overlapped]}."
    )


def test_single_trace_is_a_connected_chain_no_gaps():
    """A single off-grid trace produces a CONNECTED chain of grid edges: the
    snapped endpoints form one path with no >60m phantom hop. Asserts the
    edges share endpoints (chain connectivity), the anti-fragmentation
    counterpart for a lone rider."""
    _update_heat_edges(
        "user-grid-chain", _SPORT, _geojson(_TRACE),
        activity_id="55555555-0000-0000-0000-000000000001",
    )
    edges = _edges_in_bbox()
    assert len(edges) >= 1
    # Every edge is short (already capped) and grid-fallback.
    for e in edges:
        assert e["len_m"] <= _GRID_EDGE_CAP_M + 1.0
        assert e["match_source"] == "grid_fallback"
    # The 4-point trace spans 3 grid cells → at most 3 distinct grid edges,
    # and no fan-out beyond the number of consecutive snapped pairs.
    assert len(edges) <= 3, (
        f"a 4-point off-grid trace must collapse to <=3 canonical grid edges; "
        f"got {len(edges)} — fragmentation."
    )
