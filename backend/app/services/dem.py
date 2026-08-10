"""DEM elevation provider — Open-Meteo + Open-Elevation fallback.

Fetches real elevation data from the free Open-Meteo API (Copernicus GLO-90, 90m resolution).
Falls back to Open-Elevation API if Open-Meteo is rate-limited (429).
Results are cached in-memory keyed on rounded (lat, lon) pairs (~111m grid).

Configurable via DEM_PROVIDER env var:
  "open-meteo" (default) — fetch from api.open-meteo.com, fallback to open-elevation
  "none"                 — disable, return None for all points
"""

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_OPEN_METEO_URL = "https://api.open-meteo.com/v1/elevation"
_OPEN_ELEVATION_URL = "https://api.open-elevation.com/api/v1/lookup"
_BATCH_SIZE = 100  # Open-Meteo limit per request
_TIMEOUT_S = 10.0
_MAX_CACHE = 10_000
_USER_AGENT = "CheminsCommuns/1.0 (cycling map; github.com/common-trails)"

# Simple dict cache — (round(lat,4), round(lon,4)) → elevation.
# 4 dp ≈ 11 m at lat 45°; Copernicus GLO-90 native is 90 m so 4 dp slightly
# over-resolves the source, but the previous 3 dp (~111 m) made two adjacent
# heat_edges 50 m apart hit the SAME cache entry → slope reads 0 % when the
# real grade is 8 %+. Audit 2026-05-15.
_elevation_store: dict[tuple[float, float], float | None] = {}


def _round_coord(lat: float, lon: float) -> tuple[float, float]:
    """Round to 4 decimals (~11 m). See cache header note."""
    return round(lat, 4), round(lon, 4)


async def _fetch_open_meteo(
    client: httpx.AsyncClient,
    batch: list[tuple[float, float]],
) -> list[float | None]:
    """Fetch from Open-Meteo. Returns elevations or None per point."""
    lats = ",".join(str(lat_r) for lat_r, _ in batch)
    lons = ",".join(str(lon_r) for _, lon_r in batch)

    resp = None
    for attempt in range(3):
        resp = await client.get(
            _OPEN_METEO_URL,
            params={"latitude": lats, "longitude": lons},
        )
        if resp.status_code == 429:
            wait = 2.0 * (2 ** attempt)  # 2s, 4s, 8s
            logger.info("Open-Meteo 429, retrying in %.1fs (attempt %d/3)", wait, attempt + 1)
            await asyncio.sleep(wait)
            continue
        break

    if resp is not None and resp.status_code == 429:
        return [None] * len(batch)

    if resp is not None:
        resp.raise_for_status()
        data = resp.json()
        elevations = data.get("elevation", [])
        results: list[float | None] = []
        for j in range(len(batch)):
            if j < len(elevations) and elevations[j] is not None:
                results.append(float(elevations[j]))
            else:
                results.append(None)
        return results

    return [None] * len(batch)


async def _fetch_open_elevation(
    client: httpx.AsyncClient,
    batch: list[tuple[float, float]],
) -> list[float | None]:
    """Fallback: fetch from Open-Elevation API (POST with locations)."""
    locations = [{"latitude": lat_r, "longitude": lon_r} for lat_r, lon_r in batch]
    try:
        resp = await client.post(
            _OPEN_ELEVATION_URL,
            json={"locations": locations},
        )
        if resp.status_code == 200:
            data = resp.json()
            results_list = data.get("results", [])
            elevations: list[float | None] = []
            for j in range(len(batch)):
                if j < len(results_list) and results_list[j].get("elevation") is not None:
                    elevations.append(float(results_list[j]["elevation"]))
                else:
                    elevations.append(None)
            return elevations
        logger.warning("Open-Elevation returned %d", resp.status_code)
    except Exception:
        logger.warning("Open-Elevation failed", exc_info=True)
    return [None] * len(batch)


