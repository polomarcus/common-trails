"""Add way_geometry column to osm_road_edges for full OSM way curves.

Stores the complete multi-point LineString of the parent OSM way, alongside
the existing 2-point node-to-node segment geometry. The segment geometry is
still used for spatial matching; way_geometry is used by the heatmap matview
to render smooth road curves instead of grid-snapped staircases.

Populated by: rebuild with --rebuild-way-geom flag, or automatically on
next Overpass/PBF import.

Revision ID: 0033
Revises: 0032
Create Date: 2026-04-10 00:00:00.000000
"""
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add nullable way_geometry column (full way curve, multi-point LineString)
    op.execute("""
        SELECT AddGeometryColumn('osm_road_edges', 'way_geometry', 4326, 'LINESTRING', 2)
    """)
    # Backfill: reconstruct full way geometry from ordered segments
    op.execute("""
        UPDATE osm_road_edges oe SET way_geometry = sub.way_geom
        FROM (
            SELECT osm_way_id, tile_key,
                ST_MakeLine(
                    array_agg(ST_StartPoint(geometry) ORDER BY segment_idx)
                    || ARRAY[ST_EndPoint((array_agg(geometry ORDER BY segment_idx DESC))[1])]
                ) AS way_geom
            FROM osm_road_edges
            GROUP BY osm_way_id, tile_key
        ) sub
        WHERE oe.osm_way_id = sub.osm_way_id AND oe.tile_key = sub.tile_key
    """)
    # GIST index on way_geometry for matview JOIN performance
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_osm_road_edges_way_geometry
        ON osm_road_edges USING GIST (way_geometry)
    """)
    # Recreate matview as empty shell (fast). Populate via refresh_matview job.
    op.execute("DROP MATERIALIZED VIEW IF EXISTS heat_edges_display")
    op.execute("""
        CREATE MATERIALIZED VIEW heat_edges_display AS
        SELECT
            1::bigint AS id,
            1 AS bucket,
            'road'::text AS sport,
            ST_GeomFromText('LINESTRING(0 0, 0 0)', 4326) AS geometry,
            1 AS user_count,
            0.0::numeric AS heat_score
        WHERE false
    """)
    op.execute("CREATE UNIQUE INDEX ix_heat_edges_display_id ON heat_edges_display (id)")
    op.execute("CREATE INDEX ix_heat_edges_display_geom ON heat_edges_display USING GIST (geometry)")
    op.execute("CREATE INDEX ix_heat_edges_display_sport_bucket ON heat_edges_display (sport, bucket)")


def downgrade() -> None:
    op.execute("ALTER TABLE osm_road_edges DROP COLUMN IF EXISTS way_geometry")
