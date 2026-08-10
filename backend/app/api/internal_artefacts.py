"""Internal endpoint called by Cloud Tasks for post-ingest artefact rebuilds.

- ``POST /internal/artefacts/rebuild``
    Rebuilds PMTiles + (in prod) uploads to GCS. Triggered 5 min after
    the last ingest, deduped via constant Cloud Tasks task name.

    (The ``.fgraph`` routing-shard rebuild half was removed with the
    WASM-routing decommission — internal routing is delegated to
    external tools; there is no substrate to rebuild a graph from.)

    OIDC-protected (``CLOUD_TASKS_INVOKER_SA`` audience-scoped via
    ``verify_oidc_token`` from ``internal_ingest.py``). Idempotent —
    Cloud Tasks may retry on 5xx and we'd rather re-do work than wedge
    the queue.

(The ``POST /internal/matview/refresh`` endpoint was removed in June 2026
when the ``heat_edges_display`` materialized view was dropped — the live
MVT tile endpoint now aggregates ``heat_edges`` directly at request time
via the shared ``app.services.heat_aggregation`` builder.)

Design / rationale: ``docs/prod-ingestion-flow.md`` and
``docs/migration-runbook.md``.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Request

from app.api.internal_ingest import verify_oidc_token

router = APIRouter(prefix="/internal", tags=["internal"])
log = logging.getLogger(__name__)


@router.post("/artefacts/rebuild")
async def artefacts_rebuild(request: Request) -> dict[str, Any]:
    """Cloud Tasks → rebuild PMTiles; upload to GCS.

    Triggered 5 min after the last ingest, deduped via constant Cloud
    Tasks task name. Reads current DB state — no payload needed.

    PMTiles (always): builds locally to /tmp, uploads to
    ``gs://{HEATMAP_ARTEFACTS_BUCKET}/heatmap-display.pmtiles`` with
    ``Cache-Control: public, max-age=300``. Cloud CDN sees a fresh file
    within minutes.
    """
    verify_oidc_token(request, audience_env="INTERNAL_ARTEFACT_HANDLER_URL")

    pmtiles_status = _rebuild_and_upload_pmtiles()
    return {
        "status": "ok",
        "pmtiles": pmtiles_status,
    }


def _rebuild_and_upload_pmtiles() -> dict[str, Any]:
    """Build PMTiles into /tmp, upload to the artefacts GCS bucket.

    Returns a status dict; never raises (errors are logged and surfaced
    in the response so a partial failure doesn't 5xx the whole endpoint
    and trigger Cloud Tasks retries that would re-do the same work).
    """
    import shutil
    import tempfile

    try:
        from app.jobs import build_pmtiles
    except Exception as exc:  # pragma: no cover
        log.exception("artefact-rebuild: cannot import build_pmtiles")
        return {"ok": False, "error": f"import: {exc}"}

    target_dir = tempfile.mkdtemp(prefix="artefactrebuild_")
    # min_uc MUST track the API's K-anonymity floor: this PMTiles binary is
    # uploaded to the public artefacts bucket + served to browsers, so
    # publishing at min_uc=1 leaked single-user OSM-matched edges while the
    # API enforced K=2 (June 2026 audit S1). HEATMAP_K_ANONYMITY is 1 in dev
    # compose (full visibility) and defaults to 2 in prod.
    k_anon = int(os.environ.get("HEATMAP_K_ANONYMITY", "2"))
    try:
        try:
            build_pmtiles.main(
                target_dir, min_zoom=6, max_zoom=15, min_uc=k_anon,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("artefact-rebuild: build_pmtiles raised")
            return {"ok": False, "error": f"build: {exc}"}

        src = os.path.join(target_dir, "heatmap-display.pmtiles")
        if not os.path.exists(src):
            return {"ok": False, "error": "build produced no output"}

        bucket = os.environ.get("HEATMAP_ARTEFACTS_BUCKET", "").strip()
        if not bucket:
            log.info("artefact-rebuild: HEATMAP_ARTEFACTS_BUCKET unset → skip upload")
            return {"ok": True, "skipped_upload": True, "size_bytes": os.path.getsize(src)}

        try:
            from google.cloud import storage  # type: ignore[attr-defined]
        except ImportError:
            log.warning("google-cloud-storage not installed; can't upload PMTiles")
            return {"ok": False, "error": "google-cloud-storage missing"}

        try:
            client = storage.Client()
            blob = client.bucket(bucket).blob("heatmap-display.pmtiles")
            blob.cache_control = "public, max-age=300"
            blob.upload_from_filename(src, content_type="application/octet-stream")
            log.info("artefact-rebuild: uploaded PMTiles to gs://%s/heatmap-display.pmtiles (%d bytes)",
                     bucket, os.path.getsize(src))
            return {"ok": True, "bucket": bucket, "size_bytes": os.path.getsize(src)}
        except Exception as exc:
            log.exception("artefact-rebuild: GCS upload failed")
            return {"ok": False, "error": f"upload: {exc}"}
    finally:
        shutil.rmtree(target_dir, ignore_errors=True)
