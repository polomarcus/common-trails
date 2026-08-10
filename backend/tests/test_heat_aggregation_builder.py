"""Real execute-and-assert tests for the SHARED by-OSM-way aggregation SQL.

WHY this file exists: the by-way aggregation is the layer where the heatmap
actually MERGES cross-rider heat (it GROUPs heat_edges BY osm_way_id and emits
the OSM way's smooth geometry via a LATERAL join). It used to be triplicated
(the deleted ``heat_edges_display`` matview, the PMTiles export, the live MVT
endpoint); since June 2026 it is ONE builder —
``app/services/heat_aggregation.py::build_heat_aggregation_sql`` — used by both
the static PMTiles build and the live MVT tile endpoint (z11+).

These tests EXECUTE the real shared SQL against a seeded slice of
``heat_edges`` and assert on the produced rows (per the project's "drive the
real handler, not a source-string grep" rule — a wrong aggregation must fail
HERE). Isolation: every seeded row uses a UNIQUE throwaway sport (routed to the
``heat_edges_default`` LIST partition) + a synthetic osm_way_id; the query is
scoped to that sport via the builder's ``extra_predicate``; a ``finally``
deletes everything. No global rebuild (fast), no pollution.

golden-marked → needs PostGIS (skips in a DB-less runner).
"""
import uuid

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.heat_aggregation import (
    BUCKET_SQL,
    HEAT_SCORE_SQL,
    build_heat_aggregation_sql,
)


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


pytestmark = pytest.mark.skipif(not _db_available(), reason="needs the PostGIS DB; CI runner without it skips.")

# A synthetic OSM way id far above real OSM ids (~1.3e9) so it can't collide.
_TEST_WAY = 9_999_000_777


def _run_aggregation_for_sport(db, sport: str) -> list[dict]:
    """Execute the REAL shared aggregation SQL scoped to one sport.

    Drives ``build_heat_aggregation_sql`` with the same options the live MVT
    endpoint (z11+) uses — K-anon floor 1 (so the seeded uc=1 edge enters the
    group), grid-fallback confirmation 2, 60 m length cap, grid-fallback kept.
    A regression in the aggregation / grouping / K-anon / geometry logic fails
    HERE (unlike a source-string grep)."""
    aggregation_cte = build_heat_aggregation_sql(
        min_uc=1,
        extra_predicate=f"he.sport = '{sport}'",
        grid_fallback_min_uc=2,
        max_grid_fallback_m=60.0,
        drop_grid_fallback=False,
    )
    rows = db.execute(sa_text(
        f"{aggregation_cte} "
        f"SELECT sport, user_count, forward_count, backward_count, "
        f"({BUCKET_SQL}) AS bucket, highway_type, "
        f"({HEAT_SCORE_SQL}) AS heat_score, ST_AsText(geometry) AS wkt "
        f"FROM combined"
    )).mappings().all()
    return [dict(r) for r in rows]


