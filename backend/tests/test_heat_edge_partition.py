"""Regression: heat_edges only ever stores known partition sports.

Bug history (May 2026): bulk GPX imports with --sport offroad wrote 1M+
edges with sport='offroad' that landed in heat_edges_default partition,
because there was no `offroad` partition.

Original fix: `_normalize_heat_edge_sport()` rewrote `offroad → gravel`
so the default partition stayed empty.

Updated May 2026: migration 0035 added a real `heat_edges_offroad`
partition. Normalizer now keeps `offroad` as-is — analytics about
offroad rides are honest from this commit forward. Test_* sports are
still stripped to gravel to keep the default partition empty in
test environments.

These tests lock the rule in. They run as plain pytest (no DB needed).
"""
from app.services.ingest import _normalize_heat_edge_sport


class TestNormalizeHeatEdgeSport:
    """Locks in: heat_edges only ever gets road/gravel/mtb/offroad/running."""

    def test_offroad_kept_after_migration_0035(self):
        # offroad now has its own partition (migration 0035, May 2026).
        # Pre-migration this returned 'gravel' to avoid default partition.
        assert _normalize_heat_edge_sport("offroad") == "offroad"

    def test_real_sports_pass_through(self):
        for sport in ("road", "gravel", "mtb", "offroad", "running"):
            assert _normalize_heat_edge_sport(sport) == sport, f"{sport} should pass through"

    def test_test_sports_stripped(self):
        # Test code occasionally uses sport names like 'cache_test_mtb' —
        # those must not leak into heat_edges_default.
        assert _normalize_heat_edge_sport("cache_test_mtb") == "gravel"
        assert _normalize_heat_edge_sport("cache_test_road") == "gravel"
        assert _normalize_heat_edge_sport("test_foo") == "gravel"

    def test_unknown_sport_defaults_to_gravel(self):
        # Unexpected values must not land in the default partition either.
        assert _normalize_heat_edge_sport("bike") == "gravel"
        assert _normalize_heat_edge_sport("") == "gravel"
        assert _normalize_heat_edge_sport("hiking") == "gravel"

    def test_normalized_set_is_subset_of_partitions(self):
        # Every normalized output must be one of the actual partitions.
        # If anyone adds a new branch returning e.g. "ebike", this catches it.
        partitions = {"road", "gravel", "mtb", "offroad", "running"}
        for sport in (
            "road", "gravel", "mtb", "offroad", "running",
            "cache_test_mtb", "test_foo", "bike", "hiking", "", "unknown",
        ):
            result = _normalize_heat_edge_sport(sport)
            assert result in partitions, (
                f"_normalize_heat_edge_sport({sport!r}) returned {result!r} — "
                f"not a real partition (must be one of {partitions})"
            )
