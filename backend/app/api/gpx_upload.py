"""GPX upload API — fast async upload with background heatmap computation.

POST /gpx/upload — uploads a single GPX file.
Activity is stored immediately (fast), heatmap edges computed in background.

Production path (Cloud Run + Cloud Tasks):
    1. Parse GPX, ``ingest_activity(skip_heat_computation=True)`` — stores
       the Activity row, no heat-edge work yet (~200 ms).
    2. ``cloud_tasks.enqueue_heat_compute(activity_id, user_id)`` — pushes
       a task to the ``heat-compute`` queue, which calls back into
       ``POST /internal/ingest/heat`` to do the 5–30 s work.

TEST_MODE path: heat computation runs inline so existing tests
(``test_pending_bug_fixes.py``, ``test_heatmap.py``) keep their
deterministic single-request semantics.
"""
import logging
import os
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.api.auth import AuthenticatedUser, get_current_user
from app.config import VALID_SPORTS
from app.rate_limit import limiter, user_or_ip_key
from app.services import gpx as gpx_service
from app.services import ingest as ingest_service
from app.services.cloud_tasks import enqueue_heat_compute
from app.services.gpx_archive import archive_gpx
from app.services.provenance import COMMUNITY_SOURCE

router = APIRouter(tags=["gpx"])
log = logging.getLogger(__name__)

# Per-USER upload throttle — same SSOT default env var (UPLOAD_RATE_LIMIT) as
# /imports/files. NOTE: slowapi scopes limits PER ENDPOINT, so this is an
# INDEPENDENT bucket from /imports/files — each endpoint gets its own 200/h/user
# (they do NOT share a counter). Keyed by the session user (IP-rotation-proof).
# Callable so the env override applies at runtime (slowapi evaluates per request).
def _upload_rate_limit() -> str:
    return os.environ.get("UPLOAD_RATE_LIMIT", "200/hour")

# Constants live in `app.services.gpx` so every parse_gpx caller (HTTP
# upload, ZIP bulk, CLI bulk folder, Strava webhook) imports from the
# parser layer rather than depending on this HTTP handler module.
# Re-exported here so anything importing from the old location still
# works (the test fixture in test_import_gpx_folder_cli.py patches via
# the new path).
from app.services.gpx import MAX_GPX_COORDS, MAX_GPX_SIZE  # noqa: E402


class GpxUploadResult(BaseModel):
    activity_id: str
    status: str  # "created" | "already_exists" | "processing" | "promoted"
    name: str | None
    distance_m: float | None
    elevation_gain_m: float | None
    cells_indexed: int
    edges_indexed: int
    heatmap_status: str  # "queued" | "inline" | "skipped" | "done" | "failed"


