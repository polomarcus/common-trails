"""Per-feature LOCAL pass_count / heat_score — the whole-run-MAX regression.

Bug (verdict C, fix/pass-count-local): each emitted ``trails`` feature is ONE
masked ride (tens of km); its headline ``pass_count`` used to be the MAX over
EVERY cell the run crosses, so a QUIET stretch inherited the busiest cell's
distinct-activity count from ANYWHERE on the ride (a junction/corridor it merely
passed through). The click-tooltip reported e.g. "88 passages" on a spot that
saw far fewer, and ``heat_score`` (derived from the same MAX) lit the whole ride
at its busiest cell's brightness instead of grading it.

Fix: split each run into contiguous PIECES by the underlying cell's popularity
BUCKET (coarse log2) and emit each piece with its OWN local count. These pins
FAIL on the old whole-run-MAX code and PASS on the fix:

  * ``_split_run_by_local_pass`` cuts ONLY at existing vertices, shares the cut
    vertex (continuous), and reconstructs the run byte-for-byte (trace
    integrity). A uniform run is a strict no-op (returns ``[sub]``).
  * end-to-end through the REAL ``export_raw_geojson``: a trace crossing a HOT
    junction then continuing ALONE into a quiet stretch emits a lone-stretch
    feature with ``pass_count == 1`` (its local cell count), NOT the junction
    MAX. On the old code the lone ride was ONE feature spanning both, carrying
    the junction MAX everywhere — so no all-quiet feature with count 1 exists.
"""
from __future__ import annotations

import json
import math
import os

from app.services import raw_trace_display as rtd
from app.services.geo import haversine_m
from app.services.raw_trace_display import (
    _cell_key,
    _pass_bucket,
    _piece_feature_props,
    _split_run_by_local_pass,
    _sport_id,
    export_raw_geojson,
    lattice_deg,
    pass_piece_min_m,
)

LAT = 43.61
# ~15 m per point (> the ~11 m lattice cell) so consecutive points land in
# DISTINCT cells — makes the per-cell pass_count crisp.
STEP = 15.0 / 110_574
_LON = -55.0  # empty mid-Atlantic corridor, unused by any other test → isolated


def _north(n: int, lon: float = _LON, lat0: float = LAT) -> list:
    return [[lon, lat0 + i * STEP] for i in range(n)]


# ── _pass_bucket: coarse log2 bins ───────────────────────────────────────────

def test_pass_bucket_is_coarse_log2():
    # 0 and 1 share bucket 0; then floor(log2): 2-3→1, 4-7→2, 8-15→3, …
    assert _pass_bucket(0) == 0
    assert _pass_bucket(1) == 0
    assert _pass_bucket(2) == 1 and _pass_bucket(3) == 1
    assert _pass_bucket(4) == 2 and _pass_bucket(7) == 2
    assert _pass_bucket(8) == 3 and _pass_bucket(15) == 3
    assert _pass_bucket(88) == 6
    # Coarse: even the prod max (~88) fits in ~7 buckets, so a ride yields a
    # handful of pieces, never one-per-11 m-cell.
    assert _pass_bucket(10_000) < 14


# ── _split_run_by_local_pass: cut-at-vertex, share vertex, reconstruct ────────

def _reconstruct(pieces: list) -> list:
    """Union the pieces back into one polyline, dropping each piece's first
    vertex (the vertex it SHARES with the previous piece's last vertex)."""
    out = list(pieces[0])
    for p in pieces[1:]:
        assert p[0] == out[-1], "adjacent pieces must SHARE the cut vertex"
        out.extend(p[1:])
    return out


def test_split_uniform_run_is_a_noop():
    """A run whose cells all share a bucket (the common single-user corridor)
    returns [sub] unchanged — same object contents, no split, no mutation."""
    sid = _sport_id("road")
    deg = lattice_deg()
    sub = _north(40)
    pass_by_cell = {_cell_key(sid, p[0], p[1], deg): 5 for p in sub}  # uniform
    pieces = _split_run_by_local_pass(sub, sid, deg, pass_by_cell)
    assert len(pieces) == 1
    assert pieces[0] == sub


