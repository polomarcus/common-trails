"""Add btree index on osm_road_edges(osm_way_id) for matview / MVT joins.

Without this index, every LATERAL JOIN that lifts the smooth multi-point
``way_geometry`` from ``osm_road_edges`` for a heat_edge's ``osm_way_id``
falls back to a sequential scan of the 9.8 M-row table. With ~1.4 M
unique ``osm_way_id``s in ``heat_edges``, the matview rebuild and the
PMTiles export both grind for tens of minutes.

Required by:
- ``app/jobs/refresh_matview.py``  (LATERAL JOIN on g.osm_way_id)
- ``app/jobs/build_pmtiles.py``    (LATERAL JOIN on g.osm_way_id)
- ``app/api/heatmap.py`` fallback  (LATERAL JOIN on g.osm_way_id)
- Anything else that wants to materialise the smooth way curve

Partial index: ``WHERE osm_way_id IS NOT NULL`` — the column is nullable
for graph-internal vertices that don't correspond to a single OSM way,
and we never look those up via this path.

Use CREATE INDEX CONCURRENTLY in prod (must be run outside a transaction).
This migration uses non-CONCURRENTLY because Alembic wraps everything in
a transaction by default; for a 9.8 M-row prod table, expect the index
build to hold an ACCESS EXCLUSIVE lock for ~10 min during writes. Run
during low-traffic, or apply manually with CONCURRENTLY then ``alembic
stamp 0038``. See docs/migration-runbook.md.

Revision ID: 0038
Revises: 0037
Create Date: 2026-05-03 00:00:00.000000
"""
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_osm_road_edges_osm_way_id
        ON osm_road_edges (osm_way_id)
        WHERE osm_way_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_osm_road_edges_osm_way_id")
