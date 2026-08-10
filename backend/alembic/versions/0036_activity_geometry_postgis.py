"""Add PostGIS geometry column to activities (parallel to geometry_geojson).

Why
---
Per the May 2026 ingest audit, `activities.geometry_geojson` (TEXT) is the
single largest column in the database. Storing a LineString as JSON text
costs ~3× the bytes of the equivalent PostGIS WKB representation, on top
of forcing every reader to call `json.loads(...)` to do anything useful
with it. On Cloud SQL db-f1-micro this matters for both storage and CPU.

This migration is the **additive half** of a two-step cutover (Option A
in the May 2026 plan):

1. (this migration) Add `geometry geometry(LineString, 4326)` alongside
   the existing TEXT column, backfill from `ST_GeomFromGeoJSON(...)`,
   index it. The TEXT column stays in place — readers continue to work.
2. (future migration) Once `ingest_activity()` dual-writes both columns
   in production for long enough that the legacy column is no longer
   needed, drop `geometry_geojson` and switch readers to
   `ST_AsGeoJSON(geometry)::text` or direct geometry usage.

Idempotency
-----------
- ADD COLUMN guarded with IF NOT EXISTS.
- Backfill skips rows where `geometry IS NOT NULL` (re-run safe) and
  rows where `ST_GeomFromGeoJSON` would throw on malformed input
  (caught per-row via a PL/pgSQL EXCEPTION block, logged via NOTICE).
- Spatial index created with IF NOT EXISTS.

Backfill safety
---------------
- NULL `geometry_geojson` → row skipped (geometry stays NULL).
- Non-LineString GeoJSON or malformed JSON → row skipped, NOTICE logged.
  The migration does not fail because a handful of activities have bad
  geometry — those will simply have NULL geometry until manually fixed
  or the next reingest overwrites them.

Dimensions
----------
GPX-imported activities can carry a per-point elevation in the third
coordinate slot — `[lon, lat, ele]`. The TEXT column stored those
verbatim (3D). The new binary column is 2D-only (`geometry(LineString,
4326)`) because:
- Most queries (bbox filter, snap, length) don't need Z.
- Mixed 2D/3D rows would force a typmod-less geometry, losing the cheap
  index-friendly type guarantee.
- Elevation is *already* persisted in the legacy TEXT column and
  recomputed on demand from DEM enrichment when needed.

The backfill therefore wraps the parse in `ST_Force2D(...)` to drop the
Z component before insert. Going forward the writer uses the same
helper (`app/services/ingest._geom_from_geojson_sql`) which emits the
same `ST_SetSRID(ST_Force2D(ST_GeomFromGeoJSON(...)), 4326)` cast.

Revision ID: 0036
Revises: 0035
Create Date: 2026-05-02 00:00:00.000000
"""
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add the geometry column. Nullable for backfill + for activities
    #    that legitimately lack a track (manual entries, etc.).
    op.execute("""
        ALTER TABLE activities
        ADD COLUMN IF NOT EXISTS geometry geometry(LineString, 4326)
    """)

    # 2. Backfill from existing geometry_geojson. Per-row EXCEPTION handler
    #    so one malformed payload doesn't abort the whole migration.
    #    NOTICE-logs the offending activity_id for follow-up.
    #    `ST_Force2D` drops the optional Z (elevation) coordinate so the
    #    2D typmod accepts mixed 2D/3D source data.
    op.execute("""
        DO $$
        DECLARE
            r RECORD;
            bad_count INT := 0;
            ok_count INT := 0;
        BEGIN
            FOR r IN
                SELECT id, geometry_geojson
                FROM activities
                WHERE geometry IS NULL
                  AND geometry_geojson IS NOT NULL
            LOOP
                BEGIN
                    UPDATE activities
                    SET geometry = ST_SetSRID(
                        ST_Force2D(ST_GeomFromGeoJSON(r.geometry_geojson)),
                        4326
                    )
                    WHERE id = r.id
                      AND ST_GeometryType(
                            ST_GeomFromGeoJSON(r.geometry_geojson)
                          ) = 'ST_LineString';
                    ok_count := ok_count + 1;
                EXCEPTION WHEN OTHERS THEN
                    bad_count := bad_count + 1;
                    RAISE NOTICE 'Skipping activity % (geometry parse failed): %',
                        r.id, SQLERRM;
                END;
            END LOOP;
            RAISE NOTICE 'Backfill done: % migrated, % skipped',
                ok_count, bad_count;
        END $$
    """)

    # 3. GIST spatial index. Spatial queries (bbox filter for /me/activities,
    #    nearest-neighbour for sidebar proximity sort) will benefit
    #    immediately even before readers move off the JSON column.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_activities_geometry
        ON activities USING GIST (geometry)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_activities_geometry")
    op.execute("ALTER TABLE activities DROP COLUMN IF EXISTS geometry")
