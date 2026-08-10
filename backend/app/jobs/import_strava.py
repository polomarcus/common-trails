"""Standalone Cloud Run Job entrypoint for Strava import.

Usage:
    python -m app.jobs.import_strava

Expects IMPORT_JOB_ID env var (set as container override when the API triggers the job).
Connects to DB via DATABASE_URL, fetches the ImportJob, resolves the user's Strava token,
and runs the same import logic as the in-process background task.

Performance: uses ingest_activities_bulk() with summary polylines — 1 DB session,
batch commits, no per-activity API calls, no heat computation. ~30s for 1,400 activities.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as _Session

    from app.db.models import ImportJob

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

from app.services.strava_client import STRAVA_API_BASE
from app.services.strava_utils import classify_strava_sport_or_skip, decode_polyline


def _set_cursor(job: ImportJob, db: _Session, **fields: object) -> None:
    """Update cursor JSON fields on the job (phase, current_page, etc.)."""
    cursor_data: dict = {}
    if job.cursor:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            cursor_data = json.loads(job.cursor)
    cursor_data.update(fields)
    job.cursor = json.dumps(cursor_data)
    db.commit()


# Phase 3 (`_run_gps_upgrade`) and Phase 4 (`_run_photo_import`) live in
# `app/api/integrations_strava.py`. The Job calls them by import below
# (`from app.api.integrations_strava import _run_gps_upgrade`) instead of
# duplicating the implementation here. Local duplication was the cause of
# the 2026-05-27 OOM: the API path had been refactored to short-session
# + Cloud-Tasks-deferred heat-edge updates (PR #314 / PR-E) but the Job's
# stale copy still held the outer DB session across `await` and ran
# `_update_heat_edges` inline serial. See [[project_ingestion_audit_2026_05_27]].


async def run_import(job_id: str) -> None:
    """Main import logic — bulk ingest using summary polylines."""
    import httpx
    from sqlalchemy import text as sa_text

    from app.db.models import ImportJob, IntegrationAccount
    from app.db.session import SessionLocal
    from app.services.ingest import ingest_activities_bulk
    from app.services.provenance import STRAVA_API_SOURCE

    db = SessionLocal()
    try:
        job = db.query(ImportJob).filter(ImportJob.id == job_id).first()
        if not job:
            log.error("ImportJob %s not found — exiting", job_id)
            sys.exit(1)

        log.info("ImportJob %s: user=%s provider=%s", job.id, job.user_id, job.provider)

        # ── Zombie cleanup ────────────────────────────────────────────────
        # A previous Job that crashed before it could write status='FAILED'
        # (e.g. the 2026-05-26 JWT_SECRET incident: container exited at
        # module-import time, never reached the try/finally) leaves a row
        # with status='RUNNING' forever. The per-user queue below then
        # waits up to 6 h for that ghost to finish, blocking every
        # subsequent import for that user.
        #
        # On startup, mark any of THIS user's RUNNING jobs whose
        # `updated_at` hasn't moved in 30 min as FAILED. 30 min is
        # generous — the slowest legitimate phase (Phase 4 photo import
        # over 1400 activities) emits cursor updates every batch of 25
        # well within that window. If your job legitimately needs longer
        # without any cursor update, that's a code-smell to fix in the
        # phase handler.
        ZOMBIE_THRESHOLD_MIN = int(os.environ.get("ZOMBIE_JOB_THRESHOLD_MIN", "30"))
        cleaned = db.execute(sa_text("""
            UPDATE import_jobs
            SET status = 'FAILED',
                -- Cap to ~2 KB so repeated cleanups can't blow up the
                -- column. Postgres TEXT has no hard limit but the UI
                -- and JSON serializer choke past a few KB.
                last_error = LEFT(
                    COALESCE(last_error, '') ||
                    ' [auto-cleaned: stale RUNNING with no progress >' || :thresh || 'min]',
                    2048
                ),
                updated_at = NOW()
            WHERE user_id = :uid
              AND status = 'RUNNING'
              AND id != :me
              AND updated_at < NOW() - make_interval(mins => :thresh)
            RETURNING id
        """), {"uid": job.user_id, "me": job_id, "thresh": ZOMBIE_THRESHOLD_MIN}).fetchall()
        if cleaned:
            db.commit()
            log.warning(
                "Cleaned %d zombie RUNNING job(s) for user %s: %s",
                len(cleaned), job.user_id, [str(r[0]) for r in cleaned],
            )

        # Queue guard: serialize imports per-user (not globally). A single
        # user's RUNNING job must not block other users — with a 50-friend
        # beta the cross-user queue piled up for hours. The Strava 100/15-min
        # per-app rate limit is a separate concern best handled by Cloud
        # Tasks throttling, NOT in scope for this PR. PR D of the
        # strava-tough-review audit (Sev-2 #5).
        max_wait_minutes = int(os.environ.get("IMPORT_QUEUE_WAIT_MIN", "360"))  # 6h max
        waited = 0
        while waited < max_wait_minutes * 60:
            # Belt-and-suspenders: even if zombie cleanup above missed
            # something (race with a Job that crashed during this Job's
            # startup), the age filter here also ignores RUNNING rows
            # that haven't moved in `ZOMBIE_THRESHOLD_MIN`.
            running = db.execute(sa_text("""
                SELECT id FROM import_jobs WHERE user_id = :uid AND status = 'RUNNING'
                  AND id != :me
                  AND updated_at > NOW() - make_interval(mins => :thresh)
                LIMIT 1
            """), {"uid": job.user_id, "me": job_id, "thresh": ZOMBIE_THRESHOLD_MIN}).fetchone()
            if not running:
                break
            if waited == 0:
                log.info("Another import is running (%s) — waiting in queue...", running[0])
                # Phase marker in the cursor JSON is the canonical signal
                # for the frontend — string-matching last_error is brittle
                # (any i18n copy change breaks the UI). The text message
                # stays for ops debugging.
                _set_cursor(job, db, phase="queued")
                job.last_error = "En file d'attente — un autre import est en cours."
                db.commit()
            await asyncio.sleep(30)
            waited += 30
            db.expire_all()  # refresh session to see status changes
        if waited >= max_wait_minutes * 60:
            job.status = "FAILED"
            job.last_error = "Timeout en file d'attente (6h). Réessayez plus tard."
            db.commit()
            log.error("Queue timeout for job %s", job_id)
            return

        # Mark as RUNNING
        job.status = "RUNNING"
        db.commit()

        acct = db.query(IntegrationAccount).filter(
            IntegrationAccount.user_id == job.user_id,
            IntegrationAccount.provider == "strava",
        ).first()
        if not acct:
            job.status = "FAILED"
            job.last_error = "Compte Strava non connecté."
            db.commit()
            log.error("No Strava account for user %s", job.user_id)
            return

        user_id = job.user_id

        # Refresh token if expired (Strava tokens expire every 6h)
        from app.api.integrations_strava import refresh_strava_token
        access_token = await refresh_strava_token(acct, db)
        if not access_token:
            job.status = "FAILED"
            job.last_error = "Token Strava expiré — reconnectez Strava."
            db.commit()
            log.error("Token refresh failed for user %s", user_id)
            return

        # Read heatmap preference from cursor
        contribute_heatmap = True
        if job.cursor:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                contribute_heatmap = json.loads(job.cursor).get("contribute_heatmap", True)

        # ── Phase 1: Discover all activities (paginated, rate-limited) ────
        _set_cursor(job, db, phase="discovering")
        all_activities: list[dict] = []

        try:
            async with httpx.AsyncClient() as client:
                page = 1
                while True:
                    resp = await client.get(
                        f"{STRAVA_API_BASE}/athlete/activities",
                        params={"page": page, "per_page": 200},
                        headers={"Authorization": f"Bearer {access_token}"},
                        timeout=20.0,
                    )
                    if resp.status_code == 401:
                        job.status = "FAILED"
                        job.last_error = "Token Strava expiré — reconnectez Strava."
                        db.commit()
                        log.error("Strava token expired for user %s", user_id)
                        return
                    if resp.status_code == 429:
                        retry_after = int(resp.headers.get("Retry-After", "60"))
                        log.warning("Strava rate limit on page %d, waiting %ds", page, retry_after)
                        await asyncio.sleep(retry_after)
                        continue
                    if not resp.is_success:
                        log.warning("Strava API %s on page %d", resp.status_code, page)
                        break
                    activities = resp.json()
                    if not activities:
                        break

                    all_activities.extend(activities)
                    job.total_count = len(all_activities)
                    _set_cursor(job, db, current_page=page)
                    log.info("Discovery page %d: %d activities total", page, len(all_activities))

                    if len(activities) < 200:
                        break
                    page += 1

            # ── Phase 2: Bulk ingest using summary polylines ──────────────
            _set_cursor(job, db, phase="ingesting")
            log.info("Bulk ingesting %d activities (polyline-only, no heat computation)", len(all_activities))

            # Prepare activity data list (pure CPU, no DB/API calls).
            # Same privacy + sport gates as the in-process bulk path.
            # See [[feedback_skip_beats_pollute_heatmap]].
            activities_data: list[dict] = []
            phase2_private_skipped = 0
            phase2_sport_skipped = 0
            for act in all_activities:
                if act.get("private") is True:
                    phase2_private_skipped += 1
                    continue

                sport_type = act.get("sport_type") or act.get("type", "")
                # Pass the activity NAME for the SSOT gravel/mtb refinement
                # (a generic "Ride" named "Gravel ..." / "VTT ..." → gravel/mtb).
                sport = classify_strava_sport_or_skip(sport_type, act.get("name"))
                if sport is None:
                    phase2_sport_skipped += 1
                    continue

                geometry_geojson: str | None = None
                polyline = act.get("map", {}).get("summary_polyline") or ""
                if polyline:
                    coords = decode_polyline(polyline)
                    if len(coords) >= 2:
                        geometry_geojson = json.dumps({
                            "type": "LineString",
                            "coordinates": coords,
                        })

                activities_data.append({
                    "provider": "strava",
                    "provider_activity_id": str(act["id"]),
                    "sport": sport,
                    "name": act.get("name"),
                    "geometry_geojson": geometry_geojson,
                    "distance_m": act.get("distance"),
                    "elevation_gain_m": act.get("total_elevation_gain"),
                    "moving_time": act.get("moving_time"),
                    "total_photo_count": act.get("total_photo_count", 0),
                    "activity_date": act.get("start_date_local") or act.get("start_date"),
                })

            if phase2_private_skipped or phase2_sport_skipped:
                log.info(
                    "Cloud Run Job Phase 2: skipped %d private + %d out-of-scope sport (kept %d)",
                    phase2_private_skipped, phase2_sport_skipped, len(activities_data),
                )

            # Progress callback: uses a separate session to avoid conflicts
            # with the bulk ingest session inside ingest_activities_bulk()
            _job_id = job.id

            def _on_progress(c: int, s: int, f: int) -> None:
                progress_db = SessionLocal()
                try:
                    progress_db.execute(
                        sa_text("UPDATE import_jobs SET imported_count=:c, skipped_count=:s, failed_count=:f WHERE id=:id"),
                        {"c": c, "s": s, "f": f, "id": _job_id},
                    )
                    progress_db.commit()
                finally:
                    progress_db.close()
                log.info("Progress: %d created, %d skipped, %d failed", c, s, f)

            # Single bulk ingest: 1 DB session, batched commits, no heat computation
            result = ingest_activities_bulk(
                user_id=user_id,
                activities_data=activities_data,
                contribute_heatmap=contribute_heatmap,
                on_progress=_on_progress,
                # Strava-API provenance — personal-only (never community).
                source=STRAVA_API_SOURCE,
            )

            log.info(
                "Bulk ingest complete: created=%d skipped=%d failed=%d",
                result["created"], result["skipped"], result["failed"],
            )

            # Update counts after bulk ingest
            db.execute(sa_text(
                "UPDATE import_jobs SET imported_count=:c, skipped_count=:s, "
                "failed_count=:f, cursor=:cursor WHERE id=:id"
            ), {
                "c": result["created"], "s": result["skipped"], "f": result["failed"],
                "cursor": json.dumps({"contribute_heatmap": contribute_heatmap, "phase": "ingesting"}),
                "id": _job_id,
            })
            db.commit()
            log.info("Bulk ingest complete")

            # ── Phase 3: GPS upgrade (high-res streams) ─────────────────
            # Snapshot the polyline-only count for the progress UI BEFORE
            # the phase runs. Mirrors the API path (`integrations_strava.
            # py:669`). Without this snapshot the gps_total field stays
            # at 0 and the UI shows "0 / 0" instead of "n / m".
            from app.api.integrations_strava import (
                _count_polyline_activities,
                _emit_bulk_import_complete,
                _mark_account_synced,
                _run_gps_upgrade,
            )
            gps_total_snapshot = _count_polyline_activities(user_id)
            # Flip status='COMPLETED' here (end of Phase 2) so the Job
            # matches the API path's invariant — Phase 3 + 4 are
            # background polish that run AFTER status is COMPLETED.
            # Without this, the Phase-2 notification below would fire
            # while status='RUNNING' (PR #347 review #1). The final
            # "all phases done" UPDATE at the end of the function keeps
            # status='COMPLETED' (defensive re-write).
            db.execute(
                sa_text(
                    "UPDATE import_jobs SET gps_total=:t, status='COMPLETED' "
                    "WHERE id=:id"
                ),
                {"t": gps_total_snapshot, "id": _job_id},
            )
            db.commit()
            # Stamp last_synced_at + reset sync_failures so the daily
            # health-check cron sees this as recent (audit S2.8). The
            # API path's `_mark_account_synced` opens its own short-
            # lived session, so it's safe to call here without
            # interfering with `db`.
            _mark_account_synced(user_id)
            # Phase-2 completion notification — keeps the UI promise
            # "Vous recevrez une notification" even when Phase 3 + 4
            # have nothing to do (small accounts with 0 polyline + 0
            # photos). See [[project_ingestion_audit_2026_05_27]] S2.1.
            _emit_bulk_import_complete(
                user_id, _job_id, result, gps_total_snapshot,
            )
            _set_cursor(job, db, phase="gps_upgrade")
            log.info("Starting GPS upgrade phase (gps_total=%d)", gps_total_snapshot)
            # Reuse the API path's short-session implementation —
            # short-lived sessions per batch, heat compute deferred to
            # Cloud Tasks. No DB session is held across `await`.
            await _run_gps_upgrade(job.id, user_id, access_token)

            # ── Phase 4: Photo import ───────────────────────────────────
            # The in-process bulk path (integrations_strava._run_strava_
            # import) runs `_run_photo_import` here. The Cloud Run Job
            # path was missing it — silent feature loss when the api
            # service delegates to the Job (the default since #314).
            # Closes Sev-2 inconsistency found in the 2026-05-26 review.
            _set_cursor(job, db, phase="photo_import")
            log.info("Starting photo import phase")
            try:
                from app.api.integrations_strava import _run_photo_import
                await _run_photo_import(job.id, user_id, access_token)
            except Exception as photo_exc:
                # Photos are not load-bearing — failing them shouldn't
                # mark the whole import FAILED. Log + Sentry + continue.
                log.warning("Photo import failed (continuing): %s", photo_exc, exc_info=True)
                try:
                    import sentry_sdk
                    sentry_sdk.set_tag("strava.phase", "photo_import")
                    sentry_sdk.capture_exception(photo_exc)
                except Exception:
                    pass

            # Final: mark COMPLETED
            db.execute(sa_text(
                "UPDATE import_jobs SET status='COMPLETED', cursor=:cursor WHERE id=:id"
            ), {
                "cursor": json.dumps({"contribute_heatmap": contribute_heatmap, "phase": "done"}),
                "id": _job_id,
            })
            db.commit()
            log.info("Import COMPLETED (all phases)")

        except Exception as exc:
            job.status = "FAILED"
            job.last_error = str(exc)
            db.commit()
            log.exception("Import FAILED")
            # Parity with the API path's outer handler
            # (`integrations_strava.py:698-722`). Without these, a
            # Job-path failure was silent both to ops (no Sentry) and
            # to the user (no toast on next login). Audit 2026-05-27
            # S3.2 / PR #347 review #18.
            try:
                import sentry_sdk
                sentry_sdk.set_tag("strava.phase", "import_outer")
                sentry_sdk.set_tag("strava.job_id", job_id)
                sentry_sdk.capture_exception(exc)
            except Exception:  # noqa: BLE001
                pass
            try:
                from app.services.notifications import emit_notification
                emit_notification(
                    user_id=job.user_id,
                    kind="strava_import_failed",
                    title="Import Strava interrompu",
                    body=str(exc)[:200],
                    meta={"job_id": job_id, "phase": "outer"},
                )
            except Exception:  # noqa: BLE001 — notification path must not crash the job
                log.warning("emit_notification failed", exc_info=True)
    finally:
        db.close()


def main() -> None:
    job_id = os.environ.get("IMPORT_JOB_ID")
    if not job_id:
        log.error("IMPORT_JOB_ID env var is required")
        sys.exit(1)

    log.info("Starting Strava import job %s", job_id)
    asyncio.run(run_import(job_id))


if __name__ == "__main__":
    main()
