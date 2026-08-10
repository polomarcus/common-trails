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
from collections.abc import Callable, Iterable

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


def build_raster_pyramid(
    geojsonl_path: str,
    *,
    upload_png: Callable[[int, int, int, bytes], None],
    min_zoom: int = DEFAULT_MIN_ZOOM,
    max_zoom: int = DEFAULT_MAX_ZOOM,
) -> dict:
    """Render an XYZ PNG pyramid from a raw-display GeoJSONL and push each
    non-empty tile via ``upload_png(z, x, y, png_bytes)``.

    Returns a stats dict incl. the data-derived ``bounds`` (for the TileJSON).
    Buckets popularity on ``pass_count`` (falls back to ``user_count``). Never
    materialises more than the parsed edge list; renders occupied tiles only.
    """
    edges: list[HeatEdge] = []
    min_lon = min_lat = math.inf
    max_lon = max_lat = -math.inf

    for line in _iter_geojsonl_lines(geojsonl_path):
        try:
            feat = json.loads(line)
        except json.JSONDecodeError:
            continue
        geom = feat.get("geometry") or {}
        if geom.get("type") != "LineString":
            continue
        coords_raw = geom.get("coordinates") or []
        pts: list[tuple[float, float]] = []
        for p in coords_raw:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                lon, lat = float(p[0]), float(p[1])
                pts.append((lon, lat))
                min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
                min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
        if len(pts) < 2:
            continue
        # Simplify (RDP) before rendering — the raw geojsonl has 100k+-point
        # features; re-projecting those per crossed tile is the perf killer.
        # ~11 m tolerance is invisible at overlay zooms (see _SIMPLIFY_TOL_DEG).
        try:
            simp = list(LineString(pts).simplify(_SIMPLIFY_TOL_DEG, preserve_topology=False).coords)
            if len(simp) >= 2:
                pts = [(c[0], c[1]) for c in simp]
        except Exception:  # pragma: no cover - defensive; keep the raw pts
            pass
        props = feat.get("properties") or {}
        # Bucket on pass_count (busy corridors burn brighter); user_count is ~1
        # everywhere at K=1. render_tile_png buckets via HeatEdge.user_count, so
        # feed pass_count there.
        pop = int(props.get("pass_count") or props.get("user_count") or 1)
        edges.append(HeatEdge(coords=tuple(pts), user_count=pop,
                              sport=str(props.get("sport", "all") or "all")))

    stats = {"features": len(edges), "tiles": 0, "by_zoom": {}, "bounds": None}
    if not edges:
        log.warning("raster pyramid: 0 LineString features in %s — nothing to render", geojsonl_path)
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
