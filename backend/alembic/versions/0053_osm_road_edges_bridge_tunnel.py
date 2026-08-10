"""Add `bridge_yes` + `tunnel_yes` boolean columns to `osm_road_edges`.

# The bug (Paul, 2026-05-31 evening)

In Clapiers, the WASM routing engine detours 1.8 km / 8 min to cross
the Lez via a far-away bridge instead of using the obvious Bd de
Lauriol bridge at ~`lat=43.65170, lon=3.87082` (OSM way 4299771,
`highway=primary bridge=yes bicycle=permissive`). Overpass confirms
the bridge exists in OSM. The bridge is missing from the routing
graph entirely.

# Root cause

`wasm-router/build.sh:39` hardcodes `skip_osm=true` on the
`/routing/graph/$sport/area.pb` call that pre-builds the `.fgraph`
shards. That intentionally excludes `osm_road_edges` to keep shard
sizes small (heat_edges + dfci + trail only). Bridges that nobody
has Strava-tracked → not in `heat_edges` → not reachable.

# The fix

Introduce a "critical connectors" layer to `_build_bbox_graph` that is
**always** included regardless of `skip_osm`. The layer contains:
  - OSM ways with `bridge=yes/viaduct/aqueduct` (anywhere)
  - OSM ways with `tunnel=yes/building_passage` (anywhere)
  - Any OSM way with an endpoint within ~150 m of a bridge/tunnel
    endpoint (the "side road for the cross-then-under maneuver" Paul
    described — to reach a path under a bridge, the router often has
    to cross briefly then loop back on a side road)

This migration adds two boolean columns. The import job (`import_osm_roads.py`)
populates them from OSM tags. Existing rows get `false` defaults; new
imports populate correctly.

# Crouzet invariant

Untouched: GPX traces, heat_edges, and contributors are not modified.
This adds derived columns to the OSM cache table.

# Backfill

No backfill — Paul will re-import the Clapiers bbox tiles to populate
the bridge/tunnel flags locally. Production picks up the flags
incrementally as the next regional PBF imports run (the columns
default to `false` so the routing falls back to the current behavior
in the meantime).

Revision ID: 0053
Revises: 0052
Create Date: 2026-05-31
"""
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE osm_road_edges
        ADD COLUMN IF NOT EXISTS bridge_yes boolean NOT NULL DEFAULT false,
        ADD COLUMN IF NOT EXISTS tunnel_yes boolean NOT NULL DEFAULT false
    """)

    # Partial index for the critical-connector lookup. Sparse data (most
    # ways are neither bridge nor tunnel), so a partial index keeps the
    # index small and the ST_DWithin endpoint expansion cheap.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_osm_road_edges_bridge_tunnel
        ON osm_road_edges USING gist (geometry)
        WHERE bridge_yes OR tunnel_yes
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_osm_road_edges_bridge_tunnel")
    op.execute("ALTER TABLE osm_road_edges DROP COLUMN IF EXISTS tunnel_yes")
    op.execute("ALTER TABLE osm_road_edges DROP COLUMN IF EXISTS bridge_yes")
