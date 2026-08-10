"""Add osm_road_edges table for map-matching heatmap edges.

Revision ID: 0025
Revises: 0024
Create Date: 2026-03-23
"""
import sqlalchemy as sa
from geoalchemy2 import Geometry

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "osm_road_edges",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("tile_key", sa.Text, nullable=False),
        sa.Column("osm_way_id", sa.BigInteger, nullable=False),
        sa.Column("segment_idx", sa.Integer, nullable=False),
        sa.Column("surface", sa.Text, server_default="unknown"),
        sa.Column("highway", sa.Text, server_default="unknown"),
        sa.Column("geometry", Geometry("LINESTRING", srid=4326), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_osm_road_edges_tile_key", "osm_road_edges", ["tile_key"])
    op.create_index(
        "ix_osm_road_edges_geometry",
        "osm_road_edges",
        ["geometry"],
        postgresql_using="gist",
    )


def downgrade() -> None:
    op.drop_table("osm_road_edges")
