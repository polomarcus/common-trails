"""Internal Strava Webhook worker — Cloud Tasks → ingest.

The public `/integrations/strava/webhook` endpoint enqueues each event
here. This module does the real work:

  * fetch the activity from Strava (may trigger token refresh)
  * skip if `private == True` (heatmap pollution defense; see
    [[feedback_skip_beats_pollute_heatmap]])
  * ingest via the same pipeline as the user-triggered import
  * for `delete` events, soft-delete from our DB
  * for athlete deauthorize events, cleanup the IntegrationAccount

Auth: Cloud Tasks signs each request with an OIDC token. We verify
against `INTERNAL_STRAVA_WEBHOOK_HANDLER_URL` as audience, same
pattern as the other `/internal/*` endpoints.

Idempotency: receiving the same event twice (Strava retry, or
out-of-order create+update where the update arrives first) MUST not
double-ingest. Dedup actually fires on the unique constraint
`(user_id, provider, provider_activity_id)` in the `activities`
table — `ingest_activities_bulk` pre-loads existing provider IDs
into a set and `ON CONFLICT` shields the INSERT (`ingest.py`). We
re-fetch and re-ingest; the second insert is a no-op. NOTE: an
earlier version of this docstring claimed `file_hash + activity_date`
— that's the *file-upload* dedup path, not the Strava one. The
webhook never sets `file_hash`.

Privacy: activities marked `private == true` are dropped. Surface
this as a clear policy on the consent screen before user connects.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.internal_ingest import verify_oidc_token

router = APIRouter(prefix="/internal/strava", tags=["internal"])
log = logging.getLogger(__name__)


def _is_test_mode() -> bool:
    return os.environ.get("TEST_MODE", "false").lower() == "true"


@router.post("/webhook-event")
async def strava_webhook_worker(request: Request) -> dict[str, Any]:
    """Cloud Task → process a single Strava webhook event.

    Returns 200 on success (including idempotent no-ops + skip cases).
    Returns 5xx only on genuine transient failures (Strava API down,
    DB unavailable) so Cloud Tasks retries.
    """
    if not _is_test_mode():
        verify_oidc_token(request, audience_env="INTERNAL_STRAVA_WEBHOOK_HANDLER_URL")

    try:
        payload = await request.json()
    except Exception as exc:
        log.exception("Strava webhook worker: invalid JSON")
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    # Subscription-id allow-list — see [[project_strava_webhook_architecture]].
    # When STRAVA_WEBHOOK_SUBSCRIPTION_ID is set (populated post-bootstrap),
    # reject events from other subscription ids. Public handler can't do this
    # check (we want it ack-fast and stateless) so it lives here, in the
    # OIDC-protected worker. Mismatches return 200 to consume the task — we
    # don't want Cloud Tasks retrying a forged event indefinitely.
    expected_sub_id = os.environ.get("STRAVA_WEBHOOK_SUBSCRIPTION_ID", "").strip()
    if expected_sub_id:
        received_sub_id = str(payload.get("subscription_id", "")).strip()
        if received_sub_id != expected_sub_id:
            log.warning(
                "Strava webhook subscription_id mismatch expected=%s got=%s — dropping",
                expected_sub_id, received_sub_id,
            )
            return {"status": "skipped", "reason": "subscription_id mismatch"}

    try:
        return await process_strava_webhook_event(payload)
    except Exception as exc:
        log.exception("Strava webhook worker crashed payload=%s", payload)
        import sentry_sdk
        sentry_sdk.set_tag("strava.phase", "webhook_worker")
        sentry_sdk.set_tag("strava.aspect_type", str(payload.get("aspect_type")))
        sentry_sdk.set_tag("strava.object_type", str(payload.get("object_type")))
        sentry_sdk.capture_exception(exc)
        # 5xx → Cloud Tasks will retry with exponential backoff.
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def process_strava_webhook_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Pure (testable) entry point for a webhook event.

    Called both by the OIDC-protected HTTP handler above AND directly
    by the inline-run path in `cloud_tasks.enqueue_strava_webhook_event`
    when Cloud Tasks isn't configured (TEST_MODE / dev / unit tests).
    Returns a structured outcome dict for logging + tests.

    Async so callers in async context (the FastAPI public handler) can
    `await` directly — `asyncio.run()` from inside a running loop raises.
    """
    object_type = payload.get("object_type")
    aspect_type = payload.get("aspect_type")
    owner_id = str(payload.get("owner_id", ""))
    object_id = payload.get("object_id")

    if not owner_id:
        return {"status": "skipped", "reason": "missing owner_id"}

    if object_type == "athlete":
        return _handle_athlete_event(owner_id, aspect_type, payload.get("updates") or {})

    if object_type == "activity":
        return await _handle_activity_event(owner_id, aspect_type, object_id)

    log.info("Strava webhook: ignoring unknown object_type=%s", object_type)
    return {"status": "skipped", "reason": f"unknown object_type {object_type}"}