async def fetch_elevations(coords: list[list[float]]) -> list[float | None]:
    """Fetch elevations for a list of [lon, lat] coordinates.

    Returns a list of elevations (meters) in the same order.
    Points where the API fails return None.
    """
    if os.environ.get("DEM_PROVIDER", "open-meteo").lower() == "none":
        return [None] * len(coords)

    # Check cache first, collect misses
    results: list[float | None] = [None] * len(coords)
    misses_by_key: dict[tuple[float, float], list[int]] = {}

    for i, pt in enumerate(coords):
        lon, lat = pt[0], pt[1]
        key = _round_coord(lat, lon)
        if key in _elevation_store:
            results[i] = _elevation_store[key]
        else:
            misses_by_key.setdefault(key, []).append(i)

    if not misses_by_key:
        return results

    # Batch fetch
    unique_list = list(misses_by_key.keys())

    for batch_start in range(0, len(unique_list), _BATCH_SIZE):
        batch = unique_list[batch_start : batch_start + _BATCH_SIZE]

        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT_S,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                # Try Open-Meteo first
                elevations = await _fetch_open_meteo(client, batch)

                # If Open-Meteo returned all None (rate-limited), try Open-Elevation
                if all(e is None for e in elevations):
                    logger.info("Open-Meteo unavailable, trying Open-Elevation fallback")
                    elevations = await _fetch_open_elevation(client, batch)

                for j, (lat_r, lon_r) in enumerate(batch):
                    _elevation_store[(lat_r, lon_r)] = elevations[j] if j < len(elevations) else None
        except Exception:
            logger.warning("DEM batch failed (start=%d)", batch_start, exc_info=True)
            for lat_r, lon_r in batch:
                _elevation_store[(lat_r, lon_r)] = None

    # Evict oldest entries if cache exceeds max size
    if len(_elevation_store) > _MAX_CACHE:
        excess = len(_elevation_store) - _MAX_CACHE
        for key in list(_elevation_store)[:excess]:
            del _elevation_store[key]

    # Fill results from store
    for key, indices in misses_by_key.items():
        ele = _elevation_store.get(key)
        for idx in indices:
            results[idx] = ele

    return results


async def compute_slopes_for_edges(edges: list[list]) -> list[float]:
    """Compute slope grades for a list of compact edges.

    Each edge is [lon1, lat1, lon2, lat2, ...]. Returns slope_grade (%)
    for each edge, computed from DEM elevations at the endpoints.

    Returns 0.0 for edges where elevation lookup fails or distance is 0.
    """
    from app.services.geo import haversine_m

    if not edges:
        return []

    # Collect all unique endpoints
    all_coords: list[list[float]] = []
    coord_indices: list[tuple[int, int]] = []  # (edge_idx, 0=start/1=end)

    for i, edge in enumerate(edges):
        all_coords.append([edge[0], edge[1]])  # [lon1, lat1]
        coord_indices.append((i, 0))
        all_coords.append([edge[2], edge[3]])  # [lon2, lat2]
        coord_indices.append((i, 1))

    # Batch-fetch elevations
    elevations = await fetch_elevations(all_coords)

    # Compute slopes
    edge_elevations: list[list[float | None]] = [[None, None] for _ in edges]
    for j, (edge_idx, endpoint) in enumerate(coord_indices):
        edge_elevations[edge_idx][endpoint] = elevations[j]

    slopes: list[float] = []
    for i, edge in enumerate(edges):
        ele1, ele2 = edge_elevations[i]
        if ele1 is None or ele2 is None:
            slopes.append(0.0)
            continue
        dist = haversine_m(edge[0], edge[1], edge[2], edge[3])
        if dist < 1.0:  # avoid division by near-zero
            slopes.append(0.0)
            continue
        slopes.append(round((ele2 - ele1) / dist * 100, 1))

    return slopes


def clear_cache() -> None:
    """Clear the elevation cache (for testing)."""
    _elevation_store.clear()
