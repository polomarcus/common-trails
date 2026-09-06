"""Pre-rendered raster XYZ tile pyramid of the community heatmap.

Publishes ``{z}/{x}/{y}.png`` (256px, transparent PNG) + a ``tiles.json``
(TileJSON 2.2.0) to a public GCS prefix, so gpx.studio / VisuGPX (and any
Leaflet/OpenLayers/MapLibre client) can add the community heatmap as a custom
overlay layer ("calque") via a single URL.

Source = the raw-display GeoJSONL the pmtiles build already writes
(``raw_trace_display.export_raw_geojson`` → masked, K-gated ``LineString``s),
NOT the dead ``heat_edges`` substrate. Reuses the Pillow rasteriser in
``heatmap_raster`` (``render_tile_png``) unchanged.

Cost discipline (the load-bearing design choice): render ONLY the tiles a trace
actually crosses, with the pyramid bbox derived from the DATA — NEVER the full
``HEATMAP_RAW_BBOX`` rectangle (Europe z6-14 = ~5.3 M tiles). Popularity is
bucketed on ``pass_count`` (not ``user_count``, which is ~1 everywhere at K=1 →
a flat monochrome map).
"""
from __future__ import annotations

import gzip
import json
import logging
import math
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Sequence
from typing import NamedTuple

from shapely.geometry import LineString

from app.services.heatmap_raster import (
    TILE_PX,
    HeatEdge,
    _lonlat_to_world_px,
    render_tile_png,
)

log = logging.getLogger(__name__)

# A calque overlay never needs street-level z15+ (quadruples cost for detail an
# overlay doesn't use). z6 = national overview, z14 (~2.4 km/tile) = "where do
# people ride". Overridable via HEATMAP_RASTER_MAX_ZOOM.
DEFAULT_MIN_ZOOM = 6
DEFAULT_MAX_ZOOM = 14

# Segment-walk step in pixels when enumerating the tiles a polyline crosses.
# Must be < TILE_PX so no tile is skipped between samples; 64 px is a safe
# quarter-tile stride.
_WALK_STEP_PX = 64.0

# Douglas-Peucker tolerance (degrees) applied to every feature before rendering.
# ~0.0001° ≈ 11 m ≈ 1.2 px at z14 (~9.5 m/px) — invisible on a raster overlay,
# but the raw geojsonl carries features with 100k+ points (a full high-frequency
# ride's masked run); without this, render_tile_png re-projects those millions
# of points per crossed tile → minutes. Measured 44× fewer coords, no visible
# change at overlay zooms.
_SIMPLIFY_TOL_DEG = 0.0001


def _iter_geojsonl_lines(path: str) -> Iterable[str]:
    """Yield each non-empty line of a GeoJSONL file (transparently gunzips a
    ``.gz`` or gzip-magic file)."""
    opener = open
    with open(path, "rb") as probe:
        if probe.read(2) == b"\x1f\x8b":
            opener = gzip.open  # gzip magic
    with opener(path, "rt", encoding="utf-8") as fh:  # type: ignore[operator]
        for line in fh:
            line = line.strip()
            if line:
                yield line


