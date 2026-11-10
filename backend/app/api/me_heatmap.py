"""Private per-user "Ma heatmap" file — manifest + authenticated Range proxy.

- ``GET /me/heatmap/manifest`` — ``{status: ready|stale|missing|disabled, …}``.
  The frontend only uses the file when ``ready``; otherwise it falls back to
  ``GET /me/activities``. A ``stale``/``missing`` answer re-schedules a
  (debounced) rebuild — self-heal for any write path that forgot to.
- ``GET /me/heatmap/{version}.pmtiles`` — byte-range proxy to the PRIVATE
  bucket (``Range`` required, capped). ``/me/heatmap/{version}.summary.json``
  — the geometry-free sidebar list (stored gzip).
- ``GET|POST|DELETE /me/heatmap/share`` — the owner's share link (§10):
  status, create/regenerate (the clear token is returned only here, once),
  revoke. The public side lives in ``app.api.shared_heatmap`` (``/s/…``).
- ``POST /internal/my-heatmap/rebuild`` — Cloud Tasks → worker, OIDC.

PRIVACY: the object key is built from the AUTHENTICATED user id, never from
the URL, and the requested version must be that user's current or previous
one — another user's version is a 404 (no existence leak). Responses are
``Cache-Control: private`` (immutable per version, so a reload of /map is
served from the browser cache). Design: docs/ma-heatmap-fichier-prive.md.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user
from app.api.internal_ingest import verify_oidc_token
from app.db.session import get_db
from app.rate_limit import limiter, user_or_ip_key
from app.services import my_heatmap_files as mhf
from app.services import my_heatmap_share as share_svc

log = logging.getLogger(__name__)

router = APIRouter(tags=["me"])
internal_router = APIRouter(prefix="/internal", tags=["internal"])

# A PMTiles client asks for 16 KiB headers, directories and single tiles; a
# larger window is never needed and would only let a client pull the whole
# file through the 512Mi web instance in one go.
MAX_RANGE_BYTES = 8 * 1024 * 1024
_RANGE_RE = re.compile(r"^bytes=(\d+)-(\d+)$")
# Immutable per version (the version changes on every rebuild).
_IMMUTABLE_PRIVATE = "private, max-age=31536000, immutable"


@router.get("/me/heatmap/manifest")
def my_heatmap_manifest(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
) -> dict:
    response.headers["Cache-Control"] = "private, no-store"
    return mhf.manifest(db, current_user.user_id)


def _owned_version(db: Session, user_id: str, version: str) -> mhf.FileState:
    if not mhf.VERSION_RE.match(version) or not mhf.is_enabled_for(user_id):
        raise HTTPException(status_code=404, detail="Not found")
    state = mhf.read_state(db, user_id)
    if state is None or version not in {state.version, state.previous_version}:
        raise HTTPException(status_code=404, detail="Not found")
    return state


@router.get("/me/heatmap/{version}.pmtiles")
def my_heatmap_tiles(
    version: str,
    request: Request,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    state = _owned_version(db, current_user.user_id, version)
    db.close()  # release the pooled connection before the storage round-trip
    store = mhf.get_store()
    if store is None:
        raise HTTPException(status_code=404, detail="Not found")
    total = state.pmtiles_bytes if version == state.version else None
    return range_response(
        request, store, mhf.pmtiles_key(current_user.user_id, version), total,
        f'"{version}"', _IMMUTABLE_PRIVATE,
    )


def range_response(
    request: Request, store, key: str, total: int | None, etag: str, cache_control: str
) -> Response:
    """206 byte-range read of ``key`` (``Range: bytes=a-b`` required, capped
    at ``MAX_RANGE_BYTES``). ``total`` = the object size when known. Shared
    by the private proxy and the public share link (``/s/…``)."""
    m = _RANGE_RE.match(request.headers.get("range", "").strip())
    if not m:
        raise HTTPException(status_code=416, detail="A bytes=start-end Range is required")
    start, end = int(m.group(1)), int(m.group(2))
    if end < start or end - start + 1 > MAX_RANGE_BYTES:
        raise HTTPException(status_code=416, detail="Range too large")
    if total and start >= total:
        raise HTTPException(status_code=416, detail="Range not satisfiable")
    try:
        data = store.read(key, start, end)
    except Exception as exc:  # GCS 416 past EOF on a previous version, transient errors
        if "416" in str(exc) or "Range" in type(exc).__name__:
            raise HTTPException(status_code=416, detail="Range not satisfiable") from exc
        raise
    if data is None:
        raise HTTPException(status_code=404, detail="Not found")
    if not data:
        raise HTTPException(status_code=416, detail="Range not satisfiable")
    return Response(
        content=data,
        status_code=206,
        media_type="application/octet-stream",
        headers={
            "Content-Range": f"bytes {start}-{start + len(data) - 1}/{total or '*'}",
            "Accept-Ranges": "bytes",
            "Cache-Control": cache_control,
            "ETag": etag,
            # (GZipMiddleware leaves 206 partial responses alone; the tiles
            # inside a PMTiles are already gzip'd.)
        },
    )


@router.get("/me/heatmap/{version}.summary.json")
def my_heatmap_summary(
    version: str,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    _owned_version(db, current_user.user_id, version)
    db.close()
    store = mhf.get_store()
    data = store.read(mhf.summary_key(current_user.user_id, version)) if store else None
    if data is None:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(
        content=data,
        media_type="application/json",
        headers={
            "Content-Encoding": "gzip",
            "Cache-Control": _IMMUTABLE_PRIVATE,
            "ETag": f'"{version}"',
        },
    )


# ── Share link (owner side) ──────────────────────────────────────────────────

_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(:\d{1,5})?$")


def public_base_url(request: Request) -> str:
    """Absolute origin of THIS API as seen by the client — the share URLs
    (and the tile URL inside style.json) must be absolute for gpx.studio,
    OsmAnd, Locus. ``MY_HEATMAP_SHARE_BASE_URL`` overrides; otherwise the
    forwarded proto (Cloud Run terminates TLS) + Host, sanity-checked."""
    env = os.environ.get("MY_HEATMAP_SHARE_BASE_URL", "").strip()
    if env:
        return env.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if proto not in ("http", "https"):
        proto = request.url.scheme
    host = request.headers.get("host", "")
    if not _HOST_RE.match(host):
        host = request.url.netloc
    return f"{proto}://{host}"


@router.get("/me/heatmap/share")
def my_heatmap_share_status(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
) -> dict:
    """``{available, active, created_at?, ready?, activity_count?}`` — never
    the link itself: only the token's hash is stored."""
    response.headers["Cache-Control"] = "private, no-store"
    return share_svc.status(db, current_user.user_id)


