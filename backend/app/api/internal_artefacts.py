"""Internal endpoint called by Cloud Tasks for the post-ingest community rebuild.

- ``POST /internal/artefacts/rebuild``
    Fired by the ``artefact-rebuild-{window}`` Cloud Task
    (``cloud_tasks.enqueue_artefact_rebuild``), scheduled when its 5-min
    window closes: the uploads of a window collapse into ONE task. The handler only asks for one run of the ``build-pmtiles`` Cloud
    Run Job (``run_jobs.trigger_build_pmtiles_job``, 8 GiB) — it NEVER builds
    the heatmap itself. (It used to run ``build_pmtiles.main`` in-process on
    the 2 GiB worker, which the full build outgrew, and re-uploaded the
    mutable PMTiles outside the job's compare-and-set publish.)

    No debounce here: the deduplicated task already groups the burst.
    Trigger failure → 503 so Cloud Tasks retries; overlapping builds are
    safe (the job publishes its pointer by compare-and-set).

    OIDC-protected (``CLOUD_TASKS_INVOKER_SA`` audience-scoped via
    ``verify_oidc_token`` from ``internal_ingest.py``).

Design / rationale: ``docs/prod-ingestion-flow.md``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from app.api.internal_ingest import verify_oidc_token
from app.services import run_jobs

router = APIRouter(prefix="/internal", tags=["internal"])
log = logging.getLogger(__name__)


@router.post("/artefacts/rebuild")
async def artefacts_rebuild(request: Request) -> dict[str, str]:
    """Cloud Tasks → one ``jobs:run`` of the build-pmtiles job.

    200 ``{"status": "ok", "job": "triggered"}`` when the run was requested;
    503 when it was not (job trigger unconfigured or the Cloud Run API call
    failed), so Cloud Tasks retries the task.
    """
    verify_oidc_token(request, audience_env="INTERNAL_ARTEFACT_HANDLER_URL")

    # Sync HTTP (metadata token + jobs:run, ~8 s worst case) → threadpool.
    if not await run_in_threadpool(run_jobs.trigger_build_pmtiles_job):
        log.warning("artefact-rebuild: build-pmtiles job trigger failed → 503 (Cloud Tasks retries)")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="build-pmtiles job trigger failed",
        )
    log.info("artefact-rebuild: build-pmtiles job triggered")
    return {"status": "ok", "job": "triggered"}
