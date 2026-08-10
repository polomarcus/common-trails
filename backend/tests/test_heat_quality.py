"""Heat-edge disconnection metrics — pin the SQL and the alert math.

The metric definition lives in `app.services.heat_quality`. These tests:

1. Insert synthetic edges with a known disconnection pattern.
2. Run `compute_disconnection_metrics` against that data.
3. Pin counts AND ratios so a future refactor of the SQL surfaces here.

We also test the alert thresholds — they're declared constants and a
PR that loosens them is a privacy/quality regression that needs an
explicit owner sign-off, so the test fails CI if anyone bumps them.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text as sa_text

from app.services.heat_quality import (
    ALERT_GRID_FALLBACK_RATIO,
    ALERT_ISOLATED_RATIO,
    MONITORED_REGIONS,
    DisconnectionMetrics,
    compute_disconnection_metrics,
)

# Iceland-ish bbox far from any real data so test edges are isolated.
_TEST_BBOX = (-18.5, 65.5, -17.5, 66.0)
_TEST_PREFIX = "_heatq"


@pytest.fixture
def db_session() -> Iterator:
    from app.db.session import SessionLocal
    db = SessionLocal()
    # Wipe any leftover test edges
    db.execute(sa_text(f"""
        DELETE FROM heat_edges
        WHERE edge_key LIKE '{_TEST_PREFIX}%'
           OR ST_Within(geometry, ST_MakeEnvelope(:lon0, :lat0, :lon1, :lat1, 4326))
    """), {"lon0": _TEST_BBOX[0], "lat0": _TEST_BBOX[1],
           "lon1": _TEST_BBOX[2], "lat1": _TEST_BBOX[3]})
    db.commit()
    try:
        yield db
    finally:
        db.execute(sa_text(f"""
            DELETE FROM heat_edges
            WHERE edge_key LIKE '{_TEST_PREFIX}%'
               OR ST_Within(geometry, ST_MakeEnvelope(:lon0, :lat0, :lon1, :lat1, 4326))
        """), {"lon0": _TEST_BBOX[0], "lat0": _TEST_BBOX[1],
               "lon1": _TEST_BBOX[2], "lat1": _TEST_BBOX[3]})
        db.commit()
        db.close()


def _insert_edge(
    db,
    edge_key: str,
    lon1: float, lat1: float, lon2: float, lat2: float,
    osm_way_id: int | None = None,
    sport: str = "gravel",
    user_count: int = 1,
) -> None:
    db.execute(sa_text("""
        INSERT INTO heat_edges (
            edge_key, sport, user_count, pass_count,
            forward_count, backward_count, geometry,
            osm_way_id, surface_type, highway_type
        ) VALUES (
            :ek, :sport, :uc, :uc, :uc, 0,
            ST_MakeLine(ST_MakePoint(:lon1, :lat1), ST_MakePoint(:lon2, :lat2))::geometry(LineString, 4326),
            :osm, 'asphalt', 'residential'
        )
        ON CONFLICT (edge_key, sport) DO NOTHING
    """), {"ek": edge_key, "sport": sport, "uc": user_count,
           "lon1": lon1, "lat1": lat1, "lon2": lon2, "lat2": lat2,
           "osm": osm_way_id})
    db.commit()


# ── Alert thresholds — guard against accidental loosening ──────────────

def test_alert_thresholds_are_strict_enough() -> None:
    """The default thresholds must stay strict — a PR that loosens
    them is a quality regression that needs explicit approval. If you
    legitimately need to relax these, update this test in the same PR
    so the change is visible in review."""
    assert ALERT_GRID_FALLBACK_RATIO <= 0.20, (
        f"ALERT_GRID_FALLBACK_RATIO loosened to {ALERT_GRID_FALLBACK_RATIO} "
        f"— quality regression risk. Approve explicitly."
    )
    assert ALERT_ISOLATED_RATIO <= 0.05, (
        f"ALERT_ISOLATED_RATIO loosened to {ALERT_ISOLATED_RATIO} "
        f"— quality regression risk. Approve explicitly."
    )


def test_monitored_regions_cover_user_population() -> None:
    """At least one monitored region must cover Montpellier (lat 43.6, lon 3.87)
    since that's the demo user's home turf and the spaghetti report's example."""
    mtp = (3.87, 43.6)
    covered = any(
        lon_min <= mtp[0] <= lon_max and lat_min <= mtp[1] <= lat_max
        for _label, (lon_min, lat_min, lon_max, lat_max) in MONITORED_REGIONS
    )
    assert covered, (
        f"No monitored region covers Montpellier {mtp}. "
        f"MONITORED_REGIONS bboxes: {[bbox for _, bbox in MONITORED_REGIONS]}"
    )


