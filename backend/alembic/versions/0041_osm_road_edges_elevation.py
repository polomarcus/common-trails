"""Add elevation columns to osm_road_edges.

Populated at PBF-import time via local DEM lookup (SRTM/Copernicus GLO-90
HGT tiles on disk). Nullable — DEM may be missing for some tiles (no
HGT downloaded, void cells, ocean). Consumers must handle NULL.

Revision ID: 0041
Revises: 0040
Create Date: 2026-05-15
"""
import sqlalchemy as sa

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("osm_road_edges", sa.Column("ele_start_m", sa.Float, nullable=True))
    op.add_column("osm_road_edges", sa.Column("ele_end_m", sa.Float, nullable=True))
    op.add_column("osm_road_edges", sa.Column("ele_delta_m", sa.Float, nullable=True))
    op.add_column("osm_road_edges", sa.Column("slope_grade", sa.Float, nullable=True))


def downgrade() -> None:
    op.drop_column("osm_road_edges", "slope_grade")
    op.drop_column("osm_road_edges", "ele_delta_m")
    op.drop_column("osm_road_edges", "ele_end_m")
    op.drop_column("osm_road_edges", "ele_start_m")
