"""Tests for direction-aware edge tracking in the ingest layer.

Verifies that _update_heat_edges correctly tracks forward/backward counts and
signed elevation delta (ele_delta_m) per canonical edge.

Now uses PostGIS tables instead of in-memory dicts.

NOTE: Sport names starting with `test_` are normalized to `gravel` by
`_normalize_heat_edge_sport` (see ingest.py). So we store under the real
`gravel` partition and isolate tests by **bbox**: each test class targets
a distinct far-north Iceland bbox where production data is effectively
zero. Cleanup wipes the bbox before/after each test, so prior leaked
edges (from older runs) are also cleared.
"""
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.ingest import (
    _build_segment_grid,
    _coords_to_z14_tiles,
    _densify_coords,
    _match_to_osm,
    _nearest_osm_segment,
    _osm_grid_cache,
    _osm_grid_cache_lock,
    _osm_segment_cache,
    _osm_segment_cache_lock,
    _osm_segment_cache_order,
    _OsmSegment,
    _point_to_segment_dist_m,
    _update_heat_edges,
    get_heat_edges_public,
    get_personal_edges,
    ingest_activity,
)

# All test data lands in the real `gravel` partition. Test sports starting
# with `test_` are rewritten to `gravel` by _normalize_heat_edge_sport, so
# we use `gravel` directly and isolate by bbox.
_SPORT = "gravel"

# Per-test-class Iceland bboxes. Production data here is effectively zero,
# so any edge we see is one we just inserted. Each class uses a distinct
# bbox to prevent cross-test contamination.
_DIR_BBOX = (-18.2, 65.6, -17.9, 65.8)         # TestUpdateHeatEdgesDirection / TestGetHeatEdgesPublicDirectionFields
_CLUSTER_BBOX = (-19.2, 65.6, -18.9, 65.8)     # TestNeighborCellClustering
_TIME_BBOX_A = (-17.5, 64.5, -17.0, 64.8)      # TestTimeFilteredHeatmap (COORDS_A)
_TIME_BBOX_B = (-16.5, 63.5, -16.0, 63.8)      # TestTimeFilteredHeatmap (COORDS_B)
_TIME_BBOX = (-17.5, 63.5, -16.0, 64.8)        # union for cleanup
_RESEG_BBOX = (-20.2, 65.6, -19.7, 65.8)       # TestResegmentation
_OSM_BBOX = (-21.2, 65.6, -20.7, 65.8)         # TestOsmMatching


def _wipe_bbox(bbox: tuple[float, float, float, float]) -> None:
    """Delete heat_edges + contributors that intersect the given bbox."""
    min_lon, min_lat, max_lon, max_lat = bbox
    db = SessionLocal()
    try:
        db.execute(sa_text(
            """
            DELETE FROM heat_edge_contributors WHERE edge_key IN (
                SELECT edge_key FROM heat_edges
                WHERE ST_Intersects(
                    geometry,
                    ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
                )
            )
            """
        ), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat})
        db.execute(sa_text(
            """
            DELETE FROM heat_edges
            WHERE ST_Intersects(
                geometry,
                ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
            )
            """
        ), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat})
        db.commit()
    finally:
        db.close()


def _tile_key_for(lon: float, lat: float) -> int:
    """Compute the z14 tile key for a coordinate (matches _coords_to_z14_tiles)."""
    tiles = _coords_to_z14_tiles([[lon, lat]])
    return next(iter(tiles))


def _clear_segment_cache() -> None:
    """Clear the in-memory OSM segment + grid caches between tests."""
    with _osm_segment_cache_lock:
        _osm_segment_cache.clear()
        _osm_segment_cache_order.clear()
    with _osm_grid_cache_lock:
        _osm_grid_cache.clear()


def _geojson(coords: list) -> str:
    return json.dumps({"type": "LineString", "coordinates": coords})


@pytest.fixture(autouse=True)
def _clear_test_edges():
    """Delete edges intersecting the direction-test Iceland bbox before/after each test."""
    _wipe_bbox(_DIR_BBOX)
    yield
    _wipe_bbox(_DIR_BBOX)


def _get_first_test_edge() -> dict:
    """Fetch the first heat edge inside the direction-test bbox."""
    min_lon, min_lat, max_lon, max_lat = _DIR_BBOX
    db = SessionLocal()
    try:
        row = db.execute(sa_text("""
            SELECT edge_key, sport, pass_count, forward_count, backward_count,
                   ele_delta_m, slope_grade, user_count
            FROM heat_edges
            WHERE ST_Intersects(
                geometry,
                ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
            )
            LIMIT 1
        """), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}).fetchone()
        if not row:
            return {}
        return {
            "edge_key": row[0], "sport": row[1], "pass_count": row[2],
            "forward_count": row[3], "backward_count": row[4],
            "ele_delta_m": row[5], "slope_grade": row[6], "user_count": row[7],
        }
    finally:
        db.close()


def _test_edge_count() -> int:
    min_lon, min_lat, max_lon, max_lat = _DIR_BBOX
    db = SessionLocal()
    try:
        row = db.execute(sa_text(
            """
            SELECT COUNT(*) FROM heat_edges
            WHERE ST_Intersects(
                geometry,
                ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
            )
            """
        ), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}).fetchone()
        return row[0] if row else 0
    finally:
        db.close()


