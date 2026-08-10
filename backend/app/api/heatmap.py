"""Community heatmap API — ODbL licensed data with K-anonymity.

All endpoints return only data where user_count >= K (default K=5).
Private data (activities, activity_cells) is NEVER exposed.
"""
import gzip
import json
import logging
import math
import os
import time
from collections import OrderedDict
from functools import lru_cache

logger = logging.getLogger(__name__)
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from app.api.auth import AuthenticatedUser, get_current_user
from app.config import expand_sport
from app.services import ingest as ingest_service
from app.services.geo import haversine_m

router = APIRouter(tags=["heatmap"])

# ── Version-based caching ─────────────────────────────────────────────────────
# All caches store the edge_version at time of computation. When _edge_version
# increments (on import), caches are stale and recomputed on next request.
# This means Cloud Run serves pre-computed gzip blobs with zero DB queries
# until the next import — effectively acting as its own CDN.

from app.services.ingest import get_edge_version as _get_edge_version_raw

# Memoize edge version with 5-minute TTL — heatmap imports are rare (days/weeks),
# so checking every request is wasteful. 5 min means at most 5 min stale tiles
# after an import, which is acceptable.
_EDGE_VERSION_TTL = 300  # seconds
_cached_edge_version: tuple[int, float] | None = None


def _get_edge_version() -> int:
    global _cached_edge_version
    now = time.monotonic()
    if _cached_edge_version and now - _cached_edge_version[1] < _EDGE_VERSION_TTL:
        return _cached_edge_version[0]
    v = _get_edge_version_raw()
    _cached_edge_version = (v, now)
    return v

# Pre-compressed response cache: sport:days → (gzip_bytes, version)
_trails_gz_cache: dict[str, tuple[bytes, float]] = {}
_TRAILS_CACHE_MAX = 30  # max cached sport:days combinations

# Summary cache: (result_dict, version)
_summary_cache: tuple[dict, float] | None = None

# DFCI pre-compressed cache: (gzip_bytes, raw_json_bytes, version)
# DFCI data never changes via user import — only via DFCI import CLI.
# Use a separate version counter (timestamp-based, 1h TTL).
_dfci_gz_cache: tuple[bytes, bytes, float] | None = None
_DFCI_CACHE_TTL = 3600  # 1 hour

# Bbox-filtered trails LRU cache: cache_key → (gzip_bytes, version)
_bbox_trails_cache: dict[str, tuple[bytes, float]] = {}
_BBOX_CACHE_MAX = 20  # max cached bbox results

# ── /heatmap/stats + /heatmap/export cost guards (MEDIUM 4) ────────────────────
# Both are PUBLIC (ODbL — Paul's decision) and attacker-parameterised: a client
# can vary bbox/sport/zoom to bust any cache and force a fresh DB aggregation
# each call. Two guards, no auth gate:
#   1. a bounded statement_timeout on the aggregation (a pathological request
#      aborts instead of pinning a worker), and
#   2. a short version-keyed response cache (keyed on the query params + the
#      edge_version, mirroring /heatmap/trails) so repeated / parameter-varied
#      calls are served from memory with ZERO DB work until the next import.
_STATS_STATEMENT_TIMEOUT_MS = int(
    os.environ.get("HEATMAP_STATS_STATEMENT_TIMEOUT_MS", "10000")
)  # 10 s — plenty for a real bbox, caps a pathological whole-world scan.
_stats_cache: dict[str, tuple[dict, float]] = {}
_STATS_CACHE_MAX = int(os.environ.get("HEATMAP_STATS_CACHE_MAX", "64"))
_export_cache: dict[str, tuple[dict, float]] = {}
_EXPORT_CACHE_MAX = int(os.environ.get("HEATMAP_EXPORT_CACHE_MAX", "32"))


def _bounded_cache_put(cache: dict, key: str, value, version: float, cap: int) -> None:
    """Insert into a version-keyed dict cache, evicting the oldest-version entry
    when the cap is reached. Shared by the stats + export response caches."""
    if len(cache) >= cap and key not in cache:
        oldest = min(cache, key=lambda k: cache[k][1])
        del cache[oldest]
    cache[key] = (value, version)


HEATMAP_K_ANONYMITY = int(os.environ.get("HEATMAP_K_ANONYMITY", "2"))

# ── Concurrency limit for tile generation ─────────────────────────────────────
# Each tile generation can use 100-500MB peak memory (PostGIS query + Python
# feature buffer + gzip). Without a limit, N parallel requests use N × peak.
# Set TILE_GEN_CONCURRENCY=1-2 in production to cap memory usage.
import threading

_TILE_GEN_CONCURRENCY = int(os.environ.get("TILE_GEN_CONCURRENCY", "4"))
_tile_gen_semaphore = threading.Semaphore(_TILE_GEN_CONCURRENCY)

# ── Background tile warm-up ───────────────────────────────────────────────────
_warmup_started = False


