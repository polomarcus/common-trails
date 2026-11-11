"""Unit pins on ``_densify_coords`` — the pure function the raw-trace display
and the privacy masking both rest on. No DB, no network.

``_densify_coords`` BRIDGES the [500 m, 2500 m] downsampling band (so a
downsampled-but-continuous ride stays continuous) and only HARD-BREAKS above
2500 m (a real dropout/transfer → no phantom straight-line).
"""

import pytest

from app.services import ingest

_DEG_TO_M = 111_320.0  # metres per degree of latitude

# ── _densify_coords: bridge the downsample band, hard-break only real gaps ────

def _has_break(out):
    return any(p is None for p in out)


def test_densify_fills_small_gaps_no_break():
    """A ~100 m hop (normal downsample stride) is densified, never broken."""
    out = ingest._densify_coords([[3.80, 43.60], [3.80, 43.60 + 100 / _DEG_TO_M]])
    assert not _has_break(out)
    # `n = int(d/max_gap)` floors, so the realised step can reach ~2x max_gap
    # (e.g. d just under 2*max_gap → n=1). The invariant that matters: no step
    # ever exceeds that bound, so the snapped chain stays connected at 4dp.
    for a, b in zip(out, out[1:], strict=False):
        assert ingest._haversine_m(a[0], a[1], b[0], b[1]) <= 2 * ingest._DENSIFY_MAX_GAP_M + 1.0


def test_densify_bridges_the_downsample_band():
    """A gap in [500 m, 2500 m] (a downsampled-but-continuous stride) is
    BRIDGED — densified across with NO break sentinel — so the ride stays
    continuous."""
    out = ingest._densify_coords([[3.80, 43.60], [3.80, 43.60 + 1500 / _DEG_TO_M]])
    assert not _has_break(out), "a 1500 m downsample stride must be bridged, not broken"
    assert len(out) > 50  # densified into many ~15 m steps


def test_densify_hard_breaks_above_bridge_cap():
    """A gap ABOVE 2500 m (a real GPS dropout / car transfer) MUST insert a
    None sentinel — never a single phantom straight-line edge across the gap."""
    out = ingest._densify_coords([[3.80, 43.60], [3.80, 43.60 + 3000 / _DEG_TO_M]])
    assert _has_break(out), "a >2500 m jump must hard-break (no phantom edge)"
    # the sentinel sits between the two real endpoints
    assert out[0] is not None and out[-1] is not None
    assert None in out


def test_densify_break_threshold_is_exactly_2500m():
    """Pin the bridge cap: just under 2500 m bridges, just over breaks. Guards
    the constant against silent drift."""
    just_under = ingest._densify_coords([[3.80, 43.60], [3.80, 43.60 + 2400 / _DEG_TO_M]])
    just_over = ingest._densify_coords([[3.80, 43.60], [3.80, 43.60 + 2600 / _DEG_TO_M]])
    assert not _has_break(just_under)
    assert _has_break(just_over)


def test_densify_preserves_elevation_by_interpolation():
    """Bridged points carry linearly-interpolated elevation (3D GPX). Trace
    analytics (slope) depend on it."""
    out = ingest._densify_coords([[3.80, 43.60, 100.0], [3.80, 43.60 + 300 / _DEG_TO_M, 400.0]])
    eles = [p[2] for p in out if p is not None and len(p) > 2]
    assert eles[0] == pytest.approx(100.0, abs=5)
    assert eles[-1] == pytest.approx(400.0, abs=5)
    # monotonic increase across the bridge
    assert all(b >= a - 1e-6 for a, b in zip(eles, eles[1:], strict=False))


def test_densify_skips_zero_length_duplicate_points():
    """Consecutive identical points (d < 0.1 m) are dropped, not densified into
    a zero-length explosion."""
    out = ingest._densify_coords([[3.80, 43.60], [3.80, 43.60], [3.80, 43.60]])
    assert len(out) == 1
