"""Unit tests for endpoint masking (app.services.trace_privacy).

Pure functions, no DB. Pin the privacy invariant of the raw-trace heatmap:
no published point lies within N metres (radius) of a trace's physical start
or end (home/work), a there-and-back masks BOTH true ends, and a trace that
never leaves those discs is dropped entirely.
"""
from __future__ import annotations

import math
import random

import pytest

from app.services.geo import haversine_m
from app.services.raw_trace_display import mask_then_densify
from app.services.trace_privacy import (
    DWELL_METERS,
    HEAD_FIXES,
    mask_endpoints,
    mask_meters,
)

LAT = 43.6
# ~10 m north-south step (1° lat ≈ 110_574 m).
STEP = 10.0 / 110_574


def _line(n: int, step: float = STEP, lon: float = 3.8, lat0: float = LAT) -> list:
    return [[lon, lat0 + i * step] for i in range(n)]


def _min_dist_runs(runs: list, p: list) -> float:
    return min(haversine_m(p[0], p[1], q[0], q[1]) for run in runs for q in run)


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
    # Radial mask with end ZONES: discs of 200 m sit on each of the first/last
    # HEAD_FIXES fixes (here 0–90 m and 900–990 m) plus the dwell margin, so the
    # hole is larger than 200 m. Was `isclose(total - 400)` under the
    # along-track trim. Pin: strictly more than 400 m removed, and every kept
    # point >= 200 m from every end-zone fix.
    assert 0.0 < remaining <= total - 400.0
    zone = line[:HEAD_FIXES] + line[-HEAD_FIXES:]
    for fix in zone:
        assert _min_dist_runs(runs, fix) >= 199.0
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


# ── Radial mask (data audit C1, 2026-11) ─────────────────────────────────────
#
# The original mask trimmed 200 m ALONG-TRACK. GPS jitter while the rider stands
# at the door (waiting for a fix, 1 Hz) accumulates track length on the spot,
# so the whole 200 m was consumed within a few metres of home and the first
# published point sat on the doorstep (audit repro: 1.9 m from home). The tests
# below fail on that implementation and pin the radial definition instead:
# no published point within R of the physical start or end, ANYWHERE.

R = 200.0
M_PER_DEG_LAT = 111_195.0  # haversine sphere (R = 6 371 km), matches geo.haversine_m
HOME = [3.8, 43.6]


def _offset(origin: list, east_m: float, north_m: float, ele=None) -> list:
    lon = origin[0] + east_m / (M_PER_DEG_LAT * math.cos(math.radians(origin[1])))
    lat = origin[1] + north_m / M_PER_DEG_LAT
    return [lon, lat] if ele is None else [lon, lat, ele]


WORK = _offset(HOME, 3000.0, 0.0)


def _jitter(origin: list, seconds: int, amp: float = 5.0, seed: int = 1) -> list:
    """``seconds`` 1 Hz fixes scattered ±``amp`` m around ``origin``."""
    rng = random.Random(seed)
    return [_offset(origin, rng.uniform(-amp, amp), rng.uniform(-amp, amp))
            for _ in range(seconds)]


def _leg(east0: float, north0: float, east1: float, north1: float,
         step: float = 5.0) -> list:
    """Straight leg between two (east, north) offsets from HOME, ~``step`` m apart."""
    n = max(1, int(math.hypot(east1 - east0, north1 - north0) / step))
    return [_offset(HOME, east0 + (east1 - east0) * i / n,
                    north0 + (north1 - north0) * i / n) for i in range(n + 1)]


def _min_dist(runs: list, p: list) -> float:
    return min(haversine_m(p[0], p[1], q[0], q[1]) for run in runs for q in run)


def _d(a: list, b: list) -> float:
    return haversine_m(a[0], a[1], b[0], b[1])