def _start_tile_warmup() -> None:
    """Pre-generate z8-12 tiles in background thread on first tile request.

    Only runs once. Generates ~100 tiles for offroad/road sports covering
    the data bbox. After this, all low-zoom tiles are in memory cache.
    """
    global _warmup_started
    if _warmup_started:
        return
    _warmup_started = True

    import math
    import threading

    def _warmup():
        try:

            from app.db.session import SessionLocal
            db = SessionLocal()
            try:
                # Use metropolitan France bbox for warmup (avoids outlier demo data)
                min_lon, min_lat, max_lon, max_lat = -5.5, 41.3, 9.6, 51.1
                logger.info("[warmup] Warming France bbox: %.1f,%.1f → %.1f,%.1f", min_lon, min_lat, max_lon, max_lat)
            finally:
                db.close()

            version = _get_edge_version()
            warmed = 0
            t0 = time.monotonic()
            for z in range(8, 13):
                n = 2 ** z
                x_min = int((min_lon + 180) / 360 * n)
                x_max = int((max_lon + 180) / 360 * n)
                y_min = int((1 - math.log(math.tan(math.radians(min(85, max_lat))) + 1 / math.cos(math.radians(min(85, max_lat)))) / math.pi) / 2 * n)
                y_max = int((1 - math.log(math.tan(math.radians(max(-85, min_lat))) + 1 / math.cos(math.radians(max(-85, min_lat)))) / math.pi) / 2 * n)
                tiles = (x_max - x_min + 1) * (y_max - y_min + 1)
                if tiles > 200:
                    continue
                for sport in ['offroad', 'road']:
                    for x in range(x_min, x_max + 1):
                        for y in range(y_min, y_max + 1):
                            key = f"{sport}/{z}/{x}/{y}/all"
                            if key not in _mvt_cache:
                                try:
                                    gz = _generate_tile(sport, z, x, y)
                                    _mvt_cache[key] = (gz, version)
                                    warmed += 1
                                except Exception:
                                    pass
            logger.info("[warmup] Pre-warmed %d tiles in %.1fs", warmed, time.monotonic() - t0)
        except Exception as e:
            logger.warning("[warmup] Failed: %s", e)

    t = threading.Thread(target=_warmup, daemon=True)
    t.start()


def _reset_tile_caches() -> None:
    """Clear stale tile / response caches after a heat_edges change.

    The ``heat_edges_display`` matview was dropped (June 2026); tile
    generation now reads ``heat_edges`` directly via the shared aggregation
    builder, so there is no matview-existence state to reset — only the
    in-memory caches, which key on ``_get_edge_version`` and self-invalidate,
    but we clear them eagerly so a manual rebuild is visible immediately.
    """
    global _cached_edge_version
    _cached_edge_version = None
    _mvt_cache.clear()
    _trails_gz_cache.clear()
    _bbox_trails_cache.clear()
    _stats_cache.clear()
    _export_cache.clear()


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