def test_aggregation_groups_by_way_kanon_and_geometry():
    """One real execution of the shared aggregation SQL covering its four
    load-bearing behaviours at once (seeded, isolated):

      A. GROUP BY osm_way_id → 3 edges on one way collapse to ONE row, with
         user_count = MAX (K-anon distinct riders) and forward/backward = MAX.
      B. The row's geometry is the OSM way's full curve (way_geometry via the
         LATERAL join), NOT a 2-point heat snap.
      C. Solo grid-fallback (osm_way_id NULL, user_count = 1) is DROPPED.
      D. Confirmed grid-fallback (osm_way_id NULL, user_count >= 2) is KEPT as
         its own row with highway_type = 'unknown'.
    """
    sport = f"_agg_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        # ── seed ──────────────────────────────────────────────────────────
        # A real OSM way with a distinctive 3-point curve as its polyline
        # (osm_ways side-table since 0056; tile_key is any BIGINT here).
        way_wkt = "LINESTRING(3.8000 43.6000, 3.8005 43.6002, 3.8010 43.6000)"
        db.execute(sa_text(
            "INSERT INTO osm_road_edges (tile_key, osm_way_id, segment_idx, geometry, "
            "highway, surface, bridge_yes, tunnel_yes) VALUES "
            "(:tk, :w, 0, ST_GeomFromText('LINESTRING(3.8000 43.6000, 3.8010 43.6000)',4326), "
            "'tertiary', 'asphalt', false, false)"
        ), {"tk": _TEST_WAY, "w": _TEST_WAY})
        db.execute(sa_text(
            "INSERT INTO osm_ways (osm_way_id, way_geometry) VALUES "
            "(:w, ST_GeomFromText(:wg,4326)) "
            "ON CONFLICT (osm_way_id) DO UPDATE SET way_geometry = EXCLUDED.way_geometry"
        ), {"w": _TEST_WAY, "wg": way_wkt})

        # Three heat_edges on that ONE way, 3 distinct user_counts → MAX=3.
        # forward MAX(2,3,1)=3, backward MAX(0,1,1)=1. Each is a short 2-point
        # snap that the aggregation must REPLACE with the way curve.
        seg = "ST_GeomFromText('LINESTRING(3.8001 43.6001, 3.8004 43.6001)',4326)"
        for i, (uc, fwd, bwd) in enumerate([(1, 2, 0), (3, 3, 1), (2, 1, 1)]):
            db.execute(sa_text(
                f"INSERT INTO heat_edges (edge_key, sport, geometry, osm_way_id, "
                f"user_count, pass_count, forward_count, backward_count, match_source) "
                f"VALUES (:k, :s, {seg}, :w, :uc, :uc, :fwd, :bwd, 'spatial')"
            ), {"k": f"{sport}-osm-{i}", "s": sport, "w": _TEST_WAY,
                "uc": uc, "fwd": fwd, "bwd": bwd})

        # Grid-fallback rows (osm_way_id NULL): one solo (dropped), one confirmed.
        db.execute(sa_text(
            f"INSERT INTO heat_edges (edge_key, sport, geometry, osm_way_id, "
            f"user_count, pass_count, forward_count, backward_count, match_source) VALUES "
            f"(:k1, :s, {seg}, NULL, 1, 1, 1, 0, 'grid_fallback'), "
            f"(:k2, :s, {seg}, NULL, 2, 2, 2, 0, 'grid_fallback')"
        ), {"k1": f"{sport}-solo", "k2": f"{sport}-conf", "s": sport})
        db.commit()

        # ── execute the REAL shared aggregation SQL ─────────────────────────
        rows = _run_aggregation_for_sport(db, sport)

        # Two rows survive: the grouped OSM way + the confirmed grid-fallback.
        assert len(rows) == 2, f"expected 2 display rows, got {len(rows)}: {rows}"
        osm = next((r for r in rows if r["highway_type"] == "tertiary"), None)
        grid = next((r for r in rows if r["highway_type"] == "unknown"), None)
        assert osm is not None, f"OSM-matched row missing: {rows}"
        assert grid is not None, f"confirmed grid-fallback row missing: {rows}"

        # A. GROUP BY + MAX(user_count) + MAX(forward/backward).
        assert osm["user_count"] == 3, f"MAX user_count should be 3, got {osm['user_count']}"
        assert osm["forward_count"] == 3, f"MAX forward should be 3, got {osm['forward_count']}"
        assert osm["backward_count"] == 1, f"MAX backward should be 1, got {osm['backward_count']}"

        # B. geometry = the OSM way's 3-point curve, not the 2-point heat snap.
        assert osm["wkt"].count(",") == 2, (
            f"OSM row geometry must be the way's multi-point curve (3 pts), "
            f"got {osm['wkt']!r} — the LATERAL way_geometry join regressed"
        )
        assert "3.8005" in osm["wkt"], "the way curve's mid-vertex must be present"

        # C. solo grid-fallback dropped (D. confirmed kept).
        assert grid["user_count"] == 2, f"confirmed grid row user_count should be 2, got {grid}"
        # (only the confirmed one is here — solo never appears → len==2 above proves the drop)

        # Bonus: highway boost makes the tertiary OSM row burn brighter than the
        # 'unknown' grid row at equal-ish user_count (heat_score quality pass).
        assert osm["heat_score"] >= grid["heat_score"]
    finally:
        clean = SessionLocal()
        try:
            clean.execute(sa_text("DELETE FROM heat_edges WHERE sport = :s"), {"s": sport})
            clean.execute(sa_text("DELETE FROM osm_road_edges WHERE osm_way_id = :w"), {"w": _TEST_WAY})
            clean.execute(sa_text("DELETE FROM osm_ways WHERE osm_way_id = :w"), {"w": _TEST_WAY})
            clean.commit()
        finally:
            clean.close()
        db.close()


