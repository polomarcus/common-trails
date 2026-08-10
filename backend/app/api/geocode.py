"""Reverse geocode proxy — avoids Nominatim CORS/rate-limit issues.

Proxies requests to Nominatim with proper rate-limiting and caching.
Frontend calls our backend instead of Nominatim directly.
"""

import asyncio
import logging
import os
from functools import lru_cache

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/geocode", tags=["geocode"])

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_NOMINATIM_TIMEOUT = 5.0
# Nominatim usage policy: max 1 req/sec. The semaphore bounds CONCURRENT
# upstream calls (env-overridable) so a flood can't tie up the event loop or
# get our egress IP banned by OSM/Nominatim. Default 1 (policy-faithful).
_NOMINATIM_CONCURRENCY = int(os.environ.get("GEOCODE_CONCURRENCY", "1"))
_nominatim_semaphore = asyncio.Semaphore(_NOMINATIM_CONCURRENCY)
# Per-IP request rate limit (this is an anon Nominatim proxy). Env-overridable;
# callable so the override applies at runtime (slowapi evaluates per request).
def _geocode_rate_limit() -> str:
    return os.environ.get("GEOCODE_RATE_LIMIT", "30/minute")


@lru_cache(maxsize=500)
def _cached_label(lat_r: float, lon_r: float) -> str | None:
    """Cache key uses rounded coords (4 decimals ≈ 11m)."""
    return None  # sentinel — actual caching happens in the endpoint


# In-memory cache: (lat_r, lon_r) → label string
_label_cache: dict[tuple[float, float], str] = {}


@router.get("/reverse")
@limiter.limit(_geocode_rate_limit)
async def reverse_geocode(
    request: Request,
    lat: float = Query(...),
    lon: float = Query(...),
):
    """Return a short place label for coordinates (proxied from Nominatim)."""
    # Round to 4 decimals for cache stability (~11m)
    lat_r = round(lat, 4)
    lon_r = round(lon, 4)

    cached = _label_cache.get((lat_r, lon_r))
    if cached is not None:
        return JSONResponse(
            content={"label": cached},
            headers={"Cache-Control": "public, max-age=3600"},
        )

    # Fast-fail when the upstream semaphore is already saturated: return 429
    # WITHOUT awaiting a slot, so a flood of misses can't queue up on the event
    # loop (and can't stampede Nominatim). Cache hits above are unaffected.
    if _nominatim_semaphore.locked():
        return JSONResponse(
            status_code=429,
            content={"label": "", "detail": "geocoder busy, retry shortly"},
            headers={"Retry-After": "1", "Cache-Control": "no-store"},
        )

    try:
        async with _nominatim_semaphore, httpx.AsyncClient(timeout=_NOMINATIM_TIMEOUT) as client:
                resp = await client.get(
                    _NOMINATIM_URL,
                    params={
                        "lat": lat_r, "lon": lon_r,
                        "format": "json", "zoom": 16,
                        "accept-language": "fr",
                    },
                    headers={"User-Agent": "CheminsCommuns/1.0 (contact@cheminscommuns.org)"},
                )
                resp.raise_for_status()
                data = resp.json()

        addr = data.get("address", {})
        label = (
            addr.get("road")
            or addr.get("pedestrian")
            or addr.get("cycleway")
            or addr.get("path")
            or addr.get("hamlet")
            or addr.get("village")
            or addr.get("locality")
            or addr.get("town")
            or addr.get("city")
            or ""
        )

        # Cache result
        if len(_label_cache) < 2000:
            _label_cache[(lat_r, lon_r)] = label

        return JSONResponse(
            content={"label": label},
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except Exception:
        logger.debug("Nominatim reverse geocode failed", exc_info=True)
        return JSONResponse(
            content={"label": ""},
            headers={"Cache-Control": "public, max-age=60"},
        )
