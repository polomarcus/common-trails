"""Drift check: ``heat_edges_agg`` vs a fresh live aggregation.

The incremental maintenance of ``heat_edges_agg`` can silently rot (a missed
touched-way, a concurrency interleave, a schema/SQL change on one path only).
This is the safety net that catches divergence BEFORE it reaches the map.

It recomputes the OSM-matched aggregation LIVE from ``heat_edges`` (the SSOT
``build_heat_aggregation_sql`` at min_uc=1, the same raw/K-agnostic level the
agg table stores) and compares it, key-for-key, against ``heat_edges_agg``:

  * missing   — (osm_way_id, sport) live has but agg lacks
  * extra     — agg has but live lacks (stale row that should have been removed)
  * mismatch  — same key, different MAX user/pass/forward/backward count

Exits non-zero on ANY drift so it can gate a cron / CI. Follows the structured
metric-emission shape of ``build_pmtiles._emit_heat_quality_metrics``.

    python -m app.jobs.verify_heat_agg              # full compare, exit 1 on drift
    python -m app.jobs.verify_heat_agg --sample 5000  # cap value-compare rows
"""
import argparse
import logging
import sys
import time
from dataclasses import asdict, dataclass

from sqlalchemy import text as sa_text

from app.services.heat_aggregation import build_heat_aggregation_sql

log = logging.getLogger(__name__)


@dataclass
class DriftReport:
    agg_rows: int
    live_rows: int
    missing: int  # in live, absent from agg
    extra: int  # in agg, absent from live (stale)
    mismatched: int  # same key, different counts
    sampled: int
    elapsed_ms: int

    @property
    def clean(self) -> bool:
        return self.missing == 0 and self.extra == 0 and self.mismatched == 0


def verify_heat_agg(db=None, *, sample: int | None = None) -> DriftReport:
    """Compare ``heat_edges_agg`` against a fresh live aggregation.

    ``sample`` caps the number of overlapping keys value-compared (the count
    columns) — the row-set diff (missing/extra) is always full. ``None`` =
    compare every overlapping key.
    """
    own = db is None
    if own:
        from app.db.session import SessionLocal
        db = SessionLocal()
    try:
        t0 = time.time()
        agg_rows = db.execute(sa_text("SELECT COUNT(*) FROM heat_edges_agg")).scalar() or 0

        live_cte = build_heat_aggregation_sql(min_uc=1, drop_grid_fallback=True)
        # Materialise the live aggregate into a temp table so the set-diff +
        # value-compare are single indexed passes (not three re-aggregations).
        db.execute(sa_text("DROP TABLE IF EXISTS _heat_agg_live_check"))
        db.execute(sa_text(f"""
            CREATE TEMP TABLE _heat_agg_live_check AS
            {live_cte}
            SELECT osm_way_id, sport, user_count, pass_count,
                   forward_count, backward_count
            FROM combined
            WHERE osm_way_id IS NOT NULL
        """))
        db.execute(sa_text(
            "CREATE INDEX ON _heat_agg_live_check (osm_way_id, sport)"
        ))
        live_rows = db.execute(
            sa_text("SELECT COUNT(*) FROM _heat_agg_live_check")
        ).scalar() or 0

        missing = db.execute(sa_text("""
            SELECT COUNT(*) FROM _heat_agg_live_check l
            LEFT JOIN heat_edges_agg a
              ON a.osm_way_id = l.osm_way_id AND a.sport = l.sport
            WHERE a.osm_way_id IS NULL
        """)).scalar() or 0
        extra = db.execute(sa_text("""
            SELECT COUNT(*) FROM heat_edges_agg a
            LEFT JOIN _heat_agg_live_check l
              ON a.osm_way_id = l.osm_way_id AND a.sport = l.sport
            WHERE l.osm_way_id IS NULL
        """)).scalar() or 0

        sample_clause = f"LIMIT {int(sample)}" if sample else ""
        mismatched = db.execute(sa_text(f"""
            SELECT COUNT(*) FROM (
                SELECT a.osm_way_id FROM heat_edges_agg a
                JOIN _heat_agg_live_check l
                  ON a.osm_way_id = l.osm_way_id AND a.sport = l.sport
                WHERE a.user_count <> l.user_count
                   OR a.pass_count <> l.pass_count
                   OR a.forward_count <> l.forward_count
                   OR a.backward_count <> l.backward_count
                {sample_clause}
            ) d
        """)).scalar() or 0

        sampled = db.execute(sa_text("""
            SELECT COUNT(*) FROM heat_edges_agg a
            JOIN _heat_agg_live_check l
              ON a.osm_way_id = l.osm_way_id AND a.sport = l.sport
        """)).scalar() or 0
        if sample:
            sampled = min(sampled, sample)

        db.execute(sa_text("DROP TABLE IF EXISTS _heat_agg_live_check"))
        db.commit()

        return DriftReport(
            agg_rows=int(agg_rows), live_rows=int(live_rows),
            missing=int(missing), extra=int(extra), mismatched=int(mismatched),
            sampled=int(sampled), elapsed_ms=int((time.time() - t0) * 1000),
        )
    finally:
        if own:
            db.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(description="Verify heat_edges_agg has not drifted")
    parser.add_argument("--sample", type=int, default=None,
                        help="Cap value-compared overlapping keys (None = all).")
    args = parser.parse_args()

    report = verify_heat_agg(sample=args.sample)
    payload = {"event": "heat_agg_drift", **asdict(report), "clean": report.clean}
    if report.clean:
        log.info("heat_agg drift check CLEAN: agg=%d live=%d (%dms) data=%s",
                 report.agg_rows, report.live_rows, report.elapsed_ms, payload)
        return 0
    log.error(
        "heat_agg DRIFT: missing=%d extra=%d mismatched=%d "
        "(agg=%d live=%d, %dms) — run `python -m app.jobs.rebuild_heat_agg` data=%s",
        report.missing, report.extra, report.mismatched,
        report.agg_rows, report.live_rows, report.elapsed_ms, payload,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
