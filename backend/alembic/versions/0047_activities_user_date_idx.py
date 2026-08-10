"""Composite index on (user_id, activity_date) for cross-provider dedup.

The cross-provider dedup query in `ingest_activity` (ingest.py around
line 2667-2679) runs:

    SELECT id FROM activities
    WHERE user_id = :uid
      AND activity_date BETWEEN :date - 5min AND :date + 5min
      AND distance_m BETWEEN :dist * 0.9 AND :dist * 1.1

…on every single GPX/FIT/Strava upload. The audit (2026-05-17) measured
this at ~30-100 ms per call on a 10k-activity user — full seq scan
because there's no (user_id, activity_date) index. A 1400-file ZIP
bulk import compounds to ~70 s of wasted DB CPU.

This composite index makes the user_id + range scan a single index seek.
Concurrent creation so it doesn't block writes during the migration.

Revision ID: 0047
Revises: 0046
Create Date: 2026-05-18
"""
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `postgresql_concurrently=True` requires running outside a
    # transaction; alembic handles this via `op.execute` + autocommit.
    op.execute("COMMIT")
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS activities_user_date_idx "
        "ON activities (user_id, activity_date)",
    )


def downgrade() -> None:
    op.execute("COMMIT")
    op.execute(
        "DROP INDEX CONCURRENTLY IF EXISTS activities_user_date_idx",
    )
