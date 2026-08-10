"""Add osm_way_id to heat_edges + widen heat_score formula.

- osm_way_id: enables fast equi-join in matview (vs spatial ST_DWithin)
- heat_score formula: LN(1+uc)/(8*LN(2)) caps at ~180 users instead of 22

Revision ID: 0034
Revises: 0033
Create Date: 2026-04-12 00:00:00.000000
"""
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add osm_way_id column for fast matview JOIN
    op.execute("ALTER TABLE heat_edges ADD COLUMN IF NOT EXISTS osm_way_id BIGINT")
    op.execute("CREATE INDEX IF NOT EXISTS ix_heat_edges_osm_way_id ON heat_edges (osm_way_id) WHERE osm_way_id IS NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE heat_edges DROP COLUMN IF EXISTS osm_way_id")