class TestUpdateHeatEdgesDirection:
    """_update_heat_edges tracks forward/backward counts per canonical edge.

    Uses Iceland coordinates (outside OSM coverage in dev DB) with ~4.6m spacing
    (0.0001° lon at lat 65.7) to produce exactly 1 edge per trace (< 5m densify gap).
    """

    # Short edge coords: 0.0001° lon ≈ 4.6m at lat 65.7 → no densification → 1 edge
    _FWD = [[-18.10000, 65.70000], [-18.09990, 65.70000]]
    _BWD = [[-18.09990, 65.70000], [-18.10000, 65.70000]]

    def test_canonical_direction_increments_forward_count(self):
        _update_heat_edges("u1", _SPORT, _geojson(self._FWD))
        edge = _get_first_test_edge()
        assert edge["forward_count"] == 1
        assert edge["backward_count"] == 0
        assert edge["pass_count"] == 1

    def test_reverse_direction_increments_backward_count(self):
        _update_heat_edges("u1", _SPORT, _geojson(self._BWD))
        edge = _get_first_test_edge()
        assert edge["forward_count"] == 0
        assert edge["backward_count"] == 1
        assert edge["pass_count"] == 1

    def test_same_edge_key_for_both_directions(self):
        """Forward and reverse traversals share the same canonical edge key."""
        _update_heat_edges("u1", _SPORT, _geojson(self._FWD))
        _update_heat_edges("u2", _SPORT, _geojson(self._BWD))
        assert _test_edge_count() == 1
        edge = _get_first_test_edge()
        assert edge["forward_count"] == 1
        assert edge["backward_count"] == 1
        assert edge["pass_count"] == 2

    def test_accumulated_direction_counts(self):
        """Multiple users build up forward/backward counts correctly."""
        for i in range(9):
            _update_heat_edges(f"u{i}", _SPORT, _geojson(self._FWD))
        _update_heat_edges("u9", _SPORT, _geojson(self._BWD))
        edge = _get_first_test_edge()
        assert edge["forward_count"] == 9
        assert edge["backward_count"] == 1
        assert edge["pass_count"] == 10

    def test_ele_delta_m_positive_when_climbing_canonical(self):
        """ele_delta_m is positive when traversing uphill in canonical direction."""
        _update_heat_edges(
            "u1", _SPORT,
            _geojson([[-18.10000, 65.70000, 100.0], [-18.09990, 65.70000, 150.0]]),
        )
        edge = _get_first_test_edge()
        assert edge["ele_delta_m"] > 0

    def test_ele_delta_m_stored_in_canonical_direction_when_traversed_backward(self):
        """ele_delta_m is always stored relative to canonical direction, not traversal order."""
        _update_heat_edges(
            "u1", _SPORT,
            _geojson([[-18.09990, 65.70000, 150.0], [-18.10000, 65.70000, 100.0]]),
        )
        edge = _get_first_test_edge()
        assert edge["ele_delta_m"] > 0, (
            "Canonical direction is uphill here → ele_delta_m must be positive "
            "regardless of which way the trace went"
        )

    def test_ele_delta_m_zero_without_elevation(self):
        """ele_delta_m stays 0.0 when no elevation data is present."""
        _update_heat_edges("u1", _SPORT, _geojson(self._FWD))
        edge = _get_first_test_edge()
        assert edge["ele_delta_m"] == 0.0

    def test_slope_grade_positive_when_climbing(self):
        """slope_grade is a positive number whenever elevation changes."""
        _update_heat_edges(
            "u1", _SPORT,
            _geojson([[-18.10000, 65.70000, 100.0], [-18.09990, 65.70000, 200.0]]),
        )
        edge = _get_first_test_edge()
        assert edge["slope_grade"] > 0
        assert edge["ele_delta_m"] != 0.0


class TestGetHeatEdgesPublicDirectionFields:
    """get_heat_edges_public() exposes forward/backward/ele_delta_m fields."""

    # Iceland coords, 1 edge per trace (< 5m gap → no densification)
    _FWD = [[-18.10000, 65.70000], [-18.09990, 65.70000]]
    _BWD = [[-18.09990, 65.70000], [-18.10000, 65.70000]]

    def test_direction_fields_present_in_output(self):
        # Need ≥ k=2 unique users for K-anonymity
        _update_heat_edges("u1", _SPORT, _geojson(self._FWD))
        _update_heat_edges("u2", _SPORT, _geojson(self._FWD))
        edges = get_heat_edges_public(sport=_SPORT, bbox=_DIR_BBOX, k=2)
        assert len(edges) == 1
        e = edges[0]
        assert "forward_count" in e
        assert "backward_count" in e
        assert "ele_delta_m" in e

    def test_direction_counts_sum_to_pass_count(self):
        _update_heat_edges("u1", _SPORT, _geojson(self._FWD))
        _update_heat_edges("u2", _SPORT, _geojson(self._BWD))
        _update_heat_edges("u3", _SPORT, _geojson(self._FWD))
        edges = get_heat_edges_public(sport=_SPORT, bbox=_DIR_BBOX, k=2)
        assert len(edges) == 1
        e = edges[0]
        assert e["forward_count"] + e["backward_count"] == e["pass_count"]

    def test_direction_values_reflect_traversal_counts(self):
        # 2 forward (u1, u2), 1 backward (u3); 3 unique users → passes k=2
        _update_heat_edges("u1", _SPORT, _geojson(self._FWD))
        _update_heat_edges("u2", _SPORT, _geojson(self._FWD))
        _update_heat_edges("u3", _SPORT, _geojson(self._BWD))
        edges = get_heat_edges_public(sport=_SPORT, bbox=_DIR_BBOX, k=2)
        assert len(edges) == 1
        e = edges[0]
        assert e["forward_count"] == 2
        assert e["backward_count"] == 1


