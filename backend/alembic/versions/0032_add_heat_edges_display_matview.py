"""Add materialized view heat_edges_display for pre-computed smooth geometry.

Pre-joins heat_edges with osm_road_edges to resolve actual road curves,
merges connected edges by popularity bucket, and Chaikin-smooths the result.
MVT tile generation becomes a simple SELECT instead of expensive CTE chains.

Refresh after heatmap rebuild:
    REFRESH MATERIALIZED VIEW CONCURRENTLY heat_edges_display;

Revision ID: 0032
Revises: 0031
Create Date: 2026-04-10 00:00:00.000000
"""
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create EMPTY materialized view (instant, doesn't block startup).
    # Populated by: python -m app.jobs.refresh_matview
    # Or automatically via _schedule_matview_refresh() after heatmap ingest.
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

    # Unique index required for REFRESH CONCURRENTLY
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_heat_edges_display_id
        ON heat_edges_display (id)
    """)

    # Spatial index for fast tile queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_heat_edges_display_geom
        ON heat_edges_display USING GIST (geometry)
    """)

    # Composite index for sport + bucket filtering
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_heat_edges_display_sport_bucket
        ON heat_edges_display (sport, bucket)
    """)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS heat_edges_display")
