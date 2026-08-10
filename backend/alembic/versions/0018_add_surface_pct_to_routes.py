"""Add surface_pct column to routes.

Stores surface breakdown as JSON text (e.g. {"asphalt": 72.5, "gravel": 27.5}).
Derived cache — not versioned in route_versions.

Revision ID: 0018
Revises: 0017
Create Date: 2026-03-17 00:00:00.000000
"""
import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("routes", sa.Column("surface_pct", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("routes", "surface_pct")