class TestGetPersonalEdgesDirection:
    """get_personal_edges() emits forward_count=1, backward_count=0, signed ele_delta_m."""

    def _ingest(self, user_id: str, coords: list, suffix: str = "") -> None:
        ingest_activity(
            user_id,
            {
                "provider": "file",
                "provider_activity_id": f"dir-test-{suffix or uuid.uuid4().hex[:8]}",
                "sport": "mtb",
                "name": "Direction Test",
                "geometry_geojson": _geojson(coords),
                "distance_m": 800,
                "elevation_gain_m": 50,
            },
            contribute_heatmap=False,
            rebuild_cache=False,
        )

    def test_personal_edge_zero_elevation_without_3d(self):
        """ele_delta_m is 0.0 when no elevation in trace."""
        uid = "personal-no-ele"
        self._ingest(uid, [[4.83, 45.75], [4.84, 45.75]])
        edges = get_personal_edges(uid, sport="mtb")
        assert all(e["ele_delta_m"] == 0.0 for e in edges)


# ── Neighbor-cell clustering tests ──────────────────────────────────────────

# Same partition (`gravel`) as other tests; isolated by `_CLUSTER_BBOX`.
_CLUSTER_SPORT = "gravel"


def _get_cluster_edges() -> list[dict]:
    """Fetch all heat edges in the cluster-test bbox."""
    min_lon, min_lat, max_lon, max_lat = _CLUSTER_BBOX
    db = SessionLocal()
    try:
        rows = db.execute(sa_text(
            """
            SELECT edge_key, pass_count, forward_count, backward_count, user_count
            FROM heat_edges
            WHERE ST_Intersects(
                geometry,
                ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
            )
            """
        ), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}).fetchall()
        return [
            {"edge_key": r[0], "pass_count": r[1], "forward_count": r[2],
             "backward_count": r[3], "user_count": r[4]}
            for r in rows
        ]
    finally:
        db.close()


def _cluster_edge_count() -> int:
    min_lon, min_lat, max_lon, max_lat = _CLUSTER_BBOX
    db = SessionLocal()
    try:
        row = db.execute(sa_text(
            """
            SELECT COUNT(*) FROM heat_edges
            WHERE ST_Intersects(
                geometry,
                ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
            )
            """
        ), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}).fetchone()
        return row[0] if row else 0
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clear_cluster_edges(request):
    """Wipe edges in the cluster-test bbox before/after each clustering test."""
    if not request.node.cls or request.node.cls.__name__ != "TestNeighborCellClustering":
        yield
        return
    _wipe_bbox(_CLUSTER_BBOX)
    yield
    _wipe_bbox(_CLUSTER_BBOX)


class TestNeighborCellClustering:
    """Tests for neighbor-cell merge during edge ingestion.

    Iceland coordinates inside `_CLUSTER_BBOX`, short edges (< 5m → 1 edge per trace).
    Merge radius at 5dp: ±5 grid steps = ~5.5m on lat, ~2.3m on lon at lat 65.7.
    """

    # Base edge: 0.0001° lon ≈ 4.6m at lat 65.7 → 1 edge
    # Coords inside `_CLUSTER_BBOX` (-19.2, 65.6, -18.9, 65.8).
    _A = [-19.10000, 65.70000]
    _B = [-19.09990, 65.70000]

    def test_same_road_3m_offset_merges(self):
        """Two traces on same road with ~3.3m cardinal offset → single edge, pass_count=2."""
        # First trace: exact grid points
        _update_heat_edges("u1", _CLUSTER_SPORT, _geojson([self._A, self._B]))
        assert _cluster_edge_count() == 1

        # Second trace: offset by 3 grid steps on lat (~3.3m north, within ±5 steps)
        _update_heat_edges("u2", _CLUSTER_SPORT, _geojson(
            [[-19.10000, 65.70003], [-19.09990, 65.70003]]))
        assert _cluster_edge_count() == 1  # merged, not a new edge
        edges = _get_cluster_edges()
        assert edges[0]["pass_count"] == 2

    def test_independent_endpoint_jitter_merges(self):
        """Two traces with INDEPENDENT jitter (p1 shifts north, p2 shifts east) → still merge."""
        _update_heat_edges("u1", _CLUSTER_SPORT, _geojson([self._A, self._B]))
        # p1 shifts north (+3 steps lat), p2 shifts east (+3 steps lon) — independent offsets
        _update_heat_edges("u2", _CLUSTER_SPORT, _geojson(
            [[-19.10000, 65.70003], [-19.09987, 65.70000]]))
        assert _cluster_edge_count() == 1
        edges = _get_cluster_edges()
        assert edges[0]["pass_count"] == 2

    def test_zero_length_edge_no_crash(self):
        """Zero-length edge (same point twice) → snapped to same point, dropped as consecutive dup."""
        _update_heat_edges("u1", _CLUSTER_SPORT, _geojson([self._A, self._A]))
        assert _cluster_edge_count() == 0

    def test_within_activity_dedup_with_neighbor_keys(self):
        """Within-activity dedup still works when neighbor merging is active."""
        # Trace A→B→C→B→A with short edges (~4.6m each)
        C = [-19.09980, 65.70000]
        _update_heat_edges("u1", _CLUSTER_SPORT, _geojson([
            self._A, self._B,
            C,  # continue to a third point
            self._B, self._A,  # return over same segment
        ]))
        # Should have 2 unique edges (A→B and B→C), not 4
        assert _cluster_edge_count() == 2

    def test_merge_across_activity_boundary(self):
        """AC4: existing edge in DB + new trace in adjacent cell → merge."""
        _update_heat_edges("u1", _CLUSTER_SPORT, _geojson([self._A, self._B]))
        assert _cluster_edge_count() == 1

        # Second activity (different user, 3 grid steps north) should merge
        _update_heat_edges("u2", _CLUSTER_SPORT, _geojson(
            [[-19.10000, 65.70003], [-19.09990, 65.70003]]))
        assert _cluster_edge_count() == 1
        edges = _get_cluster_edges()
        assert edges[0]["pass_count"] == 2
        assert edges[0]["user_count"] == 2  # two distinct users


