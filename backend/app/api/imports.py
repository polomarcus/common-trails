"""File import API — supports .gpx and .zip (of .gpx files).

POST /imports/files — multipart upload with sport + contribute_heatmap.
Activities stored immediately (fast), heatmap computed in background.

Production path: each created activity is enqueued as an individual
Cloud Task hitting ``/internal/ingest/heat``. One activity per task so
retries are scoped tightly and Cloud Tasks can parallelise the work.

TEST_MODE keeps the legacy synchronous behaviour (heat computed in the
request thread).
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
from app.services.gpx_archive import archive_bundle, archive_gpx
from app.services.provenance import COMMUNITY_SOURCE
from app.services.run_jobs import trigger_ingest_archives_job

router = APIRouter(tags=["imports"])
log = logging.getLogger(__name__)

MAX_FILE_SIZE = int(os.environ.get("MAX_IMPORT_FILE_SIZE", str(50 * 1024 * 1024)))  # 50 MB

# Per-USER upload throttle (keyed by the session user, not the IP — an abuser
# can't dodge it by rotating IPs). Each accepted upload can fan out to a
# heat-compute Cloud Task, so this bounds the per-user cost of loop-uploading;
# the EXPENSIVE PMTiles rebuild is throttled separately (debounced), so this
# limit only needs to catch a runaway loop, not gate normal batches. The
# default is deliberately WELL ABOVE a realistic loose-file batch: the frontend
# ContributionDropzone loops ONE POST per loose file (multiple + drag-drop), so
# a user dropping a whole unzipped Garmin/Komoot/Strava folder of GPX/FIT can
# legitimately fire >100 requests — a 20/h default would false-429 that core
# path. 200/h/user still stops abuse. slowapi scopes limits PER ENDPOINT, so
# /imports/files and /gpx/upload each get their own independent 200/h bucket.
# Env-overridable via UPLOAD_RATE_LIMIT (slowapi syntax); read via a callable so
# the override takes effect at runtime (slowapi evaluates the provider per req).
def _upload_rate_limit() -> str:
    return os.environ.get("UPLOAD_RATE_LIMIT", "200/hour")

# Per-user quotas on the signed-URL archive flow (HIGH 3). Each /init mints a
# signed PUT URL + a PendingArchive row; without a cap a user can spam /init to
# create unbounded pending rows / drain-job triggers. Env-overridable.
MAX_OPEN_ARCHIVES_PER_USER = int(os.environ.get("MAX_OPEN_ARCHIVES_PER_USER", "5"))
MAX_ARCHIVE_INITS_PER_DAY = int(os.environ.get("MAX_ARCHIVE_INITS_PER_DAY", "20"))


class ImportResult(BaseModel):
    imported: int
    skipped: int
    failed: int
    errors: list[str]
    activity_ids: list[str]
    heatmap_status: str  # "queued" | "inline" | "skipped" | "done"
    # ODbL contribution-consent audit row backing THIS upload (gap #7).
    # None when the caller sent no consent fields (backward compat).
    consent_id: str | None = None


class ArchiveIntakeResult(BaseModel):
    """Result of a consented Strava-archive upload.

    Nothing is ingested on the request thread — members are stored to a
    per-user bucket prefix and enqueued for a paced worker. ``enqueued``
    is the number of members that will be ingested progressively.
    """
    consent_id: str
    enqueued: int
    skipped: int
    errors: list[str]
    status: str  # "accepted"


class ArchiveInitRequest(BaseModel):
    """Body of ``POST /imports/strava-archive/init``. No file — the archive
    is PUT straight to the bucket via the returned signed URL."""
    sport: str = "road"  # fallback only; activities.csv is authoritative
    consent: bool = False
    consent_version: str
    consent_text: str
    locale: str | None = None
    filename: str | None = None  # informational (original .zip name)


class ArchiveInitResult(BaseModel):
    """Result of ``init``: everything the browser needs to PUT the .zip
    directly to storage, then call ``complete``."""
    archive_id: str
    consent_id: str
    bucket_key: str
    upload_url: str          # signed GCS URL (gcs) or backend path (local)
    upload_method: str       # "PUT"
    content_type: str        # required on the PUT (bound into the signature)
    max_bytes: int
    storage_backend: str     # "gcs" | "local" — local ⇒ send the auth header


class ArchiveCompleteRequest(BaseModel):
    archive_id: str
    key: str  # the bucket_key returned by init (ownership re-checked)


class ArchiveCompleteResult(BaseModel):
    archive_id: str
    status: str  # "uploaded"
    enqueue: str  # "triggered" (job kicked now) | "scheduled" (daily backstop)


@router.post("/imports/files", response_model=ImportResult, status_code=202)
@limiter.limit(_upload_rate_limit, key_func=user_or_ip_key)
async def import_files(
    request: Request,
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    sport: str = Form("road"),
    contribute_heatmap: bool = Form(False),
    consent_version: str | None = Form(None),
    consent_text: str | None = Form(None),
    locale: str | None = Form(None),
    consent_id: str | None = Form(None),
    rebuild_display: bool = Form(True),
) -> ImportResult:
    """Upload and import a .gpx or .zip (of .gpx) file.

    Activities are stored immediately. Heatmap contribution runs
    out-of-band via Cloud Tasks (one task per activity).

    ODbL consent audit trail (gap #7): the loose GPX/FIT path must leave the
    same ``contribution_consents`` record as the archive path. The frontend
    loops one POST per file, so the design is a consent_id ROUND-TRIP —
    ONE row per upload batch, not per file:

    * first request carries ``consent_version`` + ``consent_text`` (+
      ``locale``) → ONE consent row is recorded, its id returned in
      ``ImportResult.consent_id``;
    * subsequent requests carry ``consent_id`` → ownership is re-checked
      and the SAME row is reused (nothing new recorded).

    All fields optional — a bare upload (CLI, older clients) still works and
    simply records no consent (``consent_id=None`` in the response).
    """
    if sport not in VALID_SPORTS:
        raise HTTPException(status_code=422, detail=f"Invalid sport: {sport}")

    # Record/resolve the consent audit row FIRST (mirrors the archive path:
    # the trail must exist even if the subsequent parse/ingest partially
    # fails — consent was given for the batch, not per parsed file). SSOT
    # resolver shared with /gpx/upload — no drift.
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

    # No community write without a consent row (ODbL audit coupling). If the
    # caller asks to contribute but recorded no consent (a bare API call / older
    # client — the real frontend ALWAYS sends consent), ingest the activity as
    # PERSONAL instead of publishing it to the community ODbL layer. Nothing
    # changes for real users; this closes the hole where contribute_heatmap=true
    # published with no consent audit row.
    community_ok = contribute_heatmap and recorded_consent_id is not None
    if contribute_heatmap and not community_ok:
        log.warning(
            "imports/files: contribute_heatmap set without a consent row "
            "(user=%s) — ingesting as PERSONAL, not publishing to the community layer",
            current_user.user_id,
        )

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 50 MB)")

    filename = (file.filename or "").lower()
    parsed_list: list[dict] = []
    errors: list[str] = []

    # Dense-coordinate DoS cap — same as /gpx/upload. Applied per parsed
    # item, regardless of source format (single .gpx, single .fit, or
    # any zip member). Without this cap a 49 MB .gpx with 10M trkpts at
    # /imports/files parses into hundreds of MB of Python tuples.
    MAX_COORDS_PER_FILE = 100_000

    def _check_coord_cap(parsed: dict, label: str) -> bool:
        """Return True if under cap, else append error and return False."""
        cc = parsed.get("coord_count", 0)
        if cc > MAX_COORDS_PER_FILE:
            errors.append(
                f"{label}: too many coordinates ({cc} > {MAX_COORDS_PER_FILE}); "
                "split or simplify the trace",
            )
            return False
        return True

    # Per-file byte-size cap. The outer MAX_FILE_SIZE (50 MB) bounds the
    # whole upload; this cap matches `/gpx/upload`'s 10 MB so a single
    # GPX/FIT can't allocate ~hundreds-of-MB of parser state before the
    # coord-cap kicks in post-parse. Same constant the HTTP single-file
    # path uses. ZIP archives have their own per-member cap inside
    # `parse_zip_of_gpx`. Audit 2026-05-29 GPX-S1.2.
    if filename.endswith(".gpx"):
        if len(content) > gpx_service.MAX_GPX_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"GPX file too large ({len(content)} > {gpx_service.MAX_GPX_SIZE} bytes); "
                "split or simplify the trace",
            )
        try:
            parsed = gpx_service.parse_gpx(content)
            if _check_coord_cap(parsed, filename or "upload.gpx"):
                parsed_list.append(parsed)
        except Exception as exc:
            errors.append(f"GPX parse error: {exc}")

    elif filename.endswith(".fit"):
        if len(content) > gpx_service.MAX_GPX_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"FIT file too large ({len(content)} > {gpx_service.MAX_GPX_SIZE} bytes); "
                "split or simplify the trace",
            )
        try:
            from app.services.fit_parser import parse_fit
            parsed = parse_fit(content)
            # FIT parser infers sport — override with user selection if provided
            if sport != "road" and parsed.get("sport") == "road":
                parsed["sport"] = sport
            if _check_coord_cap(parsed, filename or "upload.fit"):
                parsed_list.append(parsed)
        except ImportError:
            raise HTTPException(status_code=422, detail="FIT file support not installed (pip install fitparse)") from None
        except Exception as exc:
            errors.append(f"FIT parse error: {exc}")

    elif filename.endswith(".zip"):
        try:
            parsed_list_raw = gpx_service.parse_zip_of_gpx(content)
        except gpx_service.ZipBombError as exc:
            # 413: payload too large / suspicious — caller should split.
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        for item in parsed_list_raw:
            label = item.get("source_file", "?")
            if "error" in item:
                errors.append(f"{label}: {item['error']}")
                continue
            if _check_coord_cap(item, label):
                parsed_list.append(item)
    else:
        raise HTTPException(status_code=422, detail="Supported formats: .gpx, .fit, .zip")

    # In test mode: run synchronously for deterministic test results
    from app.config import TEST_MODE
    sync_mode = TEST_MODE

    imported = 0
    skipped = 0
    failed = 0
    activity_ids: list[str] = []
    created_activity_ids: list[str] = []  # subset that triggers heat compute

    for parsed in parsed_list:
        try:
            # Skip-out-of-scope short-circuit. `parse_gpx` stamps
            # `skip_reason` when `<trk><type>` is in `GPX_TRACK_TYPE_SKIP`
            # (yoga, swim, ski, treadmill, virtual ride…) AND
            # `parse_zip_of_gpx` stamps `skip_reason` when the matching
            # Strava activities.csv row says the sport is out-of-scope.
            # Either way, drop BEFORE touching the cascade — falling
            # back to the form-supplied sport would pollute the wrong
            # heatmap. See [[feedback_skip_beats_pollute_heatmap]].
            if parsed.get("skip_reason"):
                skipped += 1
                errors.append(
                    f"{parsed.get('source_file', '?')}: {parsed['skip_reason']}",
                )
                continue

            # Sport cascade (most → least authoritative):
            #   1. FIT — the parser writes the device's sport field
            #      directly into parsed["sport"]
            #   2. Strava activities.csv — when a ZIP archive contained
            #      activities.csv, parse_zip_of_gpx stamped the
            #      per-file sport mapped from the CSV's "Activity Type"
            #      column. This is more reliable than the in-GPX hint
            #      because Strava collapses sub-types to generic
            #      "cycling" when generating the GPX.
            #   3. GPX <trk><type> — Garmin Connect emits granular
            #      values like mountain_biking, gravel_cycling that
            #      classify_sport_from_gpx_type maps to our enum.
            #   4. Form-supplied — the dropdown in the modal, used
            #      only when none of the above resolved.
            sport_from_fit = parsed.get("sport")
            sport_from_csv = parsed.get("sport_from_csv")
            sport_from_gpx = parsed.get("sport_from_gpx")
            if sport_from_fit in VALID_SPORTS:
                effective_sport = sport_from_fit
            elif sport_from_csv in VALID_SPORTS:
                effective_sport = sport_from_csv
            elif sport_from_gpx in VALID_SPORTS:
                effective_sport = sport_from_gpx
            else:
                effective_sport = sport

            activity_data = {
                "provider": "file",
                "provider_activity_id": None,
                # User's own uploaded file (folder / ZIP / single GPX) →
                # community-eligible provenance. Feeds the ODbL heatmap.
                "source": COMMUNITY_SOURCE,
                "sport": effective_sport,
                "name": parsed.get("name"),
                "geometry_geojson": parsed.get("geometry_geojson"),
                "distance_m": parsed.get("distance_m"),
                "elevation_gain_m": parsed.get("elevation_gain_m"),
                "file_hash": parsed.get("file_hash"),
                "activity_date": parsed.get("activity_date"),
            }
            # Sync ingest offloaded to threadpool so a 1400-file ZIP
            # doesn't block the event loop and starve every other request
            # on this Cloud Run instance. The pool itself is bounded
            # (default 40 threads in starlette) — the bottleneck shifts
            # to the DB pool (5+3 on db-f1-micro) which now has matching
            # short-lived sessions from the recent ingest refactors.
            result = await run_in_threadpool(
                ingest_service.ingest_activity,
                user_id=current_user.user_id,
                activity_data=activity_data,
                contribute_heatmap=community_ok,
                skip_heat_computation=not sync_mode,
            )
            activity_ids.append(result["activity_id"])
            # "promoted" (③): a manual upload that upgraded a pre-existing
            # Strava-API twin to the community layer — counts as imported and
            # needs a heat-compute task like a fresh create.
            if result["status"] in ("created", "promoted"):
                imported += 1
                if community_ok:
                    created_activity_ids.append(result["activity_id"])
            else:
                skipped += 1
        except Exception as exc:
            failed += 1
            errors.append(str(exc))

    # Archive raw upload to uploads bucket (safety net for heatmap rebuild).
    # For single-file uploads, key by the activity_id (one file, one row).
    # For bundles (ZIP), key by content hash via archive_bundle so the
    # archival isn't disconnected from N-1 of the activities the way the
    # previous code was (which used activity_ids[0] only).
    if activity_ids:
        is_bundle = filename.endswith(".zip")
        if is_bundle:
            import hashlib
            bundle_hash = hashlib.sha256(content).hexdigest()
            background_tasks.add_task(
                archive_bundle, current_user.user_id, bundle_hash,
                file.filename or "upload.zip", content,
            )
        else:
            background_tasks.add_task(
                archive_gpx, current_user.user_id, activity_ids[0],
                file.filename or "upload.bin", content,
            )

    # Enqueue one heat-compute task per newly-created activity. In dev /
    # TEST_MODE this is a no-op because ``ingest_activity`` already did
    # the work synchronously; we report ``done``.
    heatmap_status = "skipped"
    if sync_mode:
        if created_activity_ids:
            heatmap_status = "done"
    elif created_activity_ids:
        outcomes = [
            enqueue_heat_compute(activity_id=aid, user_id=current_user.user_id)
            for aid in created_activity_ids
        ]
        # If at least one task queued successfully, surface "queued"; else
        # collapse to the most pessimistic outcome.
        if "queued" in outcomes:
            heatmap_status = "queued"
        elif "inline" in outcomes:
            heatmap_status = "inline"
        else:
            heatmap_status = "failed"

    # Event-driven display refresh (best-effort). Without this, a loose GPX/FIT
    # contribution ingests into ``activities`` but never reaches the static
    # ``heatmap-display.pmtiles`` on GCS until an unrelated rebuild — the
    # contributor uploads, consents, and sees NOTHING on the map (the exact gap
    # the archive /complete path already closes via trigger_build_pmtiles_job).
    # The frontend loops ONE POST per file and sets ``rebuild_display=True`` only
    # on the LAST file of a batch, so a 20-file drop fires ONE rebuild, not 20.
    # We fire on the batch-end signal ALONE (not on this request's ``imported``
    # count): each request only sees its own file, so gating on a per-request
    # count would MISS a batch whose last file happens to be a duplicate while
    # earlier files created activities. A wasted rebuild on an all-duplicate
    # batch is rare, idempotent, and cheap. Never raises (best-effort); no-ops
    # locally / in TEST_MODE; the daily backstop catches a missed trigger. The
    # build-pmtiles job runs on db-f1-micro with no tier bump.
    # Debounced (cost control): an abusive user looping tiny uploads must not
    # fire N expensive build-pmtiles JOBS. The FIRST trigger per cooldown
    # window fires; the rest are suppressed (the daily backstop + the next
    # upload after the cooldown still refresh — a suppressed trigger only
    # delays, never drops, the rebuild). See run_jobs.build_pmtiles_debounce_ok.
    if rebuild_display:
        try:
            from app.services.run_jobs import (
                build_pmtiles_debounce_ok,
                trigger_build_pmtiles_job,
            )
            if build_pmtiles_debounce_ok():
                trigger_build_pmtiles_job()
            else:
                log.info("build-pmtiles trigger debounced (cooldown active)")
        except Exception as exc:  # pragma: no cover - defense in depth
            log.warning("build-pmtiles trigger failed after /imports/files: %s", exc)

    return ImportResult(
        imported=imported,
        skipped=skipped,
        failed=failed,
        errors=errors,
        activity_ids=activity_ids,
        heatmap_status=heatmap_status,
        consent_id=recorded_consent_id,
    )


@router.post("/imports/strava-archive", response_model=ArchiveIntakeResult, status_code=202)
async def import_strava_archive(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    file: UploadFile = File(...),
    sport: str = Form("road"),
    consent: bool = Form(False),
    consent_version: str = Form(...),
    consent_text: str = Form(...),
    locale: str | None = Form(None),
) -> ArchiveIntakeResult:
    """Consented community-contribution intake of a user's OWN Strava archive.

    Legal basis: the user requested their own data via Strava's official
    export, downloaded the ZIP, and uploads it here with explicit consent to
    contribute (anonymised, ODbL) to the open community heatmap. This is a
    DIFFERENT basis from Strava-API-extracted data (which the 2026 API
    Policy forbids redistributing into a public heatmap).

    Consent is REQUIRED — no consent → 422, nothing stored, nothing ingested.

    The archive is NOT ingested on the request thread. Each member is stored
    to a per-user bucket prefix and enqueued; a paced worker
    (``app.jobs.ingest_pending_archives``) ingests them progressively,
    tagging each activity with provenance ``source="manual_upload"``.
    """
    if not consent:
        raise HTTPException(
            status_code=422,
            detail="Consent is required to contribute your archive to the community heatmap.",
        )
    if sport not in VALID_SPORTS:
        raise HTTPException(status_code=422, detail=f"Invalid sport: {sport}")
    if not consent_version.strip() or not consent_text.strip():
        raise HTTPException(status_code=422, detail="Missing consent version/text.")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 50 MB)")

    filename = (file.filename or "").lower()
    if not (filename.endswith(".zip") or filename.endswith(".gpx") or filename.endswith(".fit")):
        raise HTTPException(status_code=422, detail="Supported formats: .zip, .gpx, .fit")

    from app.db.session import SessionLocal
    from app.services import archive_intake

    db = SessionLocal()
    try:
        # Record consent FIRST — the audit trail must exist even if the
        # subsequent extraction partially fails.
        consent_id = archive_intake.record_consent(
            db,
            user_id=current_user.user_id,
            consent_version=consent_version.strip()[:100],
            consent_text=consent_text.strip(),
            locale=(locale or None),
            source=archive_intake.ARCHIVE_PROVENANCE,
        )
        try:
            intake = archive_intake.enqueue_archive(
                db,
                user_id=current_user.user_id,
                content=content,
                filename=filename,
                fallback_sport=sport,
                contribute_heatmap=True,
                consent_id=consent_id,
                source=archive_intake.ARCHIVE_PROVENANCE,
            )
        except gpx_service.ZipBombError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        db.close()

    return ArchiveIntakeResult(
        consent_id=consent_id,
        enqueued=intake.enqueued,
        skipped=intake.skipped,
        errors=intake.errors,
        status="accepted",
    )


def _validate_consent(consent: bool, consent_version: str, consent_text: str, sport: str) -> None:
    """Shared consent + sport gate (reused by the direct + signed-URL flows)."""
    if not consent:
        raise HTTPException(
            status_code=422,
            detail="Consent is required to contribute your archive to the community heatmap.",
        )
    if sport not in VALID_SPORTS:
        raise HTTPException(status_code=422, detail=f"Invalid sport: {sport}")
    if not consent_version.strip() or not consent_text.strip():
        raise HTTPException(status_code=422, detail="Missing consent version/text.")


@router.post("/imports/strava-archive/init", response_model=ArchiveInitResult, status_code=201)
async def init_strava_archive(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    body: ArchiveInitRequest,
) -> ArchiveInitResult:
    """Begin a consented WHOLE-archive upload via a signed direct-to-storage URL.

    Why not just POST the .zip (the ``/imports/strava-archive`` endpoint)? A
    REAL Strava export is ~70 MB zipped / ~400 MB unzipped — it can't route
    through Cloud Run (~32 MB request cap) and must never be buffered on the
    512 Mi web instance. So the browser PUTs it STRAIGHT to a per-user bucket
    key via the signed URL returned here; the unzip happens later in the 2 Gi
    importer job. Nothing is stored/parsed on THIS request beyond the consent
    row + a tiny ``pending_archives`` bookkeeping row.

    Consent is REQUIRED — no consent → 422, nothing recorded.

    Per-user quota (HIGH 3, abuse/cost): each ``init`` mints a signed PUT URL
    and creates a ``PendingArchive`` row whose ``/complete`` fires the drain
    JOB, so unbounded ``init`` spam is a cost + storage vector. Reject with 429
    when the caller already has ``MAX_OPEN_ARCHIVES_PER_USER`` archives
    in-flight (not yet drained) OR has called ``init`` more than
    ``MAX_ARCHIVE_INITS_PER_DAY`` times in the last 24 h. Both env-overridable.
    """
    _validate_consent(body.consent, body.consent_version, body.consent_text, body.sport)

    from datetime import UTC, datetime, timedelta

    from app.db.models import PendingArchive
    from app.db.session import SessionLocal
    from app.services import archive_intake

    # Quota gate FIRST — fail fast before minting a signed URL / recording
    # consent. Open = still consuming a slot (not yet terminal done/failed).
    quota_db = SessionLocal()
    try:
        open_count = (
            quota_db.query(PendingArchive)
            .filter(
                PendingArchive.user_id == current_user.user_id,
                PendingArchive.status.in_(
                    ("awaiting_upload", "uploaded", "processing")
                ),
            )
            .count()
        )
        if open_count >= MAX_OPEN_ARCHIVES_PER_USER:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Too many archives in progress ({open_count}). Wait for the "
                    "current uploads to finish importing before starting another."
                ),
            )
        day_ago = datetime.now(UTC) - timedelta(days=1)
        day_count = (
            quota_db.query(PendingArchive)
            .filter(
                PendingArchive.user_id == current_user.user_id,
                PendingArchive.created_at >= day_ago,
            )
            .count()
        )
        if day_count >= MAX_ARCHIVE_INITS_PER_DAY:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Daily archive-upload limit reached "
                    f"({MAX_ARCHIVE_INITS_PER_DAY}/day). Try again tomorrow."
                ),
            )
    finally:
        quota_db.close()

    backend, bucket_key = archive_intake.new_archive_key(current_user.user_id)

    # GCS: mint the signed PUT URL up front (fail fast before we record consent
    # if the signer is misconfigured). Local: the URL keys off archive_id, set
    # after the row is created.
    signed_url = ""
    if backend == "gcs":
        try:
            signed_url = archive_intake.generate_signed_put_url(bucket_key)
        except Exception as exc:  # noqa: BLE001 — surface a clear 503, don't 500
            log.error("signed URL generation failed", exc_info=True)
            raise HTTPException(
                status_code=503,
                detail="Upload URL unavailable (storage signer misconfigured).",
            ) from exc

    db = SessionLocal()
    try:
        consent_id = archive_intake.record_consent(
            db,
            user_id=current_user.user_id,
            consent_version=body.consent_version.strip()[:100],
            consent_text=body.consent_text.strip(),
            locale=(body.locale or None),
            source=archive_intake.ARCHIVE_PROVENANCE,
        )
        row = PendingArchive(
            user_id=current_user.user_id,
            consent_id=consent_id,
            source=archive_intake.ARCHIVE_PROVENANCE,
            storage_backend=backend,
            bucket_key=bucket_key,
            fallback_sport=body.sport,
            contribute_heatmap=True,
            status="awaiting_upload",
        )
        db.add(row)
        db.commit()
        archive_id = row.id
    finally:
        db.close()

    # Local/dev stand-in: the browser PUTs to a backend endpoint (keyed by
    # archive_id) that lands the bytes under ARCHIVE_INTAKE_DIR (mirrors #451's
    # local fallback). Prod uses the direct-to-GCS signed URL.
    upload_url = signed_url if backend == "gcs" else f"/imports/strava-archive/upload/{archive_id}"

    return ArchiveInitResult(
        archive_id=archive_id,
        consent_id=consent_id,
        bucket_key=bucket_key,
        upload_url=upload_url,
        upload_method="PUT",
        content_type=archive_intake.ARCHIVE_CONTENT_TYPE,
        max_bytes=archive_intake.MAX_ARCHIVE_BYTES,
        storage_backend=backend,
    )


@router.put("/imports/strava-archive/upload/{archive_id}", status_code=200)
async def upload_strava_archive_local(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    archive_id: str,
    request: Request,
) -> dict:
    """LOCAL/DEV ONLY stand-in for the signed-URL PUT.

    In prod the browser PUTs to GCS directly and this endpoint is never hit.
    Locally there is no bucket, so the browser PUTs the .zip here and we land
    it under ARCHIVE_INTAKE_DIR. Buffering the body on the web is acceptable
    ONLY here because dev has no Cloud Run request cap / 512 Mi limit.
    """
    from app.db.models import PendingArchive
    from app.db.session import SessionLocal
    from app.services import archive_intake

    db = SessionLocal()
    try:
        row = db.get(PendingArchive, archive_id)
        if row is None or row.user_id != current_user.user_id:
            raise HTTPException(status_code=404, detail="Unknown archive.")
        if row.storage_backend != "local":
            raise HTTPException(status_code=400, detail="This archive uses direct-to-storage upload.")
        bucket_key = row.bucket_key
    finally:
        db.close()

    content = await request.body()
    if len(content) > archive_intake.MAX_ARCHIVE_BYTES:
        raise HTTPException(status_code=413, detail="Archive too large.")
    archive_intake.store_archive_local(bucket_key, content)
    return {"stored": len(content)}


@router.post("/imports/strava-archive/complete", response_model=ArchiveCompleteResult, status_code=202)
async def complete_strava_archive(
    current_user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    body: ArchiveCompleteRequest,
) -> ArchiveCompleteResult:
    """Finalise a signed-URL upload: verify the object landed + belongs to this
    user, flip the row to ``uploaded``, and kick the importer to drain it.

    The importer is a scale-to-zero 2 Gi Cloud Run JOB, NOT a Cloud Tasks → web
    handler: the unzip of a ~400 MB payload must run on the 2 Gi job, never on
    the 512 Mi web instance. So ``complete`` only marks the row ready then fires
    a best-effort ``jobs:run`` (``enqueue="triggered"``). If the trigger can't
    run (unconfigured / transient failure), ``enqueue`` stays ``"scheduled"``
    and the daily backstop scheduler drains the row.
    """
    from app.db.models import PendingArchive
    from app.db.session import SessionLocal
    from app.services import archive_intake

    # Ownership: the key must sit under THIS user's archive-intake prefix.
    if not archive_intake.owns_key(current_user.user_id, body.key):
        raise HTTPException(status_code=403, detail="Key does not belong to you.")

    db = SessionLocal()
    try:
        row = db.get(PendingArchive, body.archive_id)
        if row is None or row.user_id != current_user.user_id:
            raise HTTPException(status_code=404, detail="Unknown archive.")
        if row.bucket_key != body.key:
            raise HTTPException(status_code=400, detail="Key does not match this archive.")
        if not archive_intake.archive_exists(row.storage_backend, row.bucket_key):
            raise HTTPException(status_code=409, detail="Upload not found in storage yet.")
        # Size guard (SSOT MAX_ARCHIVE_BYTES). The signed PUT URL binds the
        # content-TYPE but NOT the size, so a user could PUT a multi-GB object.
        # Re-check from storage METADATA (no download) BEFORE flipping to
        # 'uploaded': an oversize object would otherwise be streamed into the
        # 2 Gi importer's tmpfs and OOM the whole drain batch. Reject 413, mark
        # the row 'failed' so it NEVER drains, and free the bucket object.
        size = archive_intake.archive_size(row.storage_backend, row.bucket_key)
        if size is not None and size > archive_intake.MAX_ARCHIVE_BYTES:
            reason = archive_intake.too_large_message(size)
            row.status = "failed"
            row.last_error = reason
            db.add(row)
            db.commit()
            archive_intake.delete_archive(row.storage_backend, row.bucket_key)
            raise HTTPException(status_code=413, detail=reason)
        row.status = "uploaded"
        db.add(row)
        db.commit()
    finally:
        db.close()

    # Event-driven kick (best-effort): fire the ingest-pending-archives job so
    # the upload is drained promptly. Defense-in-depth try/except on top of the
    # helper's own never-raise contract — a trigger failure must never fail the
    # request; the daily backstop scheduler catches anything missed.
    enqueue = "scheduled"
    try:
        if trigger_ingest_archives_job():
            enqueue = "triggered"
    except Exception as exc:
        log.warning("ingest-archives job trigger failed: %s", exc)

    return ArchiveCompleteResult(archive_id=body.archive_id, status="uploaded", enqueue=enqueue)
