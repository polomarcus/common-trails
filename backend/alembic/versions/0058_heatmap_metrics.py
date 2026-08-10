"""Append-only heatmap evolution time-series ``heatmap_metrics``.

WHY: the admin monitoring dashboard needs to answer "is the community
heatmap GROWING over time?" — not just "is it fresh right now?". Level-1
freshness is derivable live from cheap indexed reads (``heat_edges_agg``,
``activities``, ``integration_accounts``); the EVOLUTION view needs
point-in-time snapshots kept forever.

This is a tiny append-only table: one row per heatmap rebuild + one per
daily cron. At friends-beta cadence that's ≲ 2 rows/day, so it stays a
few-KB table for years — reads are ``ORDER BY captured_at DESC LIMIT N``
off the index, never a scan.

Columns mirror ``compute_community_stats`` (the SSOT the home banner
already uses) so the time-series is CONSISTENT with the headline numbers:

* ``activities`` / ``contributors`` / ``network_km`` — verbatim from
  ``compute_community_stats`` (traces / contributors / km).
* ``agg_ways`` — ``COUNT(*)`` of ``heat_edges_agg`` (the ~45-87 k
  pre-aggregated OSM ways; cheap indexed count).
* ``heat_edges`` — the raw ``heat_edges`` row estimate (``pg_class``
  reltuples, same cheap source ``/readyz`` uses — NEVER a live COUNT(*)
  scan of the ~5 M-row table).
* ``grid_fallback_pct`` — NULLABLE: the rebuild snapshot leaves it NULL
  (computing it needs a raw ``heat_edges`` scan, off-limits on the hot
  path); a caller that already holds the number (e.g. an offline audit)
  may pass it in.
* ``source`` — 'rebuild' | 'daily' | free text, so the chart can tell a
  post-rebuild jump from a daily drift point.

DDL-ONLY. Nothing to backfill — the table fills as rebuilds + the daily
cron run. A fresh DB simply shows an empty evolution chart until the
first snapshot lands.

Revision ID: 0058
Revises: 0057
Create Date: 2026-07-12 00:00:00.000000
"""
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE heatmap_metrics (
            id BIGSERIAL PRIMARY KEY,
            captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            heat_edges BIGINT NOT NULL DEFAULT 0,
            agg_ways INTEGER NOT NULL DEFAULT 0,
            activities INTEGER NOT NULL DEFAULT 0,
            contributors INTEGER NOT NULL DEFAULT 0,
            network_km NUMERIC NOT NULL DEFAULT 0,
            grid_fallback_pct NUMERIC NULL,
            source TEXT NOT NULL DEFAULT 'unknown'
        )
    """)
    # The only read pattern is "latest N snapshots" → a DESC index on
    # captured_at serves it as an index-only backwards scan.
    op.execute(
        "CREATE INDEX ix_heatmap_metrics_captured_at "
        "ON heatmap_metrics (captured_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS heatmap_metrics")
