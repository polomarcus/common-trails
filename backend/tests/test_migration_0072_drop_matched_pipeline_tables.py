"""Migration 0072 — the tables of the retired map-matching pipeline are gone.

The suite runs against a database at ``alembic upgrade head``: the legacy
tables (and the trigger function / sequence that belonged to them) must be
absent, what the raw pipeline needs must still be there, and nothing left in
the schema may depend on a dropped table. The real ``upgrade()`` is then
replayed inside a rolled-back transaction: on stand-ins of the legacy objects
(partitions, trigger, sequence — it must remove them all without CASCADE) and
on a database where they are already missing (it must be a no-op).
"""
import importlib.util
import sys
from pathlib import Path

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text as sa_text

from app.db.models import Base
from app.db.session import engine

LEGACY_TABLES = (
    "heat_edges", "heat_edges_road", "heat_edges_gravel", "heat_edges_mtb",
    "heat_edges_offroad", "heat_edges_default", "heat_edge_contributors",
    "heat_edges_agg", "heat_cells", "heat_cell_contributors", "edge_popularity",
    "edge_version_state", "ch_shortcuts", "ch_build_status", "osm_road_edges",
    "osm_road_edges_default", "osm_ways", "osm_import_meta", "trail_edges",
    "minigraph_edges", "minigraph_vertices", "minigraph_edges_vertices_pgr",
)
KEPT_TABLES = ("activities", "activity_cells", "dfci_edges", "heatmap_metrics", "users", "routes")


def _migration():
    _real_alembic()  # the migration does ``from alembic import op`` at import time
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0072_drop_matched_pipeline_tables.py"
    spec = importlib.util.spec_from_file_location("migration_0072", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _real_alembic():
    """The installed ``alembic`` package. Under pytest the working directory
    (``backend/``) is on ``sys.path`` and its ``alembic/`` folder shadows the
    package — import the real one with that entry set aside, after evicting
    the shadow if something already imported it."""
    saved = sys.path[:]
    local = str(Path(__file__).resolve().parents[1] / "alembic")
    try:
        sys.path[:] = [p for p in sys.path if not (Path(p or ".") / "alembic" / "versions").is_dir()]
        for name in [m for m in sys.modules if m == "alembic" or m.startswith("alembic.")]:
            origin = getattr(sys.modules[name], "__file__", None)
            if origin is None or str(Path(origin).resolve()).startswith(local):
                del sys.modules[name]
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
    finally:
        sys.path[:] = saved
    return MigrationContext, Operations


def _relations(conn) -> set[str]:
    """Every table, partition, view, matview and sequence of the schema."""
    return {
        r[0]
        for r in conn.execute(
            sa_text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p', 'v', 'm', 'S')"
            )
        )
    }


def _legacy_leftovers(conn) -> list[str]:
    rels = _relations(conn)
    left = [t for t in LEGACY_TABLES if t in rels]
    left += [r for r in rels if r.startswith(("minigraph_", "heat_edges", "osm_road_edges"))]
    if conn.execute(
        sa_text("SELECT count(*) FROM pg_proc WHERE proname = 'heat_edges_cascade_delete_contributors'")
    ).scalar():
        left.append("heat_edges_cascade_delete_contributors()")
    return sorted(set(left))


def test_migration_chain_and_table_list():
    mod = _migration()
    assert (mod.revision, mod.down_revision) == ("0072", "0071")
    # Every table the migration names is one this test checks, and none is kept.
    assert set(mod.DROPPED_TABLES) <= set(LEGACY_TABLES)
    assert not set(mod.DROPPED_TABLES) & set(KEPT_TABLES)


def test_legacy_tables_are_absent_and_kept_tables_present():
    with engine.connect() as conn:
        assert _legacy_leftovers(conn) == []
    tables = set(sa_inspect(engine).get_table_names())
    for table in KEPT_TABLES:
        assert table in tables, f"{table} must survive 0072"
        assert table in Base.metadata.tables
    # The legacy COLUMNS of heatmap_metrics stay: build_pmtiles still writes them.
    columns = {c["name"] for c in sa_inspect(engine).get_columns("heatmap_metrics")}
    assert {"heat_edges", "agg_ways"} <= columns


def test_every_orm_model_has_its_table():
    """Migrations are the only way the schema is created (``start-web.sh`` runs
    ``alembic upgrade head``; nothing calls ``create_all``): every table the
    models declare must exist in the migrated database."""
    tables = set(sa_inspect(engine).get_table_names())
    assert set(Base.metadata.tables) <= tables, sorted(set(Base.metadata.tables) - tables)


def test_nothing_left_in_the_schema_depends_on_a_dropped_table():
    """No function body or view may mention a dropped table, and the triggers
    on ``activities`` are exactly the "Ma heatmap" ones — a trigger writing
    ``heat_edge_contributors`` would make every upload fail once the table is
    gone."""
    names = (
        "heat_edge|heat_cell|edge_popularity|edge_version_state|ch_shortcuts|ch_build_status|"
        "osm_road_edges|osm_ways|osm_import_meta|trail_edges|minigraph"
    )
    with engine.connect() as conn:
        functions = conn.execute(
            sa_text(
                "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = current_schema() AND p.prolang IN "
                "(SELECT oid FROM pg_language WHERE lanname IN ('plpgsql', 'sql')) "
                "AND p.prosrc ~* :names"
            ),
            {"names": names},
        ).fetchall()
        views = conn.execute(
            sa_text(
                "SELECT viewname FROM pg_views WHERE schemaname = current_schema() AND definition ~* :names "
                "UNION ALL SELECT matviewname FROM pg_matviews "
                "WHERE schemaname = current_schema() AND definition ~* :names"
            ),
            {"names": names},
        ).fetchall()
        triggers = conn.execute(
            sa_text(
                "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE NOT t.tgisinternal AND c.relname = 'activities' ORDER BY 1"
            )
        ).fetchall()
    assert functions == [] and views == []
    assert [t[0] for t in triggers] == [
        "activities_my_heatmap_del", "activities_my_heatmap_ins", "activities_my_heatmap_upd",
    ]