def _tiles_for_polyline(coords: tuple[tuple[float, float], ...], zoom: int):
    """Yield the ``(x, y)`` XYZ tiles a lon/lat polyline crosses at ``zoom``.

    Walks each segment in <=1/4-tile pixel steps so no crossed tile is missed,
    without the huge over-count of a bbox rectangle (a long diagonal trace's
    bbox covers tiles it never touches)."""
    n = 1 << zoom
    seen: set[tuple[int, int]] = set()
    if len(coords) < 2:
        if coords:
            px, py = _lonlat_to_world_px(coords[0][0], coords[0][1], zoom)
            tx, ty = int(px // TILE_PX), int(py // TILE_PX)
            if 0 <= tx < n and 0 <= ty < n:
                seen.add((tx, ty))
        yield from seen
        return
    prev_px, prev_py = _lonlat_to_world_px(coords[0][0], coords[0][1], zoom)
    for lon, lat in coords[1:]:
        cur_px, cur_py = _lonlat_to_world_px(lon, lat, zoom)
        dx, dy = cur_px - prev_px, cur_py - prev_py
        dist = math.hypot(dx, dy)
        steps = max(1, int(dist / _WALK_STEP_PX))
        for s in range(steps + 1):
            t = s / steps
            px = prev_px + dx * t
            py = prev_py + dy * t
            tx, ty = int(px // TILE_PX), int(py // TILE_PX)
            if 0 <= tx < n and 0 <= ty < n:
                seen.add((tx, ty))
        prev_px, prev_py = cur_px, cur_py
    yield from seen


def build_tilejson(
    *, tiles_url: str, min_zoom: int, max_zoom: int,
    bounds: tuple[float, float, float, float], attribution: str,
) -> dict:
    """A minimal TileJSON 2.2.0 pointing at the pyramid — the single URL a user
    pastes into gpx.studio / VisuGPX to add the calque."""
    min_lon, min_lat, max_lon, max_lat = bounds
    return {
        "tilejson": "2.2.0",
        "name": "CHEMINS COMMUNS — community cycling heatmap",
        "description": (
            "Community trail popularity heatmap (raw GPS traces, endpoint-masked). "
            "ODbL 1.0."
        ),
        "version": "1.0.0",
        "scheme": "xyz",
        "tiles": [tiles_url],
        "minzoom": min_zoom,
        "maxzoom": max_zoom,
        "bounds": [round(min_lon, 5), round(min_lat, 5), round(max_lon, 5), round(max_lat, 5)],
        "center": [
            round((min_lon + max_lon) / 2, 5),
            round((min_lat + max_lat) / 2, 5),
            min(min_zoom + 4, max_zoom),
        ],
        "attribution": attribution,
        "license": "ODbL-1.0",
    }


class ParsedFeature(NamedTuple):
    """One geojsonl LineString feature, parsed + RDP-simplified ONCE.

    ``bounds`` is the feature's RAW-coordinate extent (pre-simplification) —
    kept per-feature so a sport-filtered render can reconstruct the exact
    FILTERED bounds the legacy single-pass builder computed. ``coords`` may
    hold a single point (a degenerate feature): the legacy path let such a
    feature extend the bounds but never rendered it, and the render below
    preserves that.
    """

    sport: str
    coords: tuple[tuple[float, float], ...]  # simplified (lon, lat) run
    pop: int  # pass_count fallback user_count fallback 1
    bounds: tuple[float, float, float, float]  # min_lon, min_lat, max_lon, max_lat


def parse_heat_edges(geojsonl_path: str) -> list[ParsedFeature]:
    """Read + json-parse + RDP-simplify the raw-display GeoJSONL ONCE.

    Split out of ``build_raster_pyramid`` so the 5 per-sport calques (plus the
    optional combined one) can share a single parse: re-reading and
    re-simplifying the whole national corpus per pyramid was ~5-6× the
    necessary parse cost of a rebuild. Sport filtering happens at RENDER time
    (``render_pyramid_from_parsed``) on the in-memory list instead.
    """
    features: list[ParsedFeature] = []
    for line in _iter_geojsonl_lines(geojsonl_path):
        try:
            feat = json.loads(line)
        except json.JSONDecodeError:
            continue
        geom = feat.get("geometry") or {}
        if geom.get("type") != "LineString":
            continue
        props = feat.get("properties") or {}
        sport = str(props.get("sport", "all") or "all")
        coords_raw = geom.get("coordinates") or []
        pts: list[tuple[float, float]] = []
        f_min_lon = f_min_lat = math.inf
        f_max_lon = f_max_lat = -math.inf
        for p in coords_raw:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                lon, lat = float(p[0]), float(p[1])
                pts.append((lon, lat))
                f_min_lon, f_max_lon = min(f_min_lon, lon), max(f_max_lon, lon)
                f_min_lat, f_max_lat = min(f_min_lat, lat), max(f_max_lat, lat)
        if not pts:
            continue
        if len(pts) >= 2:
            # Simplify (RDP) before rendering — the raw geojsonl has
            # 100k+-point features; re-projecting those per crossed tile is
            # the perf killer. ~11 m tolerance is invisible at overlay zooms
            # (see _SIMPLIFY_TOL_DEG).
            try:
                simp = list(LineString(pts).simplify(_SIMPLIFY_TOL_DEG, preserve_topology=False).coords)
                if len(simp) >= 2:
                    pts = [(c[0], c[1]) for c in simp]
            except Exception:  # pragma: no cover - defensive; keep the raw pts
                pass
        # Bucket on pass_count (busy corridors burn brighter); user_count is ~1
        # everywhere at K=1. render_tile_png buckets via HeatEdge.user_count, so
        # feed pass_count there.
        pop = int(props.get("pass_count") or props.get("user_count") or 1)
        features.append(ParsedFeature(
            sport=sport, coords=tuple(pts), pop=pop,
            bounds=(f_min_lon, f_min_lat, f_max_lon, f_max_lat),
        ))
    return features


def render_pyramid_from_parsed(
    features: Sequence[ParsedFeature],
    *,
    upload_png: Callable[[int, int, int, bytes], None],
    min_zoom: int = DEFAULT_MIN_ZOOM,
    max_zoom: int = DEFAULT_MAX_ZOOM,
    sport_filter: Collection[str] | None = None,
    source: str = "<parsed>",
) -> dict:
    """Render an XYZ PNG pyramid from PRE-PARSED features (``parse_heat_edges``)
    and push each non-empty tile via ``upload_png(z, x, y, png_bytes)``.

    ``sport_filter`` (a set of raw sport ids, already ``expand_sport``-ed by the
    caller): when given, only features whose ``sport`` is IN the set are
    rendered — this drives the per-sport calques (``raster-<sport>/``) so a
    gpx.studio overlay can show just gravel / mtb / road. ``None`` = all sports
    (the combined ``raster/`` pyramid). The ``bounds`` in the returned stats are
    the FILTERED features' bounds (identical to a filtered single-pass parse).

    Returns a stats dict incl. the data-derived ``bounds`` (for the TileJSON).
    Never materialises more than the edge list; renders occupied tiles only.
    ``source`` labels the empty-corpus warning (the geojsonl path when called
    through the ``build_raster_pyramid`` wrapper).
    """
    edges: list[HeatEdge] = []
    min_lon = min_lat = math.inf
    max_lon = max_lat = -math.inf

    for pf in features:
        # Per-sport calque: skip features outside the requested set BEFORE they
        # touch bounds/edges, so raster-<sport>/ + its TileJSON are sport-exact.
        if sport_filter is not None and pf.sport not in sport_filter:
            continue
        f_min_lon, f_min_lat, f_max_lon, f_max_lat = pf.bounds
        min_lon, max_lon = min(min_lon, f_min_lon), max(max_lon, f_max_lon)
        min_lat, max_lat = min(min_lat, f_min_lat), max(max_lat, f_max_lat)
        if len(pf.coords) < 2:
            continue  # degenerate feature: bounds-only, never rendered
        edges.append(HeatEdge(coords=pf.coords, user_count=pf.pop, sport=pf.sport))

    stats = {"features": len(edges), "tiles": 0, "by_zoom": {}, "bounds": None}
    if not edges:
        log.warning("raster pyramid: 0 LineString features in %s — nothing to render", source)
        return stats

    bounds = (min_lon, min_lat, max_lon, max_lat)
    stats["bounds"] = [round(v, 6) for v in bounds]

    for zoom in range(min_zoom, max_zoom + 1):
        tile_edges: dict[tuple[int, int], list[HeatEdge]] = defaultdict(list)
        for edge in edges:
            for txy in _tiles_for_polyline(edge.coords, zoom):
                tile_edges[txy].append(edge)
        z_count = 0
        for (tx, ty), es in tile_edges.items():
            png = render_tile_png(es, tx, ty, zoom)
            if png is None:
                continue
            upload_png(zoom, tx, ty, png)
            z_count += 1
        stats["by_zoom"][zoom] = z_count
        stats["tiles"] += z_count
        log.info("raster pyramid z%d: %d tiles (from %d occupied cells)",
                 zoom, z_count, len(tile_edges))

    return stats


def build_raster_pyramid(
    geojsonl_path: str,
    *,
    upload_png: Callable[[int, int, int, bytes], None],
    min_zoom: int = DEFAULT_MIN_ZOOM,
    max_zoom: int = DEFAULT_MAX_ZOOM,
    sport_filter: Collection[str] | None = None,
) -> dict:
    """Back-compat one-shot API: parse ``geojsonl_path`` then render ONE pyramid.

    Thin wrapper over ``parse_heat_edges`` + ``render_pyramid_from_parsed``
    (behaviour, stats and tile bytes are identical to the historical
    single-pass implementation). Callers building SEVERAL pyramids from the
    same geojsonl (build_pmtiles' per-sport calques) should parse once and
    call ``render_pyramid_from_parsed`` per pyramid instead.
    """
    return render_pyramid_from_parsed(
        parse_heat_edges(geojsonl_path),
        upload_png=upload_png,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        sport_filter=sport_filter,
        source=geojsonl_path,
    )
