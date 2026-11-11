"""Community heatmap API — the few live endpoints left in the raw-trace world.

The community map itself is a static PMTiles artefact (``app.jobs.build_pmtiles``),
not an API. What remains here:

* ``GET /heatmap/summary`` — contributors / traces / km (same SSOT as the home
  banner's ``stats.json``);
* ``GET|POST /heatmap/dfci`` — public OSM DFCI tracks (POST = TEST_MODE seed);
* ``GET /me/cells`` — the caller's own visited cells (private).

The matched-era endpoints (``/heatmap/stats``, ``/heatmap/export``,
``/heatmap/trails``, the live ``/heatmap/tiles`` MVT, ``/heatmap/display-url``,
``/me/unexplored``) were removed: zero prod traffic and no caller left.
"""
import gzip
import json
import logging
import math
import os
import time
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from app.api.auth import AuthenticatedUser, get_current_user
from app.services import ingest as ingest_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["heatmap"])

# Summary cache: (result_dict, monotonic timestamp). Short TTL rather than a
# version key: nothing bumps a version on upload / rebuild in the raw world, so
# a version-keyed cache would freeze the numbers for the life of the process.
_SUMMARY_CACHE_TTL_S = float(os.environ.get("HEATMAP_SUMMARY_CACHE_TTL_S", "60"))
_summary_cache: tuple[dict, float] | None = None
# Indirection so tests can drive the clock without sleeping.
_monotonic = time.monotonic

# DFCI pre-compressed cache: (gzip_bytes, raw_json_bytes, wall-clock timestamp).
# DFCI data never changes via user import — only via the DFCI import CLI.
_dfci_gz_cache: tuple[bytes, bytes, float] | None = None
_DFCI_CACHE_TTL = 3600  # 1 hour

_SUMMARY_HEADERS = {
    "Cache-Control": "public, max-age=86400, stale-while-revalidate=86400",
}


def _tile_to_polygon(cell_key: str) -> dict | None:
    """Convert a 'z/x/y' tile key to a GeoJSON Polygon geometry (Web Mercator)."""
    try:
        z, x, y = (int(v) for v in cell_key.split("/"))
    except (ValueError, AttributeError):
        return None
    n = 2 ** z
    lon_w = x / n * 360.0 - 180.0
    lon_e = (x + 1) / n * 360.0 - 180.0
    lat_n = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_s = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon_w, lat_s],
                [lon_e, lat_s],
                [lon_e, lat_n],
                [lon_w, lat_n],
                [lon_w, lat_s],
            ]
        ],
    }


ODBL_LICENSE = "ODbL-1.0"
ODBL_URL = "https://opendatacommons.org/licenses/odbl/1-0/"


def _community_summary() -> dict:
    """Community summary from the cheap SSOT stats.

    Reuses ``build_pmtiles.compute_community_stats`` (the SAME numbers the home
    banner's ``stats.json`` carries) so the summary can never drift from the
    hero. Every underlying query is defensively wrapped there, so this is
    fail-soft. Mapped to the legacy ``total_*`` shape the frontend
    ``parseCommunityStats`` accepts (contributors / activities / km).
    """
    from app.db.session import SessionLocal
    from app.jobs.build_pmtiles import compute_community_stats

    k = int(os.environ.get("HEATMAP_MIN_USERS", "1") or "1")
    db = SessionLocal()
    try:
        stats = compute_community_stats(db, k)
    finally:
        db.close()
    return {
        "total_edges": 0,
        "total_contributors": int(stats.get("contributors", 0)),
        "total_activities": int(stats.get("traces", 0)),
        "total_km": int(stats.get("km", 0)),
        "sports": {},
        "source": "raw",
        "license": ODBL_LICENSE,
    }


@router.get("/heatmap/summary")
def heatmap_summary() -> JSONResponse:
    """Return the community totals (contributors / activities / km).

    Sync ``def`` — the sync SQLAlchemy aggregation runs in the threadpool, not
    on the event loop (2026-07-20 drain incident: under a pegged DB, sync DB
    work in ``async def`` handlers starved every request on the instance).

    Cached in-process for ``_SUMMARY_CACHE_TTL_S`` (60 s): cheap enough to
    recompute, and a new contribution shows up without a restart.
    """
    global _summary_cache
    now = _monotonic()
    if _summary_cache and now - _summary_cache[1] < _SUMMARY_CACHE_TTL_S:
        return JSONResponse(content=_summary_cache[0], headers=_SUMMARY_HEADERS)
    summary = _community_summary()
    _summary_cache = (summary, now)
    return JSONResponse(content=summary, headers=_SUMMARY_HEADERS)


@router.get("/heatmap/dfci")
async def heatmap_dfci(request: Request) -> Response:
    """Return DFCI fire-prevention tracks as GeoJSON (south France).

    These are official OSM-sourced forest tracks with ref:FR:DFCI tags.
    No K-anonymity needed — this is public OSM data.
    Pre-compressed and cached for fast delivery.
    """
    global _dfci_gz_cache
    accept = request.headers.get("accept-encoding", "")

    if _dfci_gz_cache and (time.time() - _dfci_gz_cache[2]) < _DFCI_CACHE_TTL:
        if "gzip" in accept:
            return Response(
                content=_dfci_gz_cache[0],
                media_type="application/json",
                headers={
                    "Content-Encoding": "gzip",
                    "X-License": ODBL_LICENSE,
                    "Cache-Control": "public, max-age=86400, stale-while-revalidate=86400",
                },
            )
        return Response(
            content=_dfci_gz_cache[1],
            media_type="application/json",
            headers={
                "X-License": ODBL_LICENSE,
                "Cache-Control": "public, max-age=86400, stale-while-revalidate=86400",
            },
        )

    geojson = ingest_service.get_dfci_geojson(limit=100_000)
    geojson["metadata"] = {
        "source": "OpenStreetMap — ref:FR:DFCI",
        "license": "ODbL-1.0",
        "feature_count": len(geojson["features"]),
    }
    raw = json.dumps(geojson, separators=(",", ":")).encode()
    gz = gzip.compress(raw, compresslevel=6)
    _dfci_gz_cache = (gz, raw, time.time())

    if "gzip" in accept:
        return Response(
            content=gz,
            media_type="application/json",
            headers={
                "Content-Encoding": "gzip",
                "X-License": ODBL_LICENSE,
                "Cache-Control": "public, max-age=86400, stale-while-revalidate=86400",
            },
        )
    return Response(
        content=raw,
        media_type="application/json",
        headers={
            "X-License": ODBL_LICENSE,
            "Cache-Control": "public, max-age=86400, stale-while-revalidate=86400",
        },
    )


@router.post("/heatmap/dfci")
async def seed_dfci(edges: list = Body(...)):
    """Seed DFCI edges for testing. Only available in TEST_MODE."""
    from app.config import TEST_MODE

    if not TEST_MODE:
        raise HTTPException(status_code=403, detail="Only available in TEST_MODE")
    count = ingest_service.append_dfci_edges(edges)
    return {"seeded": count}


@router.get("/me/cells")
async def my_cells(
    sport: str | None = Query(None, description="road|gravel|mtb|offroad"),
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)] = None,
) -> JSONResponse:
    """Return the authenticated user's visited cells as GeoJSON polygons (private data)."""
    user_id = current_user.user_id
    cell_keys = ingest_service.get_user_cell_keys(user_id, sport=sport)

    features = []
    for ck in cell_keys:
        geom = _tile_to_polygon(ck)
        if geom:
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {"cell_key": ck},
            })

    return JSONResponse(content={
        "type": "FeatureCollection",
        "features": features,
    })

