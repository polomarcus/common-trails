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


def _relation_exists(db: Session, name: str) -> bool:
    """True when a base table ``name`` exists in the connected database.

    Under the raw-trace pivot (2026-07-29) the legacy community-heat read
    models (``heat_edges`` / ``heat_edges_agg``) are being DROPPED in prod —
    the community map is served by the static raw PMTiles, not these tables.
    ``/readyz`` gates the CD worker healthcheck, so a missing table must
    degrade to 0 / null and STILL return 200, never 503. Bound param, no user
    input (same posture as ``services/activity_deletion._relation_exists``).
    """
    return bool(
        db.execute(
            text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = :n)"
            ),
            {"n": name},
        ).scalar()
    )


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadyResponse(BaseModel):
    status: str
    version: str
    heat_edges: int
    # Display aggregate freshness — lets ops see at a glance that
    # heat_edges_agg is populated + how stale it is (powers a future
    # "pmtiles stale > N days" alert). See migration 0057.
    heat_edges_agg: int = 0
    heat_edges_agg_updated_at: str | None = None


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
    """Readiness probe — verifies DB connectivity and heatmap has data.

    Uses a bounded scan (LIMIT 1) instead of COUNT(*) — was 22s on 7M rows,
    now <5ms. The actual count is reported via pg_class.reltuples (estimate,
    sufficient for a readiness probe). Frontend ServerWakeupBanner aborts
    after 3s, so the COUNT version made the page show "Starting..." forever.

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

    # heat_edges data probe — TOLERANT of the table being ABSENT. Under the
    # raw-trace pivot heat_edges is dropped in prod (the community map is
    # served by the static raw PMTiles), so a missing table must NOT 503 this
    # probe — it gates the CD worker healthcheck. Distinguish "table gone"
    # (→ warming, 200) from a genuine stall under load (table present but the
    # query errors / times out → 503, preserving the 2026-07-20 fast-degrade).
    has_data = False
    if _relation_exists(db, "heat_edges"):
        try:
            has_data = db.execute(text(
                "SELECT EXISTS (SELECT 1 FROM heat_edges LIMIT 1)"
            )).scalar()
        except Exception:
            raise HTTPException(status_code=503, detail="DB unavailable") from None
    # Used to 503 when heat_edges was empty; that gated the frontend
    # ``ServerWakeupBanner`` forever on a fresh prod DB. Now we return
    # 200 with ``status='warming'`` so the banner dismisses while the
    # heatmap is still being populated. Real outages still return
    # 503 (DB unreachable above).
    # Display aggregate freshness (small table, exact COUNT + MAX are cheap).
    # Wrapped so a pre-0057 DB (no table) or an empty agg never 503s the probe.
    agg_count = 0
    agg_updated_at: str | None = None
    try:
        row = db.execute(text(
            "SELECT COUNT(*), MAX(updated_at) FROM heat_edges_agg"
        )).first()
        if row is not None:
            agg_count = int(row[0] or 0)
            agg_updated_at = row[1].isoformat() if row[1] is not None else None
    except Exception:
        # rollback ends the txn → SET LOCAL is gone; re-arm for the queries below.
        db.rollback()
        try:
            db.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
        except Exception:
            raise HTTPException(status_code=503, detail="DB unavailable") from None

    if not has_data:
        return ReadyResponse(
            status="warming", version=APP_VERSION, heat_edges=0,
            heat_edges_agg=agg_count, heat_edges_agg_updated_at=agg_updated_at,
        )

    # Approximate count from pg_class — sums reltuples across partitions
    try:
        estimate = db.execute(text(
            "SELECT COALESCE(SUM(GREATEST(reltuples, 0)), 0)::bigint FROM pg_class "
            "WHERE relkind = 'r' AND relname LIKE 'heat_edges_%' "
            "AND relname <> 'heat_edges_agg' "
            "AND relname NOT LIKE '%idx%' AND relname NOT LIKE '%key%'"
        )).scalar() or 0
    except Exception:
        raise HTTPException(status_code=503, detail="DB unavailable") from None

    return ReadyResponse(
        status="ok", version=APP_VERSION, heat_edges=int(estimate),
        heat_edges_agg=agg_count, heat_edges_agg_updated_at=agg_updated_at,
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