@router.post("/gpx/upload", response_model=GpxUploadResult, status_code=202)
@limiter.limit(_upload_rate_limit, key_func=user_or_ip_key)
async def gpx_upload(
    request: Request,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    sport: str = Form("road"),
    contribute_heatmap: bool = Form(True),
    consent_version: str | None = Form(None),
    consent_text: str | None = Form(None),
    locale: str | None = Form(None),
    consent_id: str | None = Form(None),
) -> GpxUploadResult:
    """Upload a GPX file — activity stored immediately, heatmap deferred.

    Returns 202 Accepted with ``activity_id`` and a ``status`` field.
    The frontend can treat ``"processing"`` the same as ``"created"`` —
    the activity is queryable, the heatmap will catch up within seconds.

    ODbL consent coupling: publishing to the community layer requires a
    recorded ContributionConsent (same SSOT resolver as /imports/files). A
    ``contribute_heatmap=true`` upload with NO consent is ingested as PERSONAL
    (never published) — closing the community-write-without-consent hole.
    """
    if sport not in VALID_SPORTS:
        raise HTTPException(status_code=422, detail=f"Invalid sport: {sport}")

    # Resolve/record consent (SSOT) → community publication requires it.
    from app.db.session import SessionLocal
    from app.services import archive_intake

    db = SessionLocal()
    try:
        recorded_consent_id = archive_intake.resolve_or_record_consent(
            db,
            user_id=current_user.user_id,
            consent_id=consent_id,
            consent_version=consent_version,
            consent_text=consent_text,
            locale=locale,
        )
    except archive_intake.ConsentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        db.close()

    community_ok = contribute_heatmap and recorded_consent_id is not None
    if contribute_heatmap and not community_ok:
        log.warning(
            "gpx/upload: contribute_heatmap set without a consent row (user=%s) "
            "— ingesting as PERSONAL, not publishing to the community layer",
            current_user.user_id,
        )

    content = await file.read()
    if len(content) > MAX_GPX_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"GPX file too large (max {MAX_GPX_SIZE // (1024 * 1024)} MB)",
        )

    filename = (file.filename or "").lower()
    if not filename.endswith(".gpx"):
        raise HTTPException(status_code=422, detail="Only .gpx files are accepted")

    try:
        parsed = gpx_service.parse_gpx(content)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"GPX parse error: {exc}") from exc

    # Defend against dense-coordinate DoS: <10 MB GPX can still pack
    # millions of points and inflate ingest CPU/RAM.
    # `parsed["coord_count"]` is already populated by parse_gpx (line 195),
    # so we don't re-parse the multi-MB GeoJSON string here.
    coord_count = parsed.get("coord_count", 0)
    if coord_count > MAX_GPX_COORDS:
        raise HTTPException(
            status_code=413,
            detail=f"GPX has too many coordinates ({coord_count} > {MAX_GPX_COORDS}); split or simplify the trace",
        )

    # Skip-out-of-scope short-circuit. parse_gpx stamps `skip_reason`
    # + `skip_code` when `<trk><type>` is in `GPX_TRACK_TYPE_SKIP`
    # (yoga, swim, ski, treadmill, virtual ride, motorcycle…). Falling
    # back to the form-supplied sport would pollute the wrong heatmap.
    # See [[feedback_skip_beats_pollute_heatmap]] — heatmap is
    # community-shared, miss-import is private.
    #
    # Structured detail: `skip_code` is the stable enum the frontend
    # matches on for localisation. `message` is the human reason.
    # `track_type` is the sanitized value (≤64 chars, control chars
    # stripped) — never the raw upload bytes.
    if parsed.get("skip_reason"):
        raise HTTPException(
            status_code=422,
            detail={
                "code": parsed.get("skip_code", "GPX_TYPE_OUT_OF_SCOPE"),
                "message": parsed["skip_reason"],
                "track_type": parsed.get("skip_track_type", ""),
            },
        )

    # Sport resolution cascade: the GPX `<trk><type>` overrides the
    # form-supplied `sport` when it's granular enough to map (Garmin
    # Connect: mountain_biking → mtb, gravel_cycling → gravel, …). When
    # it's the generic `cycling` (Strava export) or absent, we keep the
    # form value. See classify_sport_from_gpx_type for the mapping.
    sport_from_gpx = parsed.get("sport_from_gpx")
    effective_sport = sport_from_gpx if sport_from_gpx in VALID_SPORTS else sport

    activity_data = {
        "provider": "file",
        "provider_activity_id": None,
        # The user is uploading their OWN file by choice → community-eligible
        # provenance (manual_upload). This is what feeds the ODbL community
        # heatmap; Strava-API syncs are stamped strava_api and stay personal.
        "source": COMMUNITY_SOURCE,
        "sport": effective_sport,
        "name": parsed.get("name"),
        "geometry_geojson": parsed.get("geometry_geojson"),
        "distance_m": parsed.get("distance_m"),
        "elevation_gain_m": parsed.get("elevation_gain_m"),
        "file_hash": parsed.get("file_hash"),
        "activity_date": parsed.get("activity_date"),
        "geometry_source": "stream",
    }

    # In test mode: run synchronously for deterministic test results.
    # The dev/TEST_MODE path purposely does NOT touch Cloud Tasks — it
    # keeps the legacy single-request semantics so unit tests can assert
    # on heatmap state immediately after upload.
    from app.config import TEST_MODE
    if TEST_MODE:
        # Sync ingest blocks the event loop; offload to a thread so other
        # async requests can progress on this worker. Cloud Run instances
        # have one uvicorn worker — without this offload, an active import
        # serialises every other endpoint.
        result = await run_in_threadpool(
            ingest_service.ingest_activity,
            user_id=current_user.user_id,
            activity_data=activity_data,
            contribute_heatmap=community_ok,
        )
        if result["status"] in ("created", "promoted"):
            background_tasks.add_task(
                archive_gpx, current_user.user_id, result["activity_id"],
                file.filename or "upload.gpx", content,
            )
        return GpxUploadResult(
            activity_id=result["activity_id"],
            status=result["status"],
            name=parsed.get("name"),
            distance_m=parsed.get("distance_m"),
            elevation_gain_m=parsed.get("elevation_gain_m"),
            cells_indexed=result.get("cells_indexed", 0),
            edges_indexed=result.get("edges_indexed", 0),
            heatmap_status="done",
        )

    # Production: store activity immediately, heatmap via Cloud Tasks.
    # Sync DB calls offloaded to threadpool (same reason as TEST_MODE above).
    result = await run_in_threadpool(
        ingest_service.ingest_activity,
        user_id=current_user.user_id,
        activity_data=activity_data,
        contribute_heatmap=community_ok,
        skip_heat_computation=True,
    )

    # Archive raw GPX to uploads bucket (safety net for heatmap rebuild).
    # "promoted" (③): the upload became the authoritative community geometry
    # of a pre-existing Strava-API row — archive it like a fresh create.
    if result["status"] in ("created", "promoted"):
        background_tasks.add_task(
            archive_gpx, current_user.user_id, result["activity_id"],
            file.filename or "upload.gpx", content,
        )

    # Backup raw GPX to cloud storage keyed by hash (dedup safety net)
    if result["status"] in ("created", "promoted") and parsed.get("file_hash"):
        from app.services.gpx_backup import backup_gpx
        background_tasks.add_task(
            backup_gpx, current_user.user_id, parsed["file_hash"], content,
        )

    # Enqueue heat-compute Cloud Task. The handler at /internal/ingest/heat
    # will load the activity row + run _update_heat_edges off the request
    # path. Heatmap_status reflects the enqueue outcome:
    #   queued — Cloud Task created, work runs out-of-band
    #   inline — Cloud Tasks not configured, ran in this process
    #   skipped — user opted out
    #   failed — enqueue raised; activity is stored but heat skipped
    heatmap_status = "skipped"
    response_status = result["status"]
    if community_ok and result["status"] in ("created", "promoted"):
        heatmap_status = enqueue_heat_compute(
            activity_id=result["activity_id"],
            user_id=current_user.user_id,
        )
        # Frontend can poll /me/activities to see the heatmap arrive; we
        # surface "processing" when the task is queued so the UI can show
        # an indeterminate spinner instead of a final "done" badge.
        if heatmap_status == "queued":
            response_status = "processing"

    return GpxUploadResult(
        activity_id=result["activity_id"],
        status=response_status,
        name=parsed.get("name"),
        distance_m=parsed.get("distance_m"),
        elevation_gain_m=parsed.get("elevation_gain_m"),
        cells_indexed=result.get("cells_indexed", 0),
        edges_indexed=0,
        heatmap_status=heatmap_status,
    )