# Stand-ins with the SHAPE that matters for a DROP: partitions, the 0046
# trigger + function, the 0056 owned sequence, runtime minigraph tables.
_LEGACY_DDL = """
CREATE TABLE heat_edges (id BIGSERIAL, edge_key TEXT NOT NULL, sport TEXT NOT NULL,
                         UNIQUE (edge_key, sport)) PARTITION BY LIST (sport);
CREATE TABLE heat_edges_road PARTITION OF heat_edges FOR VALUES IN ('road');
CREATE TABLE heat_edges_gravel PARTITION OF heat_edges FOR VALUES IN ('gravel');
CREATE TABLE heat_edges_mtb PARTITION OF heat_edges FOR VALUES IN ('mtb');
CREATE TABLE heat_edges_offroad PARTITION OF heat_edges FOR VALUES IN ('offroad');
CREATE TABLE heat_edges_default PARTITION OF heat_edges DEFAULT;
CREATE TABLE heat_edge_contributors (edge_key TEXT, user_id_hash BIGINT, activity_id UUID,
                                     PRIMARY KEY (edge_key, user_id_hash, activity_id));
CREATE FUNCTION heat_edges_cascade_delete_contributors() RETURNS TRIGGER LANGUAGE plpgsql AS $f$
BEGIN DELETE FROM heat_edge_contributors WHERE edge_key = OLD.edge_key; RETURN OLD; END; $f$;
CREATE TRIGGER heat_edges_cascade_contributors AFTER DELETE ON heat_edges
    FOR EACH ROW EXECUTE FUNCTION heat_edges_cascade_delete_contributors();
CREATE TABLE heat_edges_agg (osm_way_id BIGINT, sport TEXT, PRIMARY KEY (osm_way_id, sport));
CREATE TABLE heat_cells (id BIGSERIAL PRIMARY KEY, cell_key VARCHAR(32));
CREATE TABLE heat_cell_contributors (cell_key TEXT, sport TEXT, user_id_hash BIGINT);
CREATE TABLE edge_popularity (id BIGSERIAL PRIMARY KEY, edge_id BIGINT UNIQUE);
CREATE TABLE edge_version_state (id SMALLINT PRIMARY KEY, version BIGINT NOT NULL DEFAULT 0);
CREATE TABLE ch_shortcuts (id BIGSERIAL PRIMARY KEY, sport TEXT);
CREATE TABLE ch_build_status (sport TEXT PRIMARY KEY);
CREATE TABLE osm_ways (osm_way_id BIGINT PRIMARY KEY);
CREATE SEQUENCE osm_road_edges_id_seq;
CREATE TABLE osm_road_edges (id BIGINT NOT NULL DEFAULT nextval('osm_road_edges_id_seq'),
                             region TEXT NOT NULL DEFAULT 'adhoc', PRIMARY KEY (region, id))
    PARTITION BY LIST (region);
ALTER SEQUENCE osm_road_edges_id_seq OWNED BY osm_road_edges.id;
CREATE TABLE osm_road_edges_default PARTITION OF osm_road_edges DEFAULT;
CREATE TABLE osm_import_meta (region TEXT PRIMARY KEY);
CREATE TABLE trail_edges (id BIGSERIAL PRIMARY KEY, trail_type TEXT NOT NULL);
CREATE TABLE minigraph_vertices (id BIGSERIAL PRIMARY KEY);
CREATE TABLE minigraph_edges (id BIGSERIAL PRIMARY KEY, source BIGINT, target BIGINT);
CREATE TABLE minigraph_edges_vertices_pgr (id BIGSERIAL PRIMARY KEY);
INSERT INTO heat_edges (edge_key, sport) VALUES ('k', 'road'), ('k2', 'running');
INSERT INTO edge_version_state (id, version) VALUES (1, 3);
"""


def test_upgrade_drops_the_legacy_objects_and_is_idempotent():
    """Replays the real ``upgrade()`` in ONE rolled-back transaction (DDL is
    transactional in Postgres): once on a database where the legacy objects
    are already missing (production already lost half of them), once on
    re-created stand-ins, once more to prove the second run is a no-op."""
    MigrationContext, Operations = _real_alembic()
    mod = _migration()
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            kept_before = {t: conn.execute(sa_text(f"SELECT count(*) FROM {t}")).scalar() for t in KEPT_TABLES}
            relations_before = _relations(conn)
            with Operations.context(MigrationContext.configure(conn)):
                mod.upgrade()  # nothing to drop: must not fail
                assert _relations(conn) == relations_before

                conn.execute(sa_text(_LEGACY_DDL))
                assert set(LEGACY_TABLES) <= _relations(conn)
                mod.upgrade()
                assert _legacy_leftovers(conn) == []
                assert _relations(conn) == relations_before  # nothing else went

                mod.upgrade()  # already gone again: no-op
                mod.downgrade()  # documented no-op: brings nothing back
                assert _relations(conn) == relations_before
            for table, count in kept_before.items():
                assert conn.execute(sa_text(f"SELECT count(*) FROM {table}")).scalar() == count
        finally:
            trans.rollback()
