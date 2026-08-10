"""Non-regression: the last live readers of ``heat_edges`` / ``heat_edges_agg``
must NOT 500 when those LEGACY tables are ABSENT (raw-trace pivot 2026-07-29 —
the community map is the static raw PMTiles; these read models are dropped in
prod). Each test drives the REAL handler/function with its DB dependency faked
so the exact "relation does not exist" failure is reproduced (no live DB needed
→ runs in CI too). Every test FAILS on the pre-fix code and PASSES on the fix,
except the two ``/admin`` panels which were already fail-soft (kept here as
regression PINS so they can't silently start 500-ing).
"""
import pytest


class _Result:
    """Minimal stand-in for a SQLAlchemy Result."""

    def __init__(self, scalar=None, first=None):
        self._scalar = scalar
        self._first = first

    def scalar(self):
        return self._scalar

    def first(self):
        return self._first

    def fetchall(self):
        return []


def _sql(stmt) -> str:
    return str(getattr(stmt, "text", stmt))


class _FakeDBTablesAbsent:
    """Fake session where ``heat_edges`` + ``heat_edges_agg`` are DROPPED.

    ``information_schema`` existence probes return False for them; any direct
    query against them raises (as Postgres would on a missing relation).
    Everything else answers benignly so the handler's OTHER probes proceed.
    """

    def __init__(self):
        self.rolled_back = False

    def execute(self, stmt, params=None):
        sql = _sql(stmt)
        if "information_schema.tables" in sql:
            name = (params or {}).get("n", "")
            # Both legacy tables report ABSENT.
            return _Result(scalar=name not in ("heat_edges", "heat_edges_agg"))
        if "heat_edges_agg" in sql or "FROM heat_edges" in sql or "heat_edges he" in sql:
            raise Exception('relation "heat_edges" does not exist')
        # SET LOCAL / SELECT 1 / small unrelated probes.
        return _Result(scalar=1, first=None)

    def rollback(self):
        self.rolled_back = True

    def query(self, *a, **k):  # admin uses db.query(...) too
        raise Exception('relation "heat_edges" does not exist')


# --------------------------------------------------------------------------
# 1. /readyz — must return 200 (warming), NOT 503, when heat_edges is absent.
# --------------------------------------------------------------------------
def test_readyz_tolerates_absent_heat_edges():
    from app.api.health import readyz

    resp = readyz(db=_FakeDBTablesAbsent())  # real handler, faked deps

    assert resp.status == "warming"
    assert resp.heat_edges == 0
    assert resp.heat_edges_agg == 0
    assert resp.heat_edges_agg_updated_at is None


def test_readyz_still_503_on_real_db_down():
    """Guardrail: the tolerance must NOT swallow a genuine connectivity loss."""
    from fastapi import HTTPException

    from app.api.health import readyz

    class _DeadDB:
        def execute(self, *a, **k):
            raise Exception("connection refused")

        def rollback(self):
            pass

    with pytest.raises(HTTPException) as exc:
        readyz(db=_DeadDB())
    assert exc.value.status_code == 503


# --------------------------------------------------------------------------
# 2. /heatmap/tiles MVT — empty tile (no query) in raw mode, no 500.
# --------------------------------------------------------------------------
def test_generate_tile_raw_mode_returns_empty(monkeypatch):
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")

    # Prove the raw gate short-circuits BEFORE any DB access: a SessionLocal
    # that raises would surface as a 500 on the old code path.
    def _boom():
        raise AssertionError("raw MVT must not open a DB session")

    monkeypatch.setattr("app.db.session.SessionLocal", _boom)

    from app.api.heatmap import _generate_tile

    assert _generate_tile("all", 14, 8452, 5882) == b""
    assert _generate_tile("gravel", 11, 1000, 700, days=30) == b""


# --------------------------------------------------------------------------
# 3. ingest readers backing /heatmap/stats, /heatmap/export, /me/unexplored,
#    /heatmap/trails — empty (no query) in raw mode, no 500.
# --------------------------------------------------------------------------
def test_get_heat_cells_aggregated_raw_mode_returns_empty(monkeypatch):
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")

    def _boom():
        raise AssertionError("raw cell aggregation must not open a DB session")

    monkeypatch.setattr("app.db.session.SessionLocal", _boom)

    from app.services.ingest import get_heat_cells_aggregated

    assert get_heat_cells_aggregated(sport="gravel", bbox=(3.0, 43.0, 4.0, 44.0)) == []


def test_get_heat_edges_public_raw_mode_returns_empty(monkeypatch):
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")

    def _boom():
        raise AssertionError("raw edge export must not open a DB session")

    monkeypatch.setattr("app.db.session.SessionLocal", _boom)

    from app.services.ingest import get_heat_edges_public

    assert get_heat_edges_public(sport="road", bbox=(3.0, 43.0, 4.0, 44.0)) == []


# --------------------------------------------------------------------------
# 4. /admin heat panels — already fail-soft; PIN that a dropped table degrades
#    to the 0 / -1 sentinels instead of 500-ing the dashboard.
# --------------------------------------------------------------------------
def test_admin_heat_edge_stats_tolerates_absent_tables():
    from app.api.admin import _heat_edge_stats

    out = _heat_edge_stats(_FakeDBTablesAbsent())  # must not raise

    assert out["heatmap"]["edges"] == -1  # "unknown" sentinel, not a 500
    assert out["graph_health"]["total_vertices"] == -1


def test_admin_heatmap_freshness_tolerates_absent_agg():
    from app.api.admin import _heatmap_freshness

    out = _heatmap_freshness(_FakeDBTablesAbsent())  # must not raise

    assert out["heat_edges_agg"] == 0
    assert out["heat_agg_updated_at"] is None
