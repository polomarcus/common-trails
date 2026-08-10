"""Slim the OSM substrate: osm_ways side-table + region-partitioned osm_road_edges.

The June 2026 audit measured ``way_geometry`` duplicated onto every segment
row of a way (×13.5 on average) = 15 GB of the 16 GB heap, plus a 2.9 GB
GiST on a column no query ever filters spatially (all consumers join by
``osm_way_id``). This migration:

1. Moves the full-way polyline to a new ``osm_ways`` side-table —
   ``(osm_way_id PK, way_geometry, region)``, stored ONCE per way, NO GiST.
2. Recreates ``osm_road_edges`` partitioned ``BY LIST (region)`` so a
   region reimport is ``TRUNCATE partition + import`` (idempotent) and a
   region removal is ``DROP partition`` O(1). Partitions are created on
   demand by ``app.cli.import_osm_roads``; a DEFAULT partition catches
   test / Overpass rows (``region`` defaults to ``'adhoc'``).
3. Slims the row: ``way_geometry`` dropped (see 1), ``tile_key`` TEXT
   ``"14/x/y"`` → BIGINT ``x*100000+y`` (see ``app.services.tile_keys``),
   ``fetched_at`` dropped (per-region freshness now lives in the tiny
   ``osm_import_meta`` table written by the import guard).

DDL-ONLY — NO DATA MIGRATION. ``osm_road_edges`` is DERIVED data,
reimportable from Geofabrik PBFs in ~1 h per region batch. On any
environment this DROPS the existing substrate; reimport regions with
``python -m app.cli.import_osm_roads <region>`` afterwards. NEVER restore
the old 200 GB prod dump onto this schema — restore users+activities only
and reimport regions fresh (~7 GB for occitanie+paca-scale coverage).

Deferred (churn > gain for now): surface/highway stay TEXT, not enums.

Revision ID: 0056
Revises: 0055
Create Date: 2026-07-09 00:00:00.000000
"""
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS osm_road_edges CASCADE")

    # Full-way polyline stored ONCE per way. NO GiST on way_geometry:
    # zero spatial queries exist on it — every consumer joins by
    # osm_way_id (audited 2026-07). ``region`` = last importer that wrote
    # the way (boundary ways shared by two regional PBFs keep the most
    # recent import's copy via ON CONFLICT upsert).
    op.execute("""
        CREATE TABLE osm_ways (
            osm_way_id BIGINT PRIMARY KEY,
            way_geometry geometry(LineString, 4326) NOT NULL,
            region TEXT NOT NULL DEFAULT 'adhoc'
        )
    """)

    # Plain sequence (not IDENTITY) so the DDL works on any PG version —
    # IDENTITY on partitioned tables is PG17+ only.
    op.execute("CREATE SEQUENCE osm_road_edges_id_seq")
    op.execute("""
        CREATE TABLE osm_road_edges (
            id BIGINT NOT NULL DEFAULT nextval('osm_road_edges_id_seq'),
            region TEXT NOT NULL DEFAULT 'adhoc',
            tile_key BIGINT NOT NULL,
            osm_way_id BIGINT NOT NULL,
            segment_idx INTEGER NOT NULL,
            surface TEXT DEFAULT 'unknown',
            highway TEXT DEFAULT 'unknown',
            geometry geometry(LineString, 4326) NOT NULL,
            ele_start_m DOUBLE PRECISION,
            ele_end_m DOUBLE PRECISION,
            ele_delta_m DOUBLE PRECISION,
            slope_grade DOUBLE PRECISION,
            surface_confidence DOUBLE PRECISION,
            bridge_yes BOOLEAN NOT NULL DEFAULT false,
            tunnel_yes BOOLEAN NOT NULL DEFAULT false,
            PRIMARY KEY (region, id)
        ) PARTITION BY LIST (region)
    """)
    op.execute("ALTER SEQUENCE osm_road_edges_id_seq OWNED BY osm_road_edges.id")
    # Catch-all for rows outside a named region partition: tests, the
    # dev-only Overpass runtime fetch, fixture loads (region='adhoc').
    op.execute("CREATE TABLE osm_road_edges_default PARTITION OF osm_road_edges DEFAULT")

    # Same index set as pre-0056 minus the dropped way_geometry GiST.
    # Partitioned indexes cascade to every (future) partition.
    op.execute("CREATE INDEX ix_osm_road_edges_geometry ON osm_road_edges USING GIST (geometry)")
    op.execute("CREATE INDEX ix_osm_road_edges_osm_way_id ON osm_road_edges (osm_way_id)")
    op.execute("CREATE INDEX ix_osm_road_edges_tile_highway ON osm_road_edges (tile_key, highway)")
    op.execute("""
        CREATE INDEX ix_osm_road_edges_bridge_tunnel ON osm_road_edges
        USING GIST (geometry) WHERE bridge_yes OR tunnel_yes
    """)

    # Per-region import bookkeeping — written by the import guard in
    # app.cli.import_osm_roads (replaces the per-row fetched_at column).
    op.execute("""
        CREATE TABLE osm_import_meta (
            region TEXT PRIMARY KEY,
            imported_at TIMESTAMPTZ NOT NULL,
            row_count BIGINT NOT NULL
        )
    """)


def downgrade() -> None:
    # Recreate the pre-0056 shape EMPTY — the data is derived and the old
    # fat rows can only come back via a reimport with pre-0056 code.
    op.execute("DROP TABLE IF EXISTS osm_import_meta")
    op.execute("DROP TABLE IF EXISTS osm_road_edges CASCADE")
    op.execute("DROP TABLE IF EXISTS osm_ways")
    op.execute("""
        CREATE TABLE osm_road_edges (
            id BIGSERIAL PRIMARY KEY,
            tile_key TEXT NOT NULL,
            osm_way_id BIGINT NOT NULL,
            segment_idx INTEGER NOT NULL,
            surface TEXT DEFAULT 'unknown',
            highway TEXT DEFAULT 'unknown',
            geometry geometry(LineString, 4326) NOT NULL,
            fetched_at TIMESTAMPTZ DEFAULT now(),
            way_geometry geometry(LineString, 4326),
            ele_start_m DOUBLE PRECISION,
            ele_end_m DOUBLE PRECISION,
            ele_delta_m DOUBLE PRECISION,
            slope_grade DOUBLE PRECISION,
            surface_confidence DOUBLE PRECISION,
            bridge_yes BOOLEAN NOT NULL DEFAULT false,
            tunnel_yes BOOLEAN NOT NULL DEFAULT false
        )
    """)
    op.execute("CREATE INDEX ix_osm_road_edges_geometry ON osm_road_edges USING GIST (geometry)")
    op.execute("CREATE INDEX ix_osm_road_edges_way_geometry ON osm_road_edges USING GIST (way_geometry)")
    op.execute("""
        CREATE INDEX ix_osm_road_edges_osm_way_id ON osm_road_edges (osm_way_id)
        WHERE osm_way_id IS NOT NULL
    """)
    op.execute("CREATE INDEX ix_osm_road_edges_tile_highway ON osm_road_edges (tile_key, highway)")
    op.execute("""
        CREATE INDEX ix_osm_road_edges_bridge_tunnel ON osm_road_edges
        USING GIST (geometry) WHERE bridge_yes OR tunnel_yes
    """)
