"""Group heat_edges onto OSM road geometry.

Post-processing step: finds heat_edges that run parallel to an OSM road
within 25m, and merges them onto the OSM geometry. Multiple parallel
edges on the same road → single edge with aggregated pass_count.

This is idempotent: can be re-run safely.

Performance (2026-05-16 rewrite):
- Cursor-paginated SELECT (chunked LIMIT N WHERE edge_key > :last_key)
  instead of one big ``.fetchall()``. The previous shape held 1.67M rows
  in Python memory at once — needed 8 GiB Cloud Run Job to avoid OOM.
  The new shape uses bounded memory regardless of dataset size.
- Per-chunk commit. A timeout that kills the job loses at most one
  chunk (default 1000 rows ≈ a few seconds of work), not the whole run.
  Cursor pagination resumes naturally because the WHERE clause drops
  already-processed rows.
- Merge path collapses 5 statements per row → 1 CTE-chain statement.
  ~5× round-trip reduction. Target throughput in-region: ~500-1000 rows/s
  (up from 119 rows/s pre-rewrite).

Usage:
    python -m app.cli.group_edges_osm [--dry-run] [--bbox 3.85,43.60,3.92,43.66] [--chunk-size N]
"""
import argparse
import logging
import time
from typing import Any

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DEFAULT_CHUNK_SIZE = 1000


def _snap(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, 5), round(lon, 5)


def _edge_key(sport: str, p1: tuple[float, float], p2: tuple[float, float]) -> str:
    a, b = sorted([p1, p2])
    return f"{sport}/{a[0]},{a[1]}/{b[0]},{b[1]}"


def _build_candidate_query(bbox_filter: str) -> str:
    return f"""
        SELECT DISTINCT ON (he.edge_key)
            he.edge_key AS old_key,
            he.sport,
            he.pass_count,
            he.forward_count,
            he.backward_count,
            he.user_count,
            he.ele_delta_m,
            he.slope_grade,
            osm.osm_way_id,
            osm.segment_idx,
            osm.surface,
            osm.highway,
            osm.ele_delta_m AS osm_ele_delta_m,
            osm.slope_grade AS osm_slope_grade,
            osm.surface_confidence AS osm_surface_confidence,
            ST_Y(ST_StartPoint(osm.geometry)) AS osm_lat1,
            ST_X(ST_StartPoint(osm.geometry)) AS osm_lon1,
            ST_Y(ST_EndPoint(osm.geometry)) AS osm_lat2,
            ST_X(ST_EndPoint(osm.geometry)) AS osm_lon2,
            ST_AsText(osm.geometry) AS osm_geom_wkt,
            ST_Distance(he.geometry::geography, osm.geometry::geography) AS dist_m
        FROM heat_edges he
        JOIN osm_road_edges osm
          ON ST_DWithin(he.geometry, osm.geometry, 0.00035)
        WHERE he.edge_key > :last_key
          -- Idempotency sentinel: osm_way_id NULL = "not yet processed".
          -- The earlier `OR surface_type = 'unknown' OR IS NULL` clauses
          -- re-matched any OSM way without a surface tag (a large fraction
          -- of OSM `track`/`path` ways have no surface), which made the
          -- merge path increment pass_count on every re-run. osm_way_id is
          -- set unconditionally by both _apply_same_key and the merge CTE
          -- so it's the durable "processed" mark.
          AND he.osm_way_id IS NULL
          {bbox_filter}
          AND ST_Length(he.geometry::geography) > 1
          AND ST_Length(osm.geometry::geography) > 1
          AND LEAST(
              abs(degrees(ST_Angle(
                  ST_StartPoint(he.geometry), ST_EndPoint(he.geometry),
                  ST_StartPoint(osm.geometry), ST_EndPoint(osm.geometry)
              ))),
              abs(360 - degrees(ST_Angle(
                  ST_StartPoint(he.geometry), ST_EndPoint(he.geometry),
                  ST_StartPoint(osm.geometry), ST_EndPoint(osm.geometry)
              ))),
              abs(180 - degrees(ST_Angle(
                  ST_StartPoint(he.geometry), ST_EndPoint(he.geometry),
                  ST_StartPoint(osm.geometry), ST_EndPoint(osm.geometry)
              )))
          ) < 30
        ORDER BY he.edge_key, ST_Distance(he.geometry, osm.geometry)
        LIMIT :chunk_size
    """


