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
runs; the two mask centres are the physical start of the FIRST run and the
physical end of the LAST run, and the discs around them apply to every run.
"""
from __future__ import annotations

import math
import os
from itertools import pairwise

from app.services.geo import haversine_m

Coord = list
Run = list

DEFAULT_MASK_METERS = 200.0

# GPS-uncertainty margin around the first/last fix. The first recorded point is
# only an ESTIMATE of home: while the rider waits for a fix, consecutive fixes
# scatter by several metres (more in an urban canyon). Each disc is therefore
# widened by the spread of the leading (resp. trailing) points that stay within
# ``DWELL_METERS`` of the endpoint, so the hole is >= R around ANY of those
# fixes — i.e. around wherever home really is inside that cluster — not just
# around the first one.
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


def _cut_run(run: Run, discs: list[_Disc]) -> list[Run]:
    """Return the pieces of ``run`` lying OUTSIDE every disc.

    Each piece that touches a disc starts/ends on its exact circle (the
    boundary point is interpolated, elevation included). Never mutates
    ``run``; untouched points are shared (as the old slice-based trim did),
    boundary points are new lists.
    """
    pieces: list[Run] = []
    cur: Run = []

    def inside_any(xys: list[tuple[float, float]]) -> bool:
        return any(x * x + y * y < d.r2 for d, (x, y) in zip(discs, xys, strict=True))

    prev_xy = [d.xy(run[0]) for d in discs]
    if not inside_any(prev_xy):
        cur.append(run[0])
    for i in range(1, len(run)):
        a, b = run[i - 1], run[i]
        cur_xy = [d.xy(b) for d in discs]
        # Fast path: the whole segment is provably clear of every disc (each
        # point of it is within seg/2 of an endpoint). Covers ~all segments of
        # a densified (3 m) ride once it has left home.
        clear = True
        for d, pa, pb in zip(discs, prev_xy, cur_xy, strict=True):
            seg = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
            near = min(math.hypot(*pa), math.hypot(*pb))
            if near <= d.r + seg / 2.0:
                clear = False
                break
        if clear:
            if not cur:
                cur.append(a)
            cur.append(b)
            prev_xy = cur_xy
            continue
        ts = sorted({0.0, 1.0, *(t for d, pa, pb in zip(discs, prev_xy, cur_xy, strict=True)
                                 for t in d.crossings(pa, pb))})
        for t0, t1 in pairwise(ts):
            tm = (t0 + t1) / 2.0
            mid_xy = [(pa[0] + (pb[0] - pa[0]) * tm, pa[1] + (pb[1] - pa[1]) * tm)
                      for pa, pb in zip(prev_xy, cur_xy, strict=True)]
            if inside_any(mid_xy):
                if len(cur) >= 2:
                    pieces.append(cur)
                cur = []
                continue
            if not cur:
                cur.append(a if t0 == 0.0 else _interp(a, b, t0))
            cur.append(b if t1 == 1.0 else _interp(a, b, t1))
        prev_xy = cur_xy
    if len(cur) >= 2:
        pieces.append(cur)
    return pieces


def _dwell_spread(p0: Coord, following) -> float:
    """Max distance from ``p0`` over the ``following`` points (an iterable,
    walked away from ``p0``) that stay within ``DWELL_METERS`` of it — stops at
    the first point beyond. Bounded by ``DWELL_METERS``; 0 for a trace that
    leaves in one long step."""
    spread = 0.0
    for pt in following:
        d = haversine_m(p0[0], p0[1], pt[0], pt[1])
        if d > DWELL_METERS:
            break
        spread = max(spread, d)
    return spread


def mask_endpoints(coords: list, mask_m: float | None = None) -> list[Run]:
    """Return the display-safe RUNS of a trace with both true ends masked.

    **Radial mask** (C1 data audit, 2026-11): let ``S`` be the physical start
    (first point of the first run) and ``E`` the physical end (last point of the
    last run). EVERY point lying within ``mask_m`` metres (great-circle) of ``S``
    or of ``E`` — anywhere in the trace, not only at its ends — is dropped. The
    published geometry is the part of the polyline outside both discs, cut at
    the exact circle crossing (boundary point interpolated, elevation too).

    Each disc's radius is ``mask_m + spread`` where ``spread`` (<=
    ``DWELL_METERS``) is how far the leading/trailing fixes wander from the
    endpoint before the trace moves away (see ``_dwell_spread``): the first fix
    is only an estimate of home, so the guarantee is a hole of at least
    ``mask_m`` around EVERY fix of that initial/final cluster.

    Why radial and not along-track (the previous behaviour): GPS jitter while
    stationary at the door (waiting for a fix, 1 Hz recording) accumulates
    track length on the spot, so a 200 m ALONG-TRACK trim could be consumed
    within a few metres of home and the departure was drawn from the doorstep.
    A radius is immune to that. Masking mid-ride passes too means a loop that
    swings back past the house, or a stop at home, never draws within ``mask_m``
    of it.

    * A trace leaving the disc and re-entering it splits into several runs.
    * Track-break sentinels (``None``) still split runs; inner run ends far from
      ``S``/``E`` are kept as-is.
    * A there-and-back (``S`` ≈ ``E``) is covered by construction.
    * A trace entirely within the discs returns ``[]`` (fully masked).

    Pure: does not mutate ``coords``. ``mask_m=None`` → env ``TRACE_MASK_METERS``.
    """
    if mask_m is None:
        mask_m = mask_meters()
    runs = _split_runs(coords)
    if not runs:
        return []
    if mask_m <= 0:
        return runs
    start, end = runs[0][0], runs[-1][-1]
    forward = (pt for run in runs for pt in run)
    backward = (pt for run in reversed(runs) for pt in reversed(run))
    discs = [_Disc(start, mask_m + _dwell_spread(start, forward))]
    if haversine_m(start[0], start[1], end[0], end[1]) > 0.0:
        discs.append(_Disc(end, mask_m + _dwell_spread(end, backward)))
    out: list[Run] = []
    for run in runs:
        out.extend(_cut_run(run, discs))
    return out
