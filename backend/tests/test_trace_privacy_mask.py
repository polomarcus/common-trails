"""Unit tests for endpoint masking (app.services.trace_privacy).

Pure functions, no DB. Pin the privacy invariant of the raw-trace heatmap:
the first + last N metres of every trace are trimmed (home/work), a
there-and-back masks BOTH true ends, and a trace shorter than 2N is dropped
entirely.
"""
from __future__ import annotations

import math

from app.services.geo import haversine_m
from app.services.trace_privacy import mask_endpoints, mask_meters

LAT = 43.6
# ~10 m north-south step (1° lat ≈ 110_574 m).
STEP = 10.0 / 110_574


def _line(n: int, step: float = STEP, lon: float = 3.8, lat0: float = LAT) -> list:
    return [[lon, lat0 + i * step] for i in range(n)]


def _run_length(run: list) -> float:
    return sum(
        haversine_m(run[i][0], run[i][1], run[i + 1][0], run[i + 1][1])
        for i in range(len(run) - 1)
    )


def test_default_mask_is_200m(monkeypatch):
    monkeypatch.delenv("TRACE_MASK_METERS", raising=False)
    assert mask_meters() == 200.0


def test_env_override(monkeypatch):
    monkeypatch.setenv("TRACE_MASK_METERS", "350")
    assert mask_meters() == 350.0
    monkeypatch.setenv("TRACE_MASK_METERS", "garbage")
    assert mask_meters() == 200.0  # falls back on bad value


def test_point_to_point_trims_both_ends():
    # 100 points × 10 m = ~990 m straight line.
    line = _line(100)
    total = _run_length(line)
    runs = mask_endpoints(line, mask_m=200.0)
    assert len(runs) == 1
    remaining = _run_length(runs[0])
    # ~990 - 2*200 = ~590 m, within one segment of tolerance.
    assert math.isclose(remaining, total - 400.0, abs_tol=15.0)
    # The kept trace starts ~200 m in and ends ~200 m before the true end.
    assert haversine_m(line[0][0], line[0][1], runs[0][0][0], runs[0][0][1]) >= 190.0
    assert haversine_m(line[-1][0], line[-1][1], runs[0][-1][0], runs[0][-1][1]) >= 190.0


def test_there_and_back_masks_home_twice():
    # Out 500 m then back to the exact start (home == start == end).
    out = _line(51)  # 0..500 m
    back = list(reversed(out))[1:]  # 500..0 m, excluding the shared apex point
    trace = out + back
    home = trace[0]
    runs = mask_endpoints(trace, mask_m=200.0)
    assert runs, "a ~1 km out-and-back should survive masking"
    # No kept point may lie within (mask - tolerance) of home — both the
    # departure AND the return leg near home are trimmed.
    for run in runs:
        for pt in run:
            assert haversine_m(home[0], home[1], pt[0], pt[1]) >= 180.0


def test_short_trace_fully_masked():
    # 30 points × 10 m = ~290 m < 2 * 200 m → nothing survives.
    line = _line(30)
    assert mask_endpoints(line, mask_m=200.0) == []


def test_zero_mask_is_identity_runs():
    line = _line(10)
    runs = mask_endpoints(line, mask_m=0.0)
    assert len(runs) == 1
    assert runs[0] == line


def test_track_break_splits_runs_and_masks_outer_ends():
    # Two runs separated by a None sentinel; masking trims the front of the
    # first run and the back of the last run, keeping inner ends intact.
    run_a = _line(60, lon=3.80)          # ~590 m
    run_b = _line(60, lon=3.81)          # ~590 m, parallel
    trace = run_a + [None] + run_b
    runs = mask_endpoints(trace, mask_m=200.0)
    assert len(runs) == 2
    # First run: its ORIGINAL start is trimmed away.
    assert haversine_m(run_a[0][0], run_a[0][1], runs[0][0][0], runs[0][0][1]) >= 190.0
    # First run inner end (the break) is preserved (still ~ run_a[-1]).
    assert haversine_m(run_a[-1][0], run_a[-1][1], runs[0][-1][0], runs[0][-1][1]) < 15.0
    # Last run: inner start preserved, outer end trimmed.
    assert haversine_m(run_b[0][0], run_b[0][1], runs[1][0][0], runs[1][0][1]) < 15.0
    assert haversine_m(run_b[-1][0], run_b[-1][1], runs[1][-1][0], runs[1][-1][1]) >= 190.0


def test_preserves_input_and_elevation():
    line = [[3.8, LAT + i * STEP, 100.0 + i] for i in range(100)]
    snapshot = [list(p) for p in line]
    runs = mask_endpoints(line, mask_m=200.0)
    assert line == snapshot, "mask_endpoints must not mutate its input (Crouzet)"
    # Elevation is carried through (3-tuples out).
    assert all(len(pt) == 3 for run in runs for pt in run)
