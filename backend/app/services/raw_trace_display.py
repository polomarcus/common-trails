"""Raw-trace display source — flag-gated alternative to the matched heatmap.

Product decision (Paul, 2026-07-21): render **precise raw GPS traces** (the
Strava raw-raster / law-of-large-numbers look) instead of the K-anonymised,
OSM-matched, cross-rider ``heat_edges_agg`` aggregate. Privacy is handled by
**endpoint masking** (``trace_privacy.mask_endpoints`` — trim home/work off
each trace) rather than K-anonymity.

Selected by ``HEATMAP_DISPLAY_SOURCE=raw`` (default ``matched`` → prod
behaviour is byte-identical to today; this module is never touched). When
``raw``, ``build_pmtiles`` streams the GeoJSON produced here into the SAME
tippecanoe + GCS-upload pipeline, so the existing frontend
(``community-heatmap-layers.ts``) renders it with NO change: features are
``LineString``s in the ``trails`` layer carrying ``sport`` / ``user_count`` /
``pass_count`` / ``heat_score`` / ``highway_type`` — the props the paint ramps
key off.

Geometry is the REAL (masked) GPS polyline — precise, never snapped to a grid.
A density lattice (``HEATMAP_RAW_LATTICE_DEG``, default ~11 m) is used ONLY to
*rasterize a popularity value* (``heat_score``) so busy corridors burn brighter
through the existing ramp; it never reshapes the drawn geometry.

**Bounded-memory streaming build** (rework 2026-07, ``perf/raw-build-streaming``).
The build NEVER materialises the whole corpus of runs/points in RAM. It makes
TWO streaming passes over the consented activities (server-side cursor, one
activity's geometry in flight at a time):

  * **Pass 1 — accumulate the bounded lattice.** Per occupied cell keep an INT
    ``pass_count`` (count of distinct contributing activities) + a compact
    distinct-``user_id`` counter (a bare id, promoted to a capped set only on a
    2nd distinct user — drives the ``HEATMAP_MIN_USERS`` gate and the true
    ``user_count``). Cells are keyed by a packed int64 (not a tuple). Memory is
    bounded by OCCUPIED CELL COUNT (+ distinct users per cell), NOT by the number
    of activities or their densified points.
  * **Pass 2 — emit the GeoJSONL, streaming to disk.** Re-stream the masked
    runs and write one ``LineString`` feature per surviving (gated) sub-run as
    we go — the feature list is never held in RAM either.

The drawn geometry is the SAME masked polyline as the prototype; the lattice is
used ONLY for the per-run count/gate decision, so the render is byte-for-byte
the "precise raw traces" look — only the COUNTING is now streaming + bounded.
"""
from __future__ import annotations

import contextlib
import json
import logging
import math
import os

from sqlalchemy import text as sa_text

from app.services.geo import haversine_m
from app.services.trace_privacy import mask_endpoints, mask_meters

log = logging.getLogger(__name__)

# Glitch-outlier rejection (raw mode draws GPS points VERBATIM, so a corrupt
# coordinate — e.g. an 8-point mid-Atlantic GPS spike inside an otherwise-French
# gravel ride — draws a garbage line the matched pipeline used to reject
# implicitly, having no OSM match). One activity is ONE ride in ONE place, so a
# point implausibly far from the activity's own spatial MEDIAN is a glitch, not
# signal. Location-AGNOSTIC (works for a ride anywhere on earth — keeps all real
# desire lines) — it keys off distance-from-the-ride's-own-body, not a fixed
# bbox. Default span is generous (a single ride realistically stays well under
# it); ``HEATMAP_RAW_MAX_SPAN_KM=0`` (or ``off``) disables the guard.
DEFAULT_MAX_SPAN_KM = 300.0

# ~11 m at France latitudes (0.0001° lat ≈ 11.1 m; lon ≈ 8 m at cos 43.6°).
# The lattice is ONLY the popularity/heat_score density counter + the MIN_USERS
# gate resolution — it NEVER reshapes the drawn geometry (that stays the verbatim
# masked ``round(p, 6)`` GPS polyline). It was ~5 m, but the accumulator is
# bounded by OCCUPIED CELL COUNT, and ~5 m OOM'd the prod build (4,992,991 cells
# / 1875 traces ≈ 7 M cells at prod scale → ~7.6 GB accumulator). ~11 m cuts the
# cell count ~4-5× with no visible change to the render (an 11 m density bin is
# still far finer than the paint ramp reads). Env-overridable for a finer/coarser
# lattice if a future national build has the RAM budget.
DEFAULT_LATTICE_DEG = 0.0001

# Mean metres per degree at mid-metropolitan-France latitude (~46.5°N): the
# average of the latitude degree (~111,132 m) and the longitude degree
# (111,320·cos 46.5° ≈ 76,600 m). Used ONLY to turn the occupied-cell count into
# the "km de chemins" network-length estimate (see export_raw_geojson) — a
# banner figure, never geometry — so a single national-scale mean is fine.
_MEAN_M_PER_DEG = 93_900.0

# Compact int64 cell key packing (replaces the ~120-byte ``(sport, ilat, ilon)``
# tuple key + its two large-int members). We fold the cell into a single Python
# int: ``(sport_id << 56) | ((ilat + OFFSET) << 28) | (ilon + OFFSET)``. The
# offset makes the signed grid indices non-negative; 28 bits/coord covers
# |index| < 2^27 ≈ 134 M, i.e. any lattice ≥ ~1.3e-6° (finer than any sane
# value). One shared int object per occupied cell keys BOTH accumulator dicts.
_COORD_OFFSET = 1 << 27
_COORD_BITS = 28

# Density-POINT emission stride (metres) for the ``heat_points`` layer — the
# raster/density visual (maplibre ``heatmap`` layer type) that replaced the
# diffuse vector-line "pâté". Pass 2 walks each drawn (sub-)run and emits ONE
# weighted Point every ~``stride_m`` along it (reusing the same masked runs the
# ``trails`` layer draws — no extra DB pass, O(1) memory per run). ~20 m keeps
# the point count a fraction of the ~3 m densified vertices while staying far
# finer than the heatmap-radius reads at any zoom; overlapping traces stack
# points in the same corridor so ``heatmap-density`` accumulates into the field.
# ``HEATMAP_RAW_POINT_STRIDE_M`` overrides; tippecanoe ``--drop-densest-as-needed``
# thins further at low zoom so a coarse ride never blobs.
DEFAULT_POINT_STRIDE_M = 20.0

# Per-cell distinct-user cap. Beta density is ~1 user/cell, so cells store a bare
# ``user_id`` (see ``_add_user``) and this cap is never approached; at national
# scale it bounds a hot cell's user set to ``_USER_CAP`` ids (``user_count``
# saturates there — a tolerated display estimate, and always ≥ any realistic K).
_USER_CAP = 64

# Process-local sport → small-int id. Stable within a build run (ids assigned on
# first sight); cross-run stability is irrelevant since keys are transient.
_SPORT_IDS: dict[str, int] = {}


def _sport_id(sport: str) -> int:
    sid = _SPORT_IDS.get(sport)
    if sid is None:
        sid = len(_SPORT_IDS)
        _SPORT_IDS[sport] = sid
    return sid


def _cell_key(sport_id: int, lon: float, lat: float, deg: float) -> int:
    ilat = int(math.floor(lat / deg))
    ilon = int(math.floor(lon / deg))
    return (sport_id << 56) | ((ilat + _COORD_OFFSET) << _COORD_BITS) | (ilon + _COORD_OFFSET)


