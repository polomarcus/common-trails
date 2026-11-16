"""Heatmap: LOCAL counts on noisy corridors + popularity by PEOPLE, not passes.

fix/heatmap-popularite-locale (2026-11), two prod bugs on the public map:

1. The displayed count was not local. At a spot really ridden ~40 times by 2
   contributors the tooltip read "540 passages, 6 contributeurs", heat 0.97:
   the 540 is a junction ~2 km away on the SAME piece. Lateral GPS spread makes
   the per-cell count along one ride flicker across log2 buckets (real profile:
   540 at the junction, then 108, 33, 71, 132, 79, 92…), so the junction
   (shorter than the 200 m hysteresis) was absorbed, no cut ever fired, and the
   piece reported the MAX of each count over 2 km. Fix: rolling-median
   smoothing by travelled distance before the split + a piece reports the
   MEDIAN (typical point) of both counts.

2. Popularity counted passes, not people: 528 of those 540 passes are ONE rider
   leaving home, so the darkest lines were one person's habits. Fix: heat_score
   uses eff = min(pass_count, user_count × CAP) (CAP = 5 by default); the
   tooltip counts stay real.

The end-to-end pins drive the REAL ``export_raw_geojson`` (its signature is
unchanged) through a monkeypatched lattice / run stream, so they FAIL on the
pre-fix code for the semantic reason, not an API change:
  * ``test_noisy_corridor_reports_the_local_typical_count`` — pre-fix: one
    2.4 km piece reporting 540 / 6;
  * ``test_same_passes_more_contributors_is_more_popular`` — pre-fix: both
    corridors heat 1.0 (equal), and equal heat_points ``w``.
Review pins (fail on the first version of this fix):
  * ``test_effective_popularity_is_never_mixed_across_cells`` — eff was
    recombined from two independent medians;
  * ``test_heat_scale_tops_out_at_a_high_percentile_not_the_max`` — the scale
    was the max cell eff, so smoothed lines never reached the top.
``test_noisy_run_piece_count_stays_bounded`` is the OOM guard: the pre-fix
code was already bounded (it passes there too); it pins that the smoothing and
the 2-D (pass, effective-pass) level kept ~run_length / min_piece_m.
"""
from __future__ import annotations

import json
import math
import os
import random

# Only the API that pre-dates the fix is imported by name: the new helpers are
# reached through ``rtd.`` inside the tests, so this file still COLLECTS on the
# pre-fix code and the bug pins fail there for the semantic reason (wrong
# counts / equal heat), not an ImportError.
from app.services import raw_trace_display as rtd
from app.services.geo import haversine_m
from app.services.raw_trace_display import (
    _cell_key,
    _split_run_by_local_pass,
    _sport_id,
    export_raw_geojson,
    lattice_deg,
    pass_piece_min_m,
)

LAT = 43.61
STEP_3M = 3.0 / 110_574  # the densifier's ~3 m spacing
# The prod profile along the road after the junction (lateral-spread flicker:
# buckets 5/6/7 interleaved), plus the quieter values of the stretch itself.
_NOISY = [108, 33, 71, 132, 79, 92, 40, 38, 45, 52, 64, 71, 36, 41]


def _north(n: int, lon: float, step: float = STEP_3M) -> list:
    return [[lon, LAT + i * step] for i in range(n)]


def _cells_along(run: list, sid: int, deg: float) -> list[int]:
    """Distinct cell keys of ``run`` in first-seen order."""
    out, seen = [], set()
    for p in run:
        k = _cell_key(sid, p[0], p[1], deg)
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _run_len_m(sub: list) -> float:
    return sum(haversine_m(sub[i - 1][0], sub[i - 1][1], sub[i][0], sub[i][1])
               for i in range(1, len(sub)))


def _read(path: str) -> list[dict]:
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _patch_lattice(monkeypatch, run, sport, pass_by_cell, users_by_cell):
    """Drive the REAL emitter over ONE run with a prescribed pass-1 lattice."""
    def _iter(_db, **_kw):
        yield 1, "rider", sport, list(run)

    def _lattice(_db, _deg, **_kw):
        return pass_by_cell, users_by_cell, {}, {}, {}, {}

    monkeypatch.setattr(rtd, "iter_masked_runs", _iter)
    monkeypatch.setattr(rtd, "_accumulate_lattice", _lattice)


