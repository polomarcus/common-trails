"""Add status, distance_m, elevation_gain_m, geometry_geojson, waypoints_json to routes.

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-11 00:00:00.000000
"""
import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("routes", sa.Column("status", sa.String(20), nullable=False, server_default="published"))
    op.add_column("routes", sa.Column("distance_m", sa.Float, nullable=True))
    op.add_column("routes", sa.Column("elevation_gain_m", sa.Float, nullable=True))
    op.add_column("routes", sa.Column("geometry_geojson", sa.Text, nullable=True))
    op.add_column("routes", sa.Column("waypoints_json", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("routes", "waypoints_json")
    op.drop_column("routes", "geometry_geojson")
    op.drop_column("routes", "elevation_gain_m")
    op.drop_column("routes", "distance_m")
    op.drop_column("routes", "status")