def _handle_athlete_event(
    owner_id: str, aspect_type: str | None, updates: dict[str, Any],
) -> dict[str, Any]:
    """Athlete-level event — currently only the deauthorize case matters.

    Strava sends ``object_type=athlete, aspect_type=update,
    updates={"authorized": "false"}`` when a user revokes our app's
    access from Strava's connected-apps dashboard. We tear down the
    IntegrationAccount so the next Strava-touching call won't try to
    use a now-revoked token.

    We do NOT delete the user's activities — they imported them
    voluntarily and the disconnect doesn't retract that consent. The
    UI can offer a separate "delete imported activities" action.
    """
    if updates.get("authorized") != "false":
        return {"status": "skipped", "reason": "athlete event but not deauthorize"}

    from app.db.models import IntegrationAccount
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        acct = db.query(IntegrationAccount).filter(
            IntegrationAccount.external_user_id == owner_id,
            IntegrationAccount.provider == "strava",
        ).first()
        if not acct:
            return {"status": "skipped", "reason": "athlete deauthorize but no account on record"}

        user_id = acct.user_id
        db.delete(acct)
        db.commit()
        log.warning(
            "Strava deauthorize processed: user_id=%s owner_id=%s account removed",
            user_id, owner_id,
        )
        return {"status": "deauthorized", "user_id": user_id, "owner_id": owner_id}
    finally:
        db.close()


async def _handle_activity_event(
    owner_id: str, aspect_type: str | None, object_id: Any,
) -> dict[str, Any]:
    """Activity-level event — create / update / delete.

    For `delete`, soft-delete the matching row in our `activities`
    table (cascade to `heat_edge_contributors` already in place via
    PR #318's trigger).

    For `create` / `update`, fetch the activity from Strava and ingest
    via the existing pipeline. Idempotent via `file_hash` +
    `activity_date` composite index — same activity re-ingested is a
    no-op.

    `update` events on Strava only fire for title / type / privacy
    changes (NOT stream data), so re-fetching + re-ingesting is the
    safe path: it picks up the new type / honors a new privacy flag.
    """
    if object_id is None:
        return {"status": "skipped", "reason": "missing object_id"}

    if aspect_type == "delete":
        return _delete_activity_for_strava(owner_id, str(object_id))

    if aspect_type in ("create", "update"):
        return await _ingest_strava_activity(owner_id, int(object_id))

    log.info("Strava webhook: ignoring unknown aspect_type=%s", aspect_type)
    return {"status": "skipped", "reason": f"unknown aspect_type {aspect_type}"}


def _delete_activity_for_strava(owner_id: str, strava_activity_id: str) -> dict[str, Any]:
    """Soft-delete the activity matching this Strava ID for this owner.

    No-op if we don't have the activity on record (e.g. user deleted
    on Strava something they never imported — possible if they
    connected, deleted, then a stale event delivers).
    """
    from app.db.models import Activity, IntegrationAccount
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        acct = db.query(IntegrationAccount).filter(
            IntegrationAccount.external_user_id == owner_id,
            IntegrationAccount.provider == "strava",
        ).first()
        if not acct:
            return {"status": "skipped", "reason": "delete event for unknown owner"}

        activity = db.query(Activity).filter(
            Activity.user_id == acct.user_id,
            Activity.provider == "strava",
            Activity.provider_activity_id == strava_activity_id,
        ).first()
        if not activity:
            return {"status": "skipped", "reason": "activity not in our DB"}

        # Cascade to heat_edge_contributors via PR #318 trigger.
        db.delete(activity)
        db.commit()
        log.info(
            "Strava webhook delete processed user_id=%s strava_id=%s",
            acct.user_id, strava_activity_id,
        )
        return {"status": "deleted", "strava_activity_id": strava_activity_id}
    finally:
        db.close()


