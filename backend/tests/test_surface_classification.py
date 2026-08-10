"""Tests for surface_classification — pure function, no I/O."""
from app.services.surface_classification import classify_surface, classify_surface_simple


class TestExplicitSurface:
    def test_explicit_asphalt(self):
        cls, conf = classify_surface({"surface": "asphalt"})
        assert cls == "asphalt"
        assert conf == 1.0

    def test_explicit_paved_maps_to_asphalt(self):
        cls, _ = classify_surface({"surface": "paved"})
        assert cls == "asphalt"

    def test_explicit_compacted_is_gravel(self):
        cls, _ = classify_surface({"surface": "compacted"})
        assert cls == "gravel"

    def test_explicit_cobblestone_is_rock(self):
        cls, _ = classify_surface({"surface": "cobblestone"})
        assert cls == "rock"

    def test_surface_overrides_highway(self):
        cls, _ = classify_surface({"surface": "dirt", "highway": "secondary"})
        assert cls == "dirt"


class TestHighwayInference:
    def test_highway_secondary_infers_asphalt(self):
        cls, conf = classify_surface({"highway": "secondary"})
        assert cls == "asphalt"
        assert conf == 0.85

    def test_highway_track_infers_gravel(self):
        cls, _ = classify_surface({"highway": "track"})
        assert cls == "gravel"

    def test_residential_no_surface(self):
        cls, _ = classify_surface({"highway": "residential"})
        assert cls == "asphalt"


class TestTracktype:
    def test_tracktype_grade1_overrides(self):
        cls, _ = classify_surface({"highway": "track", "tracktype": "grade1"})
        assert cls == "asphalt"

    def test_tracktype_grade4(self):
        cls, _ = classify_surface({"highway": "track", "tracktype": "grade4"})
        assert cls == "dirt"


class TestSmoothness:
    def test_cycleway_excellent_is_asphalt(self):
        cls, _ = classify_surface({"highway": "cycleway", "smoothness": "excellent"})
        assert cls == "asphalt"

    def test_smoothness_very_bad_downgrades(self):
        cls, _ = classify_surface({"surface": "asphalt", "smoothness": "very_bad"})
        assert cls == "gravel"

    def test_smoothness_good_no_change(self):
        cls, _ = classify_surface({"surface": "dirt", "smoothness": "good"})
        assert cls == "dirt"


class TestEdgeCases:
    def test_empty_tags_unknown(self):
        cls, conf = classify_surface({})
        assert cls == "unknown"
        assert conf == 0.0

    def test_case_insensitive(self):
        cls, _ = classify_surface({"surface": "ASPHALT"})
        assert cls == "asphalt"

    def test_classify_surface_simple_returns_string(self):
        result = classify_surface_simple({"surface": "gravel"})
        assert result == "gravel"
