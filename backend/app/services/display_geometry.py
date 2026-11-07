"""Compact DISPLAY geometry for the personal "Ma heatmap" layer (/me/activities).

``/map`` loads ``GET /me/activities?limit=5000``. Shipping every activity's
full-resolution ``geometry_geojson`` (every GPS fix, full float precision) cost
1.48 MB raw / 577 KB gzipped for ONE real 38k-point ride. This module derives
the geometry the list endpoint actually needs to draw a line on a map:

1. split at GPS gaps on the RAW coords (``_split_at_gaps``) — FIRST, because a
   simplified long straight road has legitimately long segments that would be
   mistaken for dropouts if the gap test ran after simplification;
2. Douglas-Peucker per part at ``DISPLAY_SIMPLIFY_TOLERANCE_M`` (iterative —
   no recursion limit on 100k-point rides; endpoints always kept; distances in
   local equirectangular metres);
3. coordinates rounded to 5 decimals (~1 m), elevation to 0.1 m (the selected
   activity card still draws an elevation profile from these coords);
4. serialised with compact separators.

Measured on that ride: 38k → 4.7k points, 89 KB raw / 25 KB gzipped (~23×).

TRACE INTEGRITY (Crouzet): ``geometry_geojson`` stays the untouched source of
truth. The output is a DERIVED, display-only cache stored in
``activities.display_geojson`` (migration 0067) and recomputable at any time
(``python -m app.cli.backfill_display_geojson``). The single-activity detail
endpoint (``/me/activities/{id}``) keeps serving full precision.
"""

from __future__ import annotations

import json
import math

from app.services.geo import haversine_m

# A jump larger than this between two consecutive fixes is drawn as a break,
# not a straight line. Beta bug (Sète/Montpellier, 2026-11): GPS dropouts and
# stray fixes drew long straight segments across the sea on "Mes traces".
# Same threshold as the community pipeline's densifier bridge
# (``ingest._DENSIFY_MAX_BRIDGE_M`` — equality pinned by
# test_me_activities::test_threshold_matches_ingest_bridge; not imported to
# avoid an ingest ↔ display_geometry import cycle): below it, a gap is a
# downsampled export stride (measured up to ~1.5 km on real rides) and must
# stay drawn, or sparse traces get holes; above it, a real dropout.
DISPLAY_GAP_BREAK_M = 2500.0

# Douglas-Peucker tolerance. 2 m is below GPS noise (~3–5 m) and below a line
# width at any zoom the personal layer is useful at, yet drops ~88 % of the
# fixes of a 1 Hz recording.
DISPLAY_SIMPLIFY_TOLERANCE_M = 2.0
# Elevation counts in the distance too (the selected-activity card draws its
# profile from these coords): a vertical miss of this many metres weighs like
# a horizontal miss of DISPLAY_SIMPLIFY_TOLERANCE_M. Without it a straight
# road over a crest collapses to 2 points and the profile goes linear.
DISPLAY_SIMPLIFY_ELE_TOLERANCE_M = 5.0

# 5 decimals of a degree ≈ 1.1 m of latitude.
DISPLAY_COORD_DECIMALS = 5
DISPLAY_ELE_DECIMALS = 1

_EARTH_R_M = 6_371_000.0


def _split_at_gaps(geometry: dict, max_gap_m: float = DISPLAY_GAP_BREAK_M) -> dict:
    """Split a LineString into a MultiLineString wherever two consecutive
    points are more than ``max_gap_m`` apart.

    DISPLAY-ONLY (trace integrity, Crouzet): every point is emitted verbatim
    and in order — no simplification, no snapping, no interpolation; the stored
    ``geometry_geojson`` is never touched. Only the pen is lifted across the gap.
    A part reduced to a single isolated fix (an outlier between two jumps)
    cannot be drawn as a line and is left out of the display geometry. If no
    drawable part remains, the geometry is returned unchanged.
    """
    if geometry.get("type") != "LineString":
        return geometry
    coords = geometry.get("coordinates") or []
    if len(coords) < 2:
        return geometry
    parts: list[list] = [[coords[0]]]
    for prev, cur in zip(coords, coords[1:], strict=False):
        if haversine_m(prev[0], prev[1], cur[0], cur[1]) > max_gap_m:
            parts.append([cur])
        else:
            parts[-1].append(cur)
    if len(parts) == 1:
        return geometry
    drawable = [p for p in parts if len(p) >= 2]
    if not drawable:
        return geometry
    if len(drawable) == 1:
        return {"type": "LineString", "coordinates": drawable[0]}
    return {"type": "MultiLineString", "coordinates": drawable}


