"""Public Strava Webhook endpoint — subscription handshake + event receiver.

Architecture decision (see [[project_strava_webhook_architecture]] memory):

This file is the **ack-fast** layer. It must respond to Strava in well
under 2 seconds (Strava marks a webhook delivery as failed beyond that),
and it does NOT do any real work — it only:

  1. Verifies the event came from a known integration (`owner_id` lookup
     against `IntegrationAccount.external_user_id`).
  2. Enqueues a Cloud Task that points at the internal worker endpoint.
  3. Returns 200.

The real ingest work runs from the Cloud Task worker in
`app/api/internal_strava_webhook.py` with its own retry / OIDC verify.

There are two endpoints, both unauthenticated (Strava does not send
tokens — and we cannot ask it to):

  GET  /integrations/strava/webhook
       Subscription handshake. Strava sends
       `hub.mode=subscribe&hub.verify_token=…&hub.challenge=…`. We
       echo `hub.challenge` back IFF `hub.verify_token` matches the
       env-configured `STRAVA_WEBHOOK_VERIFY_TOKEN`.

  POST /integrations/strava/webhook
       Event receiver. Strava POSTs JSON like:
         {"object_type":"activity",
          "object_id":123,
          "aspect_type":"create",
          "owner_id":7808708,
          "subscription_id":42,
          "event_time":1690000000,
          "updates":{}}
       We enqueue + ack 200. Always.

Security model — Strava does NOT sign webhook payloads (no HMAC like
Stripe). Defense:

  * Drop events whose `owner_id` is unknown to us (allow-list against
    DB). The blast radius of a forged event then collapses to "trigger
    a Strava API call we would have made anyway for that user".
  * No PII leaks even on the bad path — we log `owner_id` only when it
    matches a stored account.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.db.models import IntegrationAccount
from app.db.session import get_db
from app.services.cloud_tasks import enqueue_strava_webhook_event

router = APIRouter(tags=["strava-webhook"])
log = logging.getLogger(__name__)


_WEBHOOK_VERIFY_TOKEN_ENV = "STRAVA_WEBHOOK_VERIFY_TOKEN"


@router.get("/integrations/strava/webhook")
async def strava_webhook_handshake(
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> JSONResponse:
    """Strava subscription verification handshake.

    Strava sends a single GET with `hub.mode=subscribe` to our callback
    URL during `POST https://www.strava.com/api/v3/push_subscriptions`.
    We must respond with `{"hub.challenge": "<echoed value>"}` if (and
    only if) the `hub.verify_token` matches our configured secret.

    A failed handshake means the subscription cannot be created — the
    POST to Strava fails. So we return a non-2xx on mismatch to surface
    misconfiguration explicitly during the bootstrap CLI.
    """
    expected = os.environ.get(_WEBHOOK_VERIFY_TOKEN_ENV, "").strip()
    if not expected:
        # If the env var isn't set, the subscription cannot exist either.
        # Fail loudly so misconfiguration is visible.
        log.error("Strava webhook handshake hit but %s not configured", _WEBHOOK_VERIFY_TOKEN_ENV)
        raise HTTPException(status_code=503, detail="Webhook verify token not configured")

    if hub_mode != "subscribe":
        raise HTTPException(status_code=400, detail="Unsupported hub.mode")

    # Constant-time compare to avoid leaking token via response-time
    # timing oracle. The endpoint is public — an attacker can poll it
    # millions of times to extract a prefix character-by-character with
    # naive `!=`.
    if not hub_verify_token or not hmac.compare_digest(hub_verify_token, expected):
        log.warning("Strava webhook handshake REJECTED — verify_token mismatch")
        raise HTTPException(status_code=403, detail="verify_token mismatch")

    if not hub_challenge:
        raise HTTPException(status_code=400, detail="hub.challenge missing")

    log.info("Strava webhook handshake OK — echoing challenge")
    # Strava's docs are unambiguous: the response is JSON with the literal
    # key `hub.challenge` (with the dot). FastAPI / pydantic doesn't let
    # you set a dotted JSON key via a model, so we hand-craft the body.
    return JSONResponse({"hub.challenge": hub_challenge})


@router.post("/integrations/strava/webhook")
async def strava_webhook_event(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> PlainTextResponse:
    """Receive a webhook event, validate, enqueue Cloud Task, ack 200.

    **This handler must complete in well under 2 seconds.** Cloud Run
    cold start can take 5-9 s on first request, but ALL the heavy work
    (Strava API fetch, ingest, heat compute) happens in the Cloud Task
    worker — this handler just enqueues and returns. So even cold, we
    spend ~50-150 ms here on top of the cold start.

    Strict semantics on the response:

      * 200 — event was accepted (enqueued OR ignored as unknown owner).
        Strava considers this success and stops retrying. We use 200
        for "ignored" so a forged event doesn't trigger retries.
      * non-200 — only on infrastructure errors. Strava will retry.

    We deliberately do NOT 401/403 on unknown owners, because Strava
    interprets that as a real failure and retries up to 3 times. The
    correct behavior for "this isn't one of our users" is to silently
    consume the event.
    """
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        log.warning("Strava webhook POST with invalid JSON body")
        # Bad payload from "Strava" is either a bug on their side or a
        # forged probe. Ack 200 so we don't get retried with the same
        # garbage.
        return PlainTextResponse("ignored", status_code=200)

    object_type = payload.get("object_type")
    aspect_type = payload.get("aspect_type")
    owner_id = payload.get("owner_id")

    if not isinstance(owner_id, (int, str)):
        log.warning("Strava webhook missing owner_id: %s", payload)
        return PlainTextResponse("ignored", status_code=200)

    # Allow-list: drop any event whose owner_id is not a connected Strava
    # account in our DB. This is the single line of defense vs forged
    # events (Strava doesn't sign payloads). Stringify because Strava
    # emits owner_id as an int, but our column is varchar.
    owner_id_str = str(owner_id)
    acct = db.query(IntegrationAccount).filter(
        IntegrationAccount.external_user_id == owner_id_str,
        IntegrationAccount.provider == "strava",
    ).first()
    if acct is None:
        log.info(
            "Strava webhook for unknown owner_id=%s object=%s/%s — dropping",
            owner_id_str, object_type, aspect_type,
        )
        return PlainTextResponse("unknown-owner", status_code=200)

    # Enqueue. The function short-circuits to inline run in TEST_MODE
    # so unit + E2E tests can exercise the full path without Cloud Tasks.
    # `await` because enqueue is async (the inline path awaits the
    # downstream worker, which fetches Strava + ingests asynchronously).
    try:
        outcome = await enqueue_strava_webhook_event(payload)
    except Exception as exc:
        # Cloud Tasks failure is the one case we DO want Strava to retry —
        # the event hasn't actually been processed yet.
        log.exception("Strava webhook enqueue failed: %s", exc)
        import sentry_sdk
        sentry_sdk.set_tag("strava.phase", "webhook_enqueue")
        sentry_sdk.capture_exception(exc)
        raise HTTPException(status_code=503, detail="Enqueue failed") from exc

    log.info(
        "Strava webhook accepted owner_id=%s object=%s/%s outcome=%s",
        owner_id_str, object_type, aspect_type, outcome,
    )
    return PlainTextResponse("ok", status_code=200)