# ── Empty bbox ─────────────────────────────────────────────────────────

def test_empty_bbox_returns_zero_metrics(db_session) -> None:
    m = compute_disconnection_metrics(db_session, _TEST_BBOX)
    assert m.edges_total == 0
    assert m.grid_fallback == 0
    assert m.dangling_endpoint == 0
    assert m.fully_isolated == 0
    # Ratios are 0.0 when total is 0 (no div by zero)
    assert m.grid_fallback_ratio == 0.0
    assert m.dangling_ratio == 0.0
    assert m.isolated_ratio == 0.0


# ── Single isolated edge ───────────────────────────────────────────────

def test_lone_edge_is_fully_isolated_and_grid_fallback(db_session) -> None:
    """One edge in the bbox, no OSM way matched → grid_fallback=1,
    fully_isolated=1, dangling=1 (both endpoints unique)."""
    _insert_edge(db_session, f"{_TEST_PREFIX}/lone",
                 lon1=-18.0, lat1=65.7, lon2=-18.001, lat2=65.701,
                 osm_way_id=None)
    m = compute_disconnection_metrics(db_session, _TEST_BBOX)
    assert m.edges_total == 1
    assert m.grid_fallback == 1
    assert m.dangling_endpoint == 1  # both endpoints unique → flagged
    assert m.fully_isolated == 1


# ── Chain of 3 edges sharing endpoints — none isolated ─────────────────

def test_chain_of_connected_edges_has_no_isolated(db_session) -> None:
    """Three edges chained: A→B, B→C, C→D. Endpoints A and D are
    dangling (only appear once); B and C appear twice. The middle
    edge B→C has both endpoints shared → not flagged; A→B has its A
    endpoint dangling; C→D has its D endpoint dangling. NONE are
    fully_isolated."""
    _insert_edge(db_session, f"{_TEST_PREFIX}/chain/0",
                 lon1=-18.0, lat1=65.70, lon2=-18.001, lat2=65.701)
    _insert_edge(db_session, f"{_TEST_PREFIX}/chain/1",
                 lon1=-18.001, lat1=65.701, lon2=-18.002, lat2=65.702)
    _insert_edge(db_session, f"{_TEST_PREFIX}/chain/2",
                 lon1=-18.002, lat1=65.702, lon2=-18.003, lat2=65.703)
    m = compute_disconnection_metrics(db_session, _TEST_BBOX)
    assert m.edges_total == 3
    assert m.grid_fallback == 3  # none have osm_way_id
    assert m.dangling_endpoint == 2, (
        f"Expected 2 edges with a dangling endpoint (the two end edges), got "
        f"{m.dangling_endpoint}"
    )
    assert m.fully_isolated == 0


# ── Sport filter ───────────────────────────────────────────────────────

def test_sport_filter_isolates_partition(db_session) -> None:
    """sport='gravel' must NOT count edges in 'road' partition."""
    _insert_edge(db_session, f"{_TEST_PREFIX}/g/0",
                 lon1=-18.0, lat1=65.70, lon2=-18.001, lat2=65.701, sport="gravel")
    _insert_edge(db_session, f"{_TEST_PREFIX}/r/0",
                 lon1=-18.0, lat1=65.70, lon2=-18.001, lat2=65.701, sport="road")

    m_gravel = compute_disconnection_metrics(db_session, _TEST_BBOX, sport="gravel")
    m_road = compute_disconnection_metrics(db_session, _TEST_BBOX, sport="road")
    m_all = compute_disconnection_metrics(db_session, _TEST_BBOX, sport=None)
    assert m_gravel.edges_total == 1
    assert m_road.edges_total == 1
    assert m_all.edges_total == 2


# ── osm_way_id removes the grid_fallback flag ──────────────────────────

def test_edge_with_osm_way_id_is_not_grid_fallback(db_session) -> None:
    _insert_edge(db_session, f"{_TEST_PREFIX}/osm",
                 lon1=-18.0, lat1=65.70, lon2=-18.001, lat2=65.701,
                 osm_way_id=12345)
    m = compute_disconnection_metrics(db_session, _TEST_BBOX)
    assert m.edges_total == 1
    assert m.grid_fallback == 0


