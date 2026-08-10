"""Populate + incrementally maintain the ``heat_edges_agg`` table.

``heat_edges_agg`` pre-materialises the by-``(osm_way_id, sport)`` aggregation
that the PMTiles build + live MVT fallback used to run over ~5 M ``heat_edges``
rows at build/request time (which OOMed the whole-world build on db-f1-micro).
See migration 0057 for the full design (MAX-not-SUM, raw user_count, grid
fallback stays live).

Two maintenance paths, BOTH driven by the SSOT
``app/services/heat_aggregation.py::build_heat_aggregation_sql`` (same
aggregation definition → zero drift):

* :func:`backfill_heat_agg` — the ONE heavy full aggregation. Run once via
  ``python -m app.jobs.rebuild_heat_agg`` (needs a DB tier bump in prod) and
  at the end of a full ``rebuild_heatmap``.
* :func:`recompute_heat_agg_for_ways` — the per-activity incremental refresh.
  Recompute-from-source (DELETE + re-INSERT) for only the ways an activity
  touched: idempotent, concurrency-safe, and self-healing (a way that lost
  all its heat_edges → recompute finds zero rows → the stale agg row is
  removed, not left to rot).
"""
import argparse
import logging
import os
import time

from sqlalchemy import text as sa_text

from app.services.heat_aggregation import build_heat_aggregation_sql

log = logging.getLogger(__name__)

# The columns written to heat_edges_agg (order matches the INSERT). Shared by
# the backfill + the per-way recompute so the two write paths can't diverge.
_AGG_COLS = (
    "osm_way_id, sport, geometry, user_count, pass_count, "
    "forward_count, backward_count, highway_type"
)
_AGG_ON_CONFLICT = """
    ON CONFLICT (osm_way_id, sport) DO UPDATE SET
        geometry = EXCLUDED.geometry,
        user_count = EXCLUDED.user_count,
        pass_count = EXCLUDED.pass_count,
        forward_count = EXCLUDED.forward_count,
        backward_count = EXCLUDED.backward_count,
        highway_type = EXCLUDED.highway_type,
        updated_at = now()
"""


def recompute_heat_agg_for_ways(db, way_ids: list[int]) -> tuple[int, int]:
    """Recompute-from-source the agg rows for ``way_ids`` and UPSERT them.

    Idempotent + self-healing: DELETE every existing agg row for these ways,
    then re-INSERT the current aggregate straight from ``heat_edges`` (via the
    SSOT builder scoped to the ways). A way that no longer has any qualifying
    ``heat_edges`` inserts nothing → its stale agg row stays deleted. All
    sports on a way are recomputed together (the GROUP BY handles multi-sport).

    Does NOT commit — the caller owns the transaction so the recompute can
    ride the same commit as the ``heat_edges`` write (or its own short txn).

    Returns ``(deleted, upserted)`` row counts for structured logging.
    """
    if not way_ids:
        return 0, 0

    deleted = db.execute(
        sa_text("DELETE FROM heat_edges_agg WHERE osm_way_id = ANY(:ways)"),
        {"ways": way_ids},
    ).rowcount

    agg_cte = build_heat_aggregation_sql(
        min_uc=1,
        drop_grid_fallback=True,
        way_ids_param="agg_way_ids",
    )
    upserted = db.execute(
        sa_text(f"""
            {agg_cte}
            INSERT INTO heat_edges_agg ({_AGG_COLS})
            SELECT {_AGG_COLS}
            FROM combined
            WHERE osm_way_id IS NOT NULL
            {_AGG_ON_CONFLICT}
        """),
        {"agg_way_ids": way_ids},
    ).rowcount

    return deleted or 0, upserted or 0


def backfill_heat_agg(db=None, *, min_uc: int = 1, truncate: bool = True) -> int:
    """Full (re)build of ``heat_edges_agg`` from ALL ``heat_edges``.

    This is the ONE heavy aggregation (the ~5 M-row GROUP BY). It is meant to
    run ONCE — on demand, or at the end of a full ``rebuild_heatmap``. In prod
    this pass needs a temporary DB tier bump (db-f1-micro OOMs on it); the
    steady-state readers + the incremental per-way refresh never run it.

    ``min_uc=1`` keeps the stored user_count RAW / K-agnostic (the K floor is
    applied at read time). Progress is logged so the one-time prod backfill is
    watchable.
    """
    own = db is None
    if own:
        from app.db.session import SessionLocal
        db = SessionLocal()
    try:
        t0 = time.time()
        source_rows = db.execute(
            sa_text("SELECT COUNT(*) FROM heat_edges WHERE osm_way_id IS NOT NULL")
        ).scalar()
        log.info(
            "backfill_heat_agg: aggregating %s OSM-matched heat_edges (min_uc=%d, truncate=%s)...",
            source_rows, min_uc, truncate,
        )

        if truncate:
            db.execute(sa_text("TRUNCATE heat_edges_agg"))

        agg_cte = build_heat_aggregation_sql(min_uc=min_uc, drop_grid_fallback=True)
        inserted = db.execute(sa_text(f"""
            {agg_cte}
            INSERT INTO heat_edges_agg ({_AGG_COLS})
            SELECT {_AGG_COLS}
            FROM combined
            WHERE osm_way_id IS NOT NULL
            {_AGG_ON_CONFLICT}
        """)).rowcount
        db.execute(sa_text("ANALYZE heat_edges_agg"))
        db.commit()

        total = db.execute(sa_text("SELECT COUNT(*) FROM heat_edges_agg")).scalar()
        log.info(
            "backfill_heat_agg: done — %s ways aggregated (%s rows in table) in %.0fs",
            inserted, total, time.time() - t0,
        )
        return int(total or 0)
    finally:
        if own:
            db.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description="Backfill heat_edges_agg")
    parser.add_argument(
        "--min-uc", type=int, default=1,
        help="Aggregation K floor (default 1 = raw/K-agnostic; K is applied at read).",
    )
    parser.add_argument(
        "--no-truncate", action="store_true",
        help="Upsert without truncating first (idempotent partial re-run).",
    )
    args = parser.parse_args()
    backfill_heat_agg(min_uc=args.min_uc, truncate=not args.no_truncate)


# Env flag: when "true", the per-activity incremental hook in
# ingest._update_heat_edges is a no-op. Set by rebuild_heatmap during the
# parallel loop so a full rebuild does ONE backfill at the end instead of N
# redundant per-way recomputes.
SKIP_AGG_MAINTENANCE_ENV = "SKIP_HEAT_AGG_MAINTENANCE"


def agg_maintenance_enabled() -> bool:
    return os.environ.get(SKIP_AGG_MAINTENANCE_ENV, "").lower() != "true"


if __name__ == "__main__":
    main()
