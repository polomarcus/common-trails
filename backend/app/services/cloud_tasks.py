"""Cloud Tasks client — enqueue background jobs for the API service.

Two kinds of tasks, both POSTed to the worker service with an OIDC token:

* the debounced community-artefact rebuild (``enqueue_artefact_rebuild`` /
  ``request_community_rebuild``) — one constant task name, so a burst of
  uploads collapses to a single PMTiles rebuild;
* the per-request heatmap export build (``enqueue_export_build``).

Behavior:
- TEST_MODE / no Cloud Tasks env config → nothing is enqueued (``"inline"``).
- Production → a Cloud Task is created on the configured queue.

Env vars:
- ``CLOUD_TASKS_ARTEFACT_QUEUE`` / ``INTERNAL_ARTEFACT_HANDLER_URL`` and
  ``CLOUD_TASKS_EXPORT_QUEUE`` / ``INTERNAL_EXPORT_BUILD_HANDLER_URL`` — queue
  (fully-qualified ``projects/{p}/locations/{r}/queues/{q}`` **or** a bare
  queue id, in which case ``GCP_PROJECT`` and ``CLOUD_TASKS_LOCATION`` must be
  set) and the full HTTPS handler URL for each task type.
- ``CLOUD_TASKS_INVOKER_SA`` — service-account email Cloud Tasks signs the
  OIDC token as; the receiver verifies signature + audience.

(The per-activity ``heat-compute`` task — ``enqueue_heat_compute`` →
``/internal/ingest/heat`` — was removed with the matched pipeline: uploads now
request the rebuild directly. ``CLOUD_TASKS_QUEUE`` / ``INTERNAL_HEAT_HANDLER_URL``
are no longer read here.)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# ── Artefact rebuild dispatch ────────────────────────────────────────
#
# The post-ingest PMTiles rebuild is a Cloud Task with:
#
#   1. **Its own queue + handler URL** (the worker service's
#      ``/internal/artefacts/rebuild``).
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
    """True iff the queue+handler pair is configured AND we are not in TEST_MODE."""
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        return False
    return bool(os.environ.get(queue_env)) and bool(os.environ.get(handler_url_env))


def _resolve_queue_path_for(queue_env: str) -> str:
    """Return the fully-qualified queue path for the Cloud Tasks API.

    Accepts either a full ``projects/.../queues/...`` path or a bare queue
    id; in the latter case the path is rebuilt from ``GCP_PROJECT`` and
    ``CLOUD_TASKS_LOCATION``.
    """
    raw = os.environ.get(queue_env, "").strip()
    if not raw:
        raise RuntimeError(f"{queue_env} not set")
    if raw.startswith("projects/"):
        return raw
    # Prefer GCP_PROJECT (the var the prod deploy actually sets) with a
    # GOOGLE_CLOUD_PROJECT fallback. deploy-prod.sh's env-file sets GCP_PROJECT
    # but NOT GOOGLE_CLOUD_PROJECT, so reading only the latter made every bare
    # queue path raise → artefact-rebuild "failed".
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


def request_community_rebuild() -> str:
    """Ask for the community PMTiles to be rebuilt after an upload. Never raises.

    This is what the upload paths (``/gpx/upload``, ``/imports/files``) call
    once a community-eligible activity is stored. It is the SAME debounced
    ``artefact-rebuild`` task the retired ``/internal/ingest/heat`` hop used to
    enqueue — the per-activity heat-compute task in between is gone, the
    outcome in prod is identical.

    Returns the value surfaced as ``heatmap_status`` to the client:
    ``"queued"`` (task created, or already pending — a deduped enqueue IS a
    pending rebuild), ``"inline"`` (Cloud Tasks not configured: dev / tests,
    nothing to do), ``"failed"`` (enqueue raised or was rejected — the
    activity is stored, the display catches up on the next rebuild).
    """
    try:
        outcome = enqueue_artefact_rebuild()
    except Exception:
        logger.warning("community rebuild request failed", exc_info=True)
        return "failed"
    return "queued" if outcome == "deduped" else outcome


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
