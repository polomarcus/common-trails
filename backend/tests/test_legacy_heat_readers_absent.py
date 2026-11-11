"""Non-regression: the /admin heatmap panels are fail-soft — a missing /
unreadable table degrades to nulls instead of 500-ing the dashboard.

``/readyz`` no longer reads any heatmap table — see ``test_health.py``.
"""


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
