"""Endpoint masking — the privacy mechanism for the raw-trace heatmap.

Product decision (Paul, 2026-07-21): the raw-trace display source (see
``HEATMAP_DISPLAY_SOURCE`` in ``build_pmtiles``) renders precise individual
GPS traces instead of K-anonymised, OSM-matched, cross-rider aggregates.
Privacy in that mode is NOT K-anonymity — it is achieved by **trimming the
first and last N metres of every trace** so a rider's home / work is never
drawn on the public map.

This module is the pure, side-effect-free SSOT for that trim. It NEVER mutates
the stored GPS geometry (Crouzet invariant #1) — callers pass a copy of the
parsed coordinates and get back a trimmed copy for DISPLAY only.

Coordinate convention (matches the rest of ingest): ``[lon, lat]`` or
``[lon, lat, ele]``. Track-break sentinels (``None``, emitted by
``ingest._densify_coords`` on a GPS dropout) split the trace into independent
runs; masking applies to the physical start of the FIRST run and the physical
end of the LAST run only.
"""
from __future__ import annotations

import os

from app.services.geo import haversine_m

Coord = list
Run = list

DEFAULT_MASK_METERS = 200.0


def mask_meters() -> float:
    """Resolve the mask distance from ``TRACE_MASK_METERS`` (default 200 m)."""
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


def _trim_run_front(run: Run, m: float) -> tuple[Run | None, float]:
    """Drop the first ``m`` metres (along-track) of one run.

    Returns ``(trimmed_run, 0.0)`` when the run survives, or
    ``(None, leftover)`` when the whole run is shorter than ``m`` (the caller
    then keeps consuming ``leftover`` metres from the next run).
    """
    remaining = m
    for i in range(len(run) - 1):
        seg = haversine_m(run[i][0], run[i][1], run[i + 1][0], run[i + 1][1])
        if seg <= 0:
            continue
        if remaining < seg:
            frac = remaining / seg
            boundary = _interp(run[i], run[i + 1], frac)
            return [boundary, *run[i + 1:]], 0.0
        remaining -= seg
    return None, remaining


def _trim_run_back(run: Run, m: float) -> tuple[Run | None, float]:
    reversed_run, leftover = _trim_run_front(list(reversed(run)), m)
    if reversed_run is None:
        return None, leftover
    return list(reversed(reversed_run)), 0.0


def _trim_front(runs: list[Run], m: float) -> list[Run]:
    remaining = m
    out = list(runs)
    while out:
        trimmed, leftover = _trim_run_front(out[0], remaining)
        if trimmed is not None:
            out[0] = trimmed
            return out
        # Whole run consumed by the mask (it was near home) → drop it and
        # keep eating into the next run. This is the privacy-safe choice.
        out.pop(0)
        remaining = leftover
    return []


def _trim_back(runs: list[Run], m: float) -> list[Run]:
    remaining = m
    out = list(runs)
    while out:
        trimmed, leftover = _trim_run_back(out[-1], remaining)
        if trimmed is not None:
            out[-1] = trimmed
            return out
        out.pop()
        remaining = leftover
    return []


def mask_endpoints(coords: list, mask_m: float | None = None) -> list[Run]:
    """Return the display-safe RUNS of a trace with both true ends trimmed.

    * Trims ``mask_m`` metres (along-track, interpolating the exact boundary
      point so the hole is precisely ``mask_m`` wide) from the physical start
      of the first run and the physical end of the last run.
    * A there-and-back ride (start == end == home) has both occurrences of
      home trimmed because both the start and the end of the polyline are
      trimmed.
    * A trace whose total remaining length after trimming both ends is empty
      (i.e. total length <= 2 * mask_m for a single-run trace) returns ``[]``
      — fully masked, contribute nothing.

    Pure: does not mutate ``coords``. ``mask_m=None`` → env ``TRACE_MASK_METERS``.
    """
    if mask_m is None:
        mask_m = mask_meters()
    runs = _split_runs(coords)
    if not runs:
        return []
    if mask_m <= 0:
        return runs
    runs = _trim_front(runs, mask_m)
    if not runs:
        return []
    runs = _trim_back(runs, mask_m)
    return [r for r in runs if len(r) >= 2]
