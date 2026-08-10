"""Add surface_confidence to osm_road_edges AND heat_edges.

Populated at PBF-import time from `classify_surface(tags)` which returns
(class, confidence ∈ [0, 1]). Confidence reflects how strong the OSM
signal is: explicit `surface=asphalt` → 1.0; falling back to
`highway=track` → 0.40; `smoothness=*` downgrade caps at 0.6.

The column lives in both tables:
- `osm_road_edges` — written at PBF import (SSOT for OSM-derived signal)
- `heat_edges` — written by `group_edges_osm` when linking heat edges
  to an OSM way; lets the frontend fade overlay opacity by confidence
  without needing a JOIN on every read.

Crouzet "uncertainty, not absolute truth": the column lets the
frontend fade surface overlay opacity by confidence rather than
binary-drop at `data_quality=poor`.

Revision ID: 0042
Revises: 0041
Create Date: 2026-05-15
"""
import sqlalchemy as sa

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "osm_road_edges",
        sa.Column("surface_confidence", sa.Float, nullable=True),
    )
    # heat_edges is partitioned by sport — ALTER on the parent
    # propagates to all child partitions automatically (PG ≥ 11).
    op.execute("""
        ALTER TABLE heat_edges
        ADD COLUMN IF NOT EXISTS surface_confidence DOUBLE PRECISION
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE heat_edges DROP COLUMN IF EXISTS surface_confidence")
    op.drop_column("osm_road_edges", "surface_confidence")
