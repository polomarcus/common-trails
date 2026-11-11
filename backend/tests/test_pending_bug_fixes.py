"""Non-regression pins that outlived the matched pipeline:

* the sport normaliser keeps ``offroad`` (and strips test sports);
* ``_densify_coords`` bridges sampling gaps and only breaks on real dropouts.
"""


class TestOffroadPartition:
    """sport='offroad' keeps its own bucket, not rewritten to gravel."""

    def test_normalizer_keeps_offroad(self):
        from app.services.ingest import _normalize_heat_edge_sport
        assert _normalize_heat_edge_sport("offroad") == "offroad"

    def test_normalizer_keeps_real_sports(self):
        from app.services.ingest import _normalize_heat_edge_sport
        for s in ("road", "gravel", "mtb", "offroad", "running"):
            assert _normalize_heat_edge_sport(s) == s

    def test_normalizer_strips_test_prefix(self):
        from app.services.ingest import _normalize_heat_edge_sport
        assert _normalize_heat_edge_sport("test_anything") == "gravel"
        assert _normalize_heat_edge_sport("cache_test_x") == "gravel"


class TestDensifierTrackBreak:
    """`_densify_coords` two-tier gap handling (see `_DENSIFY_MAX_BRIDGE_M`):

    - small / sampling gaps (≤ 2500 m) are densified across (bridged) so a
      downsampled-GPX run stays CONTINUOUS;
    - only > 2500 m jumps (genuine dropout / car transfer) stay a hard break.
    """

    def test_short_gap_no_break(self):
        from app.services.ingest import _densify_coords
        # 100m apart — normal contiguous recording; no None expected.
        coords = [[3.87, 43.61], [3.871, 43.61]]
        out = _densify_coords(coords)
        assert None not in out

    def test_sampling_gap_bridged_not_broken(self):
        from app.services.ingest import _densify_coords
        # ~2.4 km apart — past the old 500 m guard but within the bridge
        # band. This is the sparse-sampling case (downsampled export): it
        # must be BRIDGED (densified across), NOT broken, so the run stays
        # continuous. No None sentinel; interpolated points.
        coords = [[3.87, 43.61], [3.90, 43.61]]  # 2415 m
        out = _densify_coords(coords)
        assert None not in out, (
            "sampling-band gap must be bridged, not broken — a downsampled "
            "ride on a real path should stay continuous"
        )
        assert len(out) > 100, "the 2.4 km bridge should produce many ~15 m steps"
        assert out[0] is not None and out[-1] is not None

    def test_genuine_dropout_inserts_break(self):
        from app.services.ingest import _densify_coords
        # ~4 km apart — past the 2500 m bridge cap. Overwhelmingly a real
        # dropout or a transfer between two rides; must insert exactly one
        # None sentinel so no phantom line is painted across it.
        coords = [[3.87, 43.61], [3.92, 43.61]]  # ~4026 m
        out = _densify_coords(coords)
        assert None in out, "densifier must break on a >2500 m dropout gap"
        assert out[0] is not None and out[-1] is not None

    def test_break_separates_two_segments(self):
        from app.services.ingest import _densify_coords
        # Pattern: short / huge dropout / short. Densifier should produce
        # interpolated points for the two short legs and one None sentinel
        # between them (the huge gap is past the bridge cap).
        coords = [
            [3.87, 43.61],
            [3.871, 43.61],   # ~80m
            [3.92, 43.61],    # ~4km gap — break here
            [3.921, 43.61],   # ~80m
        ]
        out = _densify_coords(coords)
        none_count = sum(1 for p in out if p is None)
        assert none_count == 1, f"expected exactly one break, got {none_count}"