def _features_covering(feats: list, lat: float) -> list:
    return [f for f in feats
            if min(c[1] for c in f["geometry"]["coordinates"]) <= lat
            <= max(c[1] for c in f["geometry"]["coordinates"])]


def _clean_env(monkeypatch):
    for var in ("HEATMAP_MIN_USERS", "HEATMAP_PASS_SMOOTH_M",
                "HEATMAP_PASSES_PER_USER_CAP", "HEATMAP_PASS_PIECE_MIN_M",
                "HEATMAP_HEAT_NORM_PCTL"):
        monkeypatch.delenv(var, raising=False)


# ── env resolution ────────────────────────────────────────────────────────────

def test_smoothing_window_env(monkeypatch):
    monkeypatch.delenv("HEATMAP_PASS_SMOOTH_M", raising=False)
    assert rtd.pass_smooth_m() == rtd.DEFAULT_PASS_SMOOTH_M == 100.0
    monkeypatch.setenv("HEATMAP_PASS_SMOOTH_M", "150")
    assert rtd.pass_smooth_m() == 150.0
    monkeypatch.setenv("HEATMAP_PASS_SMOOTH_M", "0")
    assert rtd.pass_smooth_m() == 0.0  # explicit "off" (the hysteresis still bounds)
    for bad in ("-5", "garbage"):
        monkeypatch.setenv("HEATMAP_PASS_SMOOTH_M", bad)
        assert rtd.pass_smooth_m() == rtd.DEFAULT_PASS_SMOOTH_M


def test_passes_per_user_cap_env(monkeypatch):
    monkeypatch.delenv("HEATMAP_PASSES_PER_USER_CAP", raising=False)
    assert rtd.passes_per_user_cap() == rtd.DEFAULT_PASSES_PER_USER_CAP == 5
    monkeypatch.setenv("HEATMAP_PASSES_PER_USER_CAP", "3")
    assert rtd.passes_per_user_cap() == 3
    monkeypatch.setenv("HEATMAP_PASSES_PER_USER_CAP", "0")
    assert rtd.passes_per_user_cap() == 0  # explicit "uncapped" rollback
    for bad in ("-1", "2.5", "garbage"):
        monkeypatch.setenv("HEATMAP_PASSES_PER_USER_CAP", bad)
        assert rtd.passes_per_user_cap() == rtd.DEFAULT_PASSES_PER_USER_CAP


def test_effective_pass_formula():
    eff = rtd._effective_pass
    assert eff(540, 1, 5) == 5        # one rider's habitual route: capped
    assert eff(540, 6, 5) == 30
    assert eff(40, 10, 5) == 40       # many contributors: the passes count
    assert eff(540, 1, 0) == 540      # cap 0 = pre-fix popularity
    pbc = {1: 540, 2: 40, 3: 20, 4: 1}
    ubc = {1: "solo", 2: set(range(10)), 3: {"a", "b"}, 4: "x"}
    assert rtd._eff_histogram(pbc, ubc, 5) == {5: 1, 40: 1, 10: 1, 1: 1}
    assert rtd._eff_histogram(pbc, ubc, 0) == {540: 1, 40: 1, 20: 1, 1: 1}


def test_heat_norm_pctl_env(monkeypatch):
    monkeypatch.delenv("HEATMAP_HEAT_NORM_PCTL", raising=False)
    assert rtd.heat_norm_pctl() == rtd.DEFAULT_HEAT_NORM_PCTL == 99.9
    monkeypatch.setenv("HEATMAP_HEAT_NORM_PCTL", "95")
    assert rtd.heat_norm_pctl() == 95.0
    monkeypatch.setenv("HEATMAP_HEAT_NORM_PCTL", "100")
    assert rtd.heat_norm_pctl() == 100.0  # = the max
    for bad in ("0", "-3", "101", "garbage"):
        monkeypatch.setenv("HEATMAP_HEAT_NORM_PCTL", bad)
        assert rtd.heat_norm_pctl() == rtd.DEFAULT_HEAT_NORM_PCTL


