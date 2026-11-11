"""Health check endpoints."""
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter(tags=["health"])

# Import version from main (single source of truth, shared with Sentry)
from app.main import _APP_VERSION as APP_VERSION


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadyResponse(BaseModel):
    status: str  # "ok" | "warming" (startup data load still running)
    version: str


@router.get("/healthz", response_model=HealthResponse)
@router.get("/health", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Liveness probe — always returns OK if the process is running.

    Mounted at both ``/healthz`` (k8s/Borg convention used by Cloud Run
    startup + liveness probes) AND ``/health`` (no-z alias for external
    monitoring). The aliasing exists because something at Google's edge
    in front of ``*.run.app`` returns its own 404 for ``/healthz``
    (no trailing slash) without ever reaching the container — exact
    cause unknown, observed and reproduced May 2026, see PR #222.
    ``/healthz/`` (with trailing slash) AND ``/health`` both work
    fine; the bare ``/healthz`` is the only failure case. Cloud Run's
    internal probes use the container port directly so they're
    unaffected.
    """
    return HealthResponse(status="ok", version=APP_VERSION)


@router.get("/readyz", response_model=ReadyResponse)
def readyz(db: Session = Depends(get_db)) -> ReadyResponse:
    """Readiness probe — the DB answers and the startup data load has finished.

    * DB unreachable / stalled → **503** (the only failure mode).
    * DB fine, startup load (``/startup-status``) still running → 200
      ``"warming"``.
    * DB fine, startup load done → 200 ``"ok"``.

    The community map is a static PMTiles artefact, so readiness no longer
    depends on any heatmap table (it used to require rows in the legacy
    ``heat_edges`` table and therefore reported ``"warming"`` forever in prod).

    Both 200 states carry ``version``: the CD healthcheck requires HTTP 200 +
    a ``"version"`` key, and the frontend ``ServerWakeupBanner`` only looks at
    ``res.ok``.

    Sync ``def`` (threadpool) + a short per-transaction ``statement_timeout``
    (env ``READYZ_STATEMENT_TIMEOUT_MS``, default 2 s): under a pegged DB
    (2026-07-20 drain incident) the probe must degrade FAST with a 503, never
    hang on the event loop queueing behind batch load.
    """
    timeout_ms = int(os.environ.get("READYZ_STATEMENT_TIMEOUT_MS", "2000"))
    try:
        db.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="DB unavailable") from None

    from app.main import startup_progress

    return ReadyResponse(
        status="ok" if startup_progress.get("done") else "warming",
        version=APP_VERSION,
    )


@router.get("/startup-status")
async def startup_status() -> dict:
    """Lightweight endpoint for frontend progress bar during cold start."""
    from app.main import startup_progress
    return startup_progress


@router.get("/cache-status")
async def cache_status() -> dict:
    """Check CDN cache freshness. Returns manifest version + age."""
    import json
    import os
    import time

    bucket_name = os.environ.get("FRONTEND_BUCKET", "")
    if not bucket_name:
        return {"status": "disabled", "reason": "FRONTEND_BUCKET not set"}

    try:
        # F4: reuse singleton from cache_writer
        from app.services.cache_writer import BUCKET_NAME, CACHE_PREFIX, _get_client
        client = _get_client()
        bucket = client.bucket(BUCKET_NAME)
        blob = bucket.blob(f"{CACHE_PREFIX}/manifest.json")
        if not blob.exists():
            return {"status": "no_cache", "manifest": None}
        manifest = json.loads(blob.download_as_string())
        age_s = time.time() - manifest.get("published_at", 0)
        return {
            "status": "stale" if age_s > 86400 else "ok",
            "version": manifest.get("version"),
            "age_seconds": int(age_s),
            "rollback": manifest.get("rollback", False),
        }
    except ImportError:
        return {"status": "disabled", "reason": "google-cloud-storage not installed"}
    except Exception:
        # F5: don't leak internal error details
        return {"status": "error", "error": "Failed to check cache status"}
