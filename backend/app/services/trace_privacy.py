"""Endpoint masking — the privacy mechanism for the raw-trace heatmap.

Product decision (Paul, 2026-07-21): the raw-trace display source (see
``HEATMAP_DISPLAY_SOURCE`` in ``build_pmtiles``) renders precise individual
GPS traces instead of K-anonymised, OSM-matched, cross-rider aggregates.
Privacy in that mode is NOT K-anonymity — it is achieved by **masking every
point within N metres (radius) of the start and of the end of every trace** so a
rider's home / work is never drawn on the public map (radial since the 2026-11
data audit — the original along-track trim leaked home under stationary GPS
jitter; see ``mask_endpoints``).

This module is the pure, side-effect-free SSOT for that trim. It NEVER mutates
the stored GPS geometry (Crouzet invariant #1) — callers pass a copy of the
parsed coordinates and get back a trimmed copy for DISPLAY only.

Coordinate convention (matches the rest of ingest): ``[lon, lat]`` or
``[lon, lat, ele]``. Track-break sentinels (``None``, emitted by
``ingest._densify_coords`` on a GPS dropout) split the trace into independent
runs; the end zones are taken from the first / last recorded fixes and the
discs around them apply to every run.
"""
from __future__ import annotations

import math
import os
from itertools import pairwise

from app.services.geo import haversine_m

Coord = list
Run = list

DEFAULT_MASK_METERS = 200.0

# End zones (see ``mask_endpoints``). The first recorded fix is only an
# ESTIMATE of home: while the rider waits for a fix, consecutive fixes scatter
# by several metres, and the very first fix can be stale / cell-based, hundreds
# of metres or kilometres away. The start zone is therefore the first
# ``HEAD_FIXES`` fixes plus every following fix still within ``DWELL_METERS``
# of them; a disc of radius R is centred on every one of those fixes.
HEAD_FIXES = 10
DWELL_METERS = 30.0


def mask_meters() -> float:
    """Resolve the mask radius from ``TRACE_MASK_METERS`` (default 200 m)."""
    raw = os.environ.get("TRACE_MASK_METERS", "").strip()
    if not raw:
        return DEFAULT_MASK_METERS
    try:
        val = float(raw)
    except ValueError:
        return DEFAULT_MASK_METERS
    return max(0.0, val)


def _split_runs(coords: list) -> list[Run]:
    """Split a coord list on ``None`` break sentinels into continuous runs."""
    runs: list[Run] = []
    current: Run = []
    for pt in coords:
        if pt is None:
            if len(current) >= 2:
                runs.append(current)
            current = []
        else:
            current.append(pt)
    if len(current) >= 2:
        runs.append(current)
    return runs


def _interp(a: Coord, b: Coord, frac: float) -> Coord:
    lon = a[0] + (b[0] - a[0]) * frac
    lat = a[1] + (b[1] - a[1]) * frac
    if len(a) > 2 and len(b) > 2 and a[2] is not None and b[2] is not None:
        return [lon, lat, a[2] + (b[2] - a[2]) * frac]
    return [lon, lat]


# Metres per degree of latitude on the haversine sphere (R = 6 371 km), so the
# local projection below agrees with ``geo.haversine_m`` to well under a
# millimetre at mask scale (hundreds of metres).
_M_PER_DEG = 6_371_000.0 * math.pi / 180.0


