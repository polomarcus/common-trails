"""No-op marker for the `match_source 'grid' → 'grid_fallback'` rename.

Audit 2026-05-29 SP-S1-3. The grid-snap fallback path in
`_update_heat_edges` was writing `match_source = "grid"` while every
other reference in the codebase / docs / CLAUDE.md / dashboards
uses `"grid_fallback"`. The accompanying code change in this PR
flips the writer at `ingest.py:_update_heat_edges` to emit
`"grid_fallback"`.

**This migration is a no-op by design.** The naive backfill
(`UPDATE heat_edges SET match_source = 'grid_fallback' WHERE
match_source = 'grid'`) would touch ~3.5M+ rows in a single
transaction with no index on `match_source` (verified — no
migration has ever indexed the column). On a db-f1-micro that's a
multi-minute full-partition sequential scan holding row-exclusive
locks against every concurrent `_update_heat_edges` writer.

The cost-benefit doesn't justify it:
- No reader in the codebase filters on `match_source = 'grid'`
  today. PMTiles uses `osm_way_id IS NULL`, `heat_quality` uses
  `grid_fallback_ratio` computed from `osm_way_id` too, admin uses
  the same pattern.
- Existing `'grid'` rows are harmless until the next heat_edges
  rebuild (regularly scheduled — see CLAUDE.md "Recent feature PRs"
  for the rebuild cadence).
- The future Sentry tag / dashboard filter on `'grid_fallback'`
  will pick up new writes immediately; old rows can be backfilled
  out-of-band via a chunked CLI when desired.

PR #353 code review (S2) flagged the naive backfill as a real
db-f1-micro risk — this rewrite captures the safer trade-off.

Keeping this file as a no-op rather than deleting it preserves the
Alembic chain numbering (0049 → 0050 → 0051+) and gives a clear
historical marker. Future operator who wants to backfill old rows
should do it via a chunked CLI script, NOT a single-transaction
UPDATE.

Revision ID: 0050
Revises: 0049
Create Date: 2026-05-29
"""

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Intentionally no-op. See docstring for rationale.
    pass


def downgrade() -> None:
    # Intentionally no-op.
    pass
