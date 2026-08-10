"""Local DEM lookup — pure numpy, no external runtime deps.

Reads SRTM/Copernicus HGT files from disk and looks up elevation by
(lat, lon). Used at PBF-import time to enrich `osm_road_edges` with
`ele_start_m`, `ele_end_m`, `ele_delta_m`, `slope_grade` — eliminating
the runtime dependency on Open-Meteo for elevation.

## HGT format primer

SRTM 3-arcsecond (~90 m) tiles, the same resolution as Copernicus
GLO-90. Each tile covers a 1° × 1° square. Format:

- File naming: ``<NS><lat><EW><lon>.hgt`` — e.g. ``N43E003.hgt`` for
  the tile whose SW corner is at lat 43° N, lon 3° E.
- Content: 1201 × 1201 int16 big-endian elevation values (meters above
  geoid). Row 0 is the *northernmost* row (lat = ceil(lat) of SW + 1),
  column 0 is the westernmost column (lon = SW lon).
- Voids (water, missing data) encoded as -32768.
- Total size: 1201² × 2 bytes = ~2.9 MB per tile uncompressed.

## Tile sources

Pure HGT is published unauthenticated by:
- `https://viewfinderpanoramas.org/dem3.html` — global SRTM3 + Copernicus
  consolidated, void-filled, free, no API key.
- CGIAR-CSI SRTM v4 (`https://srtm.csi.cgiar.org/`) — also free, slightly
  more aggressive void-fill.

For France (lat 41-51, lon -5 to 10), ~150 tiles = ~450 MB. Download
once via `app.cli.download_dem`, store in ``DATA_DIR/dem/``.

## Lookup contract

`elevation_at(lat, lon)` returns `float | None`. None when:
- Tile file not on disk (DEM not downloaded for this region)
- Voids: returns None rather than -32768
- lat/lon outside the known DEM (e.g. ocean)

Per Crouzet methodology: this enriches **derived metrics** (slope, D+)
without touching stored GPX coordinates.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Tile grid: 1201 × 1201 samples spanning 1° × 1°. Pixel pitch ≈ 3 arcsec.
_TILE_SIDE_PX = 1201
_PX_PER_DEG = _TILE_SIDE_PX - 1  # 1200; last px of tile N overlaps first of N+1
_HGT_VOID = -32768

# Process-wide cache of parsed tiles. Each tile is ~2.9 MB; cap at 64
# tiles ≈ 185 MB RAM — plenty for an OSM import that visits France
# tile-by-tile but doesn't randomly jump.
_tile_cache: dict[tuple[int, int], np.ndarray | None] = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 64

# Where HGT files live. Override with DEM_DIR env var.
_DEM_DIR = Path(os.environ.get("DEM_DIR", os.environ.get("DATA_DIR", "/app/data") + "/dem"))


def _tile_filename(lat_floor: int, lon_floor: int) -> str:
    """Return ``N43E003.hgt`` style filename for the tile starting at
    (lat_floor, lon_floor)."""
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    return f"{ns}{abs(lat_floor):02d}{ew}{abs(lon_floor):03d}.hgt"


def _load_tile(lat_floor: int, lon_floor: int) -> np.ndarray | None:
    """Load an HGT tile from disk. Returns None if missing."""
    key = (lat_floor, lon_floor)
    with _cache_lock:
        if key in _tile_cache:
            return _tile_cache[key]

    path = _DEM_DIR / _tile_filename(lat_floor, lon_floor)
    if not path.exists():
        with _cache_lock:
            _tile_cache[key] = None  # negative cache to avoid repeated stat
        return None

    try:
        # numpy reads in one shot — int16 big-endian, exactly 1201²
        data = np.fromfile(path, dtype=">i2")
        if data.size != _TILE_SIDE_PX * _TILE_SIDE_PX:
            logger.warning(
                "DEM tile %s has %d samples (expected %d) — corrupt file?",
                path, data.size, _TILE_SIDE_PX * _TILE_SIDE_PX,
            )
            with _cache_lock:
                _tile_cache[key] = None
            return None
        arr = data.reshape(_TILE_SIDE_PX, _TILE_SIDE_PX)
    except Exception as exc:
        logger.warning("DEM tile %s read failed: %s", path, exc)
        with _cache_lock:
            _tile_cache[key] = None
        return None

    with _cache_lock:
        # Trim cache if full — drop arbitrary entry (LRU not worth the
        # overhead for a per-tile cache that's mostly sequential reads)
        if len(_tile_cache) >= _CACHE_MAX:
            for stale in list(_tile_cache.keys())[: _CACHE_MAX // 4]:
                _tile_cache.pop(stale, None)
        _tile_cache[key] = arr
    return arr


def elevation_at(lat: float, lon: float) -> float | None:
    """Return elevation in meters at (lat, lon), or None if unavailable.

    Bilinear interpolation between the four surrounding pixels. Voids
    (-32768) cause a None return rather than propagating a bogus value.
    """
    lat_floor = int(np.floor(lat))
    lon_floor = int(np.floor(lon))
    tile = _load_tile(lat_floor, lon_floor)
    if tile is None:
        return None

    # Fractional position within the tile. Row 0 = northern edge, so
    # row index = (1 - frac_lat) × 1200.
    frac_lat = lat - lat_floor
    frac_lon = lon - lon_floor
    row_f = (1.0 - frac_lat) * _PX_PER_DEG
    col_f = frac_lon * _PX_PER_DEG

    # Clamp to valid range (handles lat/lon exactly on the edge)
    row_f = max(0.0, min(float(_TILE_SIDE_PX - 1), row_f))
    col_f = max(0.0, min(float(_TILE_SIDE_PX - 1), col_f))

    r0, c0 = int(np.floor(row_f)), int(np.floor(col_f))
    r1 = min(r0 + 1, _TILE_SIDE_PX - 1)
    c1 = min(c0 + 1, _TILE_SIDE_PX - 1)
    dr, dc = row_f - r0, col_f - c0

    # Four corners
    v00 = int(tile[r0, c0])
    v01 = int(tile[r0, c1])
    v10 = int(tile[r1, c0])
    v11 = int(tile[r1, c1])

    # Reject voids — if any corner is a void, fall back to None
    if _HGT_VOID in (v00, v01, v10, v11):
        return None

    # Bilinear
    top = v00 * (1 - dc) + v01 * dc
    bot = v10 * (1 - dc) + v11 * dc
    return float(top * (1 - dr) + bot * dr)


def slope_grade(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float | None, float | None, float | None]:
    """Return (ele_start_m, ele_end_m, slope_grade_pct) for a segment.

    Slope is signed: positive = uphill from point 1 to point 2.
    Returns (None, None, None) if either endpoint is unavailable.
    """
    e1 = elevation_at(lat1, lon1)
    e2 = elevation_at(lat2, lon2)
    if e1 is None or e2 is None:
        return None, None, None

    # Haversine for distance
    R = 6_371_000.0  # earth radius m
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlam = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2) ** 2
    dist_m = 2 * R * np.arcsin(np.sqrt(a))
    if dist_m < 1.0:  # degenerate; slope ill-defined
        return e1, e2, 0.0
    slope_pct = 100.0 * (e2 - e1) / dist_m
    return float(e1), float(e2), float(slope_pct)


def tile_available(lat: float, lon: float) -> bool:
    """Fast check (no read): does the HGT file for this point exist?"""
    return (_DEM_DIR / _tile_filename(int(np.floor(lat)), int(np.floor(lon)))).exists()


def clear_cache() -> None:
    """Test helper: drop the in-memory tile cache."""
    with _cache_lock:
        _tile_cache.clear()