def test_stationary_jitter_at_start_does_not_leak_home():
    # 3 min of ±5 m jitter at home (~1 km of along-track noise) then a 3 km ride.
    trace = _jitter(HOME, 180) + _leg(0, 0, 3000, 0)
    runs = mask_endpoints(trace, mask_m=R)
    assert runs, "the ride itself must survive"
    assert _min_dist(runs, HOME) >= R - 1.0
    # Not just the true home: no published point within R of ANY of the
    # jittered fixes (the first fix is only an estimate of home).
    for fix in trace[:180]:
        assert _min_dist(runs, fix) >= R - 1.0
    # The cut lands on the circle around the first fix (R + dwell margin).
    assert R <= _d(trace[0], runs[0][0]) <= R + DWELL_METERS + 1.0


def test_stationary_jitter_at_end_does_not_leak_destination():
    trace = _leg(0, 0, 3000, 0) + _jitter(WORK, 180, seed=2)
    runs = mask_endpoints(trace, mask_m=R)
    assert runs
    assert _min_dist(runs, WORK) >= R - 1.0
    for fix in trace[-180:]:
        assert _min_dist(runs, fix) >= R - 1.0
    assert R <= _d(trace[-1], runs[-1][-1]) <= R + DWELL_METERS + 1.0


def test_jitter_at_both_ends_of_a_loop_from_home():
    trace = (_jitter(HOME, 180, seed=3) + _leg(0, 0, 1500, 0)
             + _leg(1500, 0, 1500, 1500) + _leg(1500, 1500, 0, 0)
             + _jitter(HOME, 180, seed=4))
    runs = mask_endpoints(trace, mask_m=R)
    assert runs
    assert _min_dist(runs, HOME) >= R - 1.0


def test_mid_ride_pass_near_home_is_masked():
    # Start at home, ride 1.5 km north, come back past home at 50 m, then
    # finish 2 km south. The along-track mask only trimmed the ends, so the
    # return pass was drawn 50 m from the house.
    trace = _leg(0, 0, 0, 1500) + _leg(0, 1500, 50, 0) + _leg(50, 0, 50, -2000)
    end = trace[-1]
    runs = mask_endpoints(trace, mask_m=R)
    assert _min_dist(runs, HOME) >= R - 1.0
    assert _min_dist(runs, end) >= R - 1.0
    # The pass splits the ride into the northern out-and-back and the southern
    # exit; every cut point lies on the boundary of the end-zone discs: >= R
    # from every zone fix, and within R + dwell margin of the nearest one.
    assert len(runs) == 2
    # (the zone holds at least the first/last HEAD_FIXES fixes, 5 m apart).
    zone = trace[:HEAD_FIXES] + trace[-HEAD_FIXES:]
    for run in runs:
        for pt in (run[0], run[-1]):
            nearest = min(_d(f, pt) for f in zone)
            assert R - 1.0 <= nearest <= R + 2 * DWELL_METERS + 1.0


def test_multi_run_dropout_near_home_is_masked():
    # GPS dropout (None sentinel) while still at home: the second run restarts
    # 30 m from home. Neither run may draw inside the home disc, and the inner
    # ends of a later dropout far from S/E are kept verbatim.
    run_a = _jitter(HOME, 60, seed=5)
    run_b = _leg(30, 0, 1500, 0)
    run_c = _leg(1500, 100, 3000, 100)
    trace = run_a + [None] + run_b + [None] + run_c
    runs = mask_endpoints(trace, mask_m=R)
    assert _min_dist(runs, HOME) >= R - 1.0
    assert _min_dist(runs, run_c[-1]) >= R - 1.0
    assert len(runs) == 2
    assert runs[0][-1] == run_b[-1]
    assert runs[1][0] == run_c[0]


def test_jitter_only_trace_is_fully_masked():
    # A recording that never leaves the doorstep (e.g. forgot to stop it):
    # along-track it was ~10 km of noise and largely published.
    assert mask_endpoints(_jitter(HOME, 600, amp=20.0, seed=6), mask_m=R) == []


