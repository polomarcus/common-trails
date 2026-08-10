"""Tier-A unit pins on the ingestion *primitives* — the pure functions that
the whole heatmap-quality story rests on. No DB, no OSM, no network: these run
in CI on every push and fail fast if anyone touches the snapping / densify /
dedup constants without meaning to.

Why these and not just the golden tests: the golden Hérault tests
(`test_gpx_golden_herault.py`, `test_gpx_golden_pass_count_pair.py`) are the
end-to-end proof, but they are CI-skipped (they need the local occitanie PBF in
`osm_road_edges`). If `_snap`, `_snap_along_segment` or `_densify_coords`
regress, the goldens go silent in CI and the bug ships. These unit pins are the
always-on tripwire for the same invariants:

  * `_snap` stays at 4dp (~11 m). 5dp re-fragments the heatmap — the explicit
    lesson behind the deleted `_snap_fine` (see ingest.py:506).
  * `_snap_along_segment` keeps a point ON the OSM polyline (0 m perpendicular)
    AND collapses two riders' nearby projections to ONE coord → ONE edge_key.
    This is the producer-side cross-rider dedup primitive.
  * `_densify_coords` BRIDGES the [500 m, 2500 m] downsampling band (so a
    downsampled-but-continuous ride stays continuous) and only HARD-BREAKS
    above 2500 m (a real dropout/transfer → no phantom straight-line).

See [[reference_heatmap_quality_invariants]] and the `/heatmap` skill.
"""
import math

import pytest

from app.services import ingest

# ── _snap: the 4dp grid that lets same-road traces merge ─────────────────────

def test_snap_is_4dp_grid():
    """`_snap` rounds to 4 decimals (~11 m). The 4dp choice is load-bearing:
    at 5dp (~1.1 m) GPS jitter stops same-road traces from sharing an edge_key
    and the heatmap fragments (ingest.py:496-503)."""
    lat, lon = ingest._snap(43.612345, 3.876543)
    assert (lat, lon) == (43.6123, 3.8765)


def test_snap_collapses_sub_grid_jitter_to_same_cell():
    """Two samples ~5 m apart (well under the 11 m cell) snap to the SAME
    cell → same edge_key downstream. This is the merge property in miniature."""
    # both samples inside the SAME 4dp cell (base offset away from the grid
    # line so the ~3 m jitter doesn't straddle a rounding boundary)
    a = ingest._snap(43.6000, 3.80001)
    b = ingest._snap(43.6000, 3.80004)  # ~2.4 m east, still rounds to 3.8000
    assert a == b == (43.6000, 3.8000)


def test_snap_separates_points_a_cell_apart():
    """Two samples ~15 m apart land in different cells — the grid still
    resolves genuinely distinct positions (no over-merge)."""
    a = ingest._snap(43.6000, 3.8000)
    b = ingest._snap(43.6000, 3.8000 + 2.0e-4)  # ~16 m
    assert a != b


# ── _snap_along_segment: arc-length bucket = cross-rider dedup ────────────────

def _perp_dist_m(px, py, ax, ay, bx, by):
    """Metres from P to the infinite line A-B (local equirectangular)."""
    dx = (bx - ax) * ingest._DEG_TO_M * ingest._COS_LAT_FRANCE
    dy = (by - ay) * ingest._DEG_TO_M
    wx = (px - ax) * ingest._DEG_TO_M * ingest._COS_LAT_FRANCE
    wy = (py - ay) * ingest._DEG_TO_M
    seg2 = dx * dx + dy * dy
    if seg2 == 0:
        return math.hypot(wx, wy)
    cross = wx * dy - wy * dx
    return abs(cross) / math.sqrt(seg2)


def test_snap_along_segment_keeps_point_on_polyline():
    """The bucketed coord must lie ON segment A-B (0 m perpendicular) — the
    whole reason arc-length bucketing replaced the deleted 5dp `_snap_fine`,
    which pushed projections up to ~0.55 m off the OSM line (ingest.py:506)."""
    ax, ay, bx, by = 3.8000, 43.6000, 3.8100, 43.6050  # ~1 km segment
    # a point already projected onto the segment, mid-way
    px, py = ingest._project_onto_segment(3.8051, 43.6024, ax, ay, bx, by)
    sx, sy = ingest._snap_along_segment(px, py, ax, ay, bx, by)
    assert _perp_dist_m(sx, sy, ax, ay, bx, by) < 0.01  # essentially 0 m


