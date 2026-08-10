"""Add slope_grade column to dfci_edges and trail_edges.

Moves DEM slope enrichment from runtime (graph tile serving) to import time.
Slopes are computed once at startup via enrich_dfci_trail_slopes() and stored.

Revision ID: 0017
Revises: 0016
Create Date: 2026-03-16 00:00:00.000000
"""
import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dfci_edges", sa.Column("slope_grade", sa.Float(), server_default="0.0"))
    op.add_column("trail_edges", sa.Column("slope_grade", sa.Float(), server_default="0.0"))


def downgrade() -> None:
    op.drop_column("trail_edges", "slope_grade")
    op.drop_column("dfci_edges", "slope_grade")