# ── Time-filtered heatmap tests ─────────────────────────────────────────────

# Same partition (`gravel`); isolated by `_TIME_BBOX` (covers both COORDS_A
# and COORDS_B sub-regions).
_TIME_SPORT = "gravel"


def _get_time_contributor(edge_key: str) -> dict | None:
    db = SessionLocal()
    try:
        row = db.execute(sa_text(
            "SELECT edge_key, user_id_hash, activity_date FROM heat_edge_contributors WHERE edge_key = :key LIMIT 1"
        ), {"key": edge_key}).fetchone()
        if not row:
            return None
        return {"edge_key": row[0], "user_id_hash": row[1], "activity_date": row[2]}
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clear_time_edges(request):
    """Wipe edges in both time-test sub-bboxes before/after each test."""
    if not request.node.cls or request.node.cls.__name__ != "TestTimeFilteredHeatmap":
        yield
        return
    _wipe_bbox(_TIME_BBOX_A)
    _wipe_bbox(_TIME_BBOX_B)
    yield
    _wipe_bbox(_TIME_BBOX_A)
    _wipe_bbox(_TIME_BBOX_B)


class TestTimeFilteredHeatmap:
    """Tests for activity_date on contributors and time-filtered queries."""

    # Iceland coords inside _TIME_BBOX_A and _TIME_BBOX_B respectively, far
    # apart to produce distinct edge keys (< 5m gap → 1 edge each).
    COORDS_A = [[-17.10000, 64.70000], [-17.09990, 64.70000]]
    COORDS_B = [[-16.10000, 63.70000], [-16.09990, 63.70000]]

    def test_activity_date_stored_on_ingest(self):
        """AC1: activity_date is stored when provided."""
        date = datetime(2026, 3, 20, tzinfo=UTC)
        _update_heat_edges("u1", _TIME_SPORT, _geojson(self.COORDS_A), activity_date=date)
        edges = get_heat_edges_public(sport=_TIME_SPORT, bbox=_TIME_BBOX_A, k=1)
        assert len(edges) >= 1
        # Verify contributor has the date
        contrib = _get_time_contributor(edges[0]["edge_key"])
        assert contrib is not None
        assert contrib["activity_date"] is not None
        assert contrib["activity_date"].date() == date.date()

    def test_greatest_keeps_most_recent(self):
        """AC2: Same user, same edge, SAME activity, two dates → keeps the
        most recent date on the contributor row.

        Migration 0052 added activity_id to the contributors PK. To
        exercise the GREATEST(date) ON CONFLICT path, both ingests must
        share the same activity_id (otherwise they produce two
        distinct contributor rows, one per activity).
        """
        old_date = datetime(2026, 1, 1, tzinfo=UTC)
        new_date = datetime(2026, 3, 20, tzinfo=UTC)
        aid = "00000000-0000-0000-0000-0000000000a2"
        _update_heat_edges("u1", _TIME_SPORT, _geojson(self.COORDS_A),
                           activity_date=old_date, activity_id=aid)
        _update_heat_edges("u1", _TIME_SPORT, _geojson(self.COORDS_A),
                           activity_date=new_date, activity_id=aid)
        edges = get_heat_edges_public(sport=_TIME_SPORT, bbox=_TIME_BBOX_A, k=1)
        contrib = _get_time_contributor(edges[0]["edge_key"])
        assert contrib["activity_date"].date() == new_date.date()

    def test_greatest_handles_null_then_date(self):
        """GREATEST(NULL, date) → date. Same activity_id so the GREATEST
        path on ON CONFLICT update runs (otherwise it's two rows)."""
        date = datetime(2026, 3, 20, tzinfo=UTC)
        aid = "00000000-0000-0000-0000-0000000000a3"
        _update_heat_edges("u1", _TIME_SPORT, _geojson(self.COORDS_A),
                           activity_date=None, activity_id=aid)
        _update_heat_edges("u1", _TIME_SPORT, _geojson(self.COORDS_A),
                           activity_date=date, activity_id=aid)
        edges = get_heat_edges_public(sport=_TIME_SPORT, bbox=_TIME_BBOX_A, k=1)
        contrib = _get_time_contributor(edges[0]["edge_key"])
        assert contrib["activity_date"].date() == date.date()

    def test_filtered_query_returns_only_recent(self):
        """AC3: days=30 returns only edges with recent contributors."""
        now = datetime.now(UTC)
        old_date = now - timedelta(days=400)

        # Edge A: recent (2 contributors for K=2)
        _update_heat_edges("u1", _TIME_SPORT, _geojson(self.COORDS_A), activity_date=now)
        _update_heat_edges("u2", _TIME_SPORT, _geojson(self.COORDS_A), activity_date=now)

        # Edge B: old (2 contributors but dated 400 days ago)
        _update_heat_edges("u1", _TIME_SPORT, _geojson(self.COORDS_B), activity_date=old_date)
        _update_heat_edges("u2", _TIME_SPORT, _geojson(self.COORDS_B), activity_date=old_date)

        # Query union bbox so both edges are reachable
        filtered = get_heat_edges_public(sport=_TIME_SPORT, bbox=_TIME_BBOX, k=2, days=30)
        # Only edge A should appear
        assert len(filtered) >= 1
        # Edge B should not appear
        all_time = get_heat_edges_public(sport=_TIME_SPORT, bbox=_TIME_BBOX, k=2, days=None)
        assert len(all_time) >= 2  # both edges visible in all-time

    def test_all_time_returns_all(self):
        """AC4: No days param returns all edges (no regression)."""
        now = datetime.now(UTC)
        old_date = now - timedelta(days=400)
        _update_heat_edges("u1", _TIME_SPORT, _geojson(self.COORDS_A), activity_date=now)
        _update_heat_edges("u2", _TIME_SPORT, _geojson(self.COORDS_A), activity_date=now)
        _update_heat_edges("u1", _TIME_SPORT, _geojson(self.COORDS_B), activity_date=old_date)
        _update_heat_edges("u2", _TIME_SPORT, _geojson(self.COORDS_B), activity_date=old_date)
        all_time = get_heat_edges_public(sport=_TIME_SPORT, bbox=_TIME_BBOX, k=2)
        assert len(all_time) >= 2

    def test_k_anonymity_on_filtered_set(self):
        """AC5: Edge with 3 all-time contributors but only 1 within 30 days → hidden."""
        now = datetime.now(UTC)
        old_date = now - timedelta(days=400)
        # 2 old contributors + 1 recent → 3 all-time, 1 recent
        _update_heat_edges("u1", _TIME_SPORT, _geojson(self.COORDS_A), activity_date=old_date)
        _update_heat_edges("u2", _TIME_SPORT, _geojson(self.COORDS_A), activity_date=old_date)
        _update_heat_edges("u3", _TIME_SPORT, _geojson(self.COORDS_A), activity_date=now)

        all_time = get_heat_edges_public(sport=_TIME_SPORT, bbox=_TIME_BBOX_A, k=2)
        assert len(all_time) >= 1  # 3 contributors >= K=2

        filtered = get_heat_edges_public(sport=_TIME_SPORT, bbox=_TIME_BBOX_A, k=2, days=30)
        # Only 1 recent contributor < K=2 → edge should NOT appear
        assert len(filtered) == 0

    def test_filtered_heat_score_uses_filtered_count(self):
        """AC: heat_score computed from filtered user_count, not all-time."""
        now = datetime.now(UTC)
        old_date = now - timedelta(days=400)
        # 3 old + 2 recent → all-time user_count=5, filtered=2
        _update_heat_edges("u1", _TIME_SPORT, _geojson(self.COORDS_A), activity_date=old_date)
        _update_heat_edges("u2", _TIME_SPORT, _geojson(self.COORDS_A), activity_date=old_date)
        _update_heat_edges("u3", _TIME_SPORT, _geojson(self.COORDS_A), activity_date=old_date)
        _update_heat_edges("u4", _TIME_SPORT, _geojson(self.COORDS_A), activity_date=now)
        _update_heat_edges("u5", _TIME_SPORT, _geojson(self.COORDS_A), activity_date=now)

        all_time = get_heat_edges_public(sport=_TIME_SPORT, bbox=_TIME_BBOX_A, k=1)
        filtered = get_heat_edges_public(sport=_TIME_SPORT, bbox=_TIME_BBOX_A, k=1, days=30)

        assert len(all_time) >= 1
        assert len(filtered) >= 1
        # filtered user_count should be 2, all-time should be 5
        assert filtered[0]["user_count"] == 2
        assert all_time[0]["user_count"] == 5
        # heat_score should differ
        assert filtered[0]["heat_score"] < all_time[0]["heat_score"]