def test_snap_along_segment_collapses_two_riders_to_one_coord():
    """Two riders whose projections fall within bucket_m/2 (0.5 m) of each
    other along the trail RELIABLY collapse to the SAME coord → SAME edge_key.
    This is the producer-side K-anonymity / dedup primitive."""
    ax, ay, bx, by = 3.8000, 43.6000, 3.8100, 43.6050
    seg_len_m = math.hypot(
        (bx - ax) * ingest._DEG_TO_M * ingest._COS_LAT_FRANCE,
        (by - ay) * ingest._DEG_TO_M,
    )
    # two arc-length positions ~0.3 m apart, both ~halfway along
    t_mid = 0.5
    t_near = t_mid + 0.3 / seg_len_m
    r1 = ingest._snap_along_segment(ax + t_mid * (bx - ax), ay + t_mid * (by - ay), ax, ay, bx, by)
    r2 = ingest._snap_along_segment(ax + t_near * (bx - ax), ay + t_near * (by - ay), ax, ay, bx, by)
    assert r1 == r2, "sub-bucket riders must collapse to one coord (one edge_key)"


def test_snap_along_segment_short_segment_returns_midpoint():
    """A segment shorter than the bucket collapses to its midpoint — every
    point on a sub-metre segment shares one coord (documented branch)."""
    ax, ay = 3.80000, 43.60000
    bx, by = 3.80000, 43.60000 + (0.5 / ingest._DEG_TO_M)  # ~0.5 m, < 1 m bucket
    r = ingest._snap_along_segment(bx, by, ax, ay, bx, by)
    assert r == (ax + 0.5 * (bx - ax), ay + 0.5 * (by - ay))


# ── _densify_coords: bridge the downsample band, hard-break only real gaps ────

def _has_break(out):
    return any(p is None for p in out)


def test_densify_fills_small_gaps_no_break():
    """A ~100 m hop (normal downsample stride) is densified, never broken."""
    out = ingest._densify_coords([[3.80, 43.60], [3.80, 43.60 + 100 / ingest._DEG_TO_M]])
    assert not _has_break(out)
    # `n = int(d/max_gap)` floors, so the realised step can reach ~2x max_gap
    # (e.g. d just under 2*max_gap → n=1). The invariant that matters: no step
    # ever exceeds that bound, so the snapped chain stays connected at 4dp.
    for a, b in zip(out, out[1:], strict=False):
        assert ingest._haversine_m(a[0], a[1], b[0], b[1]) <= 2 * ingest._DENSIFY_MAX_GAP_M + 1.0


def test_densify_bridges_the_downsample_band():
    """A gap in [500 m, 2500 m] (a downsampled-but-continuous stride) is
    BRIDGED — densified across with NO break sentinel — so the OSM matcher can
    snap the interpolated points and the ride stays continuous. This is the
    fix behind continuity 99.56% → 100% (ingest.py:531-555)."""
    out = ingest._densify_coords([[3.80, 43.60], [3.80, 43.60 + 1500 / ingest._DEG_TO_M]])
    assert not _has_break(out), "a 1500 m downsample stride must be bridged, not broken"
    assert len(out) > 50  # densified into many ~15 m steps


def test_densify_hard_breaks_above_bridge_cap():
    """A gap ABOVE 2500 m (a real GPS dropout / car transfer) MUST insert a
    None sentinel — never a single phantom straight-line edge across the gap."""
    out = ingest._densify_coords([[3.80, 43.60], [3.80, 43.60 + 3000 / ingest._DEG_TO_M]])
    assert _has_break(out), "a >2500 m jump must hard-break (no phantom edge)"
    # the sentinel sits between the two real endpoints
    assert out[0] is not None and out[-1] is not None
    assert None in out


def test_densify_break_threshold_is_exactly_2500m():
    """Pin the bridge cap: just under 2500 m bridges, just over breaks. Guards
    the constant against silent drift."""
    just_under = ingest._densify_coords([[3.80, 43.60], [3.80, 43.60 + 2400 / ingest._DEG_TO_M]])
    just_over = ingest._densify_coords([[3.80, 43.60], [3.80, 43.60 + 2600 / ingest._DEG_TO_M]])
    assert not _has_break(just_under)
    assert _has_break(just_over)


def test_densify_preserves_elevation_by_interpolation():
    """Bridged points carry linearly-interpolated elevation (3D GPX). Trace
    analytics (slope) depend on it."""
    out = ingest._densify_coords([[3.80, 43.60, 100.0], [3.80, 43.60 + 300 / ingest._DEG_TO_M, 400.0]])
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
