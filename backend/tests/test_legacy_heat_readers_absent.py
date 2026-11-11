"""Non-regression: the last readers of ``heat_edges`` / ``heat_edges_agg`` must
NOT fail when those LEGACY tables are ABSENT (raw-trace pivot 2026-07-29 — the
community map is the static raw PMTiles; these read models are dropped in
prod). Each test drives the REAL function with its DB dependency faked so the
exact "relation does not exist" failure is reproduced (no live DB needed).

``/readyz`` no longer reads any heatmap table — see ``test_health.py``.
"""


# --------------------------------------------------------------------------
# 1. ingest readers (their HTTP routes are gone; the functions remain until
#    the matched code leaves ingest.py) — empty (no query) in raw mode.
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
# 2. /admin heatmap panels — fail-soft: a missing / unreadable table degrades
#    to nulls instead of 500-ing the dashboard.
# --------------------------------------------------------------------------
class _FakeDBEverythingRaises:
    def __init__(self):
        self.rolled_back = False

    def execute(self, stmt, params=None):
        raise Exception('relation "heatmap_metrics" does not exist')

    def rollback(self):
        self.rolled_back = True


def test_admin_community_heatmap_facts_tolerates_absent_table():
    from app.api.admin import _community_heatmap_facts

    db = _FakeDBEverythingRaises()
    out = _community_heatmap_facts(db)  # must not raise

    assert out["last_build_at"] is None
    assert out["activities"] is None
    assert db.rolled_back


def test_admin_heatmap_freshness_tolerates_absent_table():
    from app.api.admin import _heatmap_freshness

    out = _heatmap_freshness(_FakeDBEverythingRaises())  # must not raise

    assert out == {
        "last_activity_at": None,
        "last_activity_date": None,
        "last_rebuild_at": None,
    }