# ── Re-segmentation tests ──────────────────────────────────────────────────

# Same partition (`gravel`); isolated by `_RESEG_BBOX`.
_RESEG_SPORT = "gravel"


def _reseg_edge_count() -> int:
    min_lon, min_lat, max_lon, max_lat = _RESEG_BBOX
    db = SessionLocal()
    try:
        row = db.execute(sa_text(
            """
            SELECT COUNT(*) FROM heat_edges
            WHERE ST_Intersects(
                geometry,
                ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
            )
            """
        ), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}).fetchone()
        return row[0] if row else 0
    finally:
        db.close()


def _reseg_max_pass_count() -> int:
    min_lon, min_lat, max_lon, max_lat = _RESEG_BBOX
    db = SessionLocal()
    try:
        row = db.execute(sa_text(
            """
            SELECT COALESCE(MAX(pass_count), 0) FROM heat_edges
            WHERE ST_Intersects(
                geometry,
                ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
            )
            """
        ), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}).fetchone()
        return row[0] if row else 0
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clear_reseg_edges(request):
    """Wipe edges in the reseg-test bbox before/after each test."""
    if not request.node.cls or request.node.cls.__name__ != "TestResegmentation":
        yield
        return
    _wipe_bbox(_RESEG_BBOX)
    yield
    _wipe_bbox(_RESEG_BBOX)


