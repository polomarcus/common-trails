"""Raster MBTiles renderer for the heatmap export (PRD #391, Phase 2.5).

Renders bbox-clipped heat_edges as colored PNG tiles into a SQLite
MBTiles container, for consumption by raster-only viewers (Alpine
Quest, OruxMaps raster mode, Locus raster mode, Gaia GPS).

Why a hand-rolled raster pipeline rather than `mapbox-gl-native` or
`gdal+mapnik`? Both bring ~500 MB of native deps. Vector MBTiles
(Phase 2) reuses tippecanoe, which is already in the container.
Raster needs *some* renderer; the lightest path is Pillow (already
pinned for OG-card generation in `app/api/share.py`).

Pipeline:

1. Caller passes a list of heat-edge dicts (LineString geometry +
   user_count + sport) — typically the result of
   `ingest.get_heat_edges_public(bbox=..., sport=..., k=...)`.
2. For each `(z, x, y)` tile inside the bbox at zoom levels in
   ``ZOOM_LEVELS``, project the line segments into 256×256 pixel
   space and rasterise with ``PIL.ImageDraw``.
3. Write the resulting PNG bytes into the MBTiles SQLite container
   (XYZ → TMS Y-flip; MBTiles spec).

Time budget: at z10–z13 a 50 km × 50 km bbox covers ≤ ~64 tiles per
zoom. Rendering each tile is dominated by the Python loop projecting
~thousands of segments — empirically 50–500 ms per tile in dev.
Total budget: well under the 60 s synchronous build wall-clock that
the backend enforces.

If the heat_edges count is large enough that the wall-clock exceeds
the budget, the caller short-circuits to a 400 ("reduce bbox" hint)
— same UX as the vector MBTiles path in Phase 2.

The MBTiles SQLite schema follows the official spec:
https://github.com/mapbox/mbtiles-spec/blob/master/1.3/spec.md
"""
from __future__ import annotations

import io
import logging
import math
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

# Zoom levels rendered into the raster MBTiles. The PRD §2.2 calls for
# z6–z14 but rendering 9 zoom levels with Pillow blows the 60 s budget
# at non-trivial bboxes. Trade-off documented in the PRD: raster
# tileset is capped at z10–z13 (z10 ≈ overview at ~150 km, z13 ≈
# neighborhood detail). Phase 3 can extend the range if Persona B/C
# users request it.
DEFAULT_ZOOM_MIN = 10
DEFAULT_ZOOM_MAX = 13

# Tile size in pixels. 256 is the spec default and what Alpine Quest /
# OruxMaps assume by default.
TILE_PX = 256

# PNG palette for popularity buckets.
#
# NOTE: this palette intentionally DOES NOT match the in-app MapLibre
# style at `frontend/lib/init-map-layers.ts:219-258`, which is a
# continuous dark-plum → hot-pink → orange interpolation driven by
# `heat_score` (a continuous value that combines pass_count + activity
# recency). The raster export uses discrete buckets on `user_count`
# because:
#   - MBTiles consumers (Alpine Quest, OruxMaps raster, Locus raster,
#     Gaia GPS) typically render with a simpler color model than
#     MapLibre's data-driven expressions.
#   - Discrete buckets reproduce well in PNG without anti-aliasing
#     artefacts from continuous interpolation.
#   - `user_count` is a more intuitive privacy/popularity axis for
#     raster users than `heat_score` (which combines pass_count and
#     activity recency).
# If a user reports "my export doesn't look like the website",
# point them at this docstring. Aligning the two palettes is tracked
# as Phase 2.6.
#
# Bucket 0 is reserved for "edges below K-anonymity" — never written.
# RGBA so consuming apps can render the heatmap as a semi-transparent
# overlay on top of their own base map (the dominant use case).
_BUCKET_RGBA: tuple[tuple[int, int, int, int], ...] = (
    (0, 0, 0, 0),            # 0 — never used (privacy floor)
    (40, 180, 80, 200),      # 1 — green (1-2 users)
    (220, 200, 60, 215),     # 2 — yellow (3-5)
    (240, 150, 50, 225),     # 3 — orange (6-15)
    (235, 70, 70, 235),      # 4 — red (16-50)
    (220, 50, 220, 245),     # 5 — magenta (51+)
)

