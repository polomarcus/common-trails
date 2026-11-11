"""Drop the tables of the retired map-matching ("matched") heatmap pipeline.

The community heatmap is built from ``activities`` (raw masked traces) and
``activity_cells`` only. No code reads or writes the tables below any more
(pinned by ``tests/test_matched_pipeline_absent.py``); this removes them:

* ``heat_edges`` — LIST-partitioned by sport (0029/0035); dropping the parent
  drops its partitions (``heat_edges_road`` / ``_gravel`` / ``_mtb`` /
  ``_offroad`` / ``_default``), its indexes, its ``id`` sequence and the
  ``heat_edges_cascade_contributors`` trigger (0046) with it;
* ``heat_edges_cascade_delete_contributors()`` — the trigger FUNCTION (0046),
  dropped after the table that used it;
* ``heat_edges_display`` — materialized view (0032/0033), already dropped by
  0055; kept here as an ``IF EXISTS`` safety net, FIRST, because it depended
  on ``heat_edges``;
* ``heat_edge_contributors``, ``heat_cell_contributors``, ``heat_cells``,
  ``heat_edges_agg`` (0001/0015/0057);
* ``edge_popularity`` (0001), ``edge_version_state`` (0040);
* ``ch_shortcuts``, ``ch_build_status`` (0030);
* ``osm_road_edges`` — LIST-partitioned by region (0056): parent, every
  region partition and the owned ``osm_road_edges_id_seq`` go together —
  ``osm_ways``, ``osm_import_meta`` (0056);
* ``trail_edges`` (0015) — reference data re-importable from OSM;
* ``minigraph_edges`` / ``minigraph_vertices`` and any other ``minigraph_*``
  table — created at RUNTIME by the long-gone pgRouting seed (see 0002),
  never by a migration.

No enum type belongs to these tables (their ``sport`` columns are TEXT;
``sport_enum`` is used by ``routes`` / ``trips`` and stays).

KEPT on purpose: ``activities``, ``activity_cells``, ``dfci_edges``,
``heatmap_metrics`` (including its legacy ``heat_edges`` / ``agg_ways``
COLUMNS, still written by ``jobs/build_pmtiles.py``) and every social table.
No kept table has a foreign key, trigger or view on a dropped table: the only
triggers on ``activities`` are the "Ma heatmap" ones (0068–0070), which touch
``my_heatmap_*`` only.

No ``CASCADE`` anywhere, on purpose: partitions, indexes, owned sequences and
table triggers go with their table without it, and an UNEXPECTED dependent
object (a hand-made view, say) must make the migration fail loudly rather
than be dropped silently. Every DROP is ``IF EXISTS`` — safe on a database
where the objects are already missing (production already lost the
``heat_edges*`` and OSM tables).

Downgrade is a documented NO-OP: the data is derived and not restorable, no
code reads these tables, and re-creating fifteen empty tables (two of them
partitioned) would only produce a schema nothing uses.

Revision ID: 0072
Revises: 0071
"""

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None

# Order matters only for readability: none of these tables references
# another through a real FOREIGN KEY (the contributors → edges link was a
# trigger precisely because ``heat_edges`` is partitioned).
DROPPED_TABLES = (
    "heat_edge_contributors",
    "heat_cell_contributors",
    "heat_edges_agg",
    "heat_edges",
    "heat_cells",
    "edge_popularity",
    "edge_version_state",
    "ch_shortcuts",
    "ch_build_status",
    "osm_import_meta",
    "osm_road_edges",
    "osm_ways",
    "trail_edges",
    "minigraph_edges",
    "minigraph_vertices",
)


def upgrade() -> None:
    # DROP TABLE takes an ACCESS EXCLUSIVE lock on each dropped table (never
    # on a kept one: no foreign key links them). Nothing reads these tables,
    # but give up after 5 s rather than queue behind a stray session: the
    # container entrypoint retries the migration.
    op.execute("SET LOCAL lock_timeout = '5s'")

    op.execute("DROP MATERIALIZED VIEW IF EXISTS heat_edges_display")
    for table in DROPPED_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    # Any other runtime-created ``minigraph_*`` table (e.g. the
    # ``minigraph_edges_vertices_pgr`` that pgr_createTopology generates).
    op.execute(
        r"""
        DO $$
        DECLARE t text;
        BEGIN
          FOR t IN
            SELECT tablename FROM pg_tables
             WHERE schemaname = current_schema() AND tablename LIKE 'minigraph\_%'
             ORDER BY tablename
          LOOP
            EXECUTE format('DROP TABLE IF EXISTS %I', t);
          END LOOP;
        END $$;
        """
    )
    # After ``heat_edges``: its trigger (gone with the table) used it.
    op.execute("DROP FUNCTION IF EXISTS heat_edges_cascade_delete_contributors()")
    # Owned by ``osm_road_edges.id`` so normally already gone with the table.
    op.execute("DROP SEQUENCE IF EXISTS osm_road_edges_id_seq")


def downgrade() -> None:
    # Intentionally a no-op — see the module docstring.
    pass