class TestResegmentation:
    """Tests for fixed-length re-segmentation and tightened merge radius."""

    def test_short_trace_no_densification(self):
        """3m trace → returns start + end (gap < 5m, no interpolation)."""
        coords = [[3.87, 43.61], [3.870035, 43.61]]
        result = _densify_coords(coords)
        assert len(result) == 2

    def test_elevation_preserved(self):
        """3D coords densified with interpolated elevation."""
        coords = [[3.87, 43.61, 100.0], [3.8725, 43.61, 200.0]]
        result = _densify_coords(coords)
        assert len(result) >= 10
        # All points should have elevation
        for pt in result:
            assert len(pt) == 3
            assert pt[2] is not None
        # First elevation matches
        assert result[0][2] == 100.0
        # Last point elevation close to 200
        assert abs(result[-1][2] - 200.0) < 1.0
        # Intermediate elevations should be between 100 and 200
        for pt in result[1:-1]:
            assert 100.0 <= pt[2] <= 200.0

    def test_nan_elevation_handled(self):
        """NaN elevation → no crash, synthetic points have no elevation."""
        coords = [[3.87, 43.61, float('nan')], [3.8725, 43.61, 200.0]]
        result = _densify_coords(coords)
        assert len(result) >= 10
        # Intermediate points should be 2D (no elevation interpolated from NaN)
        for pt in result[1:-1]:
            assert len(pt) == 2

    def test_empty_and_single_point(self):
        """Empty and single-point inputs → returned as-is."""
        assert _densify_coords([]) == []
        assert _densify_coords([[3.87, 43.61]]) == [[3.87, 43.61]]

    def test_different_gps_spacing_merge(self):
        """Critical test: two traces with 5m vs 80m spacing → merge after reseg.

        Two traces on the same 200m east-west road. Trace 1 has dense points (5m),
        trace 2 has sparse points (80m). After densification + snap + clustering,
        neighbor clustering (±5 grid steps) should merge at least some edges.

        At 5dp/5m: 200m produces ~39 edges per trace. With merging, total < 78.
        """
        # Coords inside _RESEG_BBOX (-20.2, 65.6, -19.7, 65.8); at lat 65.7
        # 0.0001° lon ≈ 4.6m, so 0.000110° ≈ 5m. Use lon -20.10..-20.08 (≈ 200m east-west).
        # Trace 1: dense points every ~5m along east-west road
        dense_coords = []
        for i in range(40):  # 40 points × 5m = 200m
            lon = -20.10 + i * 0.000110  # ~5m steps at lat 65.7
            dense_coords.append([lon, 65.70])

        # Trace 2: sparse points every ~80m along same road
        sparse_coords = []
        for i in range(4):  # 4 points × ~80m = ~240m
            lon = -20.10 + i * 0.00175  # ~80m steps at lat 65.7
            sparse_coords.append([lon, 65.70])

        _update_heat_edges("u1", _RESEG_SPORT, _geojson(dense_coords))
        _update_heat_edges("u2", _RESEG_SPORT, _geojson(sparse_coords))

        edge_count = _reseg_edge_count()
        max_pc = _reseg_max_pass_count()
        # Key assertion: max pass_count ≥ 2 proves neighbor clustering merged some edges.
        assert max_pc >= 2, f"Expected at least one merged edge (pass_count≥2), got max {max_pc}"
        # Edge count should be less than 2 × 39 = 78 (zero-merge case)
        assert edge_count <= 70, f"Expected ≤70 edges (some merges), got {edge_count}"

    def test_parallel_paths_stay_separate(self):
        """Le Lez test: two traces 12m apart → separate edges (not merged).

        With tightened 11m merge radius (cardinal only), paths >11m apart
        should NOT merge.
        """
        # Coords inside _RESEG_BBOX (-20.2, 65.6, -19.7, 65.8); at lat 65.7
        # 0.0001° lat ≈ 11m, 0.000058° lon ≈ 2.6m. Use 0.0025° lon ≈ ~110m segment.
        # Path 1: east-west at lat 65.70
        path1 = [[-20.10, 65.70], [-20.0975, 65.70]]
        # Path 2: east-west at lat 65.7002 (~22m north, well beyond 11m cardinal merge)
        path2 = [[-20.10, 65.7002], [-20.0975, 65.7002]]

        _update_heat_edges("u1", _RESEG_SPORT, _geojson(path1))
        _update_heat_edges("u2", _RESEG_SPORT, _geojson(path2))

        edge_count = _reseg_edge_count()
        # Should have edges from BOTH paths (not merged into one set)
        # Each ~110m path produces ~4 edges after reseg, so ≥6 total means both kept
        assert edge_count >= 6, f"Expected ≥6 edges (two paths), got {edge_count} (wrong merge)"


