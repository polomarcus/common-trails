"""Backfill activity_date on heat_edge_contributors.

Re-processes each activity's geometry through _update_heat_edges() with the
correct activity_date. The GREATEST logic in the contributor INSERT keeps the
per-edge maximum date automatically.

Must run inside backend container with PYTHONHASHSEED=0 so hash() matches
existing user_id_hash values.

Usage:
    docker compose exec backend python -m app.cli.backfill_contributor_dates
    docker compose exec backend python -m app.cli.backfill_contributor_dates --dry-run
"""
import argparse
import logging

from sqlalchemy import text as sa_text

from app.db.models import Activity
from app.db.session import SessionLocal
from app.services.ingest import _update_heat_edges
from app.services.provenance import COMMUNITY_SOURCE

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill activity_date on heat_edge_contributors")
    parser.add_argument("--dry-run", action="store_true", help="Count activities without modifying data")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Provenance gate (②): re-ingest ONLY community-eligible uploads
        # (source == "manual_upload") — Strava-API rows never feed heat_edges.
        activities = (
            db.query(Activity)
            .filter(Activity.contribute_heatmap == True)  # noqa: E712
            .filter(Activity.source == COMMUNITY_SOURCE)
            .order_by(Activity.activity_date.asc().nullslast())
            .all()
        )
        logger.info("Found %d activities to backfill", len(activities))

        if args.dry_run:
            logger.info("Dry run — no changes made")
            return

        for i, act in enumerate(activities):
            date = act.activity_date or act.created_at
            sport = act.sport or "road"
            if act.geometry_geojson:
                # Pass `activity_id` so heat_edge_contributors UPSERT is
                # idempotent (PR #369). Without it, `_update_heat_edges`
                # synthesizes a fresh uuid4 per call and re-running this
                # CLI inflates pass_count instead of leaving it stable.
                _update_heat_edges(
                    act.user_id, sport, act.geometry_geojson,
                    activity_date=date,
                    activity_id=str(act.id),
                )
            if i % 100 == 0:
                logger.info("Backfill progress: %d/%d", i, len(activities))

        # _update_heat_edges() commits via its own session — no outer commit needed
        logger.info("Backfill complete")

    finally:
        db.close()

    # Validation (fresh session to see committed data)
    db = SessionLocal()
    try:
        null_count = db.execute(
            sa_text("SELECT COUNT(*) FROM heat_edge_contributors WHERE activity_date IS NULL")
        ).scalar()
        logger.info("Contributors with NULL activity_date: %d", null_count)
    finally:
        db.close()


if __name__ == "__main__":
    main()
