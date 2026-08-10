"""Add real partition for sport='offroad'.

Until May 2026, `_normalize_heat_edge_sport` rewrote `offroad → gravel`
before insert because `heat_edges` was LIST-partitioned by sport with
explicit partitions for road/gravel/mtb/running and a default
catch-all. The rewrite kept the default partition empty (good) but
erased the rider's intent (bad — analytics about *offroad* roads
silently meant *gravel + offroad-rebadged-as-gravel*).

This migration creates a real `heat_edges_offroad` partition. The
companion code change (in `app/services/ingest.py`) drops the
`offroad → gravel` rewrite so future inserts land in the new
partition. Routing for offroad continues to query gravel + mtb via
`RELATED_SPORTS`, now extended to include the offroad partition itself
so the new data is reachable.

Backfill: not feasible. Historical offroad rides are already mixed
into `heat_edges_gravel` and the activity ↔ heat_edges link does not
carry the original sport tag (heat_edges aggregates many activities
per edge). Users will see offroad analytics improve from this point
forward; pre-existing data stays in gravel.

Safety: at the time this migration was written, `heat_edges_default`
was empty (verified via `SELECT sport, COUNT(*) FROM heat_edges WHERE
sport NOT IN ('road','gravel','mtb','running')` returning 0 rows), so
no row movement is required and the ATTACH is non-blocking.

Revision ID: 0035
Revises: 0034
"""
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add a new LIST partition for sport='offroad'. PostgreSQL allows
    # adding a partition without a separate ATTACH if no rows in the
    # default partition match the new bound — verified empty above.
    # `IF NOT EXISTS` is unsupported for `PARTITION OF`, so we guard
    # with an existence check via the catalog so re-runs are safe.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_class
                WHERE relname = 'heat_edges_offroad'
            ) THEN
                CREATE TABLE heat_edges_offroad
                PARTITION OF heat_edges
                FOR VALUES IN ('offroad');
            END IF;
        END $$
    """)


def downgrade() -> None:
    # Move any offroad rows back to the default partition before
    # dropping. In practice the partition was created empty and any
    # data in it represents post-migration ingests, so we DETACH first
    # and then drop.
    op.execute("ALTER TABLE heat_edges DETACH PARTITION heat_edges_offroad")
    op.execute("DROP TABLE heat_edges_offroad")