# ── Per-row operations (each is one DB round-trip) ──────────────────────

_SAME_KEY_UPDATE_SQL = sa_text("""
    UPDATE heat_edges
    SET geometry = ST_GeomFromText(:geom, 4326),
        osm_way_id = :osm_way_id,
        surface_type = COALESCE(NULLIF(:surface, 'unknown'), surface_type),
        highway_type = COALESCE(NULLIF(:highway, 'unknown'), highway_type),
        ele_delta_m = COALESCE(NULLIF(ele_delta_m, 0), :osm_ele),
        slope_grade = COALESCE(NULLIF(slope_grade, 0), :osm_slope),
        surface_confidence = COALESCE(surface_confidence, :osm_conf)
    WHERE edge_key = :key
""")

# Merge path: single CTE chain replaces the previous 5 statements
# (SELECT exists / UPDATE-or-INSERT target / UPDATE contribs / DELETE
# contrib dupes / DELETE old edge). All clauses see the same snapshot;
# move_contribs and del_dup_contribs partition contributors disjointly
# on the NOT EXISTS / EXISTS predicate so PostgreSQL never tries to
# modify the same contributor row twice in one statement.
_MERGE_CTE_SQL = sa_text("""
    WITH upsert_target AS (
        INSERT INTO heat_edges (
            edge_key, sport, pass_count, forward_count, backward_count, user_count,
            ele_delta_m, slope_grade, surface_type, highway_type, surface_confidence,
            geometry, osm_way_id
        )
        VALUES (
            :new_key, :sport, :pc, :fc, :bc, :uc,
            :ele, :slope,
            COALESCE(NULLIF(:surface, 'unknown'), 'unknown'),
            COALESCE(NULLIF(:highway, 'unknown'), 'unknown'),
            :conf,
            ST_GeomFromText(:geom, 4326), :osm_way_id
        )
        ON CONFLICT (edge_key, sport) DO UPDATE SET
            pass_count = heat_edges.pass_count + EXCLUDED.pass_count,
            forward_count = heat_edges.forward_count + EXCLUDED.forward_count,
            backward_count = heat_edges.backward_count + EXCLUDED.backward_count,
            user_count = GREATEST(heat_edges.user_count, EXCLUDED.user_count),
            osm_way_id = COALESCE(heat_edges.osm_way_id, EXCLUDED.osm_way_id),
            ele_delta_m = COALESCE(NULLIF(heat_edges.ele_delta_m, 0), EXCLUDED.ele_delta_m),
            slope_grade = COALESCE(NULLIF(heat_edges.slope_grade, 0), EXCLUDED.slope_grade),
            surface_confidence = COALESCE(heat_edges.surface_confidence, EXCLUDED.surface_confidence)
        RETURNING 1
    ),
    move_contribs AS (
        UPDATE heat_edge_contributors hec
        SET edge_key = :new_key
        WHERE hec.edge_key = :old_key
          AND NOT EXISTS (
              SELECT 1 FROM heat_edge_contributors h2
              WHERE h2.edge_key = :new_key AND h2.user_id_hash = hec.user_id_hash
          )
        RETURNING 1
    ),
    del_dup_contribs AS (
        DELETE FROM heat_edge_contributors hec
        WHERE hec.edge_key = :old_key
          AND EXISTS (
              SELECT 1 FROM heat_edge_contributors h2
              WHERE h2.edge_key = :new_key AND h2.user_id_hash = hec.user_id_hash
          )
        RETURNING 1
    )
    -- Defensive `osm_way_id IS NULL` guard: within one chunk, row A may
    -- have new_key=Y and row B may have old_key=Y (B is part of A's
    -- chain). When B is processed, upsert_target updates the row at Y
    -- (just created by A) correctly — but without this guard the final
    -- DELETE would then remove Y, destroying A's merged target. Every
    -- candidate fetched by the cursor has osm_way_id IS NULL (the WHERE
    -- filter), and both _apply_same_key and the merge CTE set osm_way_id
    -- unconditionally. So any heat_edges row at :old_key that was just
    -- promoted by an earlier chunk-row has osm_way_id IS NOT NULL → the
    -- DELETE no-ops, the chain target survives.
    DELETE FROM heat_edges
    WHERE edge_key = :old_key AND sport = :sport
      AND osm_way_id IS NULL
""")