def _raw_mode_summary() -> dict:
    """Community summary for RAW mode from the cheap SSOT stats.

    Reuses ``build_pmtiles.compute_community_stats`` (the SAME numbers the home
    banner's ``stats.json`` carries) so the summary can never drift from the
    hero, and never touches ``heat_edges`` / ``heat_edge_contributors`` (the
    latter dropped under the raw pivot). Every underlying query is defensively
    wrapped there, so this is fail-soft. Mapped to the legacy ``total_*`` shape
    the frontend ``parseCommunityStats`` accepts (contributors / activities /
    km).
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
    }


@router.get("/heatmap/summary")
def heatmap_summary() -> JSONResponse:
    """Return aggregate heatmap stats: edges/contributors/passes per sport.

    Sync ``def`` — the sync SQLAlchemy aggregation runs in the threadpool, not
    on the event loop (2026-07-20 drain incident: under a pegged DB, sync DB
    work in ``async def`` handlers starved every request on the instance).
    """
    global _summary_cache
    version = _get_edge_version()
    if _summary_cache and _summary_cache[1] == version:
        return JSONResponse(
            content=_summary_cache[0],
            headers={"Cache-Control": "public, max-age=86400, stale-while-revalidate=86400"},
        )
    from app.services.raw_trace_display import raw_display_enabled
    # RAW-trace cutover: ``get_heatmap_summary`` full-scans ``heat_edges`` +
    # ``heat_edge_contributors`` (the latter DROPPED under the raw pivot → 500)
    # with per-row geography casts — heavy and stale in raw mode. Serve the SAME
    # cheap, build-time SSOT the home banner uses (``compute_community_stats`` —
    # indexed ``activities`` aggregates + the small ``heat_edges_agg``, never the
    # dropped tables), mapped to the legacy summary shape the frontend parser
    # accepts (total_*). Matched mode is unchanged.
    summary = (
        _raw_mode_summary() if raw_display_enabled()
        else ingest_service.get_heatmap_summary()
    )
    summary["license"] = ODBL_LICENSE
    _summary_cache = (summary, version)
    return JSONResponse(
        content=summary,
        headers={"Cache-Control": "public, max-age=86400, stale-while-revalidate=86400"},
    )


@router.get("/heatmap/display-url")
def heatmap_display_url(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> JSONResponse:
    """Members-only asset gate: a short-lived SIGNED GET URL for the community
    heatmap PMTiles binary.

    Part of the 2026-07 members-only cutover. The interactive map page is gated
    client-side, but that alone does NOT protect the DATA while the PMTiles is a
    public GCS object. When the display bucket is made PRIVATE (an ops step —
    see docs/raw-trace-cutover-runbook.md), the frontend switches to fetching
    the object through this authenticated endpoint (flag
    ``NEXT_PUBLIC_HEATMAP_GATED``): only a logged-in user gets a signed URL,
    valid for a bounded TTL, that the PMTiles client loads via Range requests.

    ``get_current_user`` enforces the gate (401 for anonymous callers). Returns
    503 in environments with no display bucket configured (local dev serves the
    PMTiles statically from the frontend, so no signing is needed there).
    """
    from datetime import timedelta

    from app.services import archive_intake

    bucket_name = os.environ.get("HEATMAP_GCS_BUCKET", "").strip()
    if not bucket_name:
        raise HTTPException(
            status_code=503,
            detail="heatmap display bucket not configured (asset gate is a prod feature)",
        )
    ttl_min = int(os.environ.get("HEATMAP_DISPLAY_URL_TTL_MIN", "360"))
    object_key = os.environ.get(
        "HEATMAP_DISPLAY_OBJECT", "heatmap-display.pmtiles"
    ).strip()
    signed = archive_intake.generate_signed_get_url(
        bucket_name, object_key, timedelta(minutes=ttl_min)
    )
    # Never cache a per-user signed URL in shared caches.
    return JSONResponse(
        content={"url": signed, "expires_in": ttl_min * 60},
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/heatmap/stats")
def heatmap_stats(
    sport: str | None = Query(None, description="road|gravel|mtb|offroad"),
    zoom: int = Query(14, ge=1, le=18),
    min_lon: float | None = Query(None),
    min_lat: float | None = Query(None),
    max_lon: float | None = Query(None),
    max_lat: float | None = Query(None),
) -> dict:
    """Return aggregated heatmap statistics (ODbL, K-anonymity enforced).

    Only cells with user_count >= K are returned.
    """
    bbox = None
    if all(v is not None for v in [min_lon, min_lat, max_lon, max_lat]):
        bbox = (min_lon, min_lat, max_lon, max_lat)

    _headers = {"Cache-Control": "public, max-age=86400, stale-while-revalidate=86400"}

    # Version-keyed response cache (cost guard): serve repeated / cache-busting
    # anon calls from memory with zero DB work until the next import bumps the
    # edge_version.
    version = _get_edge_version()
    cache_key = f"{sport or 'all'}:{zoom}:{bbox}"
    cached = _stats_cache.get(cache_key)
    if cached and cached[1] == version:
        return JSONResponse(content=cached[0], headers=_headers)

    sports_to_query: list[str | None] = expand_sport(sport) if sport else [None]

    all_cells: list[dict] = []
    for s in sports_to_query:
        # Aggregate heat_edges -> z14 tiles at query time. The legacy
        # heat_cells write path was removed (May 2026 audit, improvement
        # #4) — the on-read aggregation produces the same cell_key /
        # user_count contract. See ingest.get_heat_cells_aggregated.
        # Bounded statement_timeout so a pathological bbox can't pin a worker.
        cells = ingest_service.get_heat_cells_aggregated(
            sport=s,
            zoom=zoom,
            bbox=bbox,
            k=HEATMAP_K_ANONYMITY,
            statement_timeout_ms=_STATS_STATEMENT_TIMEOUT_MS,
        )
        all_cells.extend(cells)

    content = {
        "license": ODBL_LICENSE,
        "license_url": ODBL_URL,
        "k_anonymity": HEATMAP_K_ANONYMITY,
        "cell_count": len(all_cells),
        "cells": all_cells,
    }
    _bounded_cache_put(_stats_cache, cache_key, content, version, _STATS_CACHE_MAX)
    return JSONResponse(content=content, headers=_headers)


# Feature cap for the whole-world GeoJSON export. The endpoint defaults to a
# WHOLE-WORLD bbox with NO LIMIT, so a cold full-bbox request can build a body
# far larger than Cloud Run's 32 MiB non-streaming response cap → a silent 500.
# Each cell Feature (cell_key + 4 props + a 5-point polygon) is ~300-400 bytes
# of JSON, so ~60 k features (~24 MB raw) stays safely under the cap; the
# gzip path (mirroring /heatmap/trails + /heatmap/dfci) then keeps the wire
# body tiny. Configurable via HEATMAP_EXPORT_MAX_FEATURES.
_EXPORT_MAX_FEATURES = int(os.environ.get("HEATMAP_EXPORT_MAX_FEATURES", "60000"))


@router.get("/heatmap/export")
def heatmap_export(
    request: Request,
    sport: str | None = Query(None),
    zoom: int = Query(14, ge=1, le=18),
    min_lon: float = Query(-180.0),
    min_lat: float = Query(-90.0),
    max_lon: float = Query(180.0),
    max_lat: float = Query(90.0),
) -> Response:
    """Export heatmap as GeoJSON (ODbL). K-anonymity enforced.

    Capped at ``_EXPORT_MAX_FEATURES`` features and gzipped when the client
    sends ``accept-encoding: gzip`` (same pattern as ``/heatmap/trails`` and
    ``/heatmap/dfci``) so an unbounded whole-world request can never build a
    body past Cloud Run's 32 MiB non-streaming cap. When the cap truncates the
    result, ``metadata.truncated`` is ``true`` and ``metadata.hint`` asks the
    caller to narrow the bbox.
    """
    bbox = (min_lon, min_lat, max_lon, max_lat)

    # Version-keyed response cache (cost guard): the aggregation + GeoJSON build
    # is served from memory for repeated / cache-busting anon calls until the
    # next import. Keyed on the query params + the feature cap (baked into the
    # truncated body) + edge_version.
    version = _get_edge_version()
    cache_key = f"{sport or 'all'}:{zoom}:{bbox}:{_EXPORT_MAX_FEATURES}"
    cached = _export_cache.get(cache_key)
    if cached and cached[1] == version:
        geojson = cached[0]
    else:
        sports_to_query: list[str | None] = expand_sport(sport) if sport else [None]

        all_cells: list[dict] = []
        for s in sports_to_query:
            # See `heatmap_stats` above — same aggregate-on-read path, same
            # bounded statement_timeout so a whole-world scan can't pin a worker.
            cells = ingest_service.get_heat_cells_aggregated(
                sport=s, zoom=zoom, bbox=bbox, k=HEATMAP_K_ANONYMITY,
                statement_timeout_ms=_STATS_STATEMENT_TIMEOUT_MS,
            )
            all_cells.extend(cells)

        truncated = len(all_cells) > _EXPORT_MAX_FEATURES
        if truncated:
            all_cells = all_cells[:_EXPORT_MAX_FEATURES]

        features = []
        for cell in all_cells:
            features.append({
                "type": "Feature",
                "properties": {
                    "cell_key": cell["cell_key"],
                    "sport": cell["sport"],
                    "user_count": cell["user_count"],
                    "pass_count": cell["pass_count"],
                    "license": ODBL_LICENSE,
                },
                "geometry": _tile_to_polygon(cell["cell_key"]),
            })

        metadata = {
            "license": ODBL_LICENSE,
            "license_url": ODBL_URL,
            "k_anonymity": HEATMAP_K_ANONYMITY,
            "source": "CHEMINS COMMUNS — community heatmap",
            "feature_count": len(features),
            "truncated": truncated,
        }
        if truncated:
            metadata["hint"] = (
                f"Result capped at {_EXPORT_MAX_FEATURES} features to stay under the "
                "response-size limit — narrow the bbox (min_lon/min_lat/max_lon/max_lat) "
                "for a complete export."
            )

        geojson = {
            "type": "FeatureCollection",
            "metadata": metadata,
            "features": features,
        }
        _bounded_cache_put(_export_cache, cache_key, geojson, version, _EXPORT_CACHE_MAX)

    headers = {
        "Content-Disposition": "attachment; filename=heatmap-odbl.geojson",
        "X-License": ODBL_LICENSE,
    }

    accept = request.headers.get("accept-encoding", "")
    if "gzip" in accept:
        raw = json.dumps(geojson, separators=(",", ":")).encode()
        gz = gzip.compress(raw, compresslevel=6)
        return Response(
            content=gz,
            media_type="application/json",
            headers={**headers, "Content-Encoding": "gzip"},
        )

    return JSONResponse(content=geojson, headers=headers)


@router.get("/heatmap/trails")
async def heatmap_trails(
    request: Request,
    sport: str | None = Query(None, description="road|gravel|mtb|offroad"),
    days: int | None = Query(None, ge=7, le=3650, description="Filter by last N days (min 7)"),
    min_lon: float = Query(-180.0),
    min_lat: float = Query(-90.0),
    max_lon: float = Query(180.0),
    max_lat: float = Query(90.0),
) -> Response:
    """Export trail heatmap as GeoJSON LineStrings (ODbL, K-anonymity enforced).

    Each feature is a ~11 m GPS edge segment with pass_count and user_count.
    Only edges traversed by >= K distinct users are returned.
    Pre-compresses and caches the response for fast delivery.
    """
    # RAW-trace cutover: this bulk endpoint reads ``heat_edges`` with a
    # default WHOLE-WORLD bbox and NO LIMIT — a cold full-bbox request
    # ``fetchall()``s ~5 M rows into a 512 Mi web (OOM). It is superseded by the
    # static PMTiles + the MVT tiles, so under raw it is retired: 410 Gone (the
    # frontend community layer never calls it — it loads PMTiles/MVT). Matched
    # mode keeps serving it unchanged.
    from app.services.raw_trace_display import raw_display_enabled
    if raw_display_enabled():
        raise HTTPException(
            status_code=410,
            detail={
                "error": "endpoint_retired_raw_mode",
                "message": (
                    "/heatmap/trails is retired under the raw-trace display "
                    "source. Use the static PMTiles (/export/heatmap.pmtiles) "
                    "or the /heatmap/tiles MVT endpoint for the community "
                    "heatmap, or /export/heatmap.geojsonl for a bulk export."
                ),
            },
        )

    # Use cache for full-bbox requests (no custom bbox filtering)
    is_full_bbox = (min_lon <= -179 and min_lat <= -89
                    and max_lon >= 179 and max_lat >= 89)
    cache_key = f"{sport or 'all'}:{days or 'all'}"

    if is_full_bbox:
        cached = _trails_gz_cache.get(cache_key)
        if cached and cached[1] == _get_edge_version():
            accept = request.headers.get("accept-encoding", "")
            if "gzip" in accept:
                return Response(
                    content=cached[0],
                    media_type="application/json",
                    headers={
                        "Content-Encoding": "gzip",
                        "X-License": ODBL_LICENSE,
                        "Cache-Control": "public, max-age=86400, stale-while-revalidate=86400",
                    },
                )

    bbox = (min_lon, min_lat, max_lon, max_lat)

    # Check bbox cache
    bbox_cache_key = f"{cache_key}:{min_lon:.3f},{min_lat:.3f},{max_lon:.3f},{max_lat:.3f}"
    if not is_full_bbox:
        cached_bbox = _bbox_trails_cache.get(bbox_cache_key)
        if cached_bbox and cached_bbox[1] == _get_edge_version():
            accept = request.headers.get("accept-encoding", "")
            if "gzip" in accept:
                return Response(
                    content=cached_bbox[0],
                    media_type="application/json",
                    headers={
                        "Content-Encoding": "gzip",
                        "X-License": ODBL_LICENSE,
                        "Cache-Control": "public, max-age=86400, stale-while-revalidate=86400",
                    },
                )

    sports_to_query: list[str | None] = expand_sport(sport) if sport else [None]

    all_edges: list[dict] = []
    for s in sports_to_query:
        edges = ingest_service.get_heat_edges_public(
            sport=s, bbox=bbox, k=HEATMAP_K_ANONYMITY, days=days
        )
        all_edges.extend(edges)

    features = [
        {
            "type": "Feature",
            "geometry": edge["geometry"],
            "properties": {
                "sport": edge["sport"],
                "user_count": edge["user_count"],
                "pass_count": edge["pass_count"],
                "heat_score": edge.get("heat_score", 0.0),
                "forward_count": edge.get("forward_count", 0),
                "backward_count": edge.get("backward_count", 0),
                "license": ODBL_LICENSE,
            },
        }
        for edge in all_edges
    ]

    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "license": ODBL_LICENSE,
            "license_url": ODBL_URL,
            "k_anonymity": HEATMAP_K_ANONYMITY,
            "feature_count": len(features),
            "source": "CHEMINS COMMUNS — community trail heatmap",
        },
        "features": features,
    }

    raw = json.dumps(geojson, separators=(",", ":")).encode()
    gz = gzip.compress(raw, compresslevel=6)

    if is_full_bbox:
        if len(_trails_gz_cache) >= _TRAILS_CACHE_MAX:
            oldest_key = min(_trails_gz_cache, key=lambda k: _trails_gz_cache[k][1])
            del _trails_gz_cache[oldest_key]
        _trails_gz_cache[cache_key] = (gz, _get_edge_version())
    else:
        # Evict oldest entries if cache is full
        if len(_bbox_trails_cache) >= _BBOX_CACHE_MAX:
            oldest_key = min(_bbox_trails_cache, key=lambda k: _bbox_trails_cache[k][1])
            del _bbox_trails_cache[oldest_key]
        _bbox_trails_cache[bbox_cache_key] = (gz, _get_edge_version())

    accept = request.headers.get("accept-encoding", "")
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

    return JSONResponse(
        content=geojson,
        headers={
            "X-License": ODBL_LICENSE,
            "Cache-Control": "public, max-age=86400, stale-while-revalidate=86400",
        },
    )


_mvt_cache: OrderedDict[str, tuple[bytes, float]] = OrderedDict()
# Cache cap: avg tile ~50KB, max ~500KB. 500 tiles = ~50MB worst case.
# Configurable via TILE_LRU_CACHE_MAX env var.
_MVT_CACHE_MAX = int(os.environ.get("TILE_LRU_CACHE_MAX", "500"))


@lru_cache(maxsize=16)
def _expand_sport_cached(sport: str) -> list[str]:
    """Cache expand_sport results to avoid repeated computation per tile."""
    return expand_sport(sport) if sport else [None]  # type: ignore[list-item]


def _tile_bbox_4326(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Compute tile bounding box in EPSG:4326 (lon/lat) using Web Mercator math."""
    import math
    n = 2 ** z
    lon_min = x / n * 360 - 180
    lon_max = (x + 1) / n * 360 - 180
    lat_max = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_min = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lon_min, lat_min, lon_max, lat_max


