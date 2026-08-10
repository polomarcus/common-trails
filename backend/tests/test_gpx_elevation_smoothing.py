"""GPX D+ smoothing — guards against the phantom-D+ regression.

Garmin / Wahoo barometric altimeters drift ±2 m at 1 Hz. The pre-2026-05-15
implementation summed every positive elevation delta from raw GPX, which
accumulated 200-400 m of phantom D+ on a 50 km flat ride. This test pins
the post-fix behavior: a flat ride with simulated baro noise must produce
near-zero D+, and a real climb must still be counted.

Smoothing strategy (in `gpx.py:_smoothed_elevation_gain`):
- Rolling median, window=11 (kills single-point outliers)
- Noise threshold 3 m (drops the residual ±1-2 m wobble)
- Per Crouzet methodology: applied to AGGREGATE D+ only. The stored coords
  keep raw <ele> values — the renderer plots reality, the analytics gives
  an honest estimate.
"""
from __future__ import annotations

import json

from app.services.gpx import _smoothed_elevation_gain, parse_gpx


def _gpx(track_points: list[tuple[float, float, float | None]]) -> bytes:
    """Build a minimal GPX from (lat, lon, ele) tuples."""
    trkpts = []
    for lat, lon, ele in track_points:
        if ele is None:
            trkpts.append(f"<trkpt lat='{lat}' lon='{lon}'/>")
        else:
            trkpts.append(f"<trkpt lat='{lat}' lon='{lon}'><ele>{ele}</ele></trkpt>")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
<trk><trkseg>{"".join(trkpts)}</trkseg></trk>
</gpx>""".encode()


# ── Unit: _smoothed_elevation_gain ─────────────────────────────────────

def test_flat_with_baro_noise_yields_zero_gain() -> None:
    """200 samples around 100 m with ±2 m random walk → smoothed D+ ≈ 0.

    Pre-fix this same input produced ~150-300 m of phantom D+ because every
    positive 0.5 m wobble was added. Now the 3 m noise threshold drops them
    and the rolling median flattens the wobble entirely.
    """
    import random
    random.seed(42)
    elevations = [100.0 + random.uniform(-2.0, 2.0) for _ in range(200)]
    gain = _smoothed_elevation_gain(elevations)
    assert gain < 5.0, f"Flat ride with baro noise should be < 5 m, got {gain:.1f}"


def test_real_climb_is_counted() -> None:
    """A clean 100 → 200 m climb over 50 samples must report ~100 m.

    The rolling median introduces ~10-15% under-count at the ends of the
    window (the first/last few samples are duplicated by median padding).
    Accept 80-110 m: under-count is the safer direction (under vs over-claim).
    """
    elevations = [100.0 + 2.0 * i for i in range(50)]
    gain = _smoothed_elevation_gain(elevations)
    assert 80 <= gain <= 110, f"Real 100 m climb → expected ~80-110, got {gain:.1f}"


def test_climb_plus_descent_only_counts_up() -> None:
    """Climb to 200 m then descend to 100 m → D+ ≈ 100 (gain only)."""
    elevations = [100.0 + 2.0 * i for i in range(50)] + [200.0 - 2.0 * i for i in range(1, 51)]
    gain = _smoothed_elevation_gain(elevations)
    assert 80 <= gain <= 110, f"Climb-then-descent should count ~80-110 m gain, got {gain:.1f}"


def test_short_series_threshold_only_path() -> None:
    """< window samples — rolling median skipped, only noise threshold applies.

    For 5 points spaced 1 m apart, the cumulative gain crosses 3 m at point 4
    (delta from baseline of 100 → 103). The threshold correctly captures
    this as a 3 m gain (cumulative, not per-step). Documents intentional
    behavior: small repeated gains *do* accumulate once the running total
    exceeds the threshold."""
    elevations = [100.0, 101.0, 102.0, 103.0, 104.0]
    gain = _smoothed_elevation_gain(elevations)
    assert 2 <= gain <= 4, f"Cumulative 3 m gain should count, got {gain}"


def test_short_series_above_threshold_counts() -> None:
    """5 points, +5 m step → 5 m gain (1 sample above threshold)."""
    elevations = [100.0, 100.0, 105.0, 105.0, 105.0]
    gain = _smoothed_elevation_gain(elevations)
    assert 4 <= gain <= 6, f"One 5 m step should count as ~5 m, got {gain}"


def test_empty_or_too_short_returns_zero() -> None:
    """Edge: fewer than 3 valid points → no gain (can't compute reliably)."""
    assert _smoothed_elevation_gain([]) == 0.0
    assert _smoothed_elevation_gain([100.0]) == 0.0
    assert _smoothed_elevation_gain([100.0, 110.0]) == 0.0


def test_none_entries_skipped() -> None:
    """None entries (gaps in <ele>) are dropped, not treated as zero."""
    elevations = [100.0, None, None, 110.0, 110.0]
    # 3 valid points: 100, 110, 110 → one 10 m step above 3 m threshold
    gain = _smoothed_elevation_gain(elevations)
    assert 9 <= gain <= 11, f"Expected ~10 m gain, got {gain}"


# ── Integration: parse_gpx end-to-end ──────────────────────────────────

def test_parse_gpx_flat_with_noise_does_not_inflate() -> None:
    """End-to-end: a 50-point flat ride with ±2 m noise → elevation_gain_m
    should be near zero. Pre-fix this returned ~30-60 m."""
    import random
    random.seed(7)
    pts = [(43.6 + 0.0001 * i, 3.87, 100.0 + random.uniform(-2.0, 2.0))
           for i in range(50)]
    out = parse_gpx(_gpx(pts))
    gain = out["elevation_gain_m"] or 0.0
    assert gain < 10, f"Flat noisy ride should yield ~0 gain, got {gain:.1f}"


def test_parse_gpx_preserves_raw_coords_per_crouzet() -> None:
    """The stored coords keep RAW <ele> values — smoothing only affects the
    aggregate `elevation_gain_m`. Verifies Crouzet trace-integrity rule."""
    pts = [(43.6, 3.87, 100.5), (43.601, 3.871, 100.7), (43.602, 3.872, 101.2)]
    out = parse_gpx(_gpx(pts))
    coords = json.loads(out["geometry_geojson"])["coordinates"]
    # The stored elevations must be the raw GPX values, not smoothed
    assert coords[0][2] == 100.5
    assert coords[1][2] == 100.7
    assert coords[2][2] == 101.2