def test_split_cuts_at_bucket_change_and_reconstructs_exactly():
    """A run that is HOT (pass 13, bucket 3) for its first half then QUIET
    (pass 1, bucket 0) splits into exactly two pieces; the pieces share the cut
    vertex, reconstruct the run byte-for-byte, and NO coordinate is moved."""
    sid = _sport_id("road")
    deg = lattice_deg()
    sub = _north(40)
    half = 20
    pass_by_cell = {}
    for i, p in enumerate(sub):
        pass_by_cell[_cell_key(sid, p[0], p[1], deg)] = 13 if i < half else 1

    pieces = _split_run_by_local_pass(sub, sid, deg, pass_by_cell)
    assert len(pieces) == 2, "one hot band + one quiet band → two pieces"
    # Cut at an existing vertex, shared by both pieces, exact reconstruction.
    assert _reconstruct(pieces) == sub
    # Byte-identical shape: every emitted coordinate is an original vertex.
    orig = {tuple(p) for p in sub}
    for piece in pieces:
        for c in piece:
            assert tuple(c) in orig, "split must not resample/snap/move a vertex"


# ── _piece_feature_props: the graded slice excludes the shared cut vertex ─────
# The reviewer's exact reproduction: a 40-pt run, first 20 cells pass=1, last 20
# pass=13. HOT→QUIET must grade [13, 1]; QUIET→HOT must grade [1, 13]. The
# half-fix (grade over ALL piece points) gives QUIET→HOT [13, 13] because the
# shared cut vertex is the HOT band's start. Drives the REAL functions.

def _grade_pieces(sub, pass_by_cell, sid, deg):
    """Split ``sub`` and grade every piece via the REAL _piece_feature_props;
    return the list of per-piece pass_counts in drawn order."""
    log_max = 1.0  # heat_score irrelevant here; pass_count is what we assert
    pieces = _split_run_by_local_pass(sub, sid, deg, pass_by_cell)
    last = len(pieces) - 1
    out = []
    for idx, piece in enumerate(pieces):
        _, props = _piece_feature_props(
            piece, idx == last, sid, "road", deg, pass_by_cell, {}, {}, {}, {},
            {}, log_max)
        out.append(props["pass_count"])
    return out


def test_piece_grade_excludes_shared_vertex_both_directions():
    sid = _sport_id("road")
    deg = lattice_deg()

    # HOT (pass 13) first 20 cells → QUIET (pass 1) last 20.
    hot_then_quiet = _north(40)
    pbc = {}
    for i, p in enumerate(hot_then_quiet):
        pbc[_cell_key(sid, p[0], p[1], deg)] = 13 if i < 20 else 1
    assert _grade_pieces(hot_then_quiet, pbc, sid, deg) == [13, 1]

    # QUIET (pass 1) first 20 → HOT (pass 13) last 20. The shared cut vertex is
    # the HOT band's START; grading the quiet approach over piece[:-1] keeps it
    # at 1 (the half-fix graded [13, 13]).
    quiet_then_hot = _north(40)
    pbc2 = {}
    for i, p in enumerate(quiet_then_hot):
        pbc2[_cell_key(sid, p[0], p[1], deg)] = 1 if i < 20 else 13
    assert _grade_pieces(quiet_then_hot, pbc2, sid, deg) == [1, 13]


# ── End-to-end: lone quiet stretch keeps its LOCAL count, not the junction MAX ─

def _hot_junction_corpus(n_hot: int, junction_pts: int, quiet_pts: int,
                         junction_at: str = "head"):
    """A fixed ``iter_masked_runs`` stream: ``n_hot`` runs over a SHARED junction
    plus ONE lone run that both crosses the junction AND continues ALONE into a
    quiet stretch. All at ``_LON``, sport 'road' (non-directional → no
    orientation reversal, so the geometry assertions read cleanly). Junction
    cells see ``n_hot`` + 1 activities; the quiet cells see the lone activity
    only (pass_count 1).

    ``junction_at='head'`` → lone = junction THEN quiet (HOT→QUIET, departure).
    ``junction_at='tail'`` → lone = quiet THEN junction (QUIET→HOT, approach);
    the hot runs ride the lone's TAIL cells. The approach direction is the one
    that exposes the shared-boundary-vertex half-fix (the shared cut vertex is
    the START of the hot band and would dominate the quiet approach's max)."""
    lone = _north(junction_pts + quiet_pts)
    junction = lone[:junction_pts] if junction_at == "head" else lone[quiet_pts:]

    def _factory(_db):
        for i in range(n_hot):
            yield i, f"hot-user-{i}", "road", list(junction)
        yield n_hot, "lone-user", "road", list(lone)

    return _factory, junction, lone


