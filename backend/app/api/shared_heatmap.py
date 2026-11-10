"""Public side of the "Ma heatmap" share link — ``/s/{token}/…``.

Design: docs/ma-heatmap-fichier-prive.md §10. Every route resolves the token
on EVERY request (indexed lookup on its SHA-256, no cache) → an unknown,
revoked or regenerated token is a 404 at once. The only object a token can
reach is the owner's SHARED artefact ``shares/{user}/{share_version}.pmtiles``
(endpoint-masked, ``sport`` only): the key is built from the DB row under the
``shares/`` prefix, never from the URL, so the private ``users/`` objects are
unreachable from here.

- ``GET /s/{token}/heatmap.pmtiles`` — Range proxy (MapLibre / pmtiles clients);
- ``GET /s/{token}/{z}/{x}/{y}.pbf`` — one MVT tile read out of the PMTiles;
- ``GET /s/{token}/style.json``      — MapLibre style (gpx.studio "vector style");
- ``GET /s/{token}/{z}/{x}/{y}.png`` — raster XYZ (gpx.studio overlay, OsmAnd,
  Locus), rendered on demand, cyan on transparent.

CORS ``*`` and NO credentials on ``/s/*`` only (``SharedHeatmapCORSMiddleware``
strips Origin/Cookie/Authorization before the app sees the request, so the
credentialed site-wide CORS policy and cookie auth never apply here).
``Cache-Control: public, max-age=300``: a client keeps a tile at most 5 min
after a revocation (no CDN in front of the API). Rate-limited per IP.

Never log the token.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.me_heatmap import public_base_url, range_response
from app.db.session import get_db
from app.rate_limit import limiter
from app.services import mvt_raster, pmtiles_reader
from app.services import my_heatmap_files as mhf
from app.services import my_heatmap_share as share_svc
from app.services.my_heatmap_share import ResolvedShare

log = logging.getLogger(__name__)

router = APIRouter(prefix="/s", tags=["share"])

PUBLIC_CACHE = "public, max-age=300"
# Raster zooms served (z > archive max zoom → rendered from the max-zoom tile).
RASTER_MAX_ZOOM = 18
_TILE_RATE = "600/minute"
_DOC_RATE = "120/minute"
_TILE_LAYER = mhf.TILE_LAYER
_COLOR = "#06b6d4"  # MY_TRACES_COLOR (frontend/lib/constants.ts)

_transparent_png: bytes | None = None


def _transparent() -> bytes:
    global _transparent_png
    if _transparent_png is None:
        _transparent_png = mvt_raster.render_png(None, 0)
    return _transparent_png


def _resolve(db: Session, token: str) -> ResolvedShare:
    res = share_svc.resolve(db, token)
    db.close()  # release the pooled connection before any storage round-trip
    if res is None:
        raise HTTPException(status_code=404, detail="Not found")
    return res


def _etag(res: ResolvedShare) -> str:
    # Never the version itself (it carries the data revision): an opaque hash.
    return '"' + hashlib.sha256((res.share_version or "-").encode()).hexdigest()[:20] + '"'


def _read_fn(store, key: str):
    return lambda start, end: store.read(key, start, end)


def _tile(res: ResolvedShare, z: int, x: int, y: int) -> tuple[pmtiles_reader.Header | None, bytes | None]:
    key = res.key
    store = mhf.get_store()
    if key is None or store is None:
        return None, None
    if not (0 <= z <= 26 and 0 <= x < (1 << z) and 0 <= y < (1 << z)):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        return pmtiles_reader.get_tile(key, _read_fn(store, key), z, x, y)
    except FileNotFoundError as exc:  # object deleted (revocation racing this request)
        raise HTTPException(status_code=404, detail="Not found") from exc


@router.get("/{token}/heatmap.pmtiles")
@limiter.limit(_TILE_RATE)
def shared_pmtiles(token: str, request: Request, db: Annotated[Session, Depends(get_db)]) -> Response:
    res = _resolve(db, token)
    store = mhf.get_store()
    if res.key is None or store is None:
        raise HTTPException(status_code=404, detail="Not found")
    return range_response(request, store, res.key, res.share_pmtiles_bytes, _etag(res), PUBLIC_CACHE)


@router.get("/{token}/style.json")
@limiter.limit(_DOC_RATE)
def shared_style(token: str, request: Request, db: Annotated[Session, Depends(get_db)]) -> Response:
    _resolve(db, token)
    zmin, zmax = mhf._zoom_range()
    tiles = f"{public_base_url(request)}/s/{token}/{{z}}/{{x}}/{{y}}.pbf"
    style = {
        "version": 8,
        "name": "Ma heatmap — Chemins communs",
        "sources": {
            "ma-heatmap": {
                "type": "vector",
                "tiles": [tiles],
                "minzoom": zmin,
                "maxzoom": zmax,
                "attribution": "Chemins communs",
            }
        },
        "layers": [
            {
                "id": "ma-heatmap-glow",
                "type": "line",
                "source": "ma-heatmap",
                "source-layer": _TILE_LAYER,
                "minzoom": 9,
                "layout": {"line-cap": "round", "line-join": "round"},
                "paint": {
                    "line-color": _COLOR,
                    "line-width": ["interpolate", ["linear"], ["zoom"], 9, 2, 13, 8, 15, 14],
                    "line-blur": ["interpolate", ["linear"], ["zoom"], 9, 1, 15, 6],
                    "line-opacity": ["interpolate", ["linear"], ["zoom"], 9, 0, 12, 0.18],
                },
            },
            {
                "id": "ma-heatmap-line",
                "type": "line",
                "source": "ma-heatmap",
                "source-layer": _TILE_LAYER,
                "layout": {"line-cap": "round", "line-join": "round"},
                "paint": {
                    "line-color": _COLOR,
                    "line-width": ["interpolate", ["exponential", 1.5], ["zoom"], 8, 0.6, 11, 1.2, 14, 3, 16, 4],
                    "line-opacity": ["interpolate", ["linear"], ["zoom"], 8, 0.45, 12, 0.75, 14, 0.92],
                },
            },
        ],
    }
    return Response(
        content=json.dumps(style, separators=(",", ":")),
        media_type="application/json",
        headers={"Cache-Control": PUBLIC_CACHE},
    )


@router.get("/{token}/{z}/{x}/{y}.pbf")
@limiter.limit(_TILE_RATE)
def shared_vector_tile(
    token: str, z: int, x: int, y: int, request: Request, db: Annotated[Session, Depends(get_db)]
) -> Response:
    res = _resolve(db, token)
    header, data = _tile(res, z, x, y)
    if not data or header is None:
        return Response(status_code=204, headers={"Cache-Control": PUBLIC_CACHE})
    headers = {"Cache-Control": PUBLIC_CACHE, "ETag": _etag(res)}
    if header.tile_compression == 2:
        headers["Content-Encoding"] = "gzip"
    return Response(content=data, media_type="application/vnd.mapbox-vector-tile", headers=headers)


@router.get("/{token}/{z}/{x}/{y}.png")
@limiter.limit(_TILE_RATE)
def shared_raster_tile(
    token: str, z: int, x: int, y: int, request: Request, db: Annotated[Session, Depends(get_db)]
) -> Response:
    res = _resolve(db, token)
    if not (0 <= z <= RASTER_MAX_ZOOM and 0 <= x < (1 << z) and 0 <= y < (1 << z)):
        raise HTTPException(status_code=404, detail="Not found")
    headers = {"Cache-Control": PUBLIC_CACHE}
    _zmin, zmax = mhf._zoom_range()
    store = mhf.get_store()
    if res.key is not None and store is not None:
        # The archive's real max zoom (header cached per immutable version).
        try:
            zmax = pmtiles_reader.header_and_root(res.key, _read_fn(store, res.key))[0].max_zoom
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Not found") from exc
    dz = max(0, z - zmax)
    header, data = _tile(res, z - dz, x >> dz, y >> dz)
    if not data or header is None:
        return Response(content=_transparent(), media_type="image/png", headers=headers)
    if header.tile_compression == 2:
        data = gzip.decompress(data)
    png = mvt_raster.render_png(data, z, dz, x - ((x >> dz) << dz), y - ((y >> dz) << dz))
    return Response(content=png, media_type="image/png", headers=headers)


# ── CORS: `*`, no credentials, on /s/* only ──────────────────────────────────

_STRIP_REQUEST_HEADERS = {b"origin", b"cookie", b"authorization"}
_CORS_HEADERS = [
    (b"access-control-allow-origin", b"*"),
    (b"access-control-expose-headers", b"Content-Range, Content-Length, Accept-Ranges, ETag"),
]


class SharedHeatmapCORSMiddleware:
    """Pure ASGI. For ``/s/…`` only:

    - answers CORS preflights itself (``*``, GET/HEAD, ``Range``);
    - strips ``Origin``, ``Cookie`` and ``Authorization`` from the request, so
      neither the site's credentialed CORS policy nor cookie auth can apply;
    - sets ``Access-Control-Allow-Origin: *`` (never with credentials) on the
      response.

    Must wrap the site-wide ``CORSMiddleware`` (added after it in main.py).
    Every other path is passed through untouched.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/s/"):
            await self.app(scope, receive, send)
            return
        if scope.get("method") == "OPTIONS":
            await send({
                "type": "http.response.start",
                "status": 204,
                "headers": [
                    *_CORS_HEADERS,
                    (b"access-control-allow-methods", b"GET, HEAD, OPTIONS"),
                    (b"access-control-allow-headers", b"Range, If-Match, If-None-Match, Cache-Control"),
                    (b"access-control-max-age", b"86400"),
                ],
            })
            await send({"type": "http.response.body", "body": b""})
            return
        scope = dict(scope)
        scope["headers"] = [(k, v) for k, v in scope.get("headers", []) if k.lower() not in _STRIP_REQUEST_HEADERS]

        async def _send(message):
            if message["type"] == "http.response.start":
                headers = [
                    (k, v) for k, v in message.get("headers", [])
                    if not k.lower().startswith(b"access-control-")
                ]
                message = {**message, "headers": headers + _CORS_HEADERS}
            await send(message)

        await self.app(scope, receive, _send)
