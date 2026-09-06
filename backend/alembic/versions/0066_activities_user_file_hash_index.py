"""Index the drain's per-member file_hash dedup probe.

Every archive-member ingest runs the idempotency check

    SELECT ... FROM activities
    WHERE user_id = :uid AND file_hash = :hash LIMIT 1

(``ingest_activity``). ``activities`` has no index covering it, so each
probe scans the user's whole activity set — O(members × activities) across
a drain: re-draining a 16k-member Garmin archive against a 16k-row user is
~256M row visits on the shared db-f1-micro, and the probe fires for EVERY
member (including the already-imported ones a re-drain is supposed to
skip cheaply).

Partial (``file_hash IS NOT NULL``): Strava-API rows carry no file_hash and
can never match the probe, so they don't need to be in the index. Plain
``op.create_index`` — no CONCURRENTLY (alembic runs inside a transaction);
the table is small enough (beta scale) that the brief lock is fine.

Revision ID: 0066
Revises: 0065
"""
import sqlalchemy as sa

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_activities_user_file_hash",
        "activities",
        ["user_id", "file_hash"],
        postgresql_where=sa.text("file_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_activities_user_file_hash", table_name="activities")