def _generate_tile(sport: str, z: int, x: int, y: int, days: int | None = None) -> bytes:
    """Generate MVT tile bytes — runs in thread pool to avoid blocking event loop."""
    from sqlalchemy import text as sa_text

    from app.db.session import SessionLocal
    from app.services.raw_trace_display import raw_display_enabled

    # RAW-trace cutover: the community map is served by the STATIC raw PMTiles;
    # this live MVT is only a FALLBACK behind it. Every branch below reads a
    # LEGACY read model — ``heat_edges`` (z<=11 + grid fallback),
    # ``heat_edges_agg`` (all-time via ``build_agg_read_sql``),
    # ``heat_edge_contributors`` / ``osm_*`` (time-windowed / z>11) — ALL being
    # DROPPED in prod under the raw pivot, so any query would 500. In raw mode
    # serve an EMPTY tile instead (the frontend loads the PMTiles, not this).
    # Matched mode is unchanged.
    if raw_display_enabled():
        return b""

    sport_clause = ""
    params: dict[str, object] = {"k": HEATMAP_K_ANONYMITY, "z": z, "x": x, "y": y}
    if sport != 'all':
        sports = _expand_sport_cached(sport)
        if sports and sports != [None]:
            sport_clause = "AND sport = ANY(:sports)"
            params["sports"] = [s for s in sports if s]

    # Time-based filtering: JOIN heat_edge_contributors to filter by activity_date
    days_join = ""
    days_where = ""
    if days and days > 0:
        params["days"] = days
        days_join = "JOIN heat_edge_contributors hec ON hec.edge_key = he.edge_key"
        days_where = "AND hec.activity_date >= NOW() - MAKE_INTERVAL(days => :days)"

    lon_min, lat_min, lon_max, lat_max = _tile_bbox_4326(z, x, y)
    params["lon_min"] = lon_min
    params["lat_min"] = lat_min
    params["lon_max"] = lon_max
    params["lat_max"] = lat_max

    # Spaghetti filter: drop grid-fallback edges (no OSM match) longer
    # than 60 m. Matches the ingest-time cap in `_update_heat_edges` and
    # the PMTiles export filter — without it, GPS-loss artifacts that
    # were ingested before the May 2026 fixes still render here. Spliced
    # into every WHERE clause below.
    spaghetti_filter = (
        "AND NOT (he.osm_way_id IS NULL "
        "AND ST_Length(he.geometry::geography) > 60)"
    )

    db = SessionLocal()
    try:
        # Safety net: no single tile should ever run for minutes. A tile that
        # can't render within this budget (e.g. a bad plan right after a bulk
        # heat_edges reload before ANALYZE — which once cost a 27-min hang on a
        # single z11 tile) aborts fast instead of pinning a worker / blocking
        # the pre-warm loop. Configurable via TILE_STATEMENT_TIMEOUT_MS.
        _tile_timeout_ms = int(os.environ.get("TILE_STATEMENT_TIMEOUT_MS", "30000"))
        # SET LOCAL (not SET): scope the timeout to THIS tile's transaction so
        # it never leaks onto the pooled connection for the next checkout.
        db.execute(sa_text(f"SET LOCAL statement_timeout = {_tile_timeout_ms}"))
        if z <= 11:
            snap_degrees = {6: 0.1, 7: 0.05, 8: 0.02, 9: 0.01, 10: 0.005, 11: 0.002}.get(z, 0.01)
            params["snap"] = snap_degrees
            row = db.execute(sa_text(f"""
                WITH clustered AS (
                    SELECT
                        ROUND(ST_X(ST_StartPoint(he.geometry)) / :snap) AS gx,
                        ROUND(ST_Y(ST_StartPoint(he.geometry)) / :snap) AS gy,
                        SUM(he.user_count) AS total_users,
                        SUM(he.pass_count) AS total_passes,
                        COUNT(*) AS edge_count
                    FROM heat_edges he
                    {days_join}
                    WHERE he.user_count >= :k
                      AND he.geometry && ST_MakeEnvelope(:lon_min, :lat_min, :lon_max, :lat_max, 4326)
                      {sport_clause}
                      {days_where}
                      {spaghetti_filter}
                    GROUP BY gx, gy
                ),
                points AS (
                    SELECT
                        ST_AsMVTGeom(
                            ST_Transform(
                                ST_SetSRID(ST_MakePoint(gx * :snap, gy * :snap), 4326),
                                3857
                            ),
                            ST_TileEnvelope(:z, :x, :y), 4096, 64, true
                        ) AS mvt_geom,
                        LEAST(total_users, 65535)::int AS weight,
                        LEAST(edge_count, 65535)::int AS density,
                        ROUND(LEAST(1.0, LN(1 + total_users) / (8.0 * LN(2)))::numeric, 3) AS heat_score
                    FROM clustered
                )
                SELECT ST_AsMVT(points, 'heat_points', 4096, 'mvt_geom') FROM points
            """), params).scalar()
        else:
            # z11+: Direct query on heat_edges — simple, fast, no matview dependency.
            # Zoom-dependent: simplify at z11-13, raw geometry at z14+.
            # user_count filter at z11 to avoid 100K+ edge tiles.
            # min_uc_zoom respects K_ANONYMITY — never filter below the K threshold
            min_uc_zoom = HEATMAP_K_ANONYMITY
            params["min_uc_zoom"] = max(min_uc_zoom, HEATMAP_K_ANONYMITY)

            # Row limit prevents OOM on dense tiles (each edge = ~1KB in MVT).
            # 50K edges per tile is ~50MB peak — safe under the semaphore.
            _TILE_ROW_LIMIT = 50000
            params["row_limit"] = _TILE_ROW_LIMIT

            # Continuous-heatmap CTE shared by z11-13 (with simplify) and
            # z14+ (raw). Aggregates heat_edges by (osm_way_id, sport) and
            # lifts the smooth multi-point OSM way geometry; falls back to
            # raw heat_edge geometry only for grid-fallback edges (no OSM
            # match). Without this, multiple traces on the same road
            # produced N overlapping 2-point segments (visible as
            # criss-crossing zigzags at z16+). Guarded by tests/
            # test_pending_bug_fixes::TestFallbackContinuousHeatmap.
            #
            # SHARED with the static PMTiles export via
            # app.services.heat_aggregation.build_heat_aggregation_sql — the
            # single source of truth for the by-way aggregation. The matview
            # that used to back this path was DROPPED (June 2026); this
            # endpoint now runs the LATERAL join against heat_edges directly
            # at request time (~50-200ms for this rarely-hit fallback behind
            # the primary static PMTiles), so it still resolves the smooth
            # way_geometry and never regresses to 2-point hops.
            from app.services.heat_aggregation import (
                HEAT_SCORE_SQL,
                build_agg_read_sql,
                build_heat_aggregation_sql,
                resolve_grid_fallback_display,
            )

            bbox_predicate = (
                "he.geometry && ST_MakeEnvelope("
                ":lon_min, :lat_min, :lon_max, :lat_max, 4326)"
            )
            # Per-request predicates folded into the shared builder's
            # extra_predicate (AND-ed into its `heat` CTE) so the single
            # source of truth handles the by-way aggregation while this
            # endpoint keeps its sport + time filters. Both are bound
            # params (:sports / :days), not interpolated user input.
            extra_clauses: list[str] = []
            if sport_clause:
                # sport_clause is "AND sport = ANY(:sports)"; strip the AND so
                # it composes cleanly inside the builder's predicate slot.
                # Resolves against both heat_edges (he.sport) and the agg
                # table (aliased he.sport) — same column name either way.
                extra_clauses.append("he.sport = ANY(:sports)")

            time_filtered = bool(days and days > 0)
            if time_filtered:
                # A `days` window needs per-contributor dates, which the
                # all-time agg table does not retain → read live from
                # heat_edges via the SSOT builder. Correlated EXISTS so it
                # slots in without changing the builder's FROM/JOIN shape.
                extra_clauses.append(
                    "EXISTS (SELECT 1 FROM heat_edge_contributors hec "
                    "WHERE hec.edge_key = he.edge_key "
                    "AND hec.activity_date >= NOW() - MAKE_INTERVAL(days => :days))"
                )
            extra_predicate = " AND ".join(extra_clauses) if extra_clauses else "TRUE"

            # K-anon floor for z11+ (never below the K threshold). 60 m
            # grid-fallback length cap matches the ingest-time + PMTiles
            # filters. The grid-fallback ("desire lines") keep/confirmation
            # policy comes from the SAME env-driven SSOT helper the static
            # PMTiles export uses (resolve_grid_fallback_display — Paul
            # 2026-07-20: keep by default, confirmation floor follows K) so
            # the two display paths CANNOT disagree.
            #
            # All-time (the common case): read the pre-materialised
            # ``heat_edges_agg`` via ``build_agg_read_sql`` — the OSM-matched
            # half is a cheap indexed lookup, grid fallback stays live (bbox-
            # bounded, cheap). Time-windowed: fall back to the live
            # ``build_heat_aggregation_sql`` over heat_edges. Both come from
            # the same aggregation SSOT so results can't drift.
            drop_grid_fallback, grid_fallback_min_uc = (
                resolve_grid_fallback_display(int(params["min_uc_zoom"]))
            )
            if time_filtered:
                combined_cte = build_heat_aggregation_sql(
                    min_uc=int(params["min_uc_zoom"]),
                    bbox_predicate=bbox_predicate,
                    extra_predicate=extra_predicate,
                    grid_fallback_min_uc=grid_fallback_min_uc,
                    max_grid_fallback_m=60.0,
                    drop_grid_fallback=drop_grid_fallback,
                )
            else:
                combined_cte = build_agg_read_sql(
                    min_uc=int(params["min_uc_zoom"]),
                    bbox_predicate=bbox_predicate,
                    extra_predicate=extra_predicate,
                    grid_fallback_min_uc=grid_fallback_min_uc,
                    max_grid_fallback_m=60.0,
                    drop_grid_fallback=drop_grid_fallback,
                )

            # Heat-score expression shared by both zoom branches + the PMTiles
            # export. Boosts primary/secondary roads so they read as
            # warmer-coloured — Komoot-style bright pink/orange on D-roads.
            heat_score_expr = HEAT_SCORE_SQL

            if z <= 13:
                display_snap = {11: 0.005, 12: 0.002, 13: 0.001}.get(z, 0.002)
                params["dsnap"] = display_snap
                row = db.execute(sa_text(f"""
                    {combined_cte},
                    edges AS (
                        SELECT
                            ST_AsMVTGeom(
                                ST_Transform(
                                    ST_SimplifyPreserveTopology(geometry, :dsnap),
                                    3857),
                                ST_TileEnvelope(:z, :x, :y), 4096, 64, true
                            ) AS mvt_geom,
                            user_count,
                            user_count AS pass_count,
                            {heat_score_expr} AS heat_score
                        FROM combined
                        ORDER BY user_count DESC
                        LIMIT :row_limit
                    )
                    SELECT ST_AsMVT(edges, 'trails', 4096, 'mvt_geom') FROM edges
                """), params).scalar()
            else:
                # z14+: raw geometry, no simplification
                row = db.execute(sa_text(f"""
                    {combined_cte},
                    edges AS (
                        SELECT
                            ST_AsMVTGeom(
                                ST_Transform(geometry, 3857),
                                ST_TileEnvelope(:z, :x, :y), 4096, 64, true
                            ) AS mvt_geom,
                            user_count,
                            user_count AS pass_count,
                            {heat_score_expr} AS heat_score
                        FROM combined
                        ORDER BY user_count DESC
                        LIMIT :row_limit
                    )
                    SELECT ST_AsMVT(edges, 'trails', 4096, 'mvt_geom') FROM edges
                """), params).scalar()
    finally:
        db.close()

    mvt_bytes = bytes(row) if row else b""
    # Gzip level 1 — MVT compresses well at low levels (5x less CPU than level 6,
    # only 2KB larger per tile). Critical for cold-hit tile latency.
    return gzip.compress(mvt_bytes, compresslevel=1) if mvt_bytes else b""