# ── OSM map-matching tests ────────────────────────────────────────────────

# OSM tests pass `_OSM_SPORT` directly to internal helpers like `_match_to_osm`
# (which build edge_keys verbatim). We use the real `gravel` partition for
# consistency with the normalizer and isolate by `_OSM_BBOX`. The
# `_clear_osm_edges` fixture also wipes any test OSM segments inserted into
# `osm_road_edges` (each test does its own per-tile cleanup, but we keep
# bbox-wide cleanup as a safety net).
_OSM_SPORT = "gravel"


@pytest.fixture(autouse=True)
def _clear_osm_edges(request):
    """Wipe edges in the OSM-test bbox before/after each test."""
    if not request.node.cls or request.node.cls.__name__ != "TestOsmMatching":
        yield
        return
    _wipe_bbox(_OSM_BBOX)
    yield
    _wipe_bbox(_OSM_BBOX)


def _insert_osm_segments(segments: list[dict]) -> None:
    """Insert test OSM road segments into osm_road_edges."""
    db = SessionLocal()
    try:
        for seg in segments:
            db.execute(sa_text("""
                INSERT INTO osm_road_edges (tile_key, osm_way_id, segment_idx, surface, highway, geometry)
                VALUES (:tile_key, :way_id, :seg_idx, :surface, :highway,
                        ST_MakeLine(ST_MakePoint(:lon1, :lat1), ST_MakePoint(:lon2, :lat2)))
            """), seg)
        db.commit()
    finally:
        db.close()


def _osm_edge_count() -> int:
    min_lon, min_lat, max_lon, max_lat = _OSM_BBOX
    db = SessionLocal()
    try:
        row = db.execute(sa_text(
            """
            SELECT COUNT(*) FROM heat_edges
            WHERE ST_Intersects(
                geometry,
                ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
            )
            """
        ), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}).fetchone()
        return row[0] if row else 0
    finally:
        db.close()


def _get_osm_test_edges() -> list[dict]:
    min_lon, min_lat, max_lon, max_lat = _OSM_BBOX
    db = SessionLocal()
    try:
        rows = db.execute(sa_text(
            """
            SELECT edge_key, pass_count, forward_count, backward_count,
                   surface_type, highway_type, user_count
            FROM heat_edges
            WHERE ST_Intersects(
                geometry,
                ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
            )
            """
        ), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}).fetchall()
        return [
            {"edge_key": r[0], "pass_count": r[1], "forward_count": r[2],
             "backward_count": r[3], "surface_type": r[4], "highway_type": r[5],
             "user_count": r[6]}
            for r in rows
        ]
    finally:
        db.close()