def _add_user(users: dict, key: int, user_id) -> None:
    """Fold ``user_id`` into the bounded per-cell distinct-user counter.

    Representation is compact-by-arity: ``absent`` → the FIRST user is stored as
    a bare value (no ``set`` object — ~216 bytes saved on the beta-dominant
    1-user cell); only a genuine SECOND distinct user promotes the cell to a
    ``set`` (capped at ``_USER_CAP``). Exact at the K-anonymity boundary by
    construction (2 distinct users → a 2-element set → ``_user_count`` == 2), so
    the ``HEATMAP_MIN_USERS`` gate never mis-fires at the K=2 flip.
    """
    v = users.get(key)
    if v is None:
        users[key] = user_id
    elif type(v) is set:
        if len(v) < _USER_CAP:
            v.add(user_id)
    elif v != user_id:
        users[key] = {v, user_id}


def _user_count(v) -> int:
    if v is None:
        return 0
    return len(v) if type(v) is set else 1


# ── Directional heatmap (one-way MTB/gravel singletrack) ─────────────────────
# Distinguish trail cells ridden predominantly in ONE direction (singletrack
# descents) from bidirectional ones, so the frontend can draw direction arrows
# on the one-way ones. Model = per-cell CIRCULAR CONCENTRATION of travel
# bearings: the mean resultant length R = |Σ w·e^{iθ}| / Σ w ∈ [0, 1] over the
# bearings θ of the segments through the cell (w = segment length). R≈1 = every
# pass heads the same way (one-way); R≈0 = opposite headings cancel
# (bidirectional). R itself needs no forward/backward split -- raw traces share
# no per-cell reference axis for R. FOR THE COUNTS we DO pick a per-cell
# reference: the FIRST directional segment recorded in the cell (a RUNNING
# reference, O(1)/cell, deterministic under the ORDER BY id stream, no
# per-activity bearing storage). A segment is "forward" when within +-90 deg of
# that reference (dot >= 0), else "backward"; we count DISTINCT activities per
# side (forward_count / backward_count). mtb/gravel ONLY (Paul 2026-08-05);
# every other sport keeps forward_count = backward_count = 0. The mean-reference
# chicken-egg (mean needs all passes, but we count while streaming) is dodged by
# the running reference: when R is high the spread is tight, so the first-segment
# reference ~= the mean and one side dominates -- the counts agree with R.
#
# MEMORY BOUND: bearings are folded ONLY for the sports we ever arrow
# (mtb/gravel), so the two direction stores are bounded by the MTB/GRAVEL
# occupied-cell count (~141k at prod), NOT the ~5M total cells. Compact
# representation keyed by the SAME packed int64 ``_cell_key``: one dict of
# ``complex`` (Σ w·e^{iθ}, the resultant vector) + one parallel dict of
# ``float`` (Σ w, the total weight) — ~tens of bytes/cell, a few MB at prod,
# negligible next to the pass/user lattices.
_DIRECTIONAL_SPORTS = frozenset({"mtb", "gravel"})

# Per-activity direction-dedup bits (the transient ``dir_seen`` bitmask): an
# activity counts once per cell per side, so a many-crossing one-way pass counts
# once, while a genuine both-ways activity counts on BOTH sides.
_DIR_FWD_BIT = 1
_DIR_BWD_BIT = 2

# Segments shorter than this (metres) are DROPPED from the bearing accumulator:
# stationary GPS jitter and near-duplicate densified points (``_densify_coords``
# interpolates ~3 m points) carry no reliable heading and would inject random
# bearings that depress R. Only real displacement contributes a direction.
_MIN_DIR_SEGMENT_M = 2.0

# oneway_score >= this ⇒ a cell/feature is ridden predominantly ONE way. Mirror
# of the frontend arrow-layer threshold (community-heatmap-layers.ts).
ONEWAY_SCORE_THRESHOLD = 0.6


