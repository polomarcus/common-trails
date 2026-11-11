"""GPX upload API — fast upload, community map refreshed out-of-band.

POST /gpx/upload — uploads a single GPX file.

Production path (Cloud Run):
    1. Parse GPX, ``ingest_activity`` — stores
       the Activity row (~200 ms).
    2. ``cloud_tasks.request_community_rebuild()`` — asks for the debounced
       PMTiles rebuild (one constant-named Cloud Task: a burst of uploads
       collapses to a single rebuild) so the new trace reaches the map.

TEST_MODE path: the ingest runs inline and nothing is enqueued, so tests keep
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
from app.services.cloud_tasks import request_community_rebuild
from app.services.gpx import _sanitize_track_type
from app.services.gpx_archive import archive_gpx
from app.services.provenance import COMMUNITY_SOURCE
from app.services.strava_utils import resolve_sport_cascade_with_default

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
# upload, ZIP bulk, CLI bulk folder) imports from the
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
    # The user's explicit sport. No default: a GPX whose <trk><type> can't
    # tell and that comes without one is refused (422 SPORT_REQUIRED).
    sport: str | None = Form(None),
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
    if sport is not None and sport not in VALID_SPORTS:
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

    # Sport resolution cascade (SSOT `strava_utils.resolve_sport_cascade`):
    # the GPX `<trk><type>` wins when it's granular enough to map (Garmin
    # Connect: mountain_biking → mtb, gravel_cycling → gravel, …), except
    # that a generic road is refined by the user's gravel/mtb. Generic
    # `cycling` (Strava export) or absent → the user's explicit choice.
    # Nothing at all → 422: never a default layer.
    # ``sport_is_default``: only when a bare generic type ("E-Bike Ride") is
    # the sole signal — the sport picked in the dropzone is an explicit choice.
    effective_sport, sport_is_default = resolve_sport_cascade_with_default(
        parsed.get("sport_from_gpx"), sport
    )
    if effective_sport is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SPORT_REQUIRED",
                "message": "Sport inconnu : choisissez le sport de cette trace.",
                "track_type": _sanitize_track_type(parsed.get("track_type")),
            },
        )

    activity_data = {
        "provider": "file",
        "provider_activity_id": None,
        # The user is uploading their OWN file by choice → community-eligible
        # provenance (manual_upload). This is what feeds the ODbL community
        # heatmap; Strava-API syncs are stamped strava_api and stay personal.
        "source": COMMUNITY_SOURCE,
        "sport": effective_sport,
        "sport_is_default": sport_is_default,
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

    # Production: store the activity immediately; the community map rebuild
    # is requested below, out-of-band.
    # Sync DB calls offloaded to threadpool (same reason as TEST_MODE above).
    result = await run_in_threadpool(
        ingest_service.ingest_activity,
        user_id=current_user.user_id,
        activity_data=activity_data,
        contribute_heatmap=community_ok,
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

    # Ask for the community map (static PMTiles) to be rebuilt with the new
    # trace — the debounced ``artefact-rebuild`` Cloud Task, requested directly
    # (the per-activity heat-compute task that used to sit in between is gone).
    # heatmap_status reflects the outcome:
    #   queued — a rebuild is pending (task created, or one was already queued)
    #   inline — Cloud Tasks not configured (dev): nothing to enqueue
    #   skipped — not community-eligible (opt-out, undated) or nothing new
    #   failed — the request failed; the activity is stored, the display
    #            catches up on the next rebuild
    heatmap_status = "skipped"
    response_status = result["status"]
    if (
        community_ok
        and result["status"] in ("created", "promoted")
        and parsed.get("activity_date") is not None
    ):
        heatmap_status = request_community_rebuild()
        # We surface "processing" while the rebuild is pending so the UI can
        # show an indeterminate spinner instead of a final "done" badge.
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