def test_heat_normaliser_is_a_nearest_rank_percentile():
    hist = {20: 990, 300: 10}            # 1 000 cells, 1 % outliers
    assert rtd._heat_normaliser(hist, 99.0) == 20
    assert rtd._heat_normaliser(hist, 99.5) == 300
    assert rtd._heat_normaliser(hist, 100.0) == 300   # = the max
    assert rtd._heat_normaliser({}, 99.0) == 1
    assert rtd._heat_normaliser({1: 5}, 99.0) == 1


# ── rolling median by travelled distance ──────────────────────────────────────

def test_rolling_median_ignores_spikes_and_keeps_bands():
    n = 120
    cum = [3.0 * i for i in range(n)]
    # A lone 1-point spike (a lateral crossing) is removed; a 60 m band is kept
    # with its edges where they were (±1 point).
    a = [10] * n
    a[20] = 500
    for i in range(60, 80):
        a[i] = 200
    b = [1] * n
    out_a, out_b, _ = rtd._rolling_median3(a, b, b, cum, 50.0)
    assert out_a[20] == 10, "a lone spike must not survive the median"
    assert all(v == 200 for v in out_a[62:78]), "a sustained band is preserved"
    assert out_a[40] == 10 and out_a[100] == 10
    assert out_b == b
    # Output values are always input values (lower median) — counts stay ints.
    assert set(out_a) <= set(a)