# Line widths per popularity bucket, in pixels. Wider for popular
# segments so they pop above quieter ones (line-sort-key equivalent).
_BUCKET_WIDTH: tuple[int, ...] = (0, 1, 2, 3, 4, 5)


def _bucket_for(user_count: int) -> int:
    """Map ``user_count`` → popularity bucket 1..5.

    Same thresholds as the matview ``heat_edges_display.bucket`` CASE
    (see ``docs/heatmap-pipeline.md`` § 2). Returns 0 for ``< 1`` so
    the caller can skip; should never happen if the source query has
    a ``user_count >= K`` filter, but defensive.
    """
    if user_count < 1:
        return 0
    if user_count <= 2:
        return 1
    if user_count <= 5:
        return 2
    if user_count <= 15:
        return 3
    if user_count <= 50:
        return 4
    return 5


# ── Web Mercator math ────────────────────────────────────────────────────────


def _lonlat_to_world_px(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    """Project (lon, lat) → world-pixel coordinates at the given zoom.

    World pixel space at zoom Z has size ``(2**Z * TILE_PX)`` on each
    side; the standard slippy-map projection. Coordinates can be
    fractional — the caller subtracts the tile origin and clamps to
    pixel rounding.
    """
    siny = math.sin(math.radians(lat))
    # Clamp to avoid singular log near the poles (tile math is undefined
    # at |lat| ≥ 85.05113°).
    siny = max(min(siny, 0.9999), -0.9999)
    n = float(1 << zoom)
    px = (lon + 180.0) / 360.0 * n * TILE_PX
    py = (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * n * TILE_PX
    return px, py


def _tile_range_for_bbox(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float, zoom: int,
) -> tuple[int, int, int, int]:
    """Return (x_min, y_min, x_max, y_max) XYZ tile indices covering bbox.

    Returned range is inclusive on both ends. Note XYZ Y axis goes
    *down* (Y=0 is the north edge); MBTiles flips to TMS (Y=0 south)
    at write time via :func:`xyz_to_tms_row`.
    """
    n = 1 << zoom
    px_minlon, py_maxlat = _lonlat_to_world_px(min_lon, max_lat, zoom)
    px_maxlon, py_minlat = _lonlat_to_world_px(max_lon, min_lat, zoom)
    x_min = max(0, int(px_minlon // TILE_PX))
    x_max = min(n - 1, int(px_maxlon // TILE_PX))
    y_min = max(0, int(py_maxlat // TILE_PX))
    y_max = min(n - 1, int(py_minlat // TILE_PX))
    return x_min, y_min, x_max, y_max


def xyz_to_tms_row(y: int, zoom: int) -> int:
    """Convert an XYZ tile Y to MBTiles TMS row (Y axis flip).

    MBTiles spec: rows count from the *bottom*, XYZ from the *top*.
    """
    return (1 << zoom) - 1 - y


# ── Rendering ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HeatEdge:
    """Minimal projection of ``ingest.get_heat_edges_public`` rows.

    Frozen for safety; not all callers use it but the tests do (the
    integration test seeds tiny synthetic fixtures).
    """
    coords: tuple[tuple[float, float], ...]  # ((lon, lat), ...)
    user_count: int
    sport: str = "all"

    @classmethod
    def from_edge_dict(cls, edge: dict) -> HeatEdge:
        geom = edge.get("geometry") or {}
        coords_raw = geom.get("coordinates") or []
        pts: list[tuple[float, float]] = []
        for p in coords_raw:
            # GeoJSON coords are [lon, lat] (optionally with elevation
            # as a third member — ignored for raster).
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                pts.append((float(p[0]), float(p[1])))
        return cls(
            coords=tuple(pts),
            user_count=int(edge.get("user_count", 0) or 0),
            sport=str(edge.get("sport", "all") or "all"),
        )


def _project_edge_to_tile(
    edge: HeatEdge, tile_x: int, tile_y: int, zoom: int,
) -> list[tuple[float, float]] | None:
    """Project an edge's lon/lat polyline into one tile's pixel space.

    Returns ``None`` if the edge has < 2 points (nothing to draw).
    The list may include pixels outside [0, TILE_PX) — Pillow clips at
    draw time. Caller still benefits from skipping edges whose
    bounding box is entirely outside the tile rectangle.
    """
    if len(edge.coords) < 2:
        return None
    origin_x = tile_x * TILE_PX
    origin_y = tile_y * TILE_PX
    out: list[tuple[float, float]] = []
    for lon, lat in edge.coords:
        px, py = _lonlat_to_world_px(lon, lat, zoom)
        out.append((px - origin_x, py - origin_y))
    return out


def _edge_bbox_intersects_tile(
    edge_xy: Iterable[tuple[float, float]],
    tile_min_x: float = 0.0, tile_min_y: float = 0.0,
    tile_max_x: float = float(TILE_PX), tile_max_y: float = float(TILE_PX),
) -> bool:
    """Coarse AABB rejection — avoids ImageDraw calls for far-away edges."""
    minx = float("inf")
    miny = float("inf")
    maxx = float("-inf")
    maxy = float("-inf")
    for x, y in edge_xy:
        if x < minx:
            minx = x
        if y < miny:
            miny = y
        if x > maxx:
            maxx = x
        if y > maxy:
            maxy = y
    if maxx < tile_min_x or minx > tile_max_x:
        return False
    return not (maxy < tile_min_y or miny > tile_max_y)


def render_tile_png(
    edges: Iterable[HeatEdge], tile_x: int, tile_y: int, zoom: int,
) -> bytes | None:
    """Render a single ``(z, x, y)`` tile as PNG bytes.

    Returns ``None`` if no edge falls inside the tile — empty tiles
    are skipped at write time to keep the MBTiles small.

    Lines are drawn ordered by ``bucket`` (low to high) so popular
    edges paint on top of quiet ones. Same line-sort-key semantics as
    the in-app MapLibre style.
    """
    img = Image.new("RGBA", (TILE_PX, TILE_PX), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    any_drawn = False

    # Bucketed pass: draw quiet edges first, popular last.
    bucketed: list[tuple[int, list[tuple[float, float]]]] = []
    for edge in edges:
        projected = _project_edge_to_tile(edge, tile_x, tile_y, zoom)
        if projected is None:
            continue
        if not _edge_bbox_intersects_tile(projected):
            continue
        b = _bucket_for(edge.user_count)
        if b == 0:
            continue
        bucketed.append((b, projected))

    if not bucketed:
        return None

    bucketed.sort(key=lambda t: t[0])
    for bucket, pts in bucketed:
        if len(pts) < 2:
            continue
        color = _BUCKET_RGBA[bucket]
        width = _BUCKET_WIDTH[bucket]
        # ImageDraw.line accepts a flat list of (x, y) tuples + a
        # joint/curve style. ``joint='curve'`` smooths polyline corners
        # without explicit per-segment math.
        draw.line(pts, fill=color, width=width, joint="curve")
        any_drawn = True

    if not any_drawn:
        return None

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── MBTiles container ────────────────────────────────────────────────────────


_MBTILES_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tiles (
    zoom_level INTEGER,
    tile_column INTEGER,
    tile_row INTEGER,
    tile_data BLOB,
    PRIMARY KEY (zoom_level, tile_column, tile_row)
);
"""


def init_mbtiles(
    path: str,
    *,
    bounds: tuple[float, float, float, float],
    min_zoom: int,
    max_zoom: int,
    name: str,
    description: str,
    attribution: str,
    extra_metadata: dict[str, str] | None = None,
) -> None:
    """Create the SQLite container + write the spec-required metadata.

    Spec ref: https://github.com/mapbox/mbtiles-spec/blob/master/1.3/spec.md

    Required metadata rows for a raster tileset: ``name``, ``format``
    (``png``), ``bounds``, ``minzoom``, ``maxzoom``. Recommended:
    ``description``, ``attribution``, ``type`` (``overlay`` since we
    don't carry a basemap underneath).
    """
    with sqlite3.connect(path) as con:
        con.executescript(_MBTILES_SCHEMA)
        meta: dict[str, str] = {
            "name": name,
            "format": "png",
            "type": "overlay",
            "version": "1.0",
            "description": description,
            "attribution": attribution,
            "bounds": ",".join(f"{v:.6f}" for v in bounds),
            "minzoom": str(min_zoom),
            "maxzoom": str(max_zoom),
            "generated_at": datetime.now(tz=UTC).isoformat(),
        }
        if extra_metadata:
            meta.update(extra_metadata)
        for k, v in meta.items():
            con.execute(
                "INSERT OR REPLACE INTO metadata(name, value) VALUES (?, ?)",
                (k, v),
            )
        con.commit()


def write_tile(con: sqlite3.Connection, zoom: int, x: int, y: int, png: bytes) -> None:
    """Insert one PNG tile into the MBTiles container.

    Y axis is XYZ-input, TMS-stored (the spec mandates TMS).
    """
    row = xyz_to_tms_row(y, zoom)
    con.execute(
        "INSERT OR REPLACE INTO tiles(zoom_level, tile_column, tile_row, tile_data) "
        "VALUES (?, ?, ?, ?)",
        (zoom, x, row, png),
    )


# ── End-to-end build ─────────────────────────────────────────────────────────


@dataclass
class RasterBuildResult:
    """Returned by :func:`build_raster_mbtiles` for telemetry + tests."""
    feature_count: int
    tile_count: int
    elapsed_s: float


def build_raster_mbtiles(
    edges: Iterable[dict],
    bbox: tuple[float, float, float, float],
    output_path: str,
    *,
    min_zoom: int = DEFAULT_ZOOM_MIN,
    max_zoom: int = DEFAULT_ZOOM_MAX,
    name: str = "CHEMINS COMMUNS — community trail heatmap (raster)",
    description: str = "Raster MBTiles export of the ODbL community cycling heatmap.",
    attribution: str = "(c) CHEMINS COMMUNS contributors -- ODbL 1.0",
    extra_metadata: dict[str, str] | None = None,
    budget_s: float | None = None,
) -> RasterBuildResult:
    """Render heat-edge polylines as a raster MBTiles file at ``output_path``.

    ``edges`` is an iterable of dicts shaped like ``ingest.get_heat_edges_public``
    rows (LineString geometry + user_count + sport). Tiles are rendered
    only for the bbox + zoom range; empty tiles are dropped.

    If ``budget_s`` is set and the wall-clock exceeds it mid-render,
    the function raises ``TimeoutError`` — caller maps to 400 with a
    "reduce bbox" hint. Same UX as the vector MBTiles path.
    """
    start = time.monotonic()
    materialised: list[HeatEdge] = [HeatEdge.from_edge_dict(e) for e in edges]
    materialised = [e for e in materialised if len(e.coords) >= 2]

    init_mbtiles(
        output_path,
        bounds=bbox,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        name=name,
        description=description,
        attribution=attribution,
        extra_metadata=extra_metadata,
    )

    tile_count = 0
    min_lon, min_lat, max_lon, max_lat = bbox

    # Open a single connection for the whole render pass — much faster
    # than per-tile reconnects (each connect re-opens the WAL files).
    with sqlite3.connect(output_path) as con:
        for zoom in range(min_zoom, max_zoom + 1):
            x_min, y_min, x_max, y_max = _tile_range_for_bbox(
                min_lon, min_lat, max_lon, max_lat, zoom,
            )
            for x in range(x_min, x_max + 1):
                for y in range(y_min, y_max + 1):
                    if budget_s is not None and time.monotonic() - start > budget_s:
                        log.warning(
                            "raster MBTiles build exceeded %.0fs budget at z%d (%d tiles)",
                            budget_s, zoom, tile_count,
                        )
                        raise TimeoutError(
                            f"raster build exceeded {budget_s:.0f}s budget"
                        )
                    png = render_tile_png(materialised, x, y, zoom)
                    if png is None:
                        continue
                    write_tile(con, zoom, x, y, png)
                    tile_count += 1
        con.commit()

    elapsed = time.monotonic() - start
    log.info(
        "raster MBTiles build: %d tiles, %d edges, %.2fs (z%d..z%d, bbox=%s)",
        tile_count, len(materialised), elapsed, min_zoom, max_zoom, bbox,
    )
    return RasterBuildResult(
        feature_count=len(materialised),
        tile_count=tile_count,
        elapsed_s=elapsed,
    )