async def _ingest_strava_activity(owner_id: str, strava_activity_id: int) -> dict[str, Any]:
    """Fetch the activity from Strava and ingest it.

    Hot path for `create` (most events) and `update` (rare). Idempotent
    via the existing dedup pipeline (`file_hash` + `activity_date`
    composite index).

    Privacy gate: if Strava returns `private == True`, log + skip. This
    honors the user's explicit privacy choice on the activity even
    though they opted into our webhook subscription at the account
    level.

    TEST_MODE: returns a stub status without hitting Strava or the DB,
    so the public webhook handler can be exercised end-to-end without
    network.

    Async because the FastAPI public POST handler may invoke this
    inline (when Cloud Tasks isn't configured), and `asyncio.run()`
    from inside a running event loop raises `RuntimeError`. Worker
    handler is already async so awaiting costs nothing.
    """
    if _is_test_mode():
        return {
            "status": "ingested",
            "owner_id": owner_id,
            "strava_activity_id": strava_activity_id,
            "stub": True,
        }

    import json

    import httpx

    from app.api.integrations_strava import refresh_strava_token
    from app.db.models import IntegrationAccount
    from app.db.session import SessionLocal
    from app.services.ingest import ingest_activities_bulk
    from app.services.provenance import STRAVA_API_SOURCE
    from app.services.strava_client import STRAVA_API_BASE, get_http_client
    from app.services.strava_utils import classify_strava_sport_or_skip, decode_polyline

    db = SessionLocal()
    try:
        acct = db.query(IntegrationAccount).filter(
            IntegrationAccount.external_user_id == owner_id,
            IntegrationAccount.provider == "strava",
        ).first()
        if not acct:
            return {"status": "skipped", "reason": "unknown owner"}

        user_id = acct.user_id

        # refresh_strava_token returns the *plaintext* token (Fernet
        # decrypt) when the stored access_token is still valid OR when
        # it just refreshed. Returns None if refresh failed (token
        # revoked, network error). Caller treats None as transient
        # failure so Cloud Tasks retries.
        access_token = await refresh_strava_token(acct, db)
        if not access_token:
            return {"status": "failed", "reason": "token refresh failed"}

        client = await get_http_client()
        try:
            resp = await client.get(
                f"{STRAVA_API_BASE}/activities/{strava_activity_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15.0,
            )
            resp.raise_for_status()
            activity = resp.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 404:
                # Activity deleted on Strava between create webhook and
                # our fetch. Silent drop.
                return {"status": "skipped", "reason": "activity deleted before fetch"}
            if status in (401, 403):
                # 401 = token revoked between refresh + fetch (rare race);
                # 403 = activity private OR our app deauthorized.
                # Either way, retrying won't help — drop, don't 500.
                #
                # Surface to Sentry: a deauthorized user keeps firing
                # webhook events Strava-side until they (or we) hit the
                # `/oauth/deauthorize` endpoint. Silent drops would
                # hide a meaningful signal — every recurring 401 means
                # an out-of-sync account that needs cleanup.
                log.warning(
                    "Strava activity fetch %s for user=%s strava_id=%s — dropping",
                    status, user_id, strava_activity_id,
                )
                import sentry_sdk
                sentry_sdk.set_tag("strava.phase", "webhook_fetch")
                sentry_sdk.set_tag("strava.deauth_detected", True)
                sentry_sdk.set_tag("strava.http_status", str(status))
                sentry_sdk.capture_message(
                    f"Strava webhook fetch dropped {status} (user={user_id})",
                    level="warning",
                )
                return {"status": "skipped", "reason": f"strava-{status}"}
            # 5xx + other 4xx are transient — let Cloud Tasks retry.
            raise

        if activity.get("private") is True:
            log.info(
                "Strava webhook: skipping private activity strava_id=%s owner=%s",
                strava_activity_id, owner_id,
            )
            return {"status": "skipped", "reason": "activity is private"}

        # Build the same activity_data shape as _run_strava_import's
        # Phase 2 (see L557 in integrations_strava.py). Single-item
        # ingest_activities_bulk call — idempotent via composite index.
        geometry_geojson: str | None = None
        polyline = activity.get("map", {}).get("summary_polyline") or ""
        if polyline:
            coords = decode_polyline(polyline)
            if len(coords) >= 2:
                geometry_geojson = json.dumps({
                    "type": "LineString",
                    "coordinates": coords,
                })

        sport_type = activity.get("sport_type") or activity.get("type", "")
        # Pass the activity NAME so a generic "Ride" named "Gravel ..." /
        # "VTT ..." classifies as gravel/mtb (SSOT #430 refinement),
        # consistent with the CLI + UI-upload CSV paths.
        sport = classify_strava_sport_or_skip(sport_type, activity.get("name"))
        if sport is None:
            # Out-of-scope (Yoga / Workout / Virtual / Swim / Ski /
            # unknown future type). Skip rather than fall back to a
            # default — falling back pollutes the heatmap with non-
            # cycling/running coords. See [[feedback_skip_beats_pollute_heatmap]].
            log.info(
                "Strava webhook: skipping out-of-scope sport_type=%s strava_id=%s",
                sport_type, strava_activity_id,
            )
            return {
                "status": "skipped",
                "reason": f"out-of-scope sport_type={sport_type!r}",
            }

        # Heatmap-contribution preference lives on the IntegrationAccount
        # row (PR #348 / audit S2.7). The user's choice from the import
        # consent screen was written to `acct.contribute_heatmap` when
        # they triggered the import; webhook events read it straight off
        # the row we already loaded above. Default False on accounts
        # that connected but never ran an import — opting in must be
        # an active choice. Migration 0048 backfilled existing accounts
        # from their most-recent Activity preference so behaviour is
        # preserved.
        contribute_heatmap = bool(acct.contribute_heatmap)

        activity_data = {
            "provider": "strava",
            "provider_activity_id": str(activity["id"]),
            "sport": sport,
            "name": activity.get("name"),
            "geometry_geojson": geometry_geojson,
            "distance_m": activity.get("distance"),
            "elevation_gain_m": activity.get("total_elevation_gain"),
            "moving_time": activity.get("moving_time"),
            "total_photo_count": activity.get("total_photo_count", 0),
            "activity_date": activity.get("start_date_local") or activity.get("start_date"),
        }

        result = ingest_activities_bulk(
            user_id=user_id,
            activities_data=[activity_data],
            contribute_heatmap=contribute_heatmap,
            # Strava-API provenance — personal-only (never community).
            source=STRAVA_API_SOURCE,
        )

        # Stamp last_synced_at + reset sync_failures so the daily
        # health-check cron sees this as a recent successful sync.
        # Pre-PR this was only written by the (decommissioned) monthly
        # resync — webhook-only accounts would have NULL forever and
        # the cron's sync_failures suppression never engaged. Audit
        # 2026-05-29 ST-S2.8.
        from app.api.integrations_strava import _mark_account_synced
        _mark_account_synced(user_id)

        # "Ride synced (personal view)" toast — ONLY on a genuinely-new row.
        # `result["created"]` is 0 on a dedup re-ingest (Strava retry /
        # out-of-order create+update), so the second delivery of the same
        # event emits nothing. Skipped sports never reach here (returned
        # above when classify → None). Fail-soft: emit_activity_synced
        # never raises, but wrap anyway so a notification hiccup can't
        # turn a successful ingest into a Cloud Tasks 5xx retry.
        if result["created"] >= 1:
            try:
                from app.services.notifications import emit_activity_synced
                emit_activity_synced(
                    user_id=user_id,
                    activity_name=activity.get("name"),
                    sport=sport,
                    provider_activity_id=str(activity["id"]),
                )
            except Exception:  # noqa: BLE001 — notification is best-effort
                log.warning(
                    "activity_synced notification failed user=%s strava_id=%s",
                    user_id, strava_activity_id, exc_info=True,
                )

        log.info(
            "Strava webhook ingest user_id=%s strava_id=%s sport=%s contribute=%s result=%s",
            user_id, strava_activity_id, sport, contribute_heatmap, result,
        )
        return {
            "status": "ingested",
            "user_id": user_id,
            "strava_activity_id": strava_activity_id,
            "contribute_heatmap": contribute_heatmap,
            "created": result["created"],
            "skipped": result["skipped"],
            "failed": result["failed"],
        }
    finally:
        db.close()