_MVT_HEADERS = {
    "Cache-Control": "public, max-age=86400, stale-while-revalidate=86400",
    "X-License": ODBL_LICENSE,
    "Content-Encoding": "gzip",
}
_MVT_HEADERS_EMPTY = {
    "Cache-Control": "public, max-age=86400, stale-while-revalidate=86400",
    "X-License": ODBL_LICENSE,
}


@router.get("/heatmap/tiles/{sport}/{z}/{x}/{y}.mvt")
def heatmap_tile(
    sport: str, z: int, x: int, y: int,
    days: int | None = Query(None, ge=7, le=3650, description="Filter by last N days (min 7)"),
) -> Response:
    """Serve heatmap trail edges as Mapbox Vector Tiles (MVT).

    Uses PostGIS ST_AsMVT for native binary vector tile generation.
    Each edge includes heat_score (0-1) and user_count for client styling.
    K-anonymity enforced: only edges with user_count >= K are included.
    Optional `days` param filters to activities within the last N days.
    Sync def -- FastAPI runs in threadpool automatically, allowing parallel tiles.
    """
    # Tile warmup runs in background on first request — disabled for now
    # to avoid blocking the thread pool.
    # _start_tile_warmup()
    if z < 6 or z > 16:
        return Response(content=b"", media_type="application/x-protobuf", status_code=204)

    t0 = time.monotonic()
    cache_key = f"{sport}/{z}/{x}/{y}/{days or 'all'}"
    version = _get_edge_version()
    cached = _mvt_cache.get(cache_key)
    if cached and cached[1] == version:
        _mvt_cache.move_to_end(cache_key)
        gz_bytes = cached[0]
        logger.debug("[tile] %s HIT memory (%dB, %.1fms)", cache_key, len(gz_bytes), (time.monotonic() - t0) * 1000)
        return Response(
            content=gz_bytes,
            media_type="application/x-protobuf",
            headers=_MVT_HEADERS if gz_bytes else _MVT_HEADERS_EMPTY,
        )

    # Try disk cache (persists across restarts; may contain tiles from an
    # earlier warm-up. Read-through only — nothing writes it post-matview-drop)
    if not days:
        tile_dir = os.environ.get("TILE_CACHE_DIR", "/tmp/heatmap-tiles")
        disk_path = os.path.join(tile_dir, sport, str(z), str(x), f"{y}.mvt.gz")
        if os.path.exists(disk_path):
            with open(disk_path, "rb") as f:
                gz_bytes = f.read()
            _mvt_cache[cache_key] = (gz_bytes, version)
            logger.debug("[tile] %s HIT disk (%dB, %.1fms)", cache_key, len(gz_bytes), (time.monotonic() - t0) * 1000)
            return Response(
                content=gz_bytes,
                media_type="application/x-protobuf",
                headers=_MVT_HEADERS if gz_bytes else _MVT_HEADERS_EMPTY,
            )

    # Cap concurrent tile generation to prevent OOM (each tile = 100-500MB peak)
    t_wait = time.monotonic()
    with _tile_gen_semaphore:
        wait_ms = (time.monotonic() - t_wait) * 1000
        # Re-check cache after acquiring lock (another thread may have generated it)
        cached = _mvt_cache.get(cache_key)
        if cached and cached[1] == version:
            _mvt_cache.move_to_end(cache_key)
            return Response(
                content=cached[0],
                media_type="application/x-protobuf",
                headers=_MVT_HEADERS if cached[0] else _MVT_HEADERS_EMPTY,
            )
        gz_bytes = _generate_tile(sport, z, x, y, days=days)
    elapsed_ms = (time.monotonic() - t0) * 1000
    if wait_ms > 50:
        logger.info("[tile] %s MISS → generated (%dB, %.0fms, waited %.0fms)", cache_key, len(gz_bytes), elapsed_ms, wait_ms)
    else:
        logger.info("[tile] %s MISS → generated (%dB, %.0fms)", cache_key, len(gz_bytes), elapsed_ms)

    # O(1) LRU cache via OrderedDict
    if cache_key in _mvt_cache:
        _mvt_cache.move_to_end(cache_key)
    _mvt_cache[cache_key] = (gz_bytes, version)
    while len(_mvt_cache) > _MVT_CACHE_MAX:
        _mvt_cache.popitem(last=False)

    return Response(
        content=gz_bytes,
        media_type="application/x-protobuf",
        headers=_MVT_HEADERS if gz_bytes else _MVT_HEADERS_EMPTY,
    )


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