def test_lone_quiet_stretch_keeps_local_pass_count(tmp_path, monkeypatch):
    """THE regression pin. On the old whole-run-MAX code the lone ride is ONE
    feature spanning junction+quiet carrying the junction MAX (13) everywhere,
    so NO all-quiet feature with pass_count 1 exists → this FAILS. On the fix the
    quiet stretch is its own piece with pass_count == 1 → this PASSES."""
    monkeypatch.delenv("HEATMAP_MIN_USERS", raising=False)  # gate = identity
    n_hot = 12
    junction_pts, quiet_pts = 30, 60
    factory, junction, _lone = _hot_junction_corpus(n_hot, junction_pts, quiet_pts)
    monkeypatch.setattr(rtd, "iter_masked_runs", factory)

    path = os.path.join(tmp_path, "raw.geojsonl")
    export_raw_geojson(None, path)
    with open(path) as fh:
        feats = [json.loads(line) for line in fh if line.strip()]

    # Deep-quiet = strictly BEYOND the junction's last latitude → only the lone
    # ride reaches here (the hot runs stop at the junction).
    junction_end_lat = junction[-1][1]
    deep_quiet = [
        f for f in feats
        if all(c[0] == _LON and c[1] > junction_end_lat + STEP
               for c in f["geometry"]["coordinates"])
    ]
    assert deep_quiet, (
        "the lone ride's quiet stretch must emit its OWN feature — on the old "
        "whole-run-MAX code it was folded into one junction-MAX feature")
    for f in deep_quiet:
        assert f["properties"]["pass_count"] == 1, (
            "a quiet stretch must report its LOCAL pass_count (1), NOT the "
            "distant junction MAX")
        # heat_score graded from the LOCAL pass (dim), not the junction max.
        assert f["properties"]["heat_score"] < 0.5

    # Sanity: the junction itself still burns at its true (high) count, proving
    # we regraded the quiet part WITHOUT flattening the busy part.
    n_expected = n_hot + 1
    junction_lat0 = junction[0][1]
    junction_feats = [
        f for f in feats
        if all(c[0] == _LON and c[1] <= junction_end_lat
               for c in f["geometry"]["coordinates"])
        and any(c[1] > junction_lat0 + 3 * STEP  # a mid-junction (unmasked) vertex
                for c in f["geometry"]["coordinates"])
    ]
    assert any(f["properties"]["pass_count"] == n_expected for f in junction_feats), (
        f"the junction core must still report its true pass_count {n_expected}")


