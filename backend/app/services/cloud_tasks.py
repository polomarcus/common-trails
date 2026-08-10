"""Cloud Tasks client — enqueue background jobs for the API service.

Used by the synchronous GPX upload path to defer heat-edge computation off
the user-facing HTTP request. Cloud Run charges for the full request
duration, so a 5–30 s heat compute on every upload is real money.

Behavior:
- TEST_MODE / no Cloud Tasks env config → execute the work synchronously
  (so dev + tests behave exactly like the legacy sync path).
- Production → POST a Cloud Task to the configured queue. The task body is
  the same shape the internal endpoint expects.

Env vars:
- ``CLOUD_TASKS_QUEUE`` — fully-qualified queue name
  (``projects/{p}/locations/{r}/queues/{q}``) **or** the bare queue id
  (in which case ``GOOGLE_CLOUD_PROJECT`` and ``CLOUD_TASKS_LOCATION``
  must be set so we can build the full path).
- ``INTERNAL_HEAT_HANDLER_URL`` — full HTTPS URL the task should hit, e.g.
  ``https://common-trails-api-prod-xxx.run.app/internal/ingest/heat``.
- ``CLOUD_TASKS_INVOKER_SA`` — service-account email used as the OIDC
  audience identity. Cloud Tasks signs the OIDC token; the API verifies
  the signature + audience on receipt.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _is_enabled() -> bool:
    """True iff Cloud Tasks is configured AND we are not in TEST_MODE."""
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return False
    if not os.environ.get("CLOUD_TASKS_QUEUE"):
        return False
    return bool(os.environ.get("INTERNAL_HEAT_HANDLER_URL"))


def _resolve_queue_path() -> str:
    """Return the fully-qualified queue path for the Cloud Tasks API.

    Accepts either a full ``projects/.../queues/...`` path or a bare queue
    id; in the latter case we reconstruct the path from
    ``GOOGLE_CLOUD_PROJECT`` and ``CLOUD_TASKS_LOCATION``.
    """
    raw = os.environ.get("CLOUD_TASKS_QUEUE", "").strip()
    if not raw:
        raise RuntimeError("CLOUD_TASKS_QUEUE not set")
    if raw.startswith("projects/"):
        return raw
    # Prefer GCP_PROJECT (the var the prod deploy actually sets) with a
    # GOOGLE_CLOUD_PROJECT fallback. deploy-prod.sh's env-file sets GCP_PROJECT
    # but NOT GOOGLE_CLOUD_PROJECT, so reading only the latter made every bare
    # queue path raise → Strava webhook enqueue 503 + artefact-rebuild "failed".
    # Same divergence already migrated in integrations_strava.py.
    project = (os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    location = os.environ.get("CLOUD_TASKS_LOCATION", "").strip()
    if not project or not location:
        raise RuntimeError(
            "CLOUD_TASKS_QUEUE is bare; set GCP_PROJECT and "
            "CLOUD_TASKS_LOCATION or pass a fully-qualified queue path"
        )
    return f"projects/{project}/locations/{location}/queues/{raw}"


def _enqueue_via_sdk(payload: dict[str, Any]) -> None:
    """Create a Cloud Task with an OIDC-authenticated HTTP target.

    Imports the SDK lazily so dev / test environments without the
    ``google-cloud-tasks`` extra don't fail to import this module.
    """
    from google.cloud import tasks_v2  # type: ignore[attr-defined]

    handler_url = os.environ["INTERNAL_HEAT_HANDLER_URL"]
    invoker_sa = os.environ.get("CLOUD_TASKS_INVOKER_SA", "").strip()
    queue_path = _resolve_queue_path()

    client = tasks_v2.CloudTasksClient()
    body = json.dumps(payload).encode("utf-8")

    task: dict[str, Any] = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": handler_url,
            "headers": {"Content-Type": "application/json"},
            "body": body,
        }
    }
    if invoker_sa:
        # OIDC token: Cloud Tasks signs as ``invoker_sa`` with audience =
        # the handler URL. The receiver verifies signature + audience.
        task["http_request"]["oidc_token"] = {
            "service_account_email": invoker_sa,
            "audience": handler_url,
        }

    client.create_task(request={"parent": queue_path, "task": task})


def _run_inline(payload: dict[str, Any]) -> None:
    """Execute the heat-compute work in-process (TEST_MODE / dev fallback).

    Mirrors the real handler so the dev path is identical end-to-end.
    """
    from app.api.internal_ingest import process_heat_compute

    process_heat_compute(payload["activity_id"], payload["user_id"])


def enqueue_heat_compute(activity_id: str, user_id: str) -> str:
    """Enqueue (or run inline) a heat-edge computation task for an activity.

    Returns:
        ``"queued"`` if a Cloud Task was created, ``"inline"`` if executed
        synchronously (TEST_MODE or unconfigured), ``"failed"`` if the
        enqueue raised — the activity is still stored, the heatmap
        contribution is just deferred to the next manual rebuild.
    """
    payload = {"activity_id": activity_id, "user_id": user_id}

    if not _is_enabled():
        try:
            _run_inline(payload)
        except Exception:
            logger.warning(
                "Inline heat-compute failed for activity=%s user=%s",
                activity_id, user_id, exc_info=True,
            )
            return "failed"
        return "inline"

    try:
        _enqueue_via_sdk(payload)
        logger.info(
            "Cloud Tasks enqueue OK activity=%s user=%s",
            activity_id, user_id,
        )
        return "queued"
    except Exception:
        logger.warning(
            "Cloud Tasks enqueue FAILED activity=%s user=%s — "
            "activity stored, heat compute skipped",
            activity_id, user_id, exc_info=True,
        )
        return "failed"


# ── Matview + artefact rebuild dispatch ──────────────────────────────
#
# The post-ingest rebuilds (matview refresh, PMTiles rebuild, .fgraph
# rebuild) use the SAME enqueue pattern as heat-compute, with two key
# differences:
#
#   1. **Different queue + handler URL** per task type. The receiving
#      endpoint is on the same api-prod service; only the path differs.
#   2. **Constant ``task_name``** so Cloud Tasks dedupes new enqueues
#      onto the same task. A burst of 50 ingests in 5 min → exactly
#      ONE rebuild fires (after the schedule time of the first task).
#      This is the "no cron" event-driven dedup pattern documented in
#      ``docs/migration-runbook.md`` § "Heatmap artefact freshness".
#
# Failure mode: if Cloud Tasks enqueue raises (queue not yet
# terraformed, IAM glitch), we log + return without raising. The
# ingest itself still succeeded; the artefact rebuild is just delayed
# until the next ingest re-attempts. Better than failing a user's
# upload over a downstream rebuild glitch.
#
# Local dev (TEST_MODE or no queue env): both enqueue functions short-
# circuit to a no-op. The threading.Timer pattern in ingest.py runs
# the rebuild locally so dev gets the same end result.


def _enqueue_with_const_name(
    queue_env: str,
    handler_url_env: str,
    task_name: str,
    schedule_seconds: int,
    payload: dict[str, Any] | None = None,
) -> str:
    """Enqueue a Cloud Task with a constant task name (auto-dedup).

    Cloud Tasks rejects new tasks whose name matches an in-flight or
    recently-completed task. So enqueuing the SAME task name during
    the window collapses to one. Schedule the task ``schedule_seconds``
    in the future to give the dedup window a chance to coalesce a
    burst of ingests.

    Args:
        queue_env: env var holding the queue name (full path or bare id).
        handler_url_env: env var holding the handler URL.
        task_name: the constant name. The fully-qualified task name is
            ``{queue_path}/tasks/{task_name}``.
        schedule_seconds: seconds in the future. 0 = run as soon as
            possible. Use a positive value for the dedup window.
        payload: optional JSON body. Most artefact rebuilds need none —
            the handler reads current DB state.

    Returns:
        ``"queued"`` on success, ``"deduped"`` if Cloud Tasks rejected
        the enqueue because the name is in use (this is the success
        case for our pattern), ``"inline"`` if Cloud Tasks isn't
        configured (dev path; the threading.Timer in ingest.py handles
        the actual rebuild), ``"failed"`` on any other error.
    """
    if not _is_enabled_for_queue(queue_env, handler_url_env):
        return "inline"

    try:
        from google.api_core.exceptions import AlreadyExists
        from google.cloud import tasks_v2  # type: ignore[attr-defined]
    except ImportError:
        logger.warning("google-cloud-tasks not installed; skipping %s enqueue", task_name)
        return "inline"

    handler_url = os.environ[handler_url_env]
    invoker_sa = os.environ.get("CLOUD_TASKS_INVOKER_SA", "").strip()
    queue_path = _resolve_queue_path_for(queue_env)

    client = tasks_v2.CloudTasksClient()
    body = json.dumps(payload or {}).encode("utf-8")

    task: dict[str, Any] = {
        "name": f"{queue_path}/tasks/{task_name}",
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": handler_url,
            "headers": {"Content-Type": "application/json"},
            "body": body,
        },
    }
    if invoker_sa:
        task["http_request"]["oidc_token"] = {
            "service_account_email": invoker_sa,
            "audience": handler_url,
        }
    if schedule_seconds > 0:
        from datetime import UTC, datetime, timedelta

        from google.protobuf import timestamp_pb2  # type: ignore[attr-defined]
        ts = timestamp_pb2.Timestamp()
        ts.FromDatetime(datetime.now(UTC) + timedelta(seconds=schedule_seconds))
        task["schedule_time"] = ts

    try:
        client.create_task(request={"parent": queue_path, "task": task})
        logger.info("Cloud Tasks enqueue OK task=%s schedule=+%ds", task_name, schedule_seconds)
        return "queued"
    except AlreadyExists:
        logger.debug("Cloud Tasks dedup: %s already in flight", task_name)
        return "deduped"
    except Exception:
        logger.warning("Cloud Tasks enqueue FAILED task=%s", task_name, exc_info=True)
        return "failed"


def _is_enabled_for_queue(queue_env: str, handler_url_env: str) -> bool:
    """Same gate as ``_is_enabled`` but for an arbitrary queue+handler pair."""
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return False
    return bool(os.environ.get(queue_env)) and bool(os.environ.get(handler_url_env))


def _resolve_queue_path_for(queue_env: str) -> str:
    """Same as ``_resolve_queue_path`` but for an arbitrary queue env var."""
    raw = os.environ.get(queue_env, "").strip()
    if not raw:
        raise RuntimeError(f"{queue_env} not set")
    if raw.startswith("projects/"):
        return raw
    # GCP_PROJECT (set by the prod deploy) with a GOOGLE_CLOUD_PROJECT fallback —
    # see _resolve_queue_path above.
    project = (os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    location = os.environ.get("CLOUD_TASKS_LOCATION", "").strip()
    if not project or not location:
        raise RuntimeError(
            f"{queue_env} is bare; set GCP_PROJECT and "
            "CLOUD_TASKS_LOCATION or pass a fully-qualified queue path"
        )
    return f"projects/{project}/locations/{location}/queues/{raw}"


def enqueue_artefact_rebuild() -> str:
    """Enqueue a debounced PMTiles + .fgraph rebuild.

    Constant task name ``"artefact-rebuild"`` + 5 min schedule. A
    session of 50 uploads → one rebuild after the last upload. See
    ``docs/migration-runbook.md`` § "Heatmap artefact freshness".
    """
    return _enqueue_with_const_name(
        queue_env="CLOUD_TASKS_ARTEFACT_QUEUE",
        handler_url_env="INTERNAL_ARTEFACT_HANDLER_URL",
        task_name="artefact-rebuild",
        schedule_seconds=int(os.environ.get("ARTEFACT_REBUILD_SCHEDULE_S", "300")),
    )


# ── Strava webhook events ─────────────────────────────────────────────
#
# Each Strava push event (activity create/update/delete, athlete
# deauthorize) is enqueued as a separate Cloud Task so the public
# webhook endpoint ACKs in <100 ms and the heavy work (Strava API
# fetch + ingest) runs from the Cloud Task worker with proper retry +
# OIDC verification.
#
# Unlike matview/artefact, we do NOT dedup these by name — each event
# is unique (different object_id / aspect_type combinations) and must
# be processed independently. Per-task retries handle Strava 429 and
# transient ingest failures.


# ── Heatmap export build (Phase 3 — PRD #391) ────────────────────────
#
# Each /export/heatmap/request POST enqueues one Cloud Task referencing
# a row in the `export_requests` table. The worker
# (`POST /internal/export/build/{id}`) does the build + GCS upload.
#
# Unlike matview/artefact, NOT debounced by name — each request is a
# distinct build with different filter params. The request id is the
# natural per-task dedup key, so a Cloud Tasks retry collapses cleanly.

def enqueue_export_build(request_id: str) -> str:
    """Enqueue (or run inline) an export-build task for a request id.

    Returns "queued" if a Cloud Task was created, "inline" if executed
    synchronously (TEST_MODE or unconfigured), "failed" if the enqueue
    raised. Caller does NOT need to handle the inline-run failure — we
    already log + flip the export_requests row to 'failed' inside the
    worker.
    """
    queue_env = "CLOUD_TASKS_EXPORT_QUEUE"
    handler_url_env = "INTERNAL_EXPORT_BUILD_HANDLER_URL"

    if not _is_enabled_for_queue(queue_env, handler_url_env):
        # Inline run path — same module imported lazily to avoid a
        # circular import (cloud_tasks ↔ api package).
        try:
            from app.api.export import process_export_build
            process_export_build(request_id)
        except Exception:
            logger.warning(
                "Inline export build failed request_id=%s",
                request_id, exc_info=True,
            )
            return "failed"
        return "inline"

    try:
        from google.api_core.exceptions import AlreadyExists
        from google.cloud import tasks_v2  # type: ignore[attr-defined]
    except ImportError:
        logger.warning("google-cloud-tasks not installed; skipping export-build enqueue")
        return "failed"

    handler_url = os.environ[handler_url_env].rstrip("/") + f"/{request_id}"
    invoker_sa = os.environ.get("CLOUD_TASKS_INVOKER_SA", "").strip()
    queue_path = _resolve_queue_path_for(queue_env)

    client = tasks_v2.CloudTasksClient()

    task: dict[str, Any] = {
        # Task name = request_id (UUID; sanitize just in case).
        "name": f"{queue_path}/tasks/export-{request_id}",
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": handler_url,
            "headers": {"Content-Type": "application/json"},
            "body": b"{}",
        },
    }
    if invoker_sa:
        # OIDC audience MUST be the BASE handler URL, not the
        # per-request URL — that's what the verifier checks.
        oidc_audience = os.environ[handler_url_env].rstrip("/")
        task["http_request"]["oidc_token"] = {
            "service_account_email": invoker_sa,
            "audience": oidc_audience,
        }

    try:
        client.create_task(request={"parent": queue_path, "task": task})
        logger.info("Cloud Tasks export-build enqueue OK request=%s", request_id)
        return "queued"
    except AlreadyExists:
        logger.debug("Cloud Tasks dedup: export-%s already in flight", request_id)
        return "queued"
    except Exception:
        logger.warning(
            "Cloud Tasks export-build enqueue FAILED request=%s",
            request_id, exc_info=True,
        )
        return "failed"


async def enqueue_strava_webhook_event(payload: dict[str, Any]) -> str:
    """Enqueue a Strava webhook event for async processing.

    Falls back to inline run in TEST_MODE / unconfigured envs so unit
    + E2E tests exercise the full path (handshake → POST → enqueue →
    worker → ingest) without Cloud Tasks.

    Async because the worker entry point (`process_strava_webhook_event`)
    is async — the public POST handler that calls us is also async, so
    awaiting all the way down avoids the `asyncio.run-from-running-loop`
    trap. Cloud Task creation itself is sync (google-cloud-tasks SDK)
    but the latency is the same.

    Returns ``"queued"``, ``"inline"``, or ``"failed"``. Caller (the
    public webhook handler) re-raises only on ``"failed"`` so Strava
    retries; ``"queued"`` and ``"inline"`` are both acked 200.
    """
    queue_env = "CLOUD_TASKS_STRAVA_WEBHOOK_QUEUE"
    handler_url_env = "INTERNAL_STRAVA_WEBHOOK_HANDLER_URL"

    if not _is_enabled_for_queue(queue_env, handler_url_env):
        # Inline run — same module imported lazily to avoid a circular
        # import (cloud_tasks ↔ api package).
        try:
            from app.api.internal_strava_webhook import process_strava_webhook_event
            await process_strava_webhook_event(payload)
        except Exception as exc:
            logger.warning(
                "Inline Strava webhook processing failed payload=%s",
                payload, exc_info=True,
            )
            # Mirror the OIDC-protected worker's Sentry capture
            # (`internal_strava_webhook.py:85-92`) — audit 2026-05-27
            # S3.2. Without this, an accidentally-misconfigured prod
            # (queue env unset) silently drops webhook failures.
            try:
                import sentry_sdk
                sentry_sdk.set_tag("strava.phase", "webhook_inline_fallback")
                sentry_sdk.capture_exception(exc)
            except Exception:  # noqa: BLE001 — Sentry must never break the path
                pass
            return "failed"
        return "inline"

    try:
        from google.cloud import tasks_v2  # type: ignore[attr-defined]
    except ImportError:
        logger.warning("google-cloud-tasks not installed; skipping Strava webhook enqueue")
        return "failed"

    handler_url = os.environ[handler_url_env]
    invoker_sa = os.environ.get("CLOUD_TASKS_INVOKER_SA", "").strip()
    queue_path = _resolve_queue_path_for(queue_env)

    client = tasks_v2.CloudTasksClient()
    body = json.dumps(payload).encode("utf-8")

    # Per-event dedup via task name. Strava can retry the same event up
    # to 3 times if our endpoint 5xx'd. Without a task name, all retries
    # would enqueue + fire, each costing a Strava API call we already
    # made. The name encodes the event identity so a duplicate enqueue
    # collapses to AlreadyExists (Cloud Tasks dedup window). Task name
    # chars are limited — sanitize the parts.
    owner = str(payload.get("owner_id", "0"))
    obj_id = str(payload.get("object_id", "0"))
    aspect = str(payload.get("aspect_type", "x"))[:10]
    event_time = str(payload.get("event_time", "0"))
    task_name = f"strava-{owner}-{obj_id}-{aspect}-{event_time}"
    # Cloud Tasks task names must match ^[A-Za-z0-9_-]+$ — strip anything else.
    task_name = "".join(c if (c.isalnum() or c in "-_") else "-" for c in task_name)

    task: dict[str, Any] = {
        "name": f"{queue_path}/tasks/{task_name}",
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": handler_url,
            "headers": {"Content-Type": "application/json"},
            "body": body,
        },
    }
    if invoker_sa:
        task["http_request"]["oidc_token"] = {
            "service_account_email": invoker_sa,
            "audience": handler_url,
        }

    try:
        from google.api_core.exceptions import AlreadyExists
    except ImportError:
        AlreadyExists = None  # type: ignore[assignment]

    try:
        client.create_task(request={"parent": queue_path, "task": task})
        logger.info(
            "Strava webhook enqueue OK owner=%s object=%s/%s",
            payload.get("owner_id"),
            payload.get("object_type"),
            payload.get("aspect_type"),
        )
        return "queued"
    except Exception as exc:
        if AlreadyExists is not None and isinstance(exc, AlreadyExists):
            logger.debug("Strava webhook dedup: %s already in flight", task_name)
            return "queued"  # same outcome from caller's PoV
        logger.warning(
            "Strava webhook enqueue FAILED payload=%s", payload, exc_info=True,
        )
        return "failed"