class _Disc:
    """A privacy disc of radius ``r`` metres around ``center`` (``[lon, lat]``).

    Uses a local equirectangular projection centred on the disc: exact enough
    at mask scale (error ≪ 1 m for r ≤ a few km) and, crucially, the SAME
    metric is used for the inside test and for the boundary interpolation, so a
    cut point lands exactly on the circle.
    """

    __slots__ = ("lon0", "lat0", "kx", "r", "r2")

    def __init__(self, center: Coord, r: float):
        self.lon0 = center[0]
        self.lat0 = center[1]
        self.kx = _M_PER_DEG * math.cos(math.radians(center[1]))
        self.r = r
        self.r2 = r * r

    def xy(self, p: Coord) -> tuple[float, float]:
        return (p[0] - self.lon0) * self.kx, (p[1] - self.lat0) * _M_PER_DEG

    def crossings(self, pa: tuple[float, float], pb: tuple[float, float]) -> list[float]:
        """Parameters ``t`` in (0, 1) where segment pa→pb crosses the circle."""
        dx, dy = pb[0] - pa[0], pb[1] - pa[1]
        a = dx * dx + dy * dy
        if a <= 0.0:
            return []
        b = 2.0 * (pa[0] * dx + pa[1] * dy)
        c = pa[0] * pa[0] + pa[1] * pa[1] - self.r2
        disc = b * b - 4.0 * a * c
        if disc <= 0.0:
            return []  # misses (or merely grazes) the circle
        sq = math.sqrt(disc)
        return [t for t in ((-b - sq) / (2.0 * a), (-b + sq) / (2.0 * a)) if 0.0 < t < 1.0]


class _DiscGroup:
    """The discs anchored on one end of the trace (start OR end), plus one
    bounding disc around all of them for a cheap "segment is far away" test."""

    __slots__ = ("discs", "bound")

    def __init__(self, discs: list[_Disc]):
        self.discs = discs
        c = discs[0]
        reach = 0.0
        for d in discs:
            x, y = c.xy([d.lon0, d.lat0])
            reach = max(reach, math.hypot(x, y) + d.r)
        # +1 % +1 m: absorbs the local-projection error over a multi-km span.
        self.bound = _Disc([c.lon0, c.lat0], reach * 1.01 + 1.0)


def _segment_clear(disc: _Disc, a: Coord, b: Coord) -> bool:
    """True when segment a→b provably stays outside ``disc`` (every point of
    it is within seg/2 of an endpoint, so min endpoint distance − seg/2 is a
    lower bound of its distance to the centre)."""
    pa, pb = disc.xy(a), disc.xy(b)
    seg = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
    return min(math.hypot(*pa), math.hypot(*pb)) > disc.r + seg / 2.0


def _cut_run(run: Run, groups: list[_DiscGroup]) -> list[Run]:
    """Return the pieces of ``run`` lying OUTSIDE every disc of every group.

    Each piece that touches a disc starts/ends on its exact circle (the
    boundary point is interpolated, elevation included). Never mutates
    ``run``; untouched points are shared (as the old slice-based trim did),
    boundary points are new lists.
    """
    pieces: list[Run] = []
    cur: Run = []

    def inside_any(p: Coord) -> bool:
        for g in groups:
            for d in g.discs:
                x, y = d.xy(p)
                if x * x + y * y < d.r2:
                    return True
        return False

    if not inside_any(run[0]):
        cur.append(run[0])
    for i in range(1, len(run)):
        a, b = run[i - 1], run[i]
        # Fast path: clear of every group's bounding disc. Covers ~all segments
        # of a ride once it has left home, whatever the number of anchors.
        if all(_segment_clear(g.bound, a, b) for g in groups):
            if not cur:
                cur.append(a)
            cur.append(b)
            continue
        ts = {0.0, 1.0}
        for g in groups:
            for d in g.discs:
                ts.update(d.crossings(d.xy(a), d.xy(b)))
        for t0, t1 in pairwise(sorted(ts)):
            if inside_any(_interp(a, b, (t0 + t1) / 2.0)):
                if len(cur) >= 2:
                    pieces.append(cur)
                cur = []
                continue
            if not cur:
                cur.append(a if t0 == 0.0 else _interp(a, b, t0))
            cur.append(b if t1 == 1.0 else _interp(a, b, t1))
    if len(cur) >= 2:
        pieces.append(cur)
    return pieces


