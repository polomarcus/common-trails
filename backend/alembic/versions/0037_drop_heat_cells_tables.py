"""Drop heat_cells and heat_cell_contributors — DEFERRED, do NOT run yet.

Background
----------
The May 2026 ingestion audit (improvement #4) replaced the dedicated
``heat_cells`` write path with on-read aggregation from ``heat_edges``
(see ``app/services/ingest.get_heat_cells_aggregated``). The legacy
write was costing ~30% of per-activity ingest time for no observable
read-side benefit.

The PR that added this file (``perf/drop-heat-cells-aggregate-on-read``)
intentionally **does not enable** this migration. It is staged here so
the drop is one ``alembic upgrade head`` away once the new aggregate
path has soaked in production.

When to run
-----------
Run **after** the new aggregate-on-read path has soaked in production
for at least one full heatmap rebuild cycle. Concretely:

1. Deploy the PR that introduces ``get_heat_cells_aggregated``.
2. Watch ``/heatmap/export`` and ``/heatmap/stats`` for one prod week:
   - Aggregation latency p99 < 5 s (target — see PR description).
   - GeoJSON byte size per request stays within 2x of the pre-PR
     baseline (cell counts may drift slightly, see "semantics" below).
3. Run a full ``rebuild_heatmap`` job to confirm the new path produces
   the same data without the legacy table.
4. Then uncomment the DROP statements below and ``alembic upgrade``
   to ``0037`` to actually retire the dead tables.

If anything regresses during the soak, revert the ``ingest_activity``
and ``_process_activity`` commented-out ``_update_heat_cells`` calls
(one-line uncomment each) — the legacy table is still there and will
start receiving writes again.

Semantics drift
---------------
The on-read aggregation computes ``pass_count`` as
``SUM(heat_edges.pass_count)`` over edges starting in the tile, while
the legacy ``heat_cells.pass_count`` was incremented +1 per activity
ingest. Both are heat-density signals; the new one is strictly more
granular. ``user_count`` semantics are unchanged
(``COUNT(DISTINCT user_id_hash)`` per cell).

Revision ID: 0037
Revises: 0036
"""
# NOTE: `from alembic import op` intentionally omitted — body is a
# no-op `pass` while this migration is deferred. Re-add when activating
# the actual DROP TABLE statements (see upgrade() comment).

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─────────────────────────────────────────────────────────────────
    # DEFERRED — DO NOT ENABLE YET.
    #
    # The drop is intentionally a no-op for now. After the on-read
    # aggregation in ``get_heat_cells_aggregated`` has soaked in
    # production for one rebuild cycle, uncomment the two DROP lines
    # below to actually retire the tables.
    #
    # Keeping the migration as a placeholder lets the alembic chain
    # advance to 0037 in CI/dev so the next migration (0038+) can build
    # on a deterministic head, while the legacy tables stay untouched
    # in prod until we flip the switch.
    # ─────────────────────────────────────────────────────────────────
    # op.execute("DROP TABLE IF EXISTS heat_cell_contributors")
    # op.execute("DROP TABLE IF EXISTS heat_cells CASCADE")
    pass


def downgrade() -> None:
    # Mirror of upgrade: a no-op until the DROPs above are enabled. If
    # you uncomment the upgrade DROPs in production, also uncomment the
    # CREATEs below so a downgrade restores empty tables for replay.
    # op.execute('''
    #     CREATE TABLE heat_cells (
    #         id BIGSERIAL PRIMARY KEY,
    #         cell_key VARCHAR(32) NOT NULL,
    #         zoom INTEGER NOT NULL DEFAULT 14,
    #         sport VARCHAR(50) NOT NULL DEFAULT 'road',
    #         user_count INTEGER NOT NULL DEFAULT 0,
    #         pass_count INTEGER NOT NULL DEFAULT 0,
    #         UNIQUE (cell_key, sport)
    #     )
    # ''')
    # op.execute(
    #     "CREATE INDEX ix_heat_cells_cell_key ON heat_cells (cell_key)"
    # )
    # op.execute('''
    #     CREATE TABLE heat_cell_contributors (
    #         cell_key TEXT NOT NULL,
    #         sport TEXT NOT NULL,
    #         user_id_hash BIGINT NOT NULL,
    #         PRIMARY KEY (cell_key, sport, user_id_hash)
    #     )
    # ''')
    pass
