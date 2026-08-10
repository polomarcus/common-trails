"""Add geometry_source to activities and gps_upgraded_count to import_jobs.

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-12 00:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("activities", sa.Column("geometry_source", sa.String(20), server_default="polyline"))
    op.add_column("import_jobs", sa.Column("gps_upgraded_count", sa.Integer, server_default="0"))


def downgrade() -> None:
    op.drop_column("import_jobs", "gps_upgraded_count")
    op.drop_column("activities", "geometry_source")