def test_live_endpoint_resolves_way_geometry_not_2point():
    """Post-matview-removal guard: the LIVE MVT tile path (z11+) MUST resolve
    the OSM way's smooth multi-point ``way_geometry`` — NOT regress to the
    2-point heat-edge snap. We drive the EXACT shared builder the live
    ``_generate_tile`` z11+ branch calls (bbox predicate + the same filter
    options) and assert the produced geometry is the 3-point way curve.

    This is the central correctness claim of the refactor: dropping the
    matview did not cost the live endpoint its smooth geometry.
    """
    sport = f"_live_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        way_wkt = "LINESTRING(3.8000 43.6000, 3.8005 43.6002, 3.8010 43.6000)"
        db.execute(sa_text(
            "INSERT INTO osm_road_edges (tile_key, osm_way_id, segment_idx, geometry, "
            "highway, surface, bridge_yes, tunnel_yes) VALUES "
            "(:tk, :w, 0, ST_GeomFromText('LINESTRING(3.8000 43.6000, 3.8010 43.6000)',4326), "
            "'tertiary', 'asphalt', false, false)"
        ), {"tk": _TEST_WAY, "w": _TEST_WAY})
        db.execute(sa_text(
            "INSERT INTO osm_ways (osm_way_id, way_geometry) VALUES "
            "(:w, ST_GeomFromText(:wg,4326)) "
            "ON CONFLICT (osm_way_id) DO UPDATE SET way_geometry = EXCLUDED.way_geometry"
        ), {"w": _TEST_WAY, "wg": way_wkt})
        seg = "ST_GeomFromText('LINESTRING(3.8001 43.6001, 3.8004 43.6001)',4326)"
        db.execute(sa_text(
            f"INSERT INTO heat_edges (edge_key, sport, geometry, osm_way_id, "
            f"user_count, pass_count, forward_count, backward_count, match_source) "
            f"VALUES (:k, :s, {seg}, :w, 3, 3, 3, 1, 'spatial')"
        ), {"k": f"{sport}-osm", "s": sport, "w": _TEST_WAY})
        db.commit()

        # Build EXACTLY as heatmap._generate_tile (z11+) does: bbox predicate
        # + min_uc 2 (K-anon prod default) + 60 m cap + grid kept.
        aggregation_cte = build_heat_aggregation_sql(
            min_uc=2,
            bbox_predicate=(
                "he.geometry && ST_MakeEnvelope("
                ":lon_min, :lat_min, :lon_max, :lat_max, 4326)"
            ),
            extra_predicate=f"he.sport = '{sport}'",
            grid_fallback_min_uc=2,
            max_grid_fallback_m=60.0,
            drop_grid_fallback=False,
        )
        wkt = db.execute(sa_text(
            f"{aggregation_cte} SELECT ST_AsText(geometry) FROM combined"
        ), {"lon_min": 3.79, "lat_min": 43.59,
            "lon_max": 3.81, "lat_max": 43.61}).scalar()

        assert wkt is not None, "live aggregation produced no row for the seeded way"
        assert wkt.count(",") == 2, (
            f"live endpoint geometry must be the way's 3-point curve, got {wkt!r} "
            f"— a 2-point result means the way_geometry LATERAL join regressed "
            f"(the matview-removal failure mode the brief warned about)"
        )
        assert "3.8005" in wkt, "the way curve's mid-vertex must be present"
    finally:
        clean = SessionLocal()
        try:
            clean.execute(sa_text("DELETE FROM heat_edges WHERE sport = :s"), {"s": sport})
            clean.execute(sa_text("DELETE FROM osm_road_edges WHERE osm_way_id = :w"), {"w": _TEST_WAY})
            clean.execute(sa_text("DELETE FROM osm_ways WHERE osm_way_id = :w"), {"w": _TEST_WAY})
            clean.commit()
        finally:
            clean.close()
        db.close()
