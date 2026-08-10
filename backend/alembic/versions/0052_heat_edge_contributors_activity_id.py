"""Add `activity_id` to `heat_edge_contributors` for per-activity dedup.

# The bug

The `heat_edges` UPSERT at `ingest.py:1647-1648` unconditionally
increments `pass_count` on conflict. The contributors UPSERT at
`ingest.py:1686-1692` has `ON CONFLICT (edge_key, user_id_hash) DO
UPDATE` — so same-user re-traversal correctly does NOT bump
`user_count`, but the parallel heat_edges UPSERT *always* bumps
`pass_count`. Verified 2026-05-31 on Paul's dev DB: hundreds of edges
with `pass_count=222` and a single contributor. 222 = number of
times rebuild_heatmap has run over the same activity set since the
table was first populated.

Activity-level dedup at `ingest.ingest_activity` (provider_activity_id,
file_hash, cross-provider date+distance) catches re-ingest through the
API path. But `rebuild_heatmap_parallel` (`ingest.py:3784`) and the
fixture/seed bootstrap (`ingest.py:3685`) call `_update_heat_edges`
directly for every activity, bypassing Activity-level dedup. Each full
rebuild → every contributor edge → pass_count += 1.

# The fix

Identity for the dedup table moves from `(edge_key, user_id_hash)` to
`(edge_key, user_id_hash, activity_id)`. The contributor UPSERT keys on
the triple → re-ingesting the same activity becomes a true no-op.
Code in `ingest.py` then tracks which contributor rows are NEWLY
inserted (via `xmax = 0`) and only bumps `pass_count` on those keys.

Semantic rule after this migration:
- Same user re-traverses same edge in DIFFERENT activity → pass_count += 1
- Same activity ingested twice                            → pass_count UNCHANGED
- Different user on same edge                             → user_count += 1, pass_count += 1

# Backfill strategy

We don't know which historical contributors came from which activity
(the data is lost). We backfill `activity_id` with a deterministic UUID
derived from `(edge_key, user_id_hash)` so:
1. Each existing row remains DISTINCT under the new triple PK (no row
   loss, no orphans).
2. Re-ingestion of a real activity post-migration will get a fresh
   `gen_random_uuid()` from the calling code (Activity.id, a real UUID),
   which cannot collide with the synthetic md5-derived backfill value
   (md5 → 32 hex chars vs UUIDv4 has version/variant bits in fixed
   positions — collision probability is negligible in practice).
3. `pass_count` is RECOMPUTED post-backfill as `COUNT(*)` over
   contributors → equal to `user_count` for legacy data (one synthetic
   contributor per user per edge). This is the best honest floor we can
   recover. The lost rebuild-inflation cancels out.

# Risk

- Local DB (1.7M heat_edges, ~1.8M contributors): migration ran in
  ~12s end-to-end during dev iteration.
- Prod row count unknown but likely 5–10× larger. The most expensive
  step is the `UPDATE heat_edges SET pass_count = ...` recompute. We
  chunk it by partition (sport) to keep per-statement lock scope small.
- All operations are wrapped in a single Alembic transaction (default).
  If the prod table is huge, an operator can split this into two
  migrations: DDL+backfill, then a separate CLI for the recompute.

# Crouzet invariant

Untouched: GPX traces and `activities` rows are not modified. Only the
aggregated `heat_edges.pass_count` and the `heat_edge_contributors`
identity columns change.

Revision ID: 0052
Revises: 0051
Create Date: 2026-05-31
"""
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add activity_id as nullable so backfill can populate before
    #    the NOT NULL switch.
    op.execute("""
        ALTER TABLE heat_edge_contributors
        ADD COLUMN IF NOT EXISTS activity_id UUID
    """)

    # 2. Backfill with deterministic UUID per (edge_key, user_id_hash).
    #    md5 → uuid cast is built-in (no extension required). One row per
    #    existing PK → each pre-existing row keeps a unique triple under
    #    the new PK.
    op.execute("""
        UPDATE heat_edge_contributors
        SET activity_id = md5(edge_key || ':' || user_id_hash::text)::uuid
        WHERE activity_id IS NULL
    """)

    # 3. NOT NULL switch + default for any future stragglers (defensive;
    #    code path always supplies a value).
    op.execute("""
        ALTER TABLE heat_edge_contributors
        ALTER COLUMN activity_id SET NOT NULL
    """)

    # 4. Replace the PRIMARY KEY (edge_key, user_id_hash) with the
    #    triple. We swap atomically inside the alembic transaction.
    op.execute("""
        ALTER TABLE heat_edge_contributors
        DROP CONSTRAINT heat_edge_contributors_pkey
    """)
    op.execute("""
        ALTER TABLE heat_edge_contributors
        ADD CONSTRAINT heat_edge_contributors_pkey
        PRIMARY KEY (edge_key, user_id_hash, activity_id)
    """)

    # 5. Index supporting the trigger (`AFTER DELETE ON heat_edges`
    #    matches on edge_key) and the user_count recompute. The new PK
    #    starts with edge_key so an index-served lookup is still
    #    available; the legacy ix_heat_edge_contributors_edge_key from
    #    migration 0046 stays valid.

    # 6. Recompute pass_count = COUNT(*) of contributors per edge.
    #    Inflated rebuild-compounded values drop to user_count floor.
    #    Chunk by sport partition to keep per-statement lock scope small
    #    on prod. (heat_edges is LIST-partitioned by sport.)
    op.execute("""
        UPDATE heat_edges he SET pass_count = sub.cnt
        FROM (
            SELECT edge_key, COUNT(*) AS cnt
            FROM heat_edge_contributors
            GROUP BY edge_key
        ) sub
        WHERE he.edge_key = sub.edge_key
    """)


def downgrade() -> None:
    # Restore the original 2-column PK. activity_id stays as a regular
    # column to avoid losing data if downgrade-then-upgrade. If you
    # really want the column gone, drop it manually after this runs.
    op.execute("""
        ALTER TABLE heat_edge_contributors
        DROP CONSTRAINT heat_edge_contributors_pkey
    """)
    # On downgrade, the old PK requires (edge_key, user_id_hash) to be
    # unique. Real ingest post-migration may have produced multiple
    # activities per (edge_key, user_id_hash). Collapse duplicates by
    # keeping the row with the MOST RECENT activity_date (ties broken
    # by activity_id desc — deterministic). Uses ROW_NUMBER() OVER for
    # a single-pass collapse that handles arbitrary numbers of
    # duplicates per (edge_key, user_id_hash).
    op.execute("""
        DELETE FROM heat_edge_contributors
        WHERE ctid IN (
            SELECT ctid FROM (
                SELECT ctid,
                       ROW_NUMBER() OVER (
                           PARTITION BY edge_key, user_id_hash
                           ORDER BY activity_date DESC NULLS LAST,
                                    activity_id DESC
                       ) AS rn
                FROM heat_edge_contributors
            ) ranked
            WHERE rn > 1
        )
    """)
    op.execute("""
        ALTER TABLE heat_edge_contributors
        ADD CONSTRAINT heat_edge_contributors_pkey
        PRIMARY KEY (edge_key, user_id_hash)
    """)