def _apply_same_key(db: Any, row: Any) -> None:
    """Same edge_key — just align geometry and tag with osm_way_id."""
    db.execute(_SAME_KEY_UPDATE_SQL, {
        "geom": row.osm_geom_wkt,
        "osm_way_id": row.osm_way_id,
        "key": row.old_key,
        "surface": row.surface or "unknown",
        "highway": row.highway or "unknown",
        "osm_ele": row.osm_ele_delta_m,
        "osm_slope": row.osm_slope_grade,
        "osm_conf": row.osm_surface_confidence,
    })


def _apply_merge(db: Any, row: Any, new_key: str) -> None:
    """Merge old_key into new_key via a single CTE-chain statement.

    Replaces the previous 5-statement sequence (SELECT exists / UPDATE
    or INSERT / UPDATE contribs / DELETE contrib dupes / DELETE old edge).
    """
    # Pick the elevation/slope: prefer the source heat_edges value if
    # it had one, otherwise the OSM-derived value. Matches the legacy
    # per-row logic.
    ele = row.ele_delta_m if row.ele_delta_m else row.osm_ele_delta_m
    slope = row.slope_grade if row.slope_grade else row.osm_slope_grade
    db.execute(_MERGE_CTE_SQL, {
        "new_key": new_key,
        "old_key": row.old_key,
        "sport": row.sport,
        "pc": row.pass_count,
        "fc": row.forward_count,
        "bc": row.backward_count,
        "uc": row.user_count,
        "ele": ele,
        "slope": slope,
        "surface": row.surface or "unknown",
        "highway": row.highway or "unknown",
        "conf": row.osm_surface_confidence,
        "geom": row.osm_geom_wkt,
        "osm_way_id": row.osm_way_id,
    })