def _endpoint_anchors(indexed_points, r: float) -> tuple[list[_Disc], int]:
    """Discs covering one end of the trace, and the index of the last fix
    that belongs to that end zone.

    ``indexed_points`` yields ``(index, point)`` walking AWAY from the end.
    The zone is the first ``HEAD_FIXES`` fixes, extended by every following fix
    that stays within ``DWELL_METERS`` of an anchor (the rider still waiting
    at the door). Fixes are greedily clustered: a fix within ``DWELL_METERS``
    of an existing anchor widens that anchor's radius by its distance (so the
    hole is >= ``r`` around the fix itself), otherwise it starts a new anchor.
    """
    anchors: list[list] = []  # [center, pad]
    last = -1
    for k, (idx, pt) in enumerate(indexed_points):
        best = None
        best_d = math.inf
        for anc in anchors:
            dd = haversine_m(anc[0][0], anc[0][1], pt[0], pt[1])
            if dd < best_d:
                best, best_d = anc, dd
        if best is not None and best_d <= DWELL_METERS:
            best[1] = max(best[1], best_d)
        elif k < HEAD_FIXES:
            anchors.append([pt, 0.0])
        else:
            break
        last = idx
    return [_Disc(c, r + pad) for c, pad in anchors], last


def mask_endpoints(coords: list, mask_m: float | None = None) -> list[Run]:
    """Return the display-safe RUNS of a trace with both true ends masked.

    **Radial mask** (data audit C1, 2026-11). Pass RAW fixes (before
    densification — see ``raw_trace_display.mask_then_densify``): the end
    zones are defined on recorded fixes, and densify would otherwise bridge a
    stale first fix to home with a dense line of fake points.

    1. **End zones.** The start zone is the first ``HEAD_FIXES`` fixes plus
       every following fix still within ``DWELL_METERS`` of them (rider
       waiting at the door). Same for the end zone, walking backwards. Using
       several fixes, not just the first one, covers a stale / cell-based
       first fix hundreds of metres or kilometres from home (up to
       ``HEAD_FIXES - 1`` leading outliers): home is one of the next fixes.
    2. Everything from the physical start up to the last start-zone fix (and
       from the first end-zone fix to the physical end) is dropped — this
       removes the straight outlier→home bridge.
    3. **Discs.** Every point within ``mask_m`` metres (great-circle) of ANY
       end-zone fix is dropped, anywhere in the trace — so a loop that swings
       back past the house mid-ride is masked there too. Zone fixes are
       clustered (``DWELL_METERS``) and each cluster's disc is widened by its
       spread, so the hole is >= ``mask_m`` around every zone fix. Cuts are
       interpolated exactly on the circle (elevation included).

    Why radial and not along-track (the previous behaviour): GPS jitter while
    stationary at the door accumulates track length on the spot, so a 200 m
    ALONG-TRACK trim could be consumed within a few metres of home.

    * Track-break sentinels (``None``) split runs; inner run ends far from the
      end zones are kept as-is.
    * A there-and-back (start ≈ end) is covered by construction.
    * A trace whose end zones overlap, or that never leaves the discs, returns
      ``[]`` (fully masked).

    Pure: does not mutate ``coords``. ``mask_m=None`` → env ``TRACE_MASK_METERS``.
    """
    if mask_m is None:
        mask_m = mask_meters()
    if mask_m <= 0:
        return _split_runs(coords)
    fixes = [(i, p) for i, p in enumerate(coords) if p is not None]
    if len(fixes) < 2:
        return []
    head_discs, head_last = _endpoint_anchors(iter(fixes), mask_m)
    tail_discs, tail_first = _endpoint_anchors(reversed(fixes), mask_m)
    if head_last >= tail_first:
        return []
    middle = _split_runs(coords[head_last:tail_first + 1])
    groups = [_DiscGroup(head_discs), _DiscGroup(tail_discs)]
    out: list[Run] = []
    for run in middle:
        out.extend(_cut_run(run, groups))
    return out