def test_boundary_point_interpolates_elevation():
    trace = [_offset(HOME, 0, 10.0 * i, ele=100.0 + i) for i in range(100)]
    snapshot = [list(p) for p in trace]
    runs = mask_endpoints(trace, mask_m=R)
    assert trace == snapshot
    first = runs[0][0]
    assert len(first) == 3
    # The cut point is interpolated (not a recorded fix) and its elevation
    # follows the 1 m / 10 m ramp at its own position.
    assert first not in trace
    assert math.isclose(first[2], 100.0 + _d(trace[0], first) / 10.0, abs_tol=0.05)


# ── Stale first/last fix far from home (review of #36) ───────────────────────
#
# A stale / cell-based first fix 250 m–2.5 km from home used to be bridged to
# home by ``_densify_coords`` (3 m points), the mask anchored on the outlier
# and published the bridge AND the home jitter cluster (home drawn at 0.0 m).
# These drive the REAL production order: ``mask_then_densify`` (raw fixes →
# mask → densify), exactly what ``iter_masked_runs`` yields.

def _bridge_dist(p: list, a: list, b: list) -> float:
    """Distance (m) from p to segment a–b, local projection around a."""
    k = M_PER_DEG_LAT * math.cos(math.radians(a[1]))
    bx, by = (b[0] - a[0]) * k, (b[1] - a[1]) * M_PER_DEG_LAT
    px, py = (p[0] - a[0]) * k, (p[1] - a[1]) * M_PER_DEG_LAT
    L2 = bx * bx + by * by
    t = max(0.0, min(1.0, (px * bx + py * by) / L2))
    return math.hypot(px - t * bx, py - t * by)


@pytest.mark.parametrize("outlier_m", [250.0, 600.0, 2400.0])
@pytest.mark.parametrize("with_jitter", [True, False])
def test_stale_first_fix_does_not_leak_home(outlier_m, with_jitter):
    outlier = _offset(HOME, 0.0, outlier_m)  # north; the ride goes east
    home_fixes = _jitter(HOME, 180, seed=7) if with_jitter else [list(HOME)]
    trace = [outlier] + home_fixes + _leg(0, 0, 3000, 0, step=8.0)
    runs = mask_then_densify(trace, R)
    assert runs, "the ride itself must survive"
    for fix in home_fixes:
        assert _min_dist(runs, fix) >= R - 1.0
    assert _min_dist(runs, HOME) >= R - 1.0
    # No phantom outlier→home bridge either.
    assert all(_bridge_dist(p, outlier, HOME) > 5.0
               for run in runs for p in run if _d(p, HOME) < outlier_m)


@pytest.mark.parametrize("outlier_m", [250.0, 600.0, 2400.0])
@pytest.mark.parametrize("with_jitter", [True, False])
def test_stale_last_fix_does_not_leak_destination(outlier_m, with_jitter):
    outlier = _offset(WORK, 0.0, outlier_m)
    work_fixes = _jitter(WORK, 180, seed=8) if with_jitter else [list(WORK)]
    trace = _leg(0, 0, 3000, 0, step=8.0) + work_fixes + [outlier]
    runs = mask_then_densify(trace, R)
    assert runs
    for fix in work_fixes:
        assert _min_dist(runs, fix) >= R - 1.0
    assert all(_bridge_dist(p, outlier, WORK) > 5.0
               for run in runs for p in run if _d(p, WORK) < outlier_m)


def test_mask_then_densify_output_is_dense_and_far_jump_stays_a_break():
    # Output runs are densified (< 6 m spacing: max_gap=3 → floor) for the lattice, and a raw
    # jump above the 2.5 km bridge cap is a break, never a drawn segment.
    far = _leg(6000, 0, 9000, 0, step=8.0)
    trace = _leg(0, 0, 3000, 0, step=8.0) + far
    runs = mask_then_densify(trace, R)
    assert len(runs) == 2
    for run in runs:
        assert all(_d(run[i], run[i + 1]) <= 6.01 for i in range(len(run) - 1))
