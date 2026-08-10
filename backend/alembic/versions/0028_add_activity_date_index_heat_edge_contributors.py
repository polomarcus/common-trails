"""Add index on heat_edge_contributors.activity_date for fast time-filtered K-anonymity.

Without this index, time-filtered heatmap queries (JOIN + GROUP BY + HAVING)
scan 20-50M rows at scale (1000 users). With the index: <1s.

Revision ID: 0028
Revises: 0027
Create Date: 2026-03-29 00:00:00.000000
"""
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "ix_heat_edge_contributors_activity_date "
        "ON heat_edge_contributors (activity_date) "
        "WHERE activity_date IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_heat_edge_contributors_activity_date")