def _simplify_dp(coords: list, tolerance_m: float = DISPLAY_SIMPLIFY_TOLERANCE_M) -> list:
    """Douglas-Peucker on a list of ``[lon, lat, (ele…)]`` points.

    Iterative (explicit stack), so a 100k-point ride cannot hit the recursion
    limit. Distances are point-to-SEGMENT (not to the infinite line, which
    would wrongly drop the far end of an out-and-back) in equirectangular
    metres around the part's mean latitude — exact enough at the metre scale
    over the extent of one ride. Both endpoints are always kept; kept points
    are returned verbatim (untouched values, original order).
    """
    n = len(coords)
    if n <= 2:
        return list(coords)
    lat0 = math.radians(sum(c[1] for c in coords) / n)
    kx = _EARTH_R_M * math.cos(lat0) * math.pi / 180.0
    ky = _EARTH_R_M * math.pi / 180.0
    xs = [c[0] * kx for c in coords]
    ys = [c[1] * ky for c in coords]
    # Elevation scaled so DISPLAY_SIMPLIFY_ELE_TOLERANCE_M ≡ tolerance_m; only
    # when every point carries one (mixed 2D/3D input → plain 2D).
    kz = tolerance_m / DISPLAY_SIMPLIFY_ELE_TOLERANCE_M
    zs = [float(c[2]) * kz for c in coords] if all(len(c) > 2 and c[2] is not None for c in coords) else None
    tol2 = tolerance_m * tolerance_m

    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        ax, ay = xs[a], ys[a]
        dx, dy = xs[b] - ax, ys[b] - ay
        seg2 = dx * dx + dy * dy
        best_d2 = -1.0
        best_i = a
        for i in range(a + 1, b):
            px, py = xs[i] - ax, ys[i] - ay
            if seg2 > 0.0:
                t = (px * dx + py * dy) / seg2
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                ex, ey = px - t * dx, py - t * dy
            else:
                t = 0.0
                ex, ey = px, py
            d2 = ex * ex + ey * ey
            if zs is not None:
                ez = zs[i] - (zs[a] + t * (zs[b] - zs[a]))
                d2 += ez * ez
            if d2 > best_d2:
                best_d2 = d2
                best_i = i
        if best_d2 > tol2:
            keep[best_i] = True
            stack.append((a, best_i))
            stack.append((best_i, b))
    return [c for c, k in zip(coords, keep, strict=True) if k]


def _round_coord(c: list) -> list:
    out = [round(float(c[0]), DISPLAY_COORD_DECIMALS), round(float(c[1]), DISPLAY_COORD_DECIMALS)]
    if len(c) > 2 and c[2] is not None:
        out.append(round(float(c[2]), DISPLAY_ELE_DECIMALS))
    return out


def _compact_part(coords: list, tolerance_m: float) -> list:
    return [_round_coord(c) for c in _simplify_dp(coords, tolerance_m)]


def compact_display_geometry(
    geometry: dict,
    tolerance_m: float = DISPLAY_SIMPLIFY_TOLERANCE_M,
    max_gap_m: float = DISPLAY_GAP_BREAK_M,
) -> dict:
    """The geometry ``/me/activities`` serves: gap-split (on raw coords), then
    Douglas-Peucker per part, then rounded. Pure — never mutates ``geometry``.

    A MultiLineString input is not re-split (it already has its parts) but each
    part is simplified + rounded. Any other geometry type, and LineStrings with
    fewer than 2 points, are returned unchanged.
    """
    gtype = geometry.get("type")
    if gtype == "LineString":
        coords = geometry.get("coordinates") or []
        if len(coords) < 2:
            return geometry
        geometry = _split_at_gaps(geometry, max_gap_m)
        gtype = geometry["type"]
    if gtype == "LineString":
        return {"type": "LineString", "coordinates": _compact_part(geometry["coordinates"], tolerance_m)}
    if gtype == "MultiLineString":
        return {
            "type": "MultiLineString",
            "coordinates": [
                _compact_part(p, tolerance_m) if len(p) >= 2 else p for p in (geometry.get("coordinates") or [])
            ],
        }
    return geometry


def compact_display_geojson(geometry_geojson: str | None) -> str | None:
    """``geometry_geojson`` TEXT → compact display GeoJSON TEXT (the value
    stored in ``activities.display_geojson``). ``None`` for a missing or
    unparseable geometry — best-effort: callers (ingest, backfill) must never
    fail on it; the read path serves the full-precision gap-split geometry when the cache is NULL."""
    if not geometry_geojson:
        return None
    try:
        geometry = json.loads(geometry_geojson)
        if not isinstance(geometry, dict):
            return None
        return json.dumps(compact_display_geometry(geometry), separators=(",", ":"))
    except (ValueError, TypeError, KeyError, IndexError):
        return None
