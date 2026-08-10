"""Add match_confidence + match_source columns to heat_edges.

These let us record HOW a heat_edge's ``osm_way_id`` was assigned:

- ``match_source = 'valhalla'`` — Valhalla map-matching at ingest
  (HMM-based, route-level, post-May-2026 path).
- ``match_source = 'spatial'`` — legacy per-point nearest-segment
  matcher in ``app/services/ingest._match_to_osm``. The fallback
  when Valhalla is down or returns low confidence.
- ``match_source = NULL`` — heat_edges ingested before this column
  existed. Treat as 'spatial' for migration purposes.

``match_confidence ∈ [0,1]`` is Valhalla's per-edge ``score``. Spatial
matches set this to NULL or to the inverse-distance heuristic from
``_match_to_osm`` (the latter is optional; NULL is fine).

Both nullable so legacy heat_edges keep working without a backfill.
The new ingest path (``map_matcher.py``, see
``docs/heatmap-map-matching-plan.md``) populates them on every write.

Revision ID: 0039
Revises: 0038
Create Date: 2026-05-03 00:00:00.000000
"""
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both columns added to the partitioned parent — Postgres propagates
    # to every existing partition (heat_edges_road, _gravel, _mtb,
    # _offroad, _running, _default) automatically.
    op.execute("""
        ALTER TABLE heat_edges
        ADD COLUMN IF NOT EXISTS match_confidence DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS match_source TEXT
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE heat_edges
        DROP COLUMN IF EXISTS match_confidence,
        DROP COLUMN IF EXISTS match_source
    """)
