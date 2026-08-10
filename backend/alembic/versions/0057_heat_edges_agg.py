"""Incremental by-OSM-way aggregate table ``heat_edges_agg``.

WHY: the display pipeline (static ``heatmap-display.pmtiles`` build + the
live ``/heatmap/tiles`` MVT fallback) aggregates ``heat_edges`` by
``(osm_way_id, sport)`` at build/request time — the heavy GROUP BY +
``ST_LineMerge(ST_Collect(...))`` over ~5 M rows. On ``db-f1-micro``
(0.6 GB) the whole-world PMTiles build OOMs in Postgres executing that
aggregation. ``heat_edges_agg`` pre-materialises the OSM-matched half of
that aggregation so the readers do a cheap indexed SELECT instead.

This is NOT the ``heat_edges_display`` matview that migration 0055 dropped:
that matview did a FULL REFRESH on every ingest (5–15 min, 27 min pre-warm).
``heat_edges_agg`` is maintained INCREMENTALLY — ``ingest._update_heat_edges``
recomputes-from-source ONLY the ``(osm_way_id, sport)`` rows an activity
touched (idempotent + concurrency-safe under the 4-worker rebuild). The one
heavy full aggregation runs exactly once, on demand, via
``python -m app.jobs.rebuild_heat_agg`` (or at the end of a full
``rebuild_heatmap``) — that pass needs a DB tier bump in prod, run once.

Semantics (SSOT ``app/services/heat_aggregation.py``, unchanged):
* ``user_count`` / ``pass_count`` / ``forward_count`` / ``backward_count``
  → **MAX** over a way's ~11 m sub-edges (the busiest sub-segment; a SUM
  multi-counts one ride across the way and inflates the tooltip). PRESERVED.
* ``user_count`` is stored **RAW** (aggregated at ``min_uc=1`` — every
  contributing edge included). The K-anonymity floor is applied at READ
  time (``WHERE user_count >= min_uc``) so the table stays K-agnostic and
  one materialisation serves any K. In THIS single-user (K=1) instance
  read == build_heat_aggregation_sql(min_uc=1) exactly; for K>1 the read
  filters the stored MAX user_count (privacy-safe: a way surfaces only if
  its busiest sub-segment had >= K distinct riders — see the module
  docstring's ``build_agg_read_sql`` note).

GRID-FALLBACK CHOICE (documented, deliberate): grid-fallback edges
(``osm_way_id IS NULL``) are NOT stored here. They are per-edge (no
GROUP BY to precompute), length-capped, and ~0 % for this user / <0.4 %
globally. The readers UNION the agg table (the heavy OSM-matched half)
with a LIVE, cheap ``SELECT`` of grid-fallback edges straight from
``heat_edges`` (no aggregation → never OOMs; bbox-bounded on the MVT
path; dropped entirely on the prod ``drop_grid_fallback=True`` PMTiles
build). The agg table's PK is therefore a clean ``(osm_way_id, sport)``
with ``osm_way_id NOT NULL``.

``updated_at`` is refreshed on every per-way recompute — it powers the
``/readyz`` freshness field, the ``verify_heat_agg`` drift check, and a
future "pmtiles stale > N days" alert.

DDL-ONLY — NO DATA MIGRATION. ``heat_edges_agg`` is DERIVED from
``heat_edges``; populate it with ``python -m app.jobs.rebuild_heat_agg``
after ``alembic upgrade head`` (a fresh DB starts empty → the readers
return only live grid-fallback until the backfill runs).

Revision ID: 0057
Revises: 0056
Create Date: 2026-07-11 00:00:00.000000
"""
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # geometry is geometry(Geometry, 4326) (not LineString) because the
    # aggregated geometry is USUALLY the OSM way's LineString but falls back
    # to ST_LineMerge(ST_Collect(...)) which yields a MultiLineString when
    # the merged sub-edges are disjoint (local dev without the OSM PBF).
    # ST_AsGeoJSON reproduces either exactly → byte-equivalent reads.
    op.execute("""
        CREATE TABLE heat_edges_agg (
            osm_way_id BIGINT NOT NULL,
            sport TEXT NOT NULL,
            geometry geometry(Geometry, 4326) NOT NULL,
            user_count INTEGER NOT NULL DEFAULT 0,
            pass_count INTEGER NOT NULL DEFAULT 0,
            forward_count INTEGER NOT NULL DEFAULT 0,
            backward_count INTEGER NOT NULL DEFAULT 0,
            highway_type TEXT NOT NULL DEFAULT 'unknown',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (osm_way_id, sport)
        )
    """)
    # GIST for the MVT bbox && predicate; (sport) for the sport-filtered
    # reads. The PK covers the (osm_way_id, sport) upsert conflict target.
    op.execute("CREATE INDEX ix_heat_edges_agg_geometry ON heat_edges_agg USING GIST (geometry)")
    op.execute("CREATE INDEX ix_heat_edges_agg_sport ON heat_edges_agg (sport)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS heat_edges_agg")
