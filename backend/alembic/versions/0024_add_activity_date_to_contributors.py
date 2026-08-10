"""Add activity_date to heat_edge_contributors.

Revision ID: 0024
Revises: 0023
Create Date: 2026-03-22

# Backfill runs as Python script (backfill_contributor_dates.py) — SQL cannot
# reconstruct hash() values used in user_id_hash.
"""
import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "heat_edge_contributors",
        sa.Column("activity_date", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("heat_edge_contributors", "activity_date")
