"""Cascade-delete heat_edge_contributors when a heat_edges row is deleted.

`heat_edge_contributors` had no foreign-key relationship to `heat_edges`.
Deleting a heat edge (manual prod cleanup, CLI tools like migrate_resegment,
test teardown) left orphan contributor rows. Tests worked around this by
wiping both tables together, but the schema was the wrong shape:

1. **K-anonymity correctness** — `user_count` is recomputed by
   `COUNT(DISTINCT user_id_hash)` joining heat_edge_contributors. Orphan
   rows from re-keyed/deleted edges could be picked up by a future edge
   created with the same edge_key (same physical road snapping to the
   same canonical key), inflating user_count and falsifying K.
2. **Test isolation** — fixtures had to wipe both tables; forgetting one
   created cross-test pollution that surfaced as phantom pass_count /
   user_count on freshly-inserted rows (the bug behind PR #282).

**Why trigger, not FOREIGN KEY?** `heat_edges` is partitioned by sport
(migration 0029) and PostgreSQL requires the partition key (`sport`) to
be part of any unique constraint on a partitioned table. The only unique
constraint is `(edge_key, sport)`. `heat_edge_contributors` has no
`sport` column, and adding one is a multi-table ingest refactor out of
scope here. A row-level `AFTER DELETE` trigger on `heat_edges` (which
propagates to all child partitions in PG ≥ 11) delivers the same
cascade-delete semantics at the schema level.

The trigger matches on `edge_key` alone. This is safe because edge_key
values encode sport as their prefix (`{sport}/{a}/{b}` — see
`_edge_key` in services/ingest.py), so collisions across partitions are
impossible by construction.

Revision ID: 0046
Revises: 0045
Create Date: 2026-05-18
"""

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Clean up any pre-existing orphans. One-shot; bounded by the
    #    current orphan count (handful in practice — most rebuilds use
    #    TRUNCATE on both tables together).
    op.execute("""
        DELETE FROM heat_edge_contributors c
        WHERE NOT EXISTS (
            SELECT 1 FROM heat_edges e WHERE e.edge_key = c.edge_key
        )
    """)

    # 2. Cascade-delete trigger function. Matches on edge_key alone —
    #    safe because edge_key embeds sport (see migration docstring).
    op.execute("""
        CREATE OR REPLACE FUNCTION heat_edges_cascade_delete_contributors()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
            DELETE FROM heat_edge_contributors
            WHERE edge_key = OLD.edge_key;
            RETURN OLD;
        END;
        $$
    """)

    # 3. AFTER DELETE trigger on the partitioned parent. PG ≥ 11
    #    propagates row-level triggers to all child partitions
    #    automatically, including partitions created in the future.
    op.execute("""
        CREATE TRIGGER heat_edges_cascade_contributors
        AFTER DELETE ON heat_edges
        FOR EACH ROW
        EXECUTE FUNCTION heat_edges_cascade_delete_contributors()
    """)

    # 4. Index on the FK-like column. heat_edge_contributors PK is
    #    (edge_key, user_id_hash) which already gives us a btree on
    #    (edge_key, ...), so the trigger's lookup is index-served.
    #    No new index needed — verified below.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'heat_edge_contributors'
                  AND indexdef ILIKE '%(edge_key%'
            ) THEN
                CREATE INDEX ix_heat_edge_contributors_edge_key
                    ON heat_edge_contributors (edge_key);
            END IF;
        END
        $$
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS heat_edges_cascade_contributors ON heat_edges")
    op.execute("DROP FUNCTION IF EXISTS heat_edges_cascade_delete_contributors()")
    op.execute("DROP INDEX IF EXISTS ix_heat_edge_contributors_edge_key")