@router.post("/me/heatmap/share")
@limiter.limit("10/minute", key_func=user_or_ip_key)
def my_heatmap_share_create(
    request: Request,
    response: Response,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """Create the share link, or regenerate it (the previous token dies).
    The clear token is in the returned URLs — shown this once only."""
    if not mhf.is_enabled_for(current_user.user_id):
        raise HTTPException(status_code=404, detail="Not found")
    token, created = share_svc.create_or_regenerate(db, current_user.user_id)
    log.info("my-heatmap share link (re)generated user=%s", current_user.user_id)
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "active": True,
        "created_at": created.isoformat(),
        "urls": share_svc.share_urls(public_base_url(request), token),
    }


@router.delete("/me/heatmap/share", status_code=204)
def my_heatmap_share_revoke(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    """Disable sharing: every URL of the link answers 404 from now on.
    Works with the flag off too (a user must always be able to revoke)."""
    if share_svc.revoke(db, current_user.user_id):
        log.info("my-heatmap share link revoked user=%s", current_user.user_id)
    return Response(status_code=204)


@internal_router.post("/my-heatmap/rebuild")
async def my_heatmap_rebuild(request: Request) -> dict:
    """Cloud Tasks → (re)build one user's file. Always 200 (a failure is
    recorded in ``last_error``; the manifest re-schedules on the next view —
    no hot retry loop of tippecanoe on a persistent error)."""
    verify_oidc_token(request, audience_env="INTERNAL_MY_HEATMAP_HANDLER_URL")
    try:
        body = await request.json()
    except ValueError:
        body = {}
    user_id = str((body or {}).get("user_id") or "")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")
    if not mhf.is_enabled_for(user_id):
        return {"status": "disabled"}
    from starlette.concurrency import run_in_threadpool

    return await run_in_threadpool(mhf.build_for_user, user_id)