def test_quiet_approach_into_hot_band_keeps_local_pass_count(tmp_path, monkeypatch):
    """THE half-fix pin (QUIET→HOT approach direction). The lone ride runs quiet
    FIRST, then into the hot junction. The split shares the cut vertex, and that
    shared vertex is the START of the HOT band. Grading the quiet APPROACH piece
    over ALL its points (the current-branch half-fix) lets that one hot boundary
    vertex dominate → the approach reports the junction MAX (this FAILS). Grading
    it over piece[:-1] (the fix) excludes the shared vertex → pass_count == 1
    (this PASSES). The existing test only covers the HOT→QUIET departure, which
    the half-fix already handled — this pins the direction it missed."""
    monkeypatch.delenv("HEATMAP_MIN_USERS", raising=False)  # gate = identity
    n_hot = 12
    junction_pts, quiet_pts = 30, 60
    factory, _junction, _lone = _hot_junction_corpus(
        n_hot, junction_pts, quiet_pts, junction_at="tail")
    monkeypatch.setattr(rtd, "iter_masked_runs", factory)

    path = os.path.join(tmp_path, "raw.geojsonl")
    export_raw_geojson(None, path)
    with open(path) as fh:
        feats = [json.loads(line) for line in fh if line.strip()]

    # The APPROACH feature is the one carrying the very first (lowest-lat) vertex
    # of the lone ride — only the lone ride starts down here (the hot runs live
    # in the tail junction), so this uniquely selects the quiet approach piece.
    first_v = [round(_LON, 6), round(LAT, 6)]
    approach = [f for f in feats
                if first_v in f["geometry"]["coordinates"]]
    assert len(approach) == 1, "exactly one feature carries the lone ride's start"
    assert approach[0]["properties"]["pass_count"] == 1, (
        "the QUIET approach into a HOT band must report its LOCAL pass_count "
        "(1), NOT the hot boundary vertex's junction MAX (the half-fix bug)")
    assert approach[0]["properties"]["heat_score"] < 0.5, "quiet approach → dim"

    # Sanity: the hot junction band still burns at its true count (the regrade
    # did not flatten the busy part) — the busiest feature reports n_hot + 1.
    assert max(f["properties"]["pass_count"] for f in feats) == n_hot + 1, (
        f"the hot junction must still report its true pass_count {n_hot + 1}")


def test_lone_ride_pieces_reconstruct_the_masked_run(tmp_path, monkeypatch):
    """Trace integrity end-to-end: the lone ride's emitted pieces, unioned in
    order (sharing cut vertices), reproduce its masked run byte-for-byte — the
    split only PARTITIONS, never reshapes."""
    monkeypatch.delenv("HEATMAP_MIN_USERS", raising=False)
    n_hot = 12
    factory, _junction, lone = _hot_junction_corpus(n_hot, 30, 60)
    monkeypatch.setattr(rtd, "iter_masked_runs", factory)

    path = os.path.join(tmp_path, "raw.geojsonl")
    export_raw_geojson(None, path)
    with open(path) as fh:
        feats = [json.loads(line) for line in fh if line.strip()]

    # The lone ride is the only geometry reaching beyond the junction; collect
    # every feature that lies within [lone start .. lone end] and is at _LON.
    lone_rounded = [[round(p[0], 6), round(p[1], 6)] for p in lone]
    lone_feats = [
        f for f in feats
        if all(c[0] == _LON for c in f["geometry"]["coordinates"])
        and all([round(c[0], 6), round(c[1], 6)] in lone_rounded
                for c in f["geometry"]["coordinates"])
        and any(c[1] > lone[29][1] for c in f["geometry"]["coordinates"])  # reaches quiet
    ]
    assert len(lone_feats) >= 2, "the lone ride must split into ≥2 local pieces"
    # Order pieces by their first latitude, then union (share the cut vertex).
    lone_feats.sort(key=lambda f: f["geometry"]["coordinates"][0][1])
    pieces = [f["geometry"]["coordinates"] for f in lone_feats]
    union = list(pieces[0])
    for p in pieces[1:]:
        assert p[0] == union[-1], "consecutive lone pieces must share the cut vertex"
        union.extend(p[1:])
    # RE-PINNED 2026-09-06 (perf/strip-interpolated-emission): the emitter now
    # strips exactly-collinear vertices from the DRAWN line (_strip_interpolated
    # — removing the read-time densifier's lerp artifacts; this synthetic run is
    # perfectly straight, so its interior vertices are elided too). The contract
    # becomes: the union is an ORDERED SUBSET of the masked run with the SAME
    # endpoints and shared cut vertices — the identical drawn line, partitioned,
    # never reshaped, no invented/moved/reordered vertex.
    masked = [[round(p[0], 6), round(p[1], 6)] for p in lone]
    assert union[0] == masked[0], "union must start at the masked run's start"
    assert union[-1] == masked[-1], "union must end at the masked run's end"
    search_from = 0
    for v in union:
        assert v in masked[search_from:], (
            f"union vertex {v} is not a masked-run vertex in order — the strip "
            "must only ELIDE vertices, never invent/move/reorder them")
        search_from = masked.index(v, search_from) + 1