def _bearing_rad(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Initial great-circle bearing (radians) from point 1 → point 2. The
    absolute reference is irrelevant for R (only relative bearings matter), so
    any consistent convention works."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(rlat2)
    y = math.cos(rlat1) * math.sin(rlat2) - math.sin(rlat1) * math.cos(rlat2) * math.cos(dlon)
    return math.atan2(x, y)


def _oneway_score(dir_vec: dict, dir_wt: dict, key: int) -> float:
    """Mean resultant length R∈[0,1] of a cell's accumulated bearings, or 0.0
    when the cell carries no direction data (a non-mtb/gravel cell, or one whose
    only transitions were below ``_MIN_DIR_SEGMENT_M``)."""
    w = dir_wt.get(key, 0.0)
    if w <= 0.0:
        return 0.0
    return abs(dir_vec.get(key, 0j)) / w


def _run_oneway_score(sub: list, sid: int, deg: float,
                      dir_vec: dict, dir_wt: dict) -> float:
    """One ``oneway_score`` for a drawn (sub-)run: the mean of the per-cell R
    over the DISTINCT cells the run crosses that carry direction data. 0.0 when
    none do (road/running, or an all-jitter run). Distinct-cell (not per-point)
    averaging avoids over-weighting cells that happen to hold more vertices."""
    total = 0.0
    n = 0
    seen: set[int] = set()
    for pt in sub:
        key = _cell_key(sid, pt[0], pt[1], deg)
        if key in seen:
            continue
        seen.add(key)
        if dir_wt.get(key, 0.0) > 0.0:
            total += abs(dir_vec.get(key, 0j)) / dir_wt[key]
            n += 1
    return round(total / n, 3) if n else 0.0


def _run_direction_counts(sub: list, sid: int, deg: float, pass_by_cell: dict,
                          fwd_by_cell: dict, bwd_by_cell: dict) -> tuple[int, int]:
    """Representative ``(forward_count, backward_count)`` for a drawn (sub-)run.

    Reports the distinct-activity direction counts of the run's BUSIEST distinct
    cell (max ``pass_count``) — the SAME cell-aggregation choice the emitters use
    for the feature's headline ``pass_count`` / ``user_count`` (both a MAX over
    the run's cells), so all three describe the same busiest point: "at the
    busiest point of this segment, X went one way and Y the other". On a
    ``pass_count`` tie, prefer the cell carrying the MOST direction data
    (``forward + backward``) — busy cells often tie, and a run's endpoint cell
    (which one direction only arrives at, never departs) would otherwise report a
    degenerate one-sided split; the tie-break picks a mid-run cell both
    directions actually traverse.

    Returns ``(0, 0)`` for a run with no direction data (a non-mtb/gravel run,
    or an all-jitter mtb/gravel run whose cells never got a bearing). The counts
    are distinct-activity tallies per side, so ``fwd + bwd`` tracks the cell's
    ``pass_count`` (a both-ways activity contributes to both sides) and a high
    ``oneway_score`` implies one side dominates.
    """
    best_pass = -1
    best_dir = -1
    fwd = 0
    bwd = 0
    seen: set[int] = set()
    for pt in sub:
        key = _cell_key(sid, pt[0], pt[1], deg)
        if key in seen:
            continue
        seen.add(key)
        cp = pass_by_cell.get(key, 0)
        cf = fwd_by_cell.get(key, 0)
        cb = bwd_by_cell.get(key, 0)
        if cp > best_pass or (cp == best_pass and (cf + cb) > best_dir):
            best_pass = cp
            best_dir = cf + cb
            fwd = cf
            bwd = cb
    return fwd, bwd


def _run_flow_sign(sub: list, sid: int, deg: float, dir_vec: dict) -> int:
    """``+1`` if the run travels WITH the cells' accumulated popular flow,
    ``-1`` if AGAINST it, ``0`` when the run crosses no cell that carries
    direction data (a non-mtb/gravel run, or an all-jitter one).

    The popular flow of a cell is the direction of its mean resultant vector
    ``dir_vec[cell]`` (Σ w·e^{iθ} over every pass's segment bearings — the
    length-weighted average heading of everyone who rode through). For each of
    THIS run's segments we dot the segment's unit heading with its start-cell's
    resultant and accumulate (so long, confident cells dominate the vote). A
    negative total means the run mostly opposes the crowd.

    The emitters use this to ORIENT the drawn geometry to the more-popular
    direction: when the sign is negative they reverse the coordinate order, so
    the drawn ``LineString`` — and therefore the arrows maplibre auto-rotates
    along it under ``symbol-placement:'line'`` — points the DOMINANT way on
    EVERY feature of a trail. Without this, each feature is one activity's raw
    traversal, so the minority-direction riders draw reverse arrows that overlap
    the majority ones into an ambiguous "both ways" star. Reversing only flips
    the drawn point ORDER; the polyline shape (and thus the heat line/glow/
    density) is byte-identical, so trace integrity is untouched.
    """
    total = 0.0
    prev = None
    prev_key = None
    for pt in sub:
        if prev is not None and prev_key is not None:
            rv = dir_vec.get(prev_key)
            if rv is not None and (rv.real or rv.imag):
                seg_m = haversine_m(prev[0], prev[1], pt[0], pt[1])
                if seg_m >= _MIN_DIR_SEGMENT_M:
                    theta = _bearing_rad(prev[0], prev[1], pt[0], pt[1])
                    total += rv.real * math.cos(theta) + rv.imag * math.sin(theta)
        prev = pt
        prev_key = _cell_key(sid, pt[0], pt[1], deg)
    return 1 if total > 0 else (-1 if total < 0 else 0)


def _orient_directional_run(sub: list, sid: int, deg: float, dir_vec: dict,
                            fwd: int, bwd: int) -> tuple[list, int, int]:
    """Orient a directional (mtb/gravel) sub-run + its direction counts to the
    MORE-POPULAR travel direction.

    Reverses the coordinate order when the run opposes the accumulated popular
    flow (``_run_flow_sign`` < 0) so the drawn geometry — and the line-placed
    arrows that follow it — point the dominant way; and reports
    ``forward_count`` as the count in that (now drawn) direction, i.e. the LARGER
    of the two per-direction distinct-activity tallies (``backward_count`` = the
    smaller). Together this guarantees the map arrow and the popup's forward
    bar both describe "the way the crowd rode this trail". A run with no
    direction data (sign 0) is returned unchanged with its counts as-is.
    """
    if _run_flow_sign(sub, sid, deg, dir_vec) < 0:
        sub = list(reversed(sub))
    if bwd > fwd:
        fwd, bwd = bwd, fwd
    return sub, fwd, bwd


# ── Local-count splitting (fix: per-feature pass_count / heat_score) ──────────
# Each emitted ``trails`` feature is ONE masked (sub-)run — tens of km. Its
# headline ``pass_count`` used to be the MAX over EVERY cell the run crosses, so
# a quiet stretch inherited the busiest cell's distinct-activity count from
# ANYWHERE on the ride (a junction/corridor the ride merely passed through). The
# click-tooltip then reported e.g. "88 passages" on a spot that saw far fewer,
# and ``heat_score`` (derived from the same MAX) lit the WHOLE ride at its
# busiest cell's brightness instead of grading it.
#
# Fix: split each run into contiguous PIECES whenever the underlying cell's
# ``pass_count`` changes BUCKET, and emit each piece carrying its OWN local
# count (max within the piece). Now the tooltip reports the count where you
# actually clicked and busy corridors burn bright while quiet stretches stay dim
# (the Strava-like graded look).
#
# BUCKETING = a coarse log2 bin (``floor(log2(pass_count))``: 1→0, 2-3→1, 4-7→2,
# 8-15→3, …). Coarse ON PURPOSE — a per-11 m-cell split would balloon the
# PMTiles feature count; log2 gives only ~log2(max_pop) DISTINCT bucket VALUES
# (≈7 at prod max_pop≈88). But log2 bounds the number of distinct popularity
# BANDS, NOT the number of TRANSITIONS — and that gap is what OOM'd prod.
#
# 🔴 THE OOM (fix/regrade-oom-and-pool). The naive "cut wherever the bucket
# changes" fragmented REAL prod data catastrophically: GPS noise makes a cell's
# ``pass_count`` oscillate cell-to-cell (1↔2↔1 as densified ~3 m points weave
# in/out of neighbouring 11 m cells), so a bucket-boundary crossing at EVERY
# few metres emitted one feature PER SEGMENT → 1,321,624 line features / 634 MB
# geojsonl → tippecanoe OOM-killed the 2 Gi build job → the map stopped
# updating on EVERY subsequent rebuild. Pre-regrade the build was a few thousand
# features.
#
# THE BOUND (hysteresis + minimum piece length). We walk the run accumulating
# into the current piece and only CUT to a new piece when the bucket changes AND
# the new bucket PERSISTS for ≥ ``min_piece_m`` of travel (env
# ``HEATMAP_PASS_PIECE_MIN_M``, default ``DEFAULT_PASS_PIECE_MIN_M``). A short
# flicker — a noise cell whose different bucket does NOT sustain that distance,
# or one that flickers straight back to the committed bucket — is ABSORBED into
# the current piece. This caps pieces-per-run to ~``run_length / min_piece_m``
# (each cut consumes ≥ ``min_piece_m`` of sustained travel in the NEW bucket),
# so a noisy 30 km ride yields ~O(run_km · 1000 / min_piece_m) pieces, NOT one
# per 11 m cell. On a CLEAN single-transition run (the #621 goldens, and every
# real ride whose popularity changes once through a junction) the sustained band
# is far longer than ``min_piece_m``, so the cut lands at the SAME vertex the
# naive split chose — behaviour is unchanged there. At the K=1 beta almost every
# cell is pass≈1 (one bucket → uniform run → strict no-op).
#
# TRACE INTEGRITY: splitting only CUTS the polyline at EXISTING vertices — it
# never resamples / simplifies / snaps / moves a coordinate. Adjacent pieces
# SHARE the cut vertex, so the union of the pieces reconstructs the run
# byte-for-byte and the drawn line stays continuous.

# Minimum sustained travel (metres) a NEW pass_count bucket must persist before
# the local-count split cuts a new piece — the hysteresis that stops GPS-noise
# bucket flicker from fragmenting a run into millions of features. ~200 m is far
# longer than any noise flicker (an 11 m cell, a handful of densified ~3 m
# points) yet far SHORTER than a real popularity band (a junction/corridor spans
# hundreds of metres), so it absorbs noise without merging away genuine
# hot/quiet transitions. Tunable via ``HEATMAP_PASS_PIECE_MIN_M``; a value ≥ any
# run length collapses every run to one piece (the whole-run-MAX pre-#621 look).
DEFAULT_PASS_PIECE_MIN_M = 200.0


def pass_piece_min_m() -> float:
    """Resolve the local-count split's minimum-piece hysteresis distance from
    ``HEATMAP_PASS_PIECE_MIN_M`` (default ``DEFAULT_PASS_PIECE_MIN_M`` = 200 m).
    Non-positive / unparseable falls back to the default (never 0, which would
    restore the per-segment fragmentation that OOM'd the build)."""
    raw = os.environ.get("HEATMAP_PASS_PIECE_MIN_M", "").strip()
    if not raw:
        return DEFAULT_PASS_PIECE_MIN_M
    try:
        val = float(raw)
    except ValueError:
        return DEFAULT_PASS_PIECE_MIN_M
    return val if val > 0 else DEFAULT_PASS_PIECE_MIN_M


def _pass_bucket(pass_count: int) -> int:
    """Coarse log2 popularity bucket of a cell's ``pass_count`` (``floor(log2)``,
    with 0 and 1 both in bucket 0). The split cuts a run wherever this bucket
    changes AND the change sustains ≥ ``min_piece_m`` (see
    ``_split_run_by_local_pass``), so the piece count is bounded by
    ``run_length / min_piece_m`` — NOT by the 11 m cell count, and NOT by the
    number of noise-driven bucket TRANSITIONS."""
    return pass_count.bit_length() - 1 if pass_count > 0 else 0


def _split_run_by_local_pass(sub: list, sid: int, deg: float,
                             pass_by_cell: dict,
                             min_piece_m: float | None = None) -> list[list]:
    """Split a drawn (sub-)run into contiguous PIECES by ``_pass_bucket``, so
    each emitted feature carries a LOCAL count instead of the whole-run MAX —
    BOUNDED so GPS-noise bucket flicker cannot fragment the run into millions of
    features (the fix/regrade-oom-and-pool OOM).

    Each point is bucketed by its cell's ``pass_count``. We walk the run holding
    a COMMITTED bucket for the current piece; a point whose bucket DIFFERS starts
    a candidate stretch and we accumulate its travel distance. Only when that
    different bucket PERSISTS for ≥ ``min_piece_m`` (``HEATMAP_PASS_PIECE_MIN_M``,
    default ``DEFAULT_PASS_PIECE_MIN_M``) do we CUT — at the vertex where the
    stretch BEGAN. A flicker back to the committed bucket (or one that never
    sustains the distance) is ABSORBED into the current piece. This caps
    pieces-per-run to ~``run_length / min_piece_m``.

    On a CLEAN single-transition run the sustained band far exceeds
    ``min_piece_m``, so the cut lands at the exact bucket-boundary vertex the
    naive per-change split chose (the #621 goldens are unchanged). A uniform run
    (one bucket throughout — the common single-user corridor) returns ``[sub]``
    unchanged, a strict no-op.

    TRACE INTEGRITY: cuts only at EXISTING vertices; adjacent pieces SHARE the
    cut vertex, so the union reconstructs ``sub`` byte-for-byte.
    """
    if len(sub) < 2:
        return [sub]
    if min_piece_m is None:
        min_piece_m = pass_piece_min_m()
    pieces: list[list] = []
    g0 = 0  # first point index of the current committed piece
    cur = _pass_bucket(pass_by_cell.get(_cell_key(sid, sub[0][0], sub[0][1], deg), 0))
    diff_start: int | None = None  # first vertex index of the current != cur stretch
    diff_dist = 0.0                # travel accumulated in that stretch
    prev = sub[0]
    for j in range(1, len(sub)):
        pt = sub[j]
        b = _pass_bucket(pass_by_cell.get(_cell_key(sid, pt[0], pt[1], deg), 0))
        if b == cur:
            # Flicker back to the committed bucket → absorb (drop the candidate).
            diff_start = None
            diff_dist = 0.0
        else:
            if diff_start is None:
                diff_start = j
                diff_dist = 0.0
            diff_dist += haversine_m(prev[0], prev[1], pt[0], pt[1])
            if diff_dist >= min_piece_m:
                # The new bucket has PERSISTED ≥ min_piece_m → cut at its start.
                pieces.append(sub[g0:diff_start + 1])  # shared trailing vertex
                g0 = diff_start
                cur = _pass_bucket(pass_by_cell.get(
                    _cell_key(sid, sub[diff_start][0], sub[diff_start][1], deg), 0))
                diff_start = None
                diff_dist = 0.0
        prev = pt
    pieces.append(sub[g0:])
    return pieces


def _piece_feature_props(piece: list, terminal: bool, sid: int, sport: str,
                         deg: float, pass_by_cell: dict, users_by_cell: dict,
                         dir_vec: dict, dir_wt: dict, fwd_by_cell: dict,
                         bwd_by_cell: dict, log_max: float) -> tuple[list, dict]:
    """LOCAL feature props for ONE split piece (+ its directional orientation).

    ``pass_count`` / ``user_count`` are the MAX over the PIECE's own cells — a
    LOCAL grade, not the whole-run MAX (the bug fix) — and ``heat_score`` derives
    from that local pass via the SAME log-normalisation the ungraded code used.

    GRADED SLICE (the half-fix correction): ``_split_run_by_local_pass`` makes
    adjacent pieces SHARE the cut vertex, and that shared TRAILING vertex is the
    START of the NEXT bucket's segment — it belongs to the next piece's bucket.
    Grading a non-terminal piece over ALL its points would let that single
    boundary vertex dominate the max (a QUIET approach into a HOT band would
    report the HOT count — the exact bug). So a NON-TERMINAL piece is graded over
    ``piece[:-1]`` (excluding the shared trailing vertex); the TERMINAL piece,
    whose last vertex is a genuine endpoint, is graded over all its points. The
    DRAWN geometry is always the FULL piece, so adjacent pieces still share the
    cut vertex and the union reconstructs the run byte-for-byte.

    The directional props (``oneway_score`` / ``forward_count`` /
    ``backward_count``) and the dominant-direction FLOW SIGN (#618) are computed
    on the SAME graded slice (so the excluded next-bucket cell can't skew the
    busiest-cell pick or the score), while the reversal is applied to the full
    drawn piece. Returns ``(oriented_piece, props)``; callers add their own
    geometry wrapper (and ``highway_type`` for the PMTiles path).
    """
    grade = piece if terminal else piece[:-1]
    local_pass = 1
    local_users = 1
    for pt in grade:
        key = _cell_key(sid, pt[0], pt[1], deg)
        cell_pass = pass_by_cell.get(key, 0)
        if cell_pass > local_pass:
            local_pass = cell_pass
        cell_users = _user_count(users_by_cell.get(key))
        if cell_users > local_users:
            local_users = cell_users
    heat_score = round(min(1.0, math.log1p(local_pass) / log_max), 3)
    oneway_score = _run_oneway_score(grade, sid, deg, dir_vec, dir_wt)
    directional = sport in _DIRECTIONAL_SPORTS
    if directional:
        fwd_count, bwd_count = _run_direction_counts(
            grade, sid, deg, pass_by_cell, fwd_by_cell, bwd_by_cell)
        # Orient the DRAWN geometry (the FULL piece, so the cut vertex stays
        # shared) to the dominant flow, decided on the graded slice for
        # consistency with the counts; then report the busier side as forward.
        if _run_flow_sign(grade, sid, deg, dir_vec) < 0:
            piece = list(reversed(piece))
        if bwd_count > fwd_count:
            fwd_count, bwd_count = bwd_count, fwd_count
    else:
        fwd_count, bwd_count = 0, 0
    return piece, {
        "sport": sport,
        "user_count": local_users,
        "pass_count": local_pass,
        "forward_count": fwd_count,
        "backward_count": bwd_count,
        "heat_score": heat_score,
        "oneway_score": oneway_score,
    }


# Server-side cursor batch — how many activity rows psycopg2 buffers per
# network round-trip while streaming. Bounds the client-side row buffer to a
# constant regardless of corpus size (the whole point: never fetchall()).
_ACTIVITY_STREAM_BATCH = 200


def raw_display_enabled() -> bool:
    """True when ``HEATMAP_DISPLAY_SOURCE`` selects the raw-trace source."""
    return os.environ.get("HEATMAP_DISPLAY_SOURCE", "matched").strip().lower() == "raw"


def min_users() -> int:
    """Distinct-user floor per fine cell for the RAW path (``HEATMAP_MIN_USERS``).

    Default **1** — show everything, including solo traces (Paul's current beta
    choice). Set **2** later to hide single-user pixels: this is how the
    "K=1 now → K=2 at 50 users" promise becomes a one-env-var flip with no
    rework. Endpoint masking (``trace_privacy``) is ALWAYS on and independent of
    this gate. Applies ONLY in raw mode.
    """
    raw = os.environ.get("HEATMAP_MIN_USERS", "").strip()
    if not raw:
        return 1
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def lattice_deg() -> float:
    raw = os.environ.get("HEATMAP_RAW_LATTICE_DEG", "").strip()
    if not raw:
        return DEFAULT_LATTICE_DEG
    try:
        val = float(raw)
    except ValueError:
        return DEFAULT_LATTICE_DEG
    return val if val > 0 else DEFAULT_LATTICE_DEG


def point_stride_m() -> float:
    """Resolve the ``heat_points`` emission stride from
    ``HEATMAP_RAW_POINT_STRIDE_M`` (default ``DEFAULT_POINT_STRIDE_M`` = 20 m).
    A non-positive / unparseable value falls back to the default (never 0, which
    would emit every vertex)."""
    raw = os.environ.get("HEATMAP_RAW_POINT_STRIDE_M", "").strip()
    if not raw:
        return DEFAULT_POINT_STRIDE_M
    try:
        val = float(raw)
    except ValueError:
        return DEFAULT_POINT_STRIDE_M
    return val if val > 0 else DEFAULT_POINT_STRIDE_M


def plausible_bbox() -> tuple[float, float, float, float] | None:
    """Optional region clip ``(min_lon, min_lat, max_lon, max_lat)`` from
    ``HEATMAP_RAW_BBOX`` (comma floats). ``None`` when unset — **default OFF**,
    so "keep every desire line anywhere on earth" stays the default (Paul's
    value) and region-clipping is a deliberate, explicit prod opt-in.

    Complements ``_reject_far_outliers``: the median guard drops a glitch spike
    EMBEDDED in an otherwise-good ride, but an activity that is WHOLLY corrupt
    (prod had two 8-point traces entirely mid-Atlantic, lat~20) has its own
    median ON the glitch, so only a region gate rejects it. Prod sets a generous
    Europe box (e.g. ``-12,34,32,62``); the mid-ocean spike falls outside.
    """
    raw = os.environ.get("HEATMAP_RAW_BBOX", "").strip().lower()
    if not raw or raw in ("off", "none"):
        return None
    # Accept ',' OR ';' separators: the prod value travels through gcloud
    # `--update-env-vars` (a COMMA-separated KEY=VALUE list), so deploy-prod.sh
    # ships the bbox semicolon-separated to avoid being split into 4 bogus vars.
    try:
        a, b, c, d = (float(x) for x in raw.replace(";", ",").split(","))
    except (ValueError, TypeError):
        return None
    return (a, b, c, d)


def _in_bbox(pt, bbox: tuple[float, float, float, float]) -> bool:
    return bbox[0] <= pt[0] <= bbox[2] and bbox[1] <= pt[1] <= bbox[3]


def max_span_km() -> float:
    """Resolve the glitch-outlier span from ``HEATMAP_RAW_MAX_SPAN_KM`` (default
    300 km). ``0`` / ``off`` disables the guard (return 0.0)."""
    raw = os.environ.get("HEATMAP_RAW_MAX_SPAN_KM", "").strip().lower()
    if raw in ("off", "none"):
        return 0.0
    if not raw:
        return DEFAULT_MAX_SPAN_KM
    try:
        val = float(raw)
    except ValueError:
        return DEFAULT_MAX_SPAN_KM
    return max(0.0, val)


def _reject_far_outliers(coords: list, max_km: float) -> list:
    """Drop points implausibly far from the activity's spatial MEDIAN.

    A GPS glitch (a spike thousands of km off, e.g. mid-ocean) is a tiny
    minority of an activity's points, so the median lon/lat lands on the real
    ride body; any point beyond ``max_km`` of it is a corrupt coordinate and is
    dropped. Robust to a minority of glitch points (median, not mean). Returns
    ``coords`` unchanged when ``max_km <= 0`` (guard disabled) or < 2 points.
    """
    if max_km <= 0 or len(coords) < 2:
        return coords
    lons = sorted(p[0] for p in coords)
    lats = sorted(p[1] for p in coords)
    mid = len(coords) // 2
    mlon, mlat = lons[mid], lats[mid]
    max_m = max_km * 1000.0
    return [p for p in coords if haversine_m(p[0], p[1], mlon, mlat) <= max_m]


def _stream_activities(db, *, bbox=None, since_days=None):
    """STREAM community-eligible, heatmap-consented activities one row at a time.

    Server-side cursor (``yield_per``) — psycopg2 keeps only
    ``_ACTIVITY_STREAM_BATCH`` rows client-side, so we never hold all
    activities (and, crucially, all their potentially-huge
    ``geometry_geojson`` strings) in RAM. This is the streaming replacement for
    the old ``fetchall()`` that materialised the whole corpus.

    Deterministic ``ORDER BY id`` so the TWO passes of ``export_raw_geojson``
    iterate the corpus in the EXACT same order — pass-2 emission then lines up
    with the pass-1 lattice and the output is reproducible.

    Provenance gate (compliance SSOT ``provenance.is_community_source`` /
    ``COMMUNITY_SOURCE``): ONLY ``source = 'manual_upload'`` may feed the public
    ODbL map. ``strava_api`` (Strava-API syncs — policy §5.4/§5.10) and legacy
    ``NULL`` rows are EXCLUDED — exactly like the matched pipeline gates
    contribution at ingest (``ingest.py`` → ``heat_edges``). Without this, raw
    mode would publish personal Strava-API + unknown-provenance traces.

    Optional ``bbox`` (``(min_lon, min_lat, max_lon, max_lat)``) and
    ``since_days`` scope the stream at the SQL layer — used by the raw
    COMMUNITY-EXPORT path (``aggregate_raw_for_export``) so a bbox-clipped
    export never streams the whole national corpus. Both use existing indexes
    (``ix_activities_geometry`` GiST bbox, ``activities_user_date_idx`` on
    ``activity_date``). The whole-world raw-PMTiles build passes neither, so its
    behaviour is byte-identical to before.
    """
    from app.services.provenance import community_eligible_conditions

    conditions, params = community_eligible_conditions()
    if bbox is not None:
        conditions.append(
            "geometry && ST_MakeEnvelope("
            ":min_lon, :min_lat, :max_lon, :max_lat, 4326)"
        )
        params.update({
            "min_lon": bbox[0], "min_lat": bbox[1],
            "max_lon": bbox[2], "max_lat": bbox[3],
        })
    if since_days is not None and since_days > 0:
        conditions.append(
            "activity_date >= NOW() - MAKE_INTERVAL(days => :since_days)"
        )
        params["since_days"] = int(since_days)

    where = " AND ".join(conditions)
    result = db.execute(sa_text(
        f"""
        SELECT id, user_id, sport, geometry_geojson
        FROM activities
        WHERE {where}
        ORDER BY id
        """
    ), params).yield_per(_ACTIVITY_STREAM_BATCH)
    for r in result:
        yield r[0], r[1], r[2], r[3]


def iter_masked_runs(db, *, bbox=None, sport=None, since_days=None):
    """Yield ``(activity_id, user_id, sport, run)`` for every masked run of every
    consented activity. ``run`` is a precise ``[[lon, lat, ...], ...]`` polyline.

    Endpoint masking is applied HERE, before any counting — masked-off points
    never reach the density / user lattices, so a rider's home never counts
    toward either popularity or the ``HEATMAP_MIN_USERS`` privacy gate.

    ``bbox`` / ``since_days`` scope the underlying activity stream (SQL) and
    ``sport`` filters on the NORMALISED heat-edge sport (Python — activities
    store the raw/classifier sport, which ``expand_sport`` maps the same way the
    matched export does). All three are used by the raw community-EXPORT path;
    the whole-world PMTiles build passes none of them, so it is unchanged.
    """
    from app.config import expand_sport
    from app.services.ingest import _densify_coords, _normalize_heat_edge_sport

    sport_filter = set(expand_sport(sport)) if sport else None
    m = mask_meters()
    max_span = max_span_km()
    bbox_clip = plausible_bbox()
    for aid, user_id, raw_sport, geojson_str in _stream_activities(
            db, bbox=bbox, since_days=since_days):
        norm_sport = _normalize_heat_edge_sport(raw_sport)
        if sport_filter is not None and norm_sport not in sport_filter:
            continue
        try:
            coords = json.loads(geojson_str).get("coordinates", [])
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
        if not coords or len(coords) < 2:
            continue
        # Region clip (opt-in) FIRST — drops wholly-corrupt traces (e.g. an
        # activity entirely mid-ocean) that the median guard can't, since their
        # own median sits on the glitch. This is the ``HEATMAP_RAW_BBOX`` glitch
        # guard (``bbox_clip``), independent of the caller's export ``bbox``.
        if bbox_clip is not None:
            coords = [p for p in coords if _in_bbox(p, bbox_clip)]
            if len(coords) < 2:
                continue
        # Then reject GPS-glitch outliers (points implausibly far from the ride's
        # own body) BEFORE densify — else densify would interpolate a dense line
        # straight out to the corrupt coordinate. Raw mode draws verbatim, so
        # this is the guard the OSM-match implicitly gave the matched pipeline.
        coords = _reject_far_outliers(coords, max_span)
        if len(coords) < 2:
            continue
        # Densify to <5 m spacing so the density lattice sees every cell the
        # trace crosses (default densify gap is 15 m > lattice cell).
        coords = _densify_coords(coords, max_gap=3.0)
        for run in mask_endpoints(coords, m):
            yield aid, user_id, norm_sport, run


def gate_run(run: list, sport: str, users_by_cell: dict, deg: float,
             min_u: int) -> list[list]:
    """Split ``run`` into the contiguous stretches whose fine cells have at
    least ``min_u`` DISTINCT contributing users — the ``HEATMAP_MIN_USERS``
    privacy gate at segment granularity.

    ``min_u <= 1`` is the identity (every occupied cell has ≥1 user by
    construction) → the whole run survives, byte-identical to the ungated path.
    """
    if min_u <= 1:
        return [run]
    sid = _sport_id(sport)
    out: list[list] = []
    cur: list = []
    for pt in run:
        key = _cell_key(sid, pt[0], pt[1], deg)
        if _user_count(users_by_cell.get(key)) >= min_u:
            cur.append(pt)
        else:
            if len(cur) >= 2:
                out.append(cur)
            cur = []
    if len(cur) >= 2:
        out.append(cur)
    return out


def _accumulate_lattice(db, deg: float, *, bbox=None, sport=None,
                        since_days=None) -> tuple[dict, dict, dict, dict]:
    """PASS 1 — stream the masked runs and fold them into the BOUNDED lattice.

    Returns ``(pass_by_cell, users_by_cell, dir_vec, dir_wt, fwd_by_cell,
    bwd_by_cell)``:

    Both dicts are keyed by a COMPACT int64 cell key (``_cell_key`` packs
    ``(sport_id, ilat, ilon)`` into one Python int — see the packing constants),
    NOT a ``(sport, ilat, ilon)`` tuple: one shared int object per occupied cell
    replaces a 3-tuple + its two large-int members (~120 → ~32 bytes/key).

    * ``pass_by_cell: dict[int -> int]`` — number of DISTINCT activities that
      cross the cell. An INT counter (not a set of activity ids): while a single
      activity is in flight we dedup its cells with a transient ``seen`` set
      (bounded by ONE activity's cell footprint, released when the next activity
      starts), then bump the counter once. So the counter equals
      ``COUNT(DISTINCT activity_id)`` per cell without ever storing the ids.
    * ``users_by_cell: dict[int -> user_id | set[user_id]]`` — distinct
      contributing users per cell, stored compact-by-arity via ``_add_user``: a
      bare ``user_id`` for the 1-user cell (no ~216-byte ``set`` object — the
      beta-dominant case), promoted to a ``set`` (capped at ``_USER_CAP``) only
      when a genuine SECOND distinct user arrives. Drives the true ``user_count``
      and the ``HEATMAP_MIN_USERS`` gate; exact at the K-anonymity boundary.

    Memory is therefore bounded by OCCUPIED CELL COUNT (+ distinct users/cell) —
    it does NOT grow with the number of activities or their densified points,
    which is the whole point of the rework. ``iter_masked_runs`` yields all of
    an activity's runs consecutively (it streams activity-by-activity), so the
    ``aid`` change reliably delimits a new activity for the dedup reset.

    NOTE (future national-scale): the per-cell user store is already bounded
    (bare id → capped set). If distinct-users-per-cell ever grows the
    accumulator uncomfortably at national volume, swap ``_add_user`` for a
    HyperLogLog distinct-counter (fixed ~KB/cell, ~1% error) — the gate only
    needs ``>= min_u`` and ``user_count`` tolerates an estimate. HLL trades a
    small error near K, so the capped-set (exact at the boundary) stays for now.

    ``dir_vec: dict[int -> complex]`` + ``dir_wt: dict[int -> float]`` are the
    DIRECTIONAL accumulator (one-way singletrack), populated ONLY for mtb/gravel
    cells (``_DIRECTIONAL_SPORTS``): ``dir_vec`` sums ``w·e^{iθ}`` (θ = segment
    bearing, w = segment length) and ``dir_wt`` sums ``w``; ``_oneway_score``
    reads back the mean resultant length R = |dir_vec| / dir_wt. Bounded by the
    mtb/gravel cell count, so the direction store stays a few MB at prod.

    ``fwd_by_cell: dict[int -> int]`` + ``bwd_by_cell: dict[int -> int]`` are the
    per-cell DISTINCT-activity direction counts (mtb/gravel only), split about
    the cell's running reference bearing (first directional segment recorded).
    An activity is deduped per cell per side via a transient ``dir_seen`` bitmask
    (bounded by ONE activity's cells, freed each activity — same bound as the
    ``seen`` pass-dedup set). Drives ``forward_count`` / ``backward_count`` on the
    emitted features; both stay empty (→ 0) for road/running.
    """
    pass_by_cell: dict[int, int] = {}
    users_by_cell: dict[int, object] = {}
    dir_vec: dict[int, complex] = {}
    dir_wt: dict[int, float] = {}
    dir_ref: dict[int, complex] = {}
    fwd_by_cell: dict[int, int] = {}
    bwd_by_cell: dict[int, int] = {}

    # Forward the scoping filters ONLY when set, so the whole-world PMTiles
    # build calls ``iter_masked_runs(db)`` with the exact same (single-arg)
    # signature it always has (the streaming tests spy that shape). The raw
    # EXPORT path passes bbox/sport/since_days.
    scope: dict = {}
    if bbox is not None:
        scope["bbox"] = bbox
    if sport is not None:
        scope["sport"] = sport
    if since_days is not None:
        scope["since_days"] = since_days

    current_aid = None
    seen: set[int] = set()
    dir_seen: dict[int, int] = {}
    for aid, user_id, run_sport, run in iter_masked_runs(db, **scope):
        if aid != current_aid:
            current_aid = aid
            seen = set()  # bounded by ONE activity's cells; freed each activity
            dir_seen = {}  # per-activity fwd/bwd dedup; same bound + lifetime
        sid = _sport_id(run_sport)
        directional = run_sport in _DIRECTIONAL_SPORTS
        prev = None
        prev_key = None
        for pt in run:
            key = _cell_key(sid, pt[0], pt[1], deg)
            if key not in seen:
                seen.add(key)
                pass_by_cell[key] = pass_by_cell.get(key, 0) + 1
            _add_user(users_by_cell, key, user_id)
            # Fold this segment's travel bearing into the direction accumulator
            # (mtb/gravel only). Attribute to the segment's START cell; drop
            # sub-minimum-length transitions (GPS jitter / dense collinear
            # points) so they can't inject a spurious heading.
            if directional and prev is not None:
                seg_m = haversine_m(prev[0], prev[1], pt[0], pt[1])
                if seg_m >= _MIN_DIR_SEGMENT_M:
                    theta = _bearing_rad(prev[0], prev[1], pt[0], pt[1])
                    unit = complex(math.cos(theta), math.sin(theta))
                    dir_vec[prev_key] = dir_vec.get(prev_key, 0j) + seg_m * unit
                    dir_wt[prev_key] = dir_wt.get(prev_key, 0.0) + seg_m
                    # Running reference = the FIRST directional segment in this
                    # cell. Classify this segment forward (within +-90 deg, i.e.
                    # dot >= 0) or backward, and tally DISTINCT activities per
                    # side — deduped per activity+cell+direction via dir_seen.
                    ref = dir_ref.get(prev_key)
                    if ref is None:
                        dir_ref[prev_key] = ref = unit
                    forward = (unit.real * ref.real + unit.imag * ref.imag) >= 0.0
                    bit = _DIR_FWD_BIT if forward else _DIR_BWD_BIT
                    mask = dir_seen.get(prev_key, 0)
                    if not (mask & bit):
                        dir_seen[prev_key] = mask | bit
                        if forward:
                            fwd_by_cell[prev_key] = fwd_by_cell.get(prev_key, 0) + 1
                        else:
                            bwd_by_cell[prev_key] = bwd_by_cell.get(prev_key, 0) + 1
            prev = pt
            prev_key = key
    return pass_by_cell, users_by_cell, dir_vec, dir_wt, fwd_by_cell, bwd_by_cell


def _emit_run_points(sub: list, sid: int, deg: float, pass_by_cell: dict,
                     log_max: float, sport: str, stride_m: float, fpts) -> int:
    """Write density POINTS for one drawn (sub-)run to the ``heat_points``
    GeoJSONL, returning the number written.

    Walks ``sub`` at ~``stride_m`` spacing (ALWAYS the first vertex) and writes
    one ``Point`` feature per emitted vertex carrying:

      * ``w`` — the vertex's fine-cell log-normalised popularity, the SAME
        density scale as the ``trails`` line ``heat_score`` (so the maplibre
        ``heatmap-weight`` reads the same "how busy is this corridor" signal).
      * ``sport`` — so the frontend sport chip filters the raster layer the same
        way it filters the lines.

    Overlapping traces stack points in the same corridor → the maplibre
    ``heatmap`` layer accumulates them into a clean density FIELD (the Strava
    look) instead of the diffuse vector-line blob. Streaming: one point written
    at a time, O(1) memory per run (never materialised) — the same bounded
    posture as the surrounding ``trails`` emission.
    """
    n = 0
    acc = stride_m  # prime so the FIRST vertex always emits
    prev = None
    for pt in sub:
        if prev is not None:
            acc += haversine_m(prev[0], prev[1], pt[0], pt[1])
        prev = pt
        if acc < stride_m:
            continue
        acc = 0.0
        key = _cell_key(sid, pt[0], pt[1], deg)
        w = round(min(1.0, math.log1p(pass_by_cell.get(key, 0)) / log_max), 3)
        fpts.write(json.dumps({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [round(pt[0], 6), round(pt[1], 6)],
            },
            "properties": {"w": w, "sport": sport},
        }))
        fpts.write("\n")
        n += 1
    return n


