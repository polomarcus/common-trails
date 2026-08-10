#!/usr/bin/env python3
"""Enrich DFCI/trail cache JSON files with DEM-derived slope_grade.

Downloads SRTM tiles (30m or 90m resolution) from Mapzen's public S3 bucket,
caches them locally, and computes slope_grade for each edge based on
elevation at start/end points.

This is a LOCAL-ONLY script — run on your dev machine, not in production.
The enriched cache files are then shipped (volume-mounted or baked into the
Docker image) so the import scripts never need external DEM API calls.

Usage:
    python scripts/enrich_cache_dem.py

    # Optionally specify a custom SRTM cache directory:
    SRTM_CACHE_DIR=/path/to/srtm python scripts/enrich_cache_dem.py

Requirements: numpy (usually pre-installed), no other external dependencies.
"""

import gzip
import io
import json
import math
import os
import struct
import sys
import time
import urllib.request

# ── Config ────────────────────────────────────────────────────────────────────

# Mapzen public SRTM tiles (no auth needed, gzipped .hgt)
# SRTM3 (~90m resolution, 1201x1201 grid, ~1.4MB per tile gzipped)
SRTM_BASE_URL = "https://elevation-tiles-prod.s3.amazonaws.com/skadi"
SRTM_CACHE_DIR = os.environ.get("SRTM_CACHE_DIR", os.path.expanduser("~/.cache/srtm"))
SRTM_SAMPLES = 1201  # SRTM3; use 3601 for SRTM1

# Cache file locations
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "data")
DFCI_CACHE = os.path.join(DATA_DIR, "dfci_ign_edges.json")
TRAIL_CACHE = os.path.join(DATA_DIR, "trail_edges.json")


# ── SRTM tile reader ─────────────────────────────────────────────────────────

_tile_cache: dict[str, bytes] = {}


def _tile_key(lat: float, lon: float) -> str:
    """SRTM tile filename for a given coordinate."""
    lat_int = int(math.floor(lat))
    lon_int = int(math.floor(lon))
    ns = "N" if lat_int >= 0 else "S"
    ew = "E" if lon_int >= 0 else "W"
    return f"{ns}{abs(lat_int):02d}{ew}{abs(lon_int):03d}"


def _download_tile(key: str) -> bytes | None:
    """Download and cache a single SRTM .hgt tile."""
    cache_path = os.path.join(SRTM_CACHE_DIR, f"{key}.hgt")
    if os.path.exists(cache_path):
        if key not in _tile_cache:
            with open(cache_path, "rb") as f:
                _tile_cache[key] = f.read()
        return _tile_cache[key]

    # Download from Mapzen S3
    folder = key[:3]  # e.g. "N43"
    url = f"{SRTM_BASE_URL}/{folder}/{key}.hgt.gz"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "common-trails/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            gz_data = resp.read()
        hgt_data = gzip.decompress(gz_data)
    except Exception as e:
        print(f"  [warn] Failed to download {url}: {e}", file=sys.stderr)
        return None

    os.makedirs(SRTM_CACHE_DIR, exist_ok=True)
    with open(cache_path, "wb") as f:
        f.write(hgt_data)
    _tile_cache[key] = hgt_data
    return hgt_data


def get_elevation(lat: float, lon: float) -> float | None:
    """Get elevation in meters for a coordinate from SRTM data."""
    key = _tile_key(lat, lon)
    data = _download_tile(key)
    if data is None:
        return None

    # Expected size for SRTM3: 1201*1201*2 = 2,884,802 bytes
    n = SRTM_SAMPLES
    expected = n * n * 2
    if len(data) == 3601 * 3601 * 2:
        n = 3601  # SRTM1 tile — higher resolution, use it
    elif len(data) != expected:
        return None

    # Fractional position within the tile
    lat_frac = lat - math.floor(lat)
    lon_frac = lon - math.floor(lon)

    # Row/col (row 0 = north edge of tile)
    row = int((1.0 - lat_frac) * (n - 1))
    col = int(lon_frac * (n - 1))

    row = max(0, min(n - 1, row))
    col = max(0, min(n - 1, col))

    offset = (row * n + col) * 2
    if offset + 2 > len(data):
        return None

    ele = struct.unpack(">h", data[offset : offset + 2])[0]  # big-endian int16
    if ele == -32768:  # void / no data
        return None
    return float(ele)


# ── Slope computation ─────────────────────────────────────────────────────────

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in meters between two points."""
    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def compute_slope(coords: list[list[float]]) -> float:
    """Compute slope_grade (%) for an edge using start/end points.

    Returns absolute slope (always >= 0). 0.0 if DEM data unavailable.
    """
    if len(coords) < 2:
        return 0.0

    start = coords[0]   # [lon, lat, ...]
    end = coords[-1]

    lon1, lat1 = start[0], start[1]
    lon2, lat2 = end[0], end[1]

    ele1 = get_elevation(lat1, lon1)
    ele2 = get_elevation(lat2, lon2)
    if ele1 is None or ele2 is None:
        return 0.0

    dist = haversine_m(lat1, lon1, lat2, lon2)
    if dist < 1.0:  # too short to compute meaningful slope
        return 0.0

    return abs(ele2 - ele1) / dist * 100.0


# ── Main ──────────────────────────────────────────────────────────────────────

def enrich_file(path: str, label: str) -> None:
    """Read a cache JSON, add slope_grade to each edge, write back."""
    if not os.path.exists(path):
        print(f"  [skip] {label}: file not found at {path}")
        return

    with open(path, encoding="utf-8") as f:
        edges = json.load(f)

    print(f"  {label}: {len(edges)} edges")

    # Pre-download all needed tiles
    tiles_needed: set[str] = set()
    for edge in edges:
        coords = edge.get("geometry", {}).get("coordinates", [])
        if len(coords) >= 2:
            tiles_needed.add(_tile_key(coords[0][1], coords[0][0]))
            tiles_needed.add(_tile_key(coords[-1][1], coords[-1][0]))

    print(f"  {label}: downloading {len(tiles_needed)} SRTM tiles...")
    for i, key in enumerate(sorted(tiles_needed)):
        _download_tile(key)
        if (i + 1) % 10 == 0:
            print(f"    {i + 1}/{len(tiles_needed)} tiles downloaded")

    # Enrich edges
    t0 = time.monotonic()
    enriched = 0
    for edge in edges:
        coords = edge.get("geometry", {}).get("coordinates", [])
        slope = compute_slope(coords)
        edge["slope_grade"] = round(slope, 2)
        if slope > 0:
            enriched += 1

    elapsed = time.monotonic() - t0
    print(f"  {label}: {enriched}/{len(edges)} edges enriched with slopes ({elapsed:.1f}s)")

    # Write back
    with open(path, "w", encoding="utf-8") as f:
        json.dump(edges, f, separators=(",", ":"))

    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"  {label}: written {size_mb:.1f}MB to {path}")


def main() -> None:
    print("=== Enriching DFCI/trail cache files with SRTM DEM slopes ===")
    print(f"SRTM cache: {SRTM_CACHE_DIR}")
    print()

    enrich_file(DFCI_CACHE, "DFCI IGN")
    print()
    enrich_file(TRAIL_CACHE, "Trails")

    print()
    print("Done. Cache files now contain slope_grade for each edge.")
    print("Next steps:")
    print("  1. Rebuild backend: docker compose build backend")
    print("  2. Reset DB volume: docker compose down -v && docker compose up -d")
    print("     (or just restart — import will re-read the enriched cache)")


if __name__ == "__main__":
    main()
