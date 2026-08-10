"""Drop expired rows from the ``export_requests`` table.

Phase 3 of the heatmap-export feature (PRD #391 §5). The async pipeline
inserts a row per export request with a 24 h TTL (``expires_at`` column,
DDL default ``NOW() + INTERVAL '24 hours'``). The GCS lifecycle on the
``archive/`` prefix already deletes the binaries after 90 days; this job
just keeps the DB tracking table small.

Run locally:
    python -m app.jobs.cleanup_export_requests

Production: schedule alongside the existing matview-refresh / artefact-
rebuild Cloud Run jobs. ~1 ms per row at any realistic volume; running
hourly or daily are both fine.
"""
import logging

from sqlalchemy import text as sa_text

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> int:
    """Delete export_requests rows past their ``expires_at``.

    Returns the number of rows deleted (for tests + cron logging).
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        # Single DELETE — RETURNING id so we can log + return the count.
        # No batching: the table is tiny by design (24 h TTL, low-traffic
        # endpoint), so a single statement is always safe.
        result = db.execute(sa_text("""
            DELETE FROM export_requests
            WHERE expires_at < NOW()
            RETURNING id
        """))
        rows = result.fetchall()
        db.commit()
        count = len(rows)
        log.info("cleanup_export_requests: deleted %d expired row(s)", count)
        return count
    finally:
        db.close()


if __name__ == "__main__":
    main()