def test_rolling_median_window_is_bounded_on_a_stop():
    """1 000 fixes piled on the same spot (a café stop at 1 Hz): the window
    is capped per side, so the smoothing stays O(n · cap), not O(n²)."""
    n = 1000
    cum = [0.0] * n
    a = list(range(n))
    out, _, _ = rtd._rolling_median3(a, [1] * n, [1] * n, cum, 50.0)
    cap = rtd._SMOOTH_MAX_HALF_PTS
    assert out[0] == (cap // 2)  # window [0, cap] → lower median
    assert out[n // 2] == n // 2


# ── (a) bug 1: local typical count on a noisy corridor, not the junction max ──

def _noisy_corridor(lon: float, junction_m: float, total_m: float = 2400.0,
                    junction_at_m: float = 300.0):
    """One 3 m-spaced run north along ``lon``. Cells within
    [junction_at_m, junction_at_m + junction_m) are a HOT junction: 540 passes
    / 6 contributors (the rider + 5). Every other cell is the noisy prod profile
    cycled cell after cell, 2 contributors (the rider + one other). Returns
    (run, pass_by_cell, users_by_cell, junction lat range, stretch values)."""
    sid = _sport_id("road")
    deg = lattice_deg()
    run = _north(int(total_m / 3.0), lon)
    pbc: dict = {}
    ubc: dict = {}
    j_lo = LAT + junction_at_m / 110_574
    j_hi = LAT + (junction_at_m + junction_m) / 110_574
    stretch_vals = []
    for idx, key in enumerate(_cells_along(run, sid, deg)):
        ilat = (((key >> rtd._COORD_BITS) & ((1 << rtd._COORD_BITS) - 1))
                - rtd._COORD_OFFSET)
        cell_lat = (ilat + 0.5) * deg
        if j_lo <= cell_lat < j_hi:
            pbc[key] = 540
            ubc[key] = {"rider", "o1", "o2", "o3", "o4", "o5"}
        else:
            pbc[key] = _NOISY[idx % len(_NOISY)]
            ubc[key] = {"rider", "other"}
            stretch_vals.append(pbc[key])
    return run, pbc, ubc, (j_lo, j_hi), sorted(stretch_vals)


def test_noisy_corridor_reports_the_local_typical_count(tmp_path, monkeypatch):
    """THE bug-1 pin. A 60 m hot junction (540 / 6) then a 2 km noisy stretch
    (2 contributors). Pre-fix: the junction is absorbed into ONE piece that
    reports 540 passes / 6 contributors everywhere (FAILS here). Fixed: the
    spot 2 km past the junction reports a count inside the stretch's own
    inter-quartile range and 2 contributors — both from the same (typical)
    place — and its heat is graded from min(pass, 2 × 5)."""
    _clean_env(monkeypatch)
    run, pbc, ubc, (j_lo, j_hi), vals = _noisy_corridor(-57.0, junction_m=60.0)
    _patch_lattice(monkeypatch, run, "road", pbc, ubc)
    path = os.path.join(tmp_path, "trails.geojsonl")
    export_raw_geojson(None, path)
    feats = _read(path)

    spot_lat = LAT + 2300.0 / 110_574  # 2 km past the junction
    at_spot = _features_covering(feats, spot_lat)
    assert len(at_spot) == 1
    p = at_spot[0]["properties"]
    q25, q75 = vals[len(vals) // 4], vals[(3 * len(vals)) // 4]
    assert q25 <= p["pass_count"] <= q75, (
        f"the spot must report the stretch's typical count ({q25}..{q75}), "
        f"got {p['pass_count']} (pre-fix: the junction MAX 540)")
    assert p["user_count"] == 2, (
        "user_count must describe the same typical place (2 contributors), "
        "not the junction's 6")
    # Popularity: every stretch cell has eff = min(pass ≥ 33, 2 × 5) = 10; the
    # scale's top is the p99 of cell eff = the junction's min(540, 6 × 5) = 30
    # (its ~5 cells are > 1 % of the ~220).
    expected = round(math.log1p(10) / math.log1p(30), 3)
    assert p["heat_score"] == expected
    assert p["heat_score"] < 0.75, "pre-fix: 0.97+ (the junction's 540)"
    # The junction's count may only be reported by a piece that actually
    # covers the junction — it never leaks along the ride.
    for f in feats:
        if f["properties"]["pass_count"] > max(vals):
            lats = [c[1] for c in f["geometry"]["coordinates"]]
            assert min(lats) <= j_hi and max(lats) >= j_lo


def test_long_junction_still_reads_hot_on_its_own_piece(tmp_path, monkeypatch):
    """Sanity companion: a junction LONGER than the hysteresis (400 m) is its
    own piece and still reports its real 540 / 6 — the fix grades the quiet
    part without flattening the busy one."""
    _clean_env(monkeypatch)
    run, pbc, ubc, (j_lo, j_hi), vals = _noisy_corridor(-57.2, junction_m=400.0)
    _patch_lattice(monkeypatch, run, "road", pbc, ubc)
    path = os.path.join(tmp_path, "trails.geojsonl")
    export_raw_geojson(None, path)
    feats = _read(path)

    hot = _features_covering(feats, (j_lo + j_hi) / 2)
    assert [(f["properties"]["pass_count"], f["properties"]["user_count"])
            for f in hot] == [(540, 6)]
    quiet = _features_covering(feats, LAT + 2300.0 / 110_574)
    assert len(quiet) == 1
    assert quiet[0]["properties"]["user_count"] == 2
    assert quiet[0]["properties"]["pass_count"] <= max(vals)
    assert hot[0]["properties"]["heat_score"] > quiet[0]["properties"]["heat_score"]


# ── (b) bug 2: same passes, more contributors → more popular ─────────────────

def test_same_passes_more_contributors_is_more_popular(tmp_path, monkeypatch):
    """THE bug-2 pin. Corridor SOLO: 20 activities by ONE rider. Corridor
    CROWD: 20 activities by 10 riders. Same pass_count (20) everywhere.
    Pre-fix: both heat 1.0 and equal heat_points w (FAILS). Fixed: the crowd
    corridor is strictly more popular (lines AND points share the scale),
    while the tooltip counts stay REAL (20 passes; 1 vs 10 contributors)."""
    _clean_env(monkeypatch)
    solo_lon, crowd_lon = -57.6, -57.9
    corridor = 200  # 15 m points → ~3 km, distinct cells

    def _iter(_db, **_kw):
        aid = 0
        for _ in range(20):
            aid += 1
            yield aid, "solo-rider", "road", _north(corridor, solo_lon, 15.0 / 110_574)
        for i in range(20):
            aid += 1
            yield aid, f"crowd-{i % 10}", "road", _north(corridor, crowd_lon, 15.0 / 110_574)

    monkeypatch.setattr(rtd, "iter_masked_runs", _iter)
    lines = os.path.join(tmp_path, "trails.geojsonl")
    points = os.path.join(tmp_path, "points.geojsonl")
    export_raw_geojson(None, lines, points_path=points)
    feats = _read(lines)
    pts = _read(points)

    def _at(items, lon):
        def _lon(f):
            c = f["geometry"]["coordinates"]
            return c[0] if f["geometry"]["type"] == "Point" else c[0][0]
        return [f for f in items if _lon(f) == lon]

    solo = _at(feats, solo_lon)
    crowd = _at(feats, crowd_lon)
    assert len(solo) == 20 and len(crowd) == 20
    for f in solo:
        assert (f["properties"]["pass_count"], f["properties"]["user_count"]) == (20, 1)
    for f in crowd:
        assert (f["properties"]["pass_count"], f["properties"]["user_count"]) == (20, 10)
    heat_solo = {f["properties"]["heat_score"] for f in solo}
    heat_crowd = {f["properties"]["heat_score"] for f in crowd}
    assert len(heat_solo) == 1 and len(heat_crowd) == 1
    assert heat_crowd.pop() > heat_solo.pop(), (
        "20 passes by 10 riders must burn brighter than 20 passes by one")

    w_solo = {round(f["properties"]["w"], 3) for f in _at(pts, solo_lon)}
    w_crowd = {round(f["properties"]["w"], 3) for f in _at(pts, crowd_lon)}
    assert w_solo and w_crowd
    assert min(w_crowd) > max(w_solo), "heat_points share the people-based scale"


# ── eff is smoothed per cell, never recombined from two medians ───────────────

def _patterned_corridor(lon: float, pattern: list, total_m: float = 1200.0):
    """One 3 m-spaced run whose cells cycle through ``pattern`` — a list of
    ``(pass_count, n_users)`` cell kinds."""
    sid = _sport_id("road")
    deg = lattice_deg()
    run = _north(int(total_m / 3.0), lon)
    pbc, ubc = {}, {}
    for idx, key in enumerate(_cells_along(run, sid, deg)):
        pc, nu = pattern[idx % len(pattern)]
        pbc[key] = pc
        ubc[key] = {f"u{k}" for k in range(nu)}
    return run, pbc, ubc


def test_effective_popularity_is_never_mixed_across_cells(tmp_path, monkeypatch):
    """Review pin. Cells cycle X, Y, X, Y, Z:
      X = 100 passes / 2 riders  → eff min(100, 10)  = 10
      Y =  10 passes / 10 riders → eff min(10, 50)   = 10
      Z = 100 passes / 10 riders → eff min(100, 50)  = 50 (one cell in five)
    Every window's MEDIAN pass is 100 (X, Z) and median users 10 (Y, Z), so
    recombining min(median pass, median users × 5) = 50 — the minority Z's
    popularity, at the top of the scale (heat 1.0) — although 80 % of the
    stretch is eff 10. The previous version did exactly that (FAILS here); eff
    must be read per cell and smoothed as its own series → 10."""
    _clean_env(monkeypatch)
    X, Y, Z = (100, 2), (10, 10), (100, 10)
    run, pbc, ubc = _patterned_corridor(-58.6, [X, Y, X, Y, Z])
    _patch_lattice(monkeypatch, run, "road", pbc, ubc)
    path = os.path.join(tmp_path, "trails.geojsonl")
    export_raw_geojson(None, path)
    # (The run's first metres see a truncated window and may form their own
    # short piece — read the piece in the middle of the stretch.)
    mid = _features_covering(_read(path), LAT + 600.0 / 110_574)
    assert len(mid) == 1
    p = mid[0]["properties"]
    # Tooltip counts stay the typical REAL counts of the stretch.
    assert (p["pass_count"], p["user_count"]) == (100, 10)
    # Colour: the typical CELL eff (10) on a scale topped by p99 = Z's 50.
    assert p["heat_score"] == round(math.log1p(10) / math.log1p(50), 3), (
        f"heat {p['heat_score']}: eff recombined from two medians (50 → 1.0)")

    # Unit view: every smoothed eff value is the eff of some real cell.
    sid = _sport_id("road")
    _p, _u, eff_s, _cum = rtd._local_series(
        run, sid, lattice_deg(), pbc, ubc, 5, rtd.pass_smooth_m())
    assert set(eff_s) <= {10, 50}


def test_heat_scale_tops_out_at_a_high_percentile_not_the_max(tmp_path, monkeypatch):
    """Review pin (normaliser). A ~15 km corridor of eff 20 (4 riders × 5) plus
    a one-cell outlier patch of eff 300 (60 riders), under 0.1 % of the cells.
    Scaled by the MAX (previous version), the corridor's lines read
    log1p(20)/log1p(300) ≈ 0.53 and never reach the top of the ramp (FAILS
    there); scaled by the DEFAULT percentile (p99.9) of cell eff they read 1.0 —
    and heat_points w share that scale. The corridor is long enough that the
    lone outlier cell stays above the p99.9 rank."""
    _clean_env(monkeypatch)
    assert rtd.heat_norm_pctl() == 99.9
    sid = _sport_id("road")
    deg = lattice_deg()
    main = _north(5000, -58.9)                       # ~15 km, ~1,350 cells
    patch = _north(3, -58.95)                        # 1 cell, off the corridor
    pbc, ubc = {}, {}
    for key in _cells_along(main, sid, deg):
        pbc[key] = 40
        ubc[key] = {f"r{k}" for k in range(4)}
    for key in _cells_along(patch, sid, deg):
        pbc[key] = 400
        ubc[key] = {f"o{k}" for k in range(60)}

    def _iter(_db, **_kw):
        yield 1, "rider", "road", list(main)
        yield 2, "other", "road", list(patch)

    monkeypatch.setattr(rtd, "iter_masked_runs", _iter)
    monkeypatch.setattr(rtd, "_accumulate_lattice",
                        lambda _db, _deg, **_kw: (pbc, ubc, {}, {}, {}, {}))
    lines = os.path.join(tmp_path, "trails.geojsonl")
    points = os.path.join(tmp_path, "points.geojsonl")
    export_raw_geojson(None, lines, points_path=points)
    corridor = [f for f in _read(lines)
                if f["geometry"]["coordinates"][0][0] == -58.9]
    assert corridor and all(f["properties"]["heat_score"] == 1.0 for f in corridor)
    w = {f["properties"]["w"] for f in _read(points)
         if f["geometry"]["coordinates"][0] == -58.9}
    assert w == {1.0}


# ── (c) OOM guard: a long noisy run stays bounded ─────────────────────────────

def test_noisy_run_piece_count_stays_bounded(tmp_path, monkeypatch):
    """A 15 km run whose per-cell pass AND user counts are random across many
    levels (worst case for the 2-D level) never yields more than
    ~run_length / min_piece_m pieces — with the default smoothing, with the
    smoothing OFF, and end-to-end through the real emitter."""
    _clean_env(monkeypatch)
    rng = random.Random(1234)
    sid = _sport_id("road")
    deg = lattice_deg()
    run = _north(5000, -58.2)  # ~15 km at 3 m
    pbc, ubc = {}, {}
    for key in _cells_along(run, sid, deg):
        pbc[key] = rng.choice([1, 2, 3, 5, 9, 17, 33, 65, 129, 257, 540])
        ubc[key] = set(range(rng.randint(1, min(pbc[key], 12))))
    min_m = pass_piece_min_m()
    bound = math.ceil(_run_len_m(run) / min_m) + 1

    for smooth in (None, 0.0):
        pieces = _split_run_by_local_pass(run, sid, deg, pbc, users_by_cell=ubc,
                                          smooth_m=smooth)
        assert len(pieces) <= bound, (smooth, len(pieces), bound)
        # Trace integrity: the pieces partition the run at shared vertices.
        union = list(pieces[0])
        for piece in pieces[1:]:
            assert piece[0] == union[-1]
            union.extend(piece[1:])
        assert union == run

    # The generator the emitters use agrees with the point-list view.
    n_iter = sum(1 for _ in rtd._iter_local_pieces(
        run, sid, "road", deg, pbc, ubc, {}, {}, {}, {}, 1.0,
        cap=rtd.passes_per_user_cap(), smooth_m=rtd.pass_smooth_m(), min_piece_m=min_m))
    assert n_iter == len(_split_run_by_local_pass(run, sid, deg, pbc, users_by_cell=ubc))

    _patch_lattice(monkeypatch, run, "road", pbc, ubc)
    written = export_raw_geojson(None, os.path.join(tmp_path, "t.geojsonl"))
    assert 1 <= written <= bound, f"{written} features for one run (bound {bound})"
    print(f"\n[noisy-15km] bound={bound} features={written}")