# ── Mixed topology — pin every ratio at once ───────────────────────────

def test_mixed_topology_pins_all_ratios(db_session) -> None:
    """One bbox with all three failure modes at once, driving the REAL
    compute_disconnection_metrics (added when the metric SQL was
    rewritten O(n²)→O(n) in 2026-07 — same semantics, new plan shape):

    - a 3-edge dangling chain A→B→C→D, OSM-matched: the two end edges
      each have one degree-1 endpoint (dangling), the middle edge none.
    - two disjoint isolated edges, grid-fallback (osm_way_id NULL):
      both endpoints degree-1 → dangling AND fully_isolated.

    Totals: 5 edges, grid_fallback=2, dangling=4, isolated=2."""
    _insert_edge(db_session, f"{_TEST_PREFIX}/mix/chain/0",
                 lon1=-18.0, lat1=65.70, lon2=-18.001, lat2=65.701,
                 osm_way_id=999001)
    _insert_edge(db_session, f"{_TEST_PREFIX}/mix/chain/1",
                 lon1=-18.001, lat1=65.701, lon2=-18.002, lat2=65.702,
                 osm_way_id=999001)
    _insert_edge(db_session, f"{_TEST_PREFIX}/mix/chain/2",
                 lon1=-18.002, lat1=65.702, lon2=-18.003, lat2=65.703,
                 osm_way_id=999002)
    _insert_edge(db_session, f"{_TEST_PREFIX}/mix/iso/0",
                 lon1=-18.1, lat1=65.80, lon2=-18.101, lat2=65.801,
                 osm_way_id=None)
    _insert_edge(db_session, f"{_TEST_PREFIX}/mix/iso/1",
                 lon1=-18.2, lat1=65.85, lon2=-18.201, lat2=65.851,
                 osm_way_id=None)

    m = compute_disconnection_metrics(db_session, _TEST_BBOX)
    assert m.edges_total == 5
    assert m.grid_fallback == 2
    assert m.dangling_endpoint == 4  # 2 chain ends + 2 isolated edges
    assert m.fully_isolated == 2
    assert m.grid_fallback_ratio == pytest.approx(0.4)
    assert m.dangling_ratio == pytest.approx(0.8)
    assert m.isolated_ratio == pytest.approx(0.4)


def test_shared_endpoint_across_sports_still_counts_all_sports(db_session) -> None:
    """With sport=None, endpoint degree is computed across ALL sports —
    a gravel edge and a road edge meeting at the same point make that
    point degree-2 (not dangling). Pins the UNION-ALL degree semantics
    the O(n) rewrite must preserve."""
    _insert_edge(db_session, f"{_TEST_PREFIX}/x/g",
                 lon1=-18.0, lat1=65.70, lon2=-18.001, lat2=65.701, sport="gravel")
    _insert_edge(db_session, f"{_TEST_PREFIX}/x/r",
                 lon1=-18.001, lat1=65.701, lon2=-18.002, lat2=65.702, sport="road")

    m_all = compute_disconnection_metrics(db_session, _TEST_BBOX, sport=None)
    assert m_all.edges_total == 2
    assert m_all.fully_isolated == 0  # shared midpoint → neither is isolated

    # Filtered per sport, the shared point is degree-1 again.
    m_gravel = compute_disconnection_metrics(db_session, _TEST_BBOX, sport="gravel")
    assert m_gravel.edges_total == 1
    assert m_gravel.fully_isolated == 1


# ── as_log_dict shape ──────────────────────────────────────────────────

def test_as_log_dict_is_flat_and_jsonable() -> None:
    """The structured-log dict must be flat (no nested tuples) so
    Cloud Logging / Sentry consume it without further parsing."""
    import json
    m = DisconnectionMetrics(
        bbox=(3.85, 43.58, 3.95, 43.65),
        sport="gravel",
        edges_total=100,
        grid_fallback=80,
        dangling_endpoint=20,
        fully_isolated=5,
    )
    d = m.as_log_dict()
    # No bbox tuple — bbox_min_lon/lat/max_lon/lat as separate keys
    assert "bbox" not in d
    assert d["bbox_min_lon"] == 3.85
    assert d["bbox_max_lat"] == 43.65
    # Ratios pre-computed (avoid log consumer having to do math)
    assert d["grid_fallback_ratio"] == 0.8
    assert d["isolated_ratio"] == 0.05
    # Round-trips through JSON
    assert json.loads(json.dumps(d))["edges_total"] == 100
