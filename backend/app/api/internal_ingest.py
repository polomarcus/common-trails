"""Internal ingestion endpoints — invoked by Cloud Tasks, not end users.

These endpoints exist so the synchronous user-facing upload paths
(``/gpx/upload``, ``/imports/files``) can return immediately with a 202
"queued for processing" response while the heavy heat-edge computation
(5–30 s) happens off the request thread.

Auth model:
- Cloud Tasks signs each request with an OIDC token whose audience equals
  the handler URL. We verify the token using ``google-auth`` against the
  service-account email configured in ``CLOUD_TASKS_INVOKER_SA``.
- In TEST_MODE we skip verification entirely so the dev loop / unit tests
  can hit ``/internal/ingest/heat`` directly.
- Anything else (no token, wrong audience, wrong signer, expired) → 401.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

router = APIRouter(prefix="/internal", tags=["internal"])
log = logging.getLogger(__name__)


class HeatComputeRequest(BaseModel):
    activity_id: str
    user_id: str


class HeatComputeResult(BaseModel):
    activity_id: str
    status: str  # "ok" | "noop" | "missing"
    edges_indexed: int = 0
    cells_indexed: int = 0


def _is_test_mode() -> bool:
    return os.environ.get("TEST_MODE", "false").lower() == "true"


def _verify_oidc_token(request: Request) -> None:
    """Wrapper for legacy callers — uses INTERNAL_HEAT_HANDLER_URL audience."""
    verify_oidc_token(request, audience_env="INTERNAL_HEAT_HANDLER_URL")


def verify_oidc_token(request: Request, audience_env: str) -> None:
    """Verify the request was signed by Cloud Tasks.

    Cloud Tasks → ``Authorization: Bearer <oidc_jwt>`` with::

        iss = https://accounts.google.com
        email = CLOUD_TASKS_INVOKER_SA
        aud = $audience_env  (the configured handler URL)

    Each ``/internal/<thing>`` endpoint passes its own
    ``audience_env`` (e.g. ``INTERNAL_ARTEFACT_HANDLER_URL``) so token
    leakage between handlers is impossible — a token signed for the
    ingest-heat URL won't pass for the artefact-rebuild URL.

    The ``id_token.verify_oauth2_token`` helper checks the signature
    against Google's public keys, the expiry, the issuer, and the
    audience.
    """
    if _is_test_mode():
        return  # dev/test escape hatch — the endpoint can be hit directly

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = auth_header.split(" ", 1)[1].strip()

    expected_audience = os.environ.get(audience_env, "")
    expected_email = os.environ.get("CLOUD_TASKS_INVOKER_SA", "")
    if not expected_audience or not expected_email:
        # Mis-configured production: refuse rather than accept anything.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal endpoint {audience_env} not configured for OIDC verification",
        )

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError as exc:  # pragma: no cover — prod has google-auth
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="google-auth not installed; cannot verify OIDC token",
        ) from exc

    try:
        claims = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=expected_audience,
        )
    except Exception as exc:  # broad: any verification failure → 401
        log.warning("OIDC verification failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OIDC token") from exc

    email = claims.get("email", "")
    if email != expected_email:
        log.warning("OIDC token email mismatch: got %s expected %s", email, expected_email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token signer not authorised",
        )


def process_heat_compute(activity_id: str, user_id: str) -> dict[str, Any]:
    """Compute heat cells + edges for a single previously-stored activity.

    Idempotent: re-runs are no-ops because ``_update_heat_edges`` does
    ``ON CONFLICT (edge_key, sport) DO UPDATE`` and the contributors
    table has a primary key on ``(edge_key, user_id_hash)`` — same user +
    same trace → same hash → UPSERT collapses to UPDATE, ``user_count``
    stays correct.

    Returns a dict with status / counts; intentionally not raising on a
    missing activity (Cloud Tasks shouldn't retry forever on a stale
    payload).
    """
    from app.db.models import Activity
    from app.db.session import SessionLocal
    from app.services import ingest as ingest_service

    db = SessionLocal()
    try:
        activity = db.query(Activity).filter(
            Activity.id == activity_id,
            Activity.user_id == user_id,
        ).first()
        if not activity:
            log.warning("Heat compute: activity %s not found for user %s", activity_id, user_id)
            return {"activity_id": activity_id, "status": "missing", "edges_indexed": 0, "cells_indexed": 0}

        if not activity.contribute_heatmap:
            return {"activity_id": activity_id, "status": "noop", "edges_indexed": 0, "cells_indexed": 0}

        # PROVENANCE GATE (②): only community-eligible rides (source ==
        # "manual_upload") contribute to the community heat_edges. Strava-API
        # syncs (strava_api) + legacy NULL rows are personal-only under
        # Strava's 2026 API Policy — shown from `activities`, never here.
        # This is THE prod chokepoint (webhook + GPX-upload heat tasks both
        # flow through it). See app/services/provenance.py.
        from app.services.provenance import is_community_source
        if not is_community_source(activity.source):
            return {"activity_id": activity_id, "status": "noop", "edges_indexed": 0, "cells_indexed": 0}

        sport = activity.sport or "road"
        geometry_geojson = activity.geometry_geojson
        activity_date = activity.activity_date
    finally:
        db.close()

    # heat_cells write path removed in PR #214 — cells are aggregated from
    # heat_edges at query time. cells_indexed is always 0 now and is kept
    # in the response purely so old clients don't break on the missing key.
    #
    # RAW-TRACE DISPLAY (pivot 2026-07-29): under HEATMAP_DISPLAY_SOURCE=raw the
    # community map renders precise GPS traces straight from `activities`, so the
    # OSM-matched heat_edges write is pointless AND its substrate
    # (osm_road_edges/osm_ways) is DROPPED in prod. Skip the matched compute — but
    # still enqueue the artefact rebuild below so the raw PMTiles picks up the new
    # trace. This is the prod async chokepoint (single-GPX upload + /imports/files
    # async both flow here); the inline path is gated in ingest.ingest_activity.
    from app.services.raw_trace_display import raw_display_enabled
    edges_indexed = 0
    if geometry_geojson and not raw_display_enabled():
        edges_indexed, _ = ingest_service._update_heat_edges(
            user_id, sport, geometry_geojson,
            activity_date=activity_date, activity_id=str(activity_id),
        )

    # Post-ingest artefact rebuild (event-driven, see migration-runbook.md
    # § "Heatmap artefact freshness"). In prod with Cloud Tasks configured,
    # enqueues a debounced artefact-rebuild task with a const name so a burst
    # of ingests collapses to one rebuild. In dev/local, the threading.Timer
    # pattern in ingest._schedule_pmtiles_rebuild already fired during
    # ``_update_heat_edges``. (The matview-refresh enqueue was removed in
    # June 2026 when heat_edges_display was dropped.)
    try:
        from app.services.cloud_tasks import enqueue_artefact_rebuild
        enqueue_artefact_rebuild()
    except Exception:
        log.warning(
            "Cloud Tasks enqueue failed for post-ingest rebuild (activity=%s) — "
            "the threading.Timer fallback in ingest.py will still fire locally",
            activity_id, exc_info=True,
        )

    log.info(
        "Heat compute done activity=%s user=%s edges=%d",
        activity_id, user_id, edges_indexed,
    )
    return {
        "activity_id": activity_id,
        "status": "ok",
        "edges_indexed": edges_indexed,
        "cells_indexed": 0,
    }


@router.post("/ingest/heat", response_model=HeatComputeResult)
async def ingest_heat(request: Request, body: HeatComputeRequest) -> HeatComputeResult:
    """Cloud Tasks → process a single activity's heatmap contribution.

    Returns 200 on success (including idempotent no-ops) so Cloud Tasks
    stops retrying. Raises 5xx for transient failures (e.g. DB down) so
    Cloud Tasks does retry. 4xx for auth failures or malformed bodies →
    Cloud Tasks gives up.
    """
    _verify_oidc_token(request)

    try:
        result = process_heat_compute(body.activity_id, body.user_id)
    except Exception as exc:
        # Genuine transient failure → 5xx so Cloud Tasks retries.
        log.exception("Heat compute crashed for activity=%s", body.activity_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return HeatComputeResult(**result)
