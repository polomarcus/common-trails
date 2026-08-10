"""Add composite index on osm_road_edges (tile_key, highway) for faster graph tile queries.

The graph_tiles endpoint filters by both tile_key AND highway type. A composite index
avoids the extra heap lookup after the tile_key filter.

Revision ID: 0031
Revises: 0030
Create Date: 2026-04-09 00:00:00.000000
"""
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "ix_osm_road_edges_tile_highway ON osm_road_edges (tile_key, highway)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_osm_road_edges_tile_highway")