# ── Bounded split: GPS-noise bucket flicker must NOT fragment (the OOM) ────────
# fix/regrade-oom-and-pool. On REAL prod data GPS noise makes a cell's
# pass_count oscillate cell-to-cell, so the naive "cut wherever the bucket
# changes" split emitted one feature PER SEGMENT → 1,321,624 line features /
# 634 MB geojsonl → tippecanoe OOM-killed the 2 Gi build → the map stopped
# updating on every rebuild. The fix bounds pieces-per-run to
# ~ceil(run_len / min_piece_m) via a minimum-piece-length hysteresis. These
# pins drive the REAL _split_run_by_local_pass (no inline mirror): the SAME
# function with a near-zero min_piece_m reproduces the pre-fix per-segment
# explosion, proving the bound is what tamed it.

def _run_len_m(sub: list) -> float:
    return sum(haversine_m(sub[i - 1][0], sub[i - 1][1], sub[i][0], sub[i][1])
               for i in range(1, len(sub)))


def _bound(sub: list, min_piece_m: float) -> int:
    """The design cap: ~one piece per min_piece_m of travel (+1 for the leading
    partial band). Each cut consumes >= min_piece_m of sustained new-bucket
    travel, so the piece count cannot exceed this."""
    return math.ceil(_run_len_m(sub) / min_piece_m) + 1


def test_cell_to_cell_noise_does_not_fragment(monkeypatch):
    """THE OOM pin. A long run whose pass_count oscillates 1↔64 EVERY cell
    (pure GPS-noise flicker) must NOT emit one feature per segment. With a
    near-zero min_piece_m the REAL function reproduces the pre-fix explosion
    (≈one piece per point); with the default hysteresis the flicker is absorbed
    and the piece count collapses far below the ceil(run_len/min) bound."""
    monkeypatch.delenv("HEATMAP_PASS_PIECE_MIN_M", raising=False)  # default 200 m
    sid = _sport_id("road")
    deg = lattice_deg()
    n = 800
    sub = _north(n)
    # pass_count alternates 1 (bucket 0) and 64 (bucket 6) on adjacent cells.
    pbc = {_cell_key(sid, p[0], p[1], deg): (1 if i % 2 == 0 else 64)
           for i, p in enumerate(sub)}

    # BEFORE (naive per-change split, reproduced via min_piece_m→0): explodes to
    # ~one piece per segment — the 1.3 M-feature prod OOM in miniature.
    before = len(_split_run_by_local_pass(sub, sid, deg, pbc, min_piece_m=1e-6))
    assert before >= n - 2, (
        f"sanity: near-zero min must reproduce the per-segment explosion "
        f"(got {before} for {n} points)")

    # AFTER (default hysteresis): pure cell-to-cell flicker never sustains a new
    # bucket for min_piece_m → it is fully absorbed → a single piece.
    min_m = pass_piece_min_m()
    after = len(_split_run_by_local_pass(sub, sid, deg, pbc))
    assert after <= _bound(sub, min_m), (
        f"piece count {after} must be bounded by ~ceil(run_len/{min_m}m) "
        f"= {_bound(sub, min_m)}, not the {before}-piece explosion")
    assert after < before // 4, (
        f"the fix must collapse the {before}-piece flicker (got {after})")
    print(f"\n[noisy-flicker] points={n} run={_run_len_m(sub):.0f}m "
          f"min_piece_m={min_m} BEFORE={before} AFTER={after}")