def export_raw_geojson(db, path: str, points_path: str | None = None,
                       stats_out: dict | None = None) -> int:
    """Stream masked raw traces as newline-delimited GeoJSON. Returns feature
    count (one ``LineString`` feature per surviving masked (sub-)run).

    When ``points_path`` is given, ALSO stream a companion ``heat_points``
    GeoJSONL of weighted density Points (``_emit_run_points``) built from the
    SAME surviving sub-runs in the SAME pass-2 loop — no extra DB pass, still
    O(1)-per-run memory. ``build_pmtiles`` feeds both files to tippecanoe as two
    named layers (``trails`` lines + ``heat_points`` points) so the frontend can
    render the raster density heatmap over the points while keeping the crisp
    line/arrow layers over ``trails``. The return value stays the ``trails``
    feature count (backward-compatible with every existing caller/test).

    TWO STREAMING passes over the consented activities — neither the corpus of
    runs nor the feature list is ever materialised in RAM (see the module
    docstring + ``_accumulate_lattice``):

    1. ``_accumulate_lattice`` — build the bounded per-``(sport, cell)`` lattice:
       ``pass_count`` (distinct activities → popularity, drives ``pass_count`` +
       ``heat_score``) and distinct users (drives ``user_count`` AND the
       ``HEATMAP_MIN_USERS`` gate). The lattice is ONLY a counter; the render
       stays the raw trace geometry.
    2. Re-stream the masked runs and emit each (gated into sub-runs when
       ``min_users >= 2``, then SPLIT into local-count pieces by
       ``_split_run_by_local_pass``) with ``pass_count`` / ``heat_score`` = the
       log-normalised max popularity of the PIECE's own cells — so a quiet
       stretch is graded dim instead of inheriting a distant junction's MAX —
       writing to disk as we go so the existing colour/width/opacity ramp lights
       busy corridors and grades the quiet ones.

    Default (``HEATMAP_MIN_USERS=1``) emits whole runs (``gate_run`` is the
    identity) — geometry identical to the ungated path.
    """
    deg = lattice_deg()
    min_u = min_users()

    # PASS 1 — bounded lattice (no run/point corpus held in RAM).
    pass_by_cell, users_by_cell, dir_vec, dir_wt, fwd_by_cell, bwd_by_cell = \
        _accumulate_lattice(db, deg)

    max_pop = max(pass_by_cell.values(), default=1)
    log_max = math.log1p(max_pop) or 1.0

    # Community-NETWORK length estimate for the home "km de chemins" banner —
    # a free by-product of pass 1 (no extra DB work). The lattice is a ~11 m
    # grid; a trace advances ~one cell per cell-edge of travel, so the count of
    # OCCUPIED cells × the cell's mean edge length ≈ the length of the UNIQUE
    # network the community has reclaimed (deduped by geography — repeat rides
    # of the same path share cells and are counted once). This replaces the old
    # ``heat_edges_agg`` SUM(ST_Length) query, which is gone under the raw pivot
    # (the table was dropped) and otherwise spams a warning + silently falls
    # back to total km RIDDEN (over-counts every repeat). Filled only when a
    # caller asks (``build_pmtiles``); every other caller/test is unaffected.
    if stats_out is not None:
        occupied = len(pass_by_cell)
        stats_out["occupied_cells"] = occupied
        stats_out["network_m"] = occupied * deg * _MEAN_M_PER_DEG

    # PASS 2 — re-stream the SAME masked runs (ORDER BY id → identical order)
    # and write each feature straight to the file. Only one run is in flight.
    # When points_path is set, the companion heat_points file is written from
    # the SAME surviving sub-runs in this loop (no extra DB pass). ExitStack so
    # both files close on any exit without nesting the whole loop a level deeper.
    written = 0
    points_written = 0
    suppressed = 0
    stride_m = point_stride_m()
    with contextlib.ExitStack() as stack:
        f = stack.enter_context(open(path, "w"))
        fpts = stack.enter_context(open(points_path, "w")) if points_path else None
        for _aid, _user_id, sport, run in iter_masked_runs(db):
            subruns = gate_run(run, sport, users_by_cell, deg, min_u)
            if not subruns:
                suppressed += 1
            sid = _sport_id(sport)
            for sub in subruns:
                # Density POINTS are emitted ONCE per sub-run (each vertex
                # carries its own per-cell weight, so the raster is unaffected by
                # how the LINE is partitioned) — before the split, O(1)/run.
                if fpts is not None:
                    points_written += _emit_run_points(
                        sub, sid, deg, pass_by_cell, log_max, sport,
                        stride_m, fpts)
                # Split the sub-run into local-count PIECES (a quiet stretch must
                # NOT inherit a distant junction's pass_count / brightness).
                # pass_count = passes (distinct activities); user_count =
                # distinct USERS — NEVER conflate the two on a public map.
                pieces = _split_run_by_local_pass(sub, sid, deg, pass_by_cell)
                last = len(pieces) - 1
                for idx, piece in enumerate(pieces):
                    oriented, props = _piece_feature_props(
                        piece, idx == last, sid, sport, deg, pass_by_cell,
                        users_by_cell, dir_vec, dir_wt, fwd_by_cell,
                        bwd_by_cell, log_max)
                    props["highway_type"] = ""
                    feature = {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[round(p[0], 6), round(p[1], 6)]
                                            for p in oriented],
                        },
                        "properties": props,
                    }
                    f.write(json.dumps(feature))
                    f.write("\n")
                    written += 1
    log.info("raw-trace export (streaming): %d line features, %d heat_points, "
             "%d cells, max_pop=%d, mask=%.0fm, cell=%.6f°, stride=%.0fm, "
             "min_users=%d, fully_suppressed_runs=%d",
             written, points_written, len(pass_by_cell), max_pop, mask_meters(),
             deg, stride_m, min_u, suppressed)
    return written