@router.post("/heatmap/trails")
async def seed_trails(edges: list = Body(...)):
    """Seed marked trail edges (GR/GT/PR/EV/GRP) for testing. Only in TEST_MODE."""
    from app.config import TEST_MODE

    if not TEST_MODE:
        raise HTTPException(status_code=403, detail="Only available in TEST_MODE")
    count = ingest_service.append_trail_edges(edges)
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


@router.get("/me/unexplored")
async def unexplored_zones(
    lat: float | None = Query(None, description="Reference latitude"),
    lon: float | None = Query(None, description="Reference longitude"),
    sport: str | None = Query(None, description="road|gravel|mtb|offroad"),
    limit: int = Query(20, ge=1, le=100),
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)] = None,
) -> JSONResponse:
    """Return community heatmap cells near the user that they have never visited.

    Compares user's personal activity_cells against the K-anonymity-filtered
    community heat_cells. Returns cells that appear in the community heatmap
    but NOT in the user's personal coverage.
    """
    user_id = current_user.user_id

    # User's visited cell keys (any sport)
    user_cell_keys = ingest_service.get_user_cell_keys(user_id)

    # Community cells with K-anonymity (unexplored by user). Aggregated
    # from heat_edges at read time — see ingest.get_heat_cells_aggregated.
    all_community = ingest_service.get_heat_cells_aggregated(
        sport=sport, k=HEATMAP_K_ANONYMITY
    )
    unexplored = [c for c in all_community if c["cell_key"] not in user_cell_keys]

    # Build set of user-adjacent tile coords to filter out cells too close
    # to areas the user has already covered (within 2 tiles ~ 2-3km at z14)
    user_tile_coords: set[tuple[int, int, int]] = set()
    for key in user_cell_keys:
        try:
            z, x, y = (int(v) for v in key.split("/"))
            user_tile_coords.add((z, x, y))
        except (ValueError, AttributeError):
            continue
    user_neighbor_keys: set[str] = set()
    for z, x, y in user_tile_coords:
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                user_neighbor_keys.add(f"{z}/{x + dx}/{y + dy}")
    # Only keep cells that are NOT adjacent to user's coverage
    unexplored = [c for c in unexplored if c["cell_key"] not in user_neighbor_keys]

    # Compute tile center lat/lon for each cell
    def _cell_center(cell_key: str) -> tuple[float, float] | None:
        try:
            z, x, y = (int(v) for v in cell_key.split("/"))
            n = 2 ** z
            lon_c = (x + 0.5) / n * 360.0 - 180.0
            lat_c = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / n))))
            return lat_c, lon_c
        except (ValueError, AttributeError):
            return None

    result = []
    for cell in unexplored:
        center = _cell_center(cell["cell_key"])
        if center is None:
            continue
        cell_lat, cell_lon = center
        dist_km: float | None = None
        if lat is not None and lon is not None:
            dist_km = haversine_m(lon, lat, cell_lon, cell_lat) / 1000.0
        result.append({
            "cell_key": cell["cell_key"],
            "sport": cell.get("sport"),
            "user_count": cell["user_count"],
            "lat": cell_lat,
            "lon": cell_lon,
            "dist_km": dist_km,
        })

    # Sort by distance if reference point provided, otherwise by popularity
    if lat is not None and lon is not None:
        result.sort(key=lambda c: c["dist_km"] or 9999)
    else:
        result.sort(key=lambda c: -c["user_count"])

    return JSONResponse(content={"cells": result[:limit]})
