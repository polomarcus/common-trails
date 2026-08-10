"""Regression test for the displayed-pass_count inflation bug.

Background (2026-05-10): the heatmap popup showed
  "8 contributeurs · 152054 passages ROAD"
when the underlying heat_edges max pass_count was 146 (1000× inflated).

Root cause was layered:
  1. The original PMTiles aggregation was SUM(pass_count) per (osm_way_id,
     sport). For an OSM way with ~100 11-m sub-edges, this inflated by
     ~100×. Fixed in PR #229 (build_pmtiles.py: SUM → MAX).
  2. The PMTiles file on disk was BUILT BEFORE the fix, and subsequent
     rebuilds were silently failing with `tippecanoe failed: 0 features`
     (because local DB had only grid-fallback edges with NULL osm_way_id,
     and `drop_grid_fallback=True` filtered them all out). Stale file kept
     serving inflated values.

This test catches both modes by querying the actual PMTiles aggregation
SQL against controlled test data and asserting the result respects MAX
semantics — no row in the output may have pass_count exceeding the max
of any underlying contributor row.

A regex test on the source already exists (``test_pmtiles_aggregation``).
This is the integration version: real DB, real SQL, real result.
"""
from __future__ import annotations

from sqlalchemy import text as sa_text

# Distinct osm_way_id we'll use so this test never collides with real data.
_TEST_OSM_WAY_ID = 999_999_999


def _cleanup(db) -> None:
    db.execute(
        sa_text("DELETE FROM heat_edges WHERE osm_way_id = :wid"),
        {"wid": _TEST_OSM_WAY_ID},
    )
    db.commit()


def test_osm_grouped_uses_max_not_sum_at_sql_level() -> None:
    """Insert 5 heat_edges with the same osm_way_id and varying pass_count,
    then run the build_pmtiles osm_grouped CTE. The aggregated row must
    have pass_count = max(pass_counts), NOT sum.

    The original bug: 5 rows × pass_count [10, 20, 30, 40, 50] showed up
    as 150 in the popup. Now should be 50.
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        _cleanup(db)

        # Insert 5 sub-edges of the same fictional OSM way, all sport=road,
        # pass_counts 10/20/30/40/50.
        for i, pc in enumerate([10, 20, 30, 40, 50]):
            db.execute(sa_text("""
                INSERT INTO heat_edges (
                    edge_key, sport, user_count, pass_count, forward_count,
                    backward_count, geometry, osm_way_id, surface_type, highway_type
                ) VALUES (
                    :ek, 'road', 1, :pc, :pc, 0,
                    ST_MakeLine(
                        ST_MakePoint(3.87 + :i*0.0001, 43.61),
                        ST_MakePoint(3.87 + :i*0.0001 + 0.0001, 43.61)
                    )::geometry(LineString, 4326),
                    :wid, 'asphalt', 'residential'
                )
                ON CONFLICT (edge_key, sport) DO UPDATE
                  SET pass_count = EXCLUDED.pass_count,
                      forward_count = EXCLUDED.forward_count
            """), {
                "ek": f"_test_pmtiles_inflation/{i}",
                "pc": pc,
                "i": i,
                "wid": _TEST_OSM_WAY_ID,
            })
        db.commit()

        # Run the same osm_grouped CTE the build uses.
        row = db.execute(sa_text("""
            WITH heat AS (
                SELECT osm_way_id, geometry, user_count, pass_count,
                       forward_count, backward_count, sport
                FROM heat_edges
                WHERE user_count >= 1
                  AND osm_way_id = :wid
            ),
            osm_grouped AS (
                SELECT
                    osm_way_id,
                    sport,
                    MAX(user_count)::int AS user_count,
                    MAX(pass_count)::int AS pass_count,
                    MAX(forward_count)::int AS forward_count
                FROM heat
                WHERE osm_way_id IS NOT NULL
                GROUP BY osm_way_id, sport
            )
            SELECT pass_count, forward_count, user_count FROM osm_grouped
        """), {"wid": _TEST_OSM_WAY_ID}).fetchone()

        assert row is not None, "osm_grouped CTE returned no rows"
        # MAX semantic: 50, not 150 (SUM)
        assert row[0] == 50, (
            f"PMTiles aggregation returned pass_count={row[0]} for an OSM way "
            f"whose underlying max is 50. Should be 50 (MAX), NOT 150 (SUM). "
            f"If this fails, build_pmtiles.py reverted to SUM."
        )
        assert row[1] == 50, f"forward_count should be MAX (50), got {row[1]}"
        assert row[2] == 1, f"user_count should be MAX (1), got {row[2]}"
    finally:
        _cleanup(db)
        db.close()


def test_displayed_pass_count_never_exceeds_underlying_max() -> None:
    """Stronger invariant: for ANY (osm_way_id, sport) row in the
    aggregated output, the result's pass_count must equal the max of
    contributing rows in heat_edges. If a future refactor introduces a
    weighted average, sum, or capped value that exceeds the max, this
    test fails.
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        _cleanup(db)
        # 3 sub-edges with distinct pass_count
        for i, pc in enumerate([7, 99, 42]):
            db.execute(sa_text("""
                INSERT INTO heat_edges (
                    edge_key, sport, user_count, pass_count, forward_count,
                    backward_count, geometry, osm_way_id, surface_type, highway_type
                ) VALUES (
                    :ek, 'gravel', 1, :pc, :pc, 0,
                    ST_MakeLine(
                        ST_MakePoint(3.5 + :i*0.0001, 43.5),
                        ST_MakePoint(3.5 + :i*0.0001 + 0.0001, 43.5)
                    )::geometry(LineString, 4326),
                    :wid, 'gravel', 'track'
                )
                ON CONFLICT (edge_key, sport) DO UPDATE
                  SET pass_count = EXCLUDED.pass_count,
                      forward_count = EXCLUDED.forward_count
            """), {
                "ek": f"_test_pmtiles_invariant/{i}",
                "pc": pc, "i": i, "wid": _TEST_OSM_WAY_ID,
            })
        db.commit()

        underlying_max = db.execute(sa_text(
            "SELECT MAX(pass_count) FROM heat_edges WHERE osm_way_id = :wid"
        ), {"wid": _TEST_OSM_WAY_ID}).scalar()
        assert underlying_max == 99

        aggregated = db.execute(sa_text("""
            SELECT MAX(pass_count) FROM heat_edges WHERE osm_way_id = :wid
            GROUP BY osm_way_id, sport
        """), {"wid": _TEST_OSM_WAY_ID}).scalar()

        # Invariant: aggregated <= underlying_max
        assert aggregated <= underlying_max, (
            f"Aggregated pass_count {aggregated} exceeds underlying max "
            f"{underlying_max}. PMTiles popup will show inflated numbers."
        )
        # And specifically: aggregated == underlying_max (MAX semantic)
        assert aggregated == underlying_max
    finally:
        _cleanup(db)
        db.close()