def aggregate_raw_for_export(db, bbox, sport, min_uc: int,
                             days: int | None) -> list[dict]:
    """Raw-mode community-EXPORT aggregation — the RAW analogue of
    ``export._query_aggregated_heat_edges``.

    WHY: after the raw-trace cutover the OSM substrate (``osm_ways`` /
    ``osm_road_edges``) is DROPPED, so the matched aggregation
    (``build_heat_aggregation_sql`` — ``LEFT JOIN osm_ways`` + ``LEFT JOIN
    LATERAL osm_road_edges``) 500s. Under raw the community EXPORT must reflect
    the SAME masked ``manual_upload`` traces the raw PMTiles render from — NOT
    the stale ``heat_edges_agg`` — so this reads ``activities`` through the
    IDENTICAL pipeline as ``export_raw_geojson`` (provenance gate + endpoint
    masking + glitch guards + the ``HEATMAP_MIN_USERS`` per-cell gate), scoped
    to the export's ``bbox`` / ``sport`` / ``days`` window, and returns feature
    dicts shaped exactly like the matched builder's rows so every serializer
    (GeoJSON / KML / GPX / MBTiles vector+raster) consumes them unchanged.

    Privacy: the per-cell distinct-user gate floor is ``max(min_uc,
    HEATMAP_MIN_USERS)`` — an export never leaks below either the export's
    K-anonymity floor or the raw map's own gate. Endpoint masking is always on.
    Geometry is the precise masked polyline (a ``LineString`` per surviving
    sub-run); ``forward_count`` / ``backward_count`` are the per-direction
    distinct-activity counts of the run's busiest cell (mtb/gravel ONLY; 0 for
    every other sport — see ``_run_direction_counts``). Directionality is ALSO
    surfaced as ``oneway_score`` (circular concentration R∈[0,1], mtb/gravel
    only) — see ``_run_oneway_score``. ``days`` filters on
    ``activities.activity_date``.

    Bounded memory: reuses the two-pass streaming lattice, scoped by bbox +
    date at the SQL layer, so a 50 km export never materialises the national
    corpus (the #3 OOM class the read paths were guilty of).
    """
    deg = lattice_deg()
    gate = max(int(min_uc), min_users())
    since = days if (days and days > 0) else None

    # PASS 1 — bounded lattice over the SCOPED corpus (popularity + user gate
    # + mtb/gravel directional accumulator).
    pass_by_cell, users_by_cell, dir_vec, dir_wt, fwd_by_cell, bwd_by_cell = \
        _accumulate_lattice(db, deg, bbox=bbox, sport=sport, since_days=since)
    max_pop = max(pass_by_cell.values(), default=1)
    log_max = math.log1p(max_pop) or 1.0

    # PASS 2 — re-stream the SAME masked runs (identical ORDER BY id) and build
    # one feature per surviving (gated) sub-run. Only one run is ever in flight.
    features: list[dict] = []
    for _aid, _uid, run_sport, run in iter_masked_runs(
            db, bbox=bbox, sport=sport, since_days=since):
        sid = _sport_id(run_sport)
        for sub in gate_run(run, run_sport, users_by_cell, deg, gate):
            # SAME local-count split as the PMTiles build (map + export must not
            # diverge): each piece carries its own busiest-cell pass/user/heat.
            pieces = _split_run_by_local_pass(sub, sid, deg, pass_by_cell)
            last = len(pieces) - 1
            for idx, piece in enumerate(pieces):
                oriented, props = _piece_feature_props(
                    piece, idx == last, sid, run_sport, deg, pass_by_cell,
                    users_by_cell, dir_vec, dir_wt, fwd_by_cell, bwd_by_cell,
                    log_max)
                props["geometry"] = {
                    "type": "LineString",
                    "coordinates": [[round(p[0], 6), round(p[1], 6)]
                                    for p in oriented],
                }
                features.append(props)
    return features