def test_noisy_bands_split_bounded_but_still_grades(monkeypatch):
    """Genuine hot/quiet bands (each longer than min_piece_m) WITH per-cell
    noise on top: the split still cuts at the real band transitions (so quiet
    stretches keep their local dim grade) AND stays bounded by
    ceil(run_len/min) — it does not fragment on the intra-band noise."""
    monkeypatch.delenv("HEATMAP_PASS_PIECE_MIN_M", raising=False)
    sid = _sport_id("road")
    deg = lattice_deg()
    n = 600
    band = 30  # ~450 m > 200 m default → each band sustains one cut
    sub = _north(n)
    pbc = {}
    for i, p in enumerate(sub):
        high = (i // band) % 2 == 1
        even = i % 2 == 0
        # base band + a per-cell noise flip that stays within the band's half of
        # the popularity scale (high: 64/32 → buckets 6/5; low: 1/2 → buckets 0/1).
        pv = (64 if even else 32) if high else (1 if even else 2)
        pbc[_cell_key(sid, p[0], p[1], deg)] = pv

    min_m = pass_piece_min_m()
    pieces = _split_run_by_local_pass(sub, sid, deg, pbc)
    n_bands = math.ceil(n / band)
    assert len(pieces) > 1, "genuine bands must still split (regrade must work)"
    assert len(pieces) <= _bound(sub, min_m), (
        f"noisy bands: {len(pieces)} pieces must stay under the "
        f"ceil(run_len/{min_m}m)={_bound(sub, min_m)} cap")
    # Trace integrity survives the bounded split.
    assert _reconstruct(pieces) == sub

    # The split tracks the real bands, not the noise: piece count is a small
    # multiple of the band count (~1.5×, from the detection-latency zone around
    # each transition), never the ~600-point per-cell explosion.
    assert len(pieces) <= 2 * n_bands, (
        f"{len(pieces)} pieces must stay a small multiple of {n_bands} bands")
    assert len(pieces) < n // 4, "must not fragment toward one piece per cell"
    print(f"\n[noisy-bands] points={n} bands={n_bands} "
          f"min_piece_m={min_m} pieces={len(pieces)}")


# ── _strip_interpolated: only the densifier's lerp points are removed ─────────

def test_strip_interpolated_removes_only_densifier_points():
    """Real (bending) vertices survive the emission strip EXACTLY; the ≤3 m
    lerp points the read-time densifier injects are all removed. This is the
    load-bearing guarantee of perf/strip-interpolated-emission: −61% geojsonl
    measured with ZERO drawn-shape change."""
    from app.services.ingest import _densify_coords
    # Genuine bends (each triple deviates far beyond _EMIT_SIMPLIFY_TOL_DEG).
    orig = [[3.8700, 43.6100], [3.8720, 43.6111], [3.8735, 43.6118],
            [3.8760, 43.6120], [3.8770, 43.6140]]
    dense = _densify_coords([list(p) for p in orig], max_gap=3.0)
    assert len(dense) > 5 * 5, "densifier must have injected many lerp points"
    stripped = rtd._strip_interpolated(dense)
    assert [[p[0], p[1]] for p in stripped] == orig, (
        "strip must return exactly the original vertices — nothing more "
        "(lerp left behind), nothing less (a real bend removed)")


def test_strip_interpolated_keeps_endpoints_and_short_runs():
    two = [[3.87, 43.61], [3.88, 43.62]]
    assert rtd._strip_interpolated(two) == two
    # A perfectly straight run collapses to its endpoints — same drawn line.
    straight = [[3.87, 43.61 + i * 1e-4] for i in range(50)]
    s = rtd._strip_interpolated(straight)
    assert s[0] == tuple(straight[0]) or list(s[0]) == straight[0]
    assert list(s[-1]) == straight[-1] or s[-1] == tuple(straight[-1])
    assert len(s) == 2


def test_strip_tolerance_order_of_magnitude_is_pinned():
    """Review pin (PR #24): a bend deviating ~1e-7° (≈1 cm — 100× the strip
    tolerance, far below GPS noise) must SURVIVE the strip. Guards against a
    future tolerance inflation silently simplifying REAL geometry while the
    macroscopic-bend tests still pass."""
    mid = [3.8710 + 1e-7, 43.6105]  # 1 cm off the exact chord midpoint
    pts = [[3.8700, 43.6100], mid, [3.8720, 43.6110]]
    out = [list(p) for p in rtd._strip_interpolated(pts)]
    assert mid in out, "a 1 cm bend must never be stripped"
    # ...while a truly-collinear midpoint IS stripped (the feature works).
    exact = [[3.8700, 43.6100], [3.8710, 43.6105], [3.8720, 43.6110]]
    assert len(rtd._strip_interpolated(exact)) == 2
