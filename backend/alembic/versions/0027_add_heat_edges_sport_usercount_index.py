"""Add composite partial index on heat_edges (sport, user_count) for faster graph queries.

Revision ID: 0027
Revises: 0026
Create Date: 2026-03-27 00:00:00.000000
"""
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "ix_heat_edges_sport_usercount ON heat_edges (sport, user_count) "
        "WHERE user_count >= 1"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_heat_edges_sport_usercount")