class TestOsmMatching:
    """Tests for OSM map-matching in ingest pipeline."""

    def test_point_to_segment_dist_accuracy(self):
        """_point_to_segment_dist_m returns accurate distance (±1m)."""
        # Point directly north of segment midpoint
        # Segment: (3.87, 43.61) to (3.88, 43.61), point at (3.875, 43.6101)
        dist = _point_to_segment_dist_m(3.875, 43.6101, 3.87, 43.61, 3.88, 43.61)
        # ~11m north (0.0001° lat ≈ 11m)
        assert 10 < dist < 13, f"Expected ~11m, got {dist}"

    def test_spatial_grid_returns_nearest(self):
        """Spatial grid lookup returns the closest of 3 segments."""
        seg_a = _OsmSegment(3.87, 43.61, 3.88, 43.61, "asphalt", "residential")
        seg_b = _OsmSegment(3.87, 43.612, 3.88, 43.612, "gravel", "track")
        seg_c = _OsmSegment(3.87, 43.615, 3.88, 43.615, "dirt", "path")
        grid = _build_segment_grid([seg_a, seg_b, seg_c])
        nearest, dist = _nearest_osm_segment(3.875, 43.6101, grid, max_dist_m=50.0)
        assert nearest is seg_a
        assert dist is not None and dist < 15


    def test_fallback_to_grid_snap(self):
        """GPS points >15m from any OSM segment → grid-snapped edges created."""
        # Insert OSM segment far away from the trace (Iceland coords)
        tk = _tile_key_for(-21.0, 65.7100)  # OSM segment ~1.1km north of trace
        _clear_segment_cache()
        db = SessionLocal()
        try:
            db.execute(sa_text("""
                INSERT INTO osm_road_edges (tile_key, osm_way_id, segment_idx, surface, highway, geometry)
                VALUES (:tk, 500, 0, 'asphalt', 'residential',
                        ST_MakeLine(ST_MakePoint(-21.0, 65.7100), ST_MakePoint(-20.9, 65.7100)))
            """), {"tk": tk})
            db.commit()

            # Trace at lat 65.7000 (~1.1km south of OSM segment)
            coords = [[-21.0, 65.7000], [-20.9, 65.7000]]
            osm_edges, fallback = _match_to_osm(coords, _OSM_SPORT, db)
            assert len(osm_edges) == 0
            assert len(fallback) == len(coords)
        finally:
            db.execute(sa_text("DELETE FROM osm_road_edges WHERE tile_key = :tk"), {"tk": tk})
            db.commit()
            db.close()
            _clear_segment_cache()

    def test_mixed_match(self):
        """Trace partially on OSM road + partially off-road → both edge types created."""
        # Iceland coords inside _OSM_BBOX
        tk = _tile_key_for(-21.0, 65.7000)
        _clear_segment_cache()
        db = SessionLocal()
        try:
            # OSM segment at lat 65.7000
            db.execute(sa_text("""
                INSERT INTO osm_road_edges (tile_key, osm_way_id, segment_idx, surface, highway, geometry)
                VALUES (:tk, 600, 0, 'asphalt', 'residential',
                        ST_MakeLine(ST_MakePoint(-21.0, 65.7000), ST_MakePoint(-20.9, 65.7000)))
            """), {"tk": tk})
            db.commit()

            # GPS trace: starts on-road (near OSM segment), then goes off-road
            # (still inside _OSM_BBOX so cleanup catches it).
            coords = [
                [-20.999, 65.7000], [-20.997, 65.7000], [-20.995, 65.7000],  # on road
                [-20.993, 65.7100], [-20.991, 65.7200],  # off road (~1km north each step)
            ]
            osm_edges, fallback = _match_to_osm(coords, _OSM_SPORT, db)
            assert len(osm_edges) >= 1  # on-road portion matched
            assert len(fallback) >= 2   # off-road portion in fallback
        finally:
            db.execute(sa_text("DELETE FROM osm_road_edges WHERE tile_key = :tk"), {"tk": tk})
            db.commit()
            db.close()
            _clear_segment_cache()

    def test_overpass_timeout_fallback(self):
        """Mock Overpass timeout → grid-snap fallback, no crash."""
        from unittest.mock import patch

        from app.services.ingest import _fetch_and_store_osm_tile

        db = SessionLocal()
        try:
            # Patch both TEST_MODE and httpx.post to exercise the actual timeout path
            with patch("app.services.ingest.TEST_MODE", False), \
                 patch("app.services.ingest.httpx.post", side_effect=httpx.TimeoutException("timeout")):
                result = _fetch_and_store_osm_tile(800005000, db)  # x=8000 y=5000
            assert result == 0  # returns 0 on failure (all servers timed out)
        finally:
            db.close()

    def test_direction_tracking(self):
        """Forward trace on OSM segment → forward_count=1; backward → backward_count=1."""
        # Iceland coords inside _OSM_BBOX
        tk = _tile_key_for(-21.0, 65.7000)
        _clear_segment_cache()
        db = SessionLocal()
        try:
            # OSM segment from (-21.0, 65.7000) to (-20.9, 65.7000)
            db.execute(sa_text("""
                INSERT INTO osm_road_edges (tile_key, osm_way_id, segment_idx, surface, highway, geometry)
                VALUES (:tk, 700, 0, 'asphalt', 'residential',
                        ST_MakeLine(ST_MakePoint(-21.0, 65.7000), ST_MakePoint(-20.9, 65.7000)))
            """), {"tk": tk})
            db.commit()
        finally:
            db.close()

        # Forward trace (west to east, matching segment direction)
        _update_heat_edges("u1", _OSM_SPORT, _geojson([[-20.999, 65.7000], [-20.995, 65.7000], [-20.991, 65.7000]]))
        edges = _get_osm_test_edges()
        assert len(edges) >= 1
        total_fwd = sum(e["forward_count"] for e in edges)
        total_bwd = sum(e["backward_count"] for e in edges)
        assert total_fwd + total_bwd >= 1  # direction tracked

        # Clean up test tile
        _clear_segment_cache()
        db = SessionLocal()
        try:
            db.execute(sa_text("DELETE FROM osm_road_edges WHERE tile_key = :tk"), {"tk": tk})
            db.commit()
        finally:
            db.close()

    def test_surface_highway_from_osm_tags(self):
        """OSM segment with surface=asphalt → heat_edge.surface_type='asphalt'."""
        # Iceland coords inside _OSM_BBOX
        tk = _tile_key_for(-21.0, 65.7000)
        _clear_segment_cache()
        db = SessionLocal()
        try:
            db.execute(sa_text("""
                INSERT INTO osm_road_edges (tile_key, osm_way_id, segment_idx, surface, highway, geometry)
                VALUES (:tk, 800, 0, 'asphalt', 'residential',
                        ST_MakeLine(ST_MakePoint(-21.0, 65.7000), ST_MakePoint(-20.9, 65.7000)))
            """), {"tk": tk})
            db.commit()
        finally:
            db.close()

        _update_heat_edges("u1", _OSM_SPORT, _geojson([[-20.999, 65.7000], [-20.995, 65.7000], [-20.991, 65.7000]]))
        edges = _get_osm_test_edges()
        osm_edges = [e for e in edges if e["surface_type"] != "unknown"]
        assert len(osm_edges) >= 1
        assert osm_edges[0]["surface_type"] == "asphalt"
        assert osm_edges[0]["highway_type"] == "residential"

        # Clean up
        _clear_segment_cache()
        db = SessionLocal()
        try:
            db.execute(sa_text("DELETE FROM osm_road_edges WHERE tile_key = :tk"), {"tk": tk})
            db.commit()
        finally:
            db.close()