def run_grouping(
    dry_run: bool = True,
    bbox: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict[str, int]:
    """Group heat_edges onto OSM road geometry.

    Returns a stats dict {merged, updated, errors, chunks}. The return
    value is mostly for tests; the CLI ignores it.
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        bbox_filter = ""
        bbox_params: dict[str, Any] = {}
        if bbox:
            parts = [float(x) for x in bbox.split(",")]
            min_lon, min_lat, max_lon, max_lat = parts
            bbox_filter = (
                "AND he.geometry && "
                "ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)"
            )
            bbox_params = {
                "min_lon": min_lon, "min_lat": min_lat,
                "max_lon": max_lon, "max_lat": max_lat,
            }

        candidate_sql = sa_text(_build_candidate_query(bbox_filter))

        if dry_run:
            # Dry-run keeps the old shape (no cursor needed, just report)
            logger.info("Finding grid-snap edges with nearby OSM roads (dry-run)...")
            rows = db.execute(
                candidate_sql,
                {**bbox_params, "last_key": "", "chunk_size": 10_000_000},
            ).fetchall()
            would_merge = 0
            new_keys: set[str] = set()
            for row in rows:
                sp1 = _snap(row.osm_lat1, row.osm_lon1)
                sp2 = _snap(row.osm_lat2, row.osm_lon2)
                new_key = _edge_key(row.sport, sp1, sp2)
                if new_key != row.old_key:
                    would_merge += 1
                new_keys.add(new_key)
            logger.info(
                "DRY RUN: %d edges would be re-keyed → %d unique OSM keys "
                "(%.0f%% reduction)",
                would_merge, len(new_keys),
                (1 - len(new_keys) / len(rows)) * 100 if rows else 0,
            )
            return {
                "merged": would_merge,
                "updated": len(rows) - would_merge,
                "errors": 0,
                "chunks": 1,
            }

        # Chunked execution with cursor pagination.
        # `last_key` advances through edge_keys; the WHERE clause drops
        # rows whose osm_way_id is now set (i.e. processed) so we never
        # revisit them. Per-chunk commit means a timeout loses at most
        # one chunk's worth of work.
        logger.info("Processing in chunks of %d rows (cursor paginated)...", chunk_size)
        last_key = ""
        merged = 0
        updated = 0
        errors = 0
        chunks = 0
        t_start = time.monotonic()

        while True:
            rows = db.execute(
                candidate_sql,
                {**bbox_params, "last_key": last_key, "chunk_size": chunk_size},
            ).fetchall()
            if not rows:
                break

            chunks += 1
            chunk_merged = 0
            chunk_updated = 0

            for row in rows:
                try:
                    sp1 = _snap(row.osm_lat1, row.osm_lon1)
                    sp2 = _snap(row.osm_lat2, row.osm_lon2)
                    new_key = _edge_key(row.sport, sp1, sp2)

                    if new_key == row.old_key:
                        _apply_same_key(db, row)
                        chunk_updated += 1
                    else:
                        _apply_merge(db, row, new_key)
                        chunk_merged += 1
                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        logger.warning("Error processing %s: %s", row.old_key, e)

            # Advance cursor BEFORE commit so a crash mid-commit doesn't
            # cost us the chunk boundary
            last_key = rows[-1].old_key
            db.commit()

            merged += chunk_merged
            updated += chunk_updated
            elapsed = time.monotonic() - t_start
            total_done = merged + updated
            rate = total_done / max(elapsed, 0.001)
            logger.info(
                "  chunk %d done: +%d merged, +%d updated (totals: "
                "merged=%d updated=%d errors=%d). elapsed=%.0fs rate=%.0f rows/s",
                chunks, chunk_merged, chunk_updated,
                merged, updated, errors, elapsed, rate,
            )

        # Recount user_count for affected edges. Single statement, no chunking
        # needed because it's a UPDATE...FROM subquery (set-based, not per-row).
        # Since migration 0052 contributors PK is
        # (edge_key, user_id_hash, activity_id), so a user with N
        # activities on the same edge has N contributor rows.
        # user_count drives K-anonymity → must DISTINCT on user_id_hash.
        logger.info("Recounting user_count for grouped edges...")
        db.execute(sa_text("""
            UPDATE heat_edges SET user_count = sub.cnt
            FROM (
                SELECT edge_key, COUNT(DISTINCT user_id_hash) AS cnt
                FROM heat_edge_contributors
                GROUP BY edge_key
            ) sub
            WHERE heat_edges.edge_key = sub.edge_key
              AND heat_edges.user_count != sub.cnt
        """))
        db.commit()

        logger.info(
            "Grouping complete: %d merged, %d geometry-updated, %d errors, %d chunks",
            merged, updated, errors, chunks,
        )

        total = db.execute(sa_text("SELECT COUNT(*) FROM heat_edges")).scalar()
        logger.info("Total heat_edges after grouping: %d", total)

        return {
            "merged": merged,
            "updated": updated,
            "errors": errors,
            "chunks": chunks,
        }

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Group heat_edges onto OSM road geometry")
    parser.add_argument("--dry-run", action="store_true", help="Report without modifying DB")
    parser.add_argument("--bbox", help="Limit to bbox: min_lon,min_lat,max_lon,max_lat")
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Rows per chunk (default {DEFAULT_CHUNK_SIZE})",
    )
    args = parser.parse_args()
    run_grouping(dry_run=args.dry_run, bbox=args.bbox, chunk_size=args.chunk_size)
