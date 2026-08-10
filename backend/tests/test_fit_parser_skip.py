"""FIT out-of-scope sport skip — a Garmin swim/virtual/indoor .fit must NOT
pollute the community heatmap (mirrors the GPX skip-list). Drives the real
`_fit_skip_reason` with a lightweight mock FitFile (no .fit binary needed)."""
from app.services.fit_parser import _FIT_EXTRA_SKIP, _fit_skip_reason


class _FakeSession:
    def __init__(self, sport=None, sub_sport=None):
        self._v = {"sport": sport, "sub_sport": sport and sub_sport or sub_sport}

    def get_value(self, k):
        return self._v.get(k)


class _FakeFit:
    def __init__(self, sessions):
        self._sessions = sessions

    def get_messages(self, kind):
        return self._sessions if kind == "session" else []


def _skip(sport=None, sub_sport=None):
    return _fit_skip_reason(_FakeFit([_FakeSession(sport, sub_sport)]))


def test_out_of_scope_sports_are_skipped():
    for s in ("swimming", "open_water_swimming", "alpine_skiing", "rowing",
              "kayaking", "sailing", "training", "fitness_equipment",
              "motorcycling", "driving", "golf"):
        assert _skip(s) is not None, f"{s} should be skipped"


def test_sub_sport_out_of_scope_is_skipped():
    # e.g. sport='cycling', sub_sport='indoor_cycling' → skip (trainer)
    assert _skip("cycling", "indoor_cycling") is not None


def test_in_scope_sports_are_kept():
    for s in ("cycling", "road_cycling", "gravel_cycling", "mountain_biking",
              "running", "trail_running", "walking", "hiking"):
        assert _skip(s) is None, f"{s} should NOT be skipped"


def test_no_sport_field_is_kept():
    assert _skip(None) is None
    assert _fit_skip_reason(_FakeFit([])) is None


def test_fit_extra_skip_covers_fit_native_sports():
    # these are NOT in the GPX list; the FIT-native extras must catch them
    assert "training" in _FIT_EXTRA_SKIP
    assert "fitness_equipment" in _FIT_EXTRA_SKIP
    assert "motorcycling" in _FIT_EXTRA_SKIP
