"""Private per-user "Ma heatmap" file — manifest + authenticated Range proxy.

- ``GET /me/heatmap/manifest`` — ``{status: ready|stale|missing|disabled, …}``.
  The frontend only uses the file when ``ready``; otherwise it falls back to
  ``GET /me/activities``. A ``stale``/``missing`` answer re-schedules a
  (debounced) rebuild — self-heal for any write path that forgot to.
- ``GET /me/heatmap/{version}.pmtiles`` — byte-range proxy to the PRIVATE
  bucket (``Range`` required, capped). ``/me/heatmap/{version}.summary.json``
  — the geometry-free sidebar list (stored gzip).
- ``POST /internal/my-heatmap/rebuild`` — Cloud Tasks → worker, OIDC.

PRIVACY: the object key is built from the AUTHENTICATED user id, never from
the URL, and the requested version must be that user's current or previous
one — another user's version is a 404 (no existence leak). Responses are
``Cache-Control: private`` (immutable per version, so a reload of /map is
served from the browser cache). Design: docs/ma-heatmap-fichier-prive.md.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_current_user
from app.api.internal_ingest import verify_oidc_token
from app.db.session import get_db
from app.services import my_heatmap_files as mhf

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
    m = _RANGE_RE.match(request.headers.get("range", "").strip())
    if not m:
        raise HTTPException(status_code=416, detail="A bytes=start-end Range is required")
    start, end = int(m.group(1)), int(m.group(2))
    if end < start or end - start + 1 > MAX_RANGE_BYTES:
        raise HTTPException(status_code=416, detail="Range too large")
    store = mhf.get_store()
    if store is None:
        raise HTTPException(status_code=404, detail="Not found")
    if state.pmtiles_bytes and version == state.version and start >= state.pmtiles_bytes:
        raise HTTPException(status_code=416, detail="Range not satisfiable")
    try:
        data = store.read(mhf.pmtiles_key(current_user.user_id, version), start, end)
    except Exception as exc:  # GCS 416 past EOF on a previous version, transient errors
        if "416" in str(exc) or "Range" in type(exc).__name__:
            raise HTTPException(status_code=416, detail="Range not satisfiable") from exc
        raise
    if data is None:
        raise HTTPException(status_code=404, detail="Not found")
    if not data:
        raise HTTPException(status_code=416, detail="Range not satisfiable")
    total = str(state.pmtiles_bytes) if version == state.version and state.pmtiles_bytes else "*"
    return Response(
        content=data,
        status_code=206,
        media_type="application/octet-stream",
        headers={
            "Content-Range": f"bytes {start}-{start + len(data) - 1}/{total}",
            "Accept-Ranges": "bytes",
            "Cache-Control": _IMMUTABLE_PRIVATE,
            "ETag": f'"{version}"',
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
