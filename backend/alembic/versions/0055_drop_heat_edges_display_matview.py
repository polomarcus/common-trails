"""Drop the heat_edges_display materialized view.

The matview (added in 0032) only accelerated the LIVE ``/heatmap/tiles``
MVT endpoint — which the frontend uses ONLY as a fallback behind the
primary static PMTiles (``frontend/lib/cdn-cache.ts``). For a rarely-hit
fallback it cost a refresh-on-every-ingest, a 5-15 min build, a
``WHERE false`` placeholder gotcha, and a 27-min pre-warm hazard.

The live endpoint now runs the by-OSM-way aggregation against ``heat_edges``
directly at request time (the shared builder
``app.services.heat_aggregation.build_heat_aggregation_sql``), so it still
resolves the smooth ``way_geometry`` (no 2-point regression) — it just pays
a ~50-200 ms LATERAL-join cost on the rare fallback request instead of a
continuous refresh tax.

Revision ID: 0054
Revises: 0053
Create Date: 2026-06-14 00:00:00.000000
"""
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # DROP the matview + its indexes. The indexes are dropped implicitly with
    # the matview, but we drop them explicitly first for clarity / idempotence
    # on partially-migrated DBs.
    op.execute("DROP INDEX IF EXISTS ix_heat_edges_display_id")
    op.execute("DROP INDEX IF EXISTS ix_heat_edges_display_geom")
    op.execute("DROP INDEX IF EXISTS ix_heat_edges_display_sport_bucket")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS heat_edges_display")


def downgrade() -> None:
    # Recreate the EMPTY placeholder matview (matches 0032's upgrade) so a
    # downgrade restores the prior schema shape. It is populated by the
    # now-deleted refresh_matview job in the pre-0054 world; a downgrade is
    # not expected in practice (the matview path is gone from the code).
    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS heat_edges_display AS
        SELECT
            1::bigint AS id,
            1 AS bucket,
            'road'::text AS sport,
            ST_GeomFromText('LINESTRING(0 0, 0 0)', 4326) AS geometry,
            1 AS user_count,
            0.0::numeric AS heat_score
        WHERE false
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_heat_edges_display_id
        ON heat_edges_display (id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_heat_edges_display_geom
        ON heat_edges_display USING GIST (geometry)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_heat_edges_display_sport_bucket
        ON heat_edges_display (sport, bucket)
    """)
