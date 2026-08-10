"""Standalone Cloud Run Job: monthly resync of all Strava-connected users.

Usage:
    python -m app.jobs.resync_strava

Triggered by Cloud Scheduler monthly. Iterates all users with a valid Strava
IntegrationAccount, refreshes tokens, and incrementally imports new activities.

Uses a PostgreSQL advisory lock to prevent concurrent runs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.services.strava_client import STRAVA_API_BASE

if TYPE_CHECKING:
    from app.db.models import IntegrationAccount
    from app.db.session import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "5"))
BATCH_DELAY_SECONDS = int(os.environ.get("BATCH_DELAY_SECONDS", "60"))
MAX_PAGES_PER_USER = int(os.environ.get("MAX_PAGES_PER_USER", "5"))
ADVISORY_LOCK_ID = 2026032201  # arbitrary fixed int for pg_try_advisory_lock
MAX_429_RETRIES = 3  # max retries per page on rate limit

# ── Trailing-window reconciliation ────────────────────────────────────────────
# The catch-up scan is a FIXED TRAILING WINDOW, not a cursor. Scanning
# `after = now - RESYNC_WINDOW_DAYS` on every run re-checks the whole recent
# window regardless of chronology; ingest dedup (provider_activity_id) absorbs
# what we already have. See `_resync_user` for the full "dropped-then-cursor-
# advanced" failure this closes, and the FIX 3 cold-start note.
#
# 45 days is comfortably longer than any realistic gap between weekly runs +
# the longest plausible outage window, and — at one user's activity volume —
# fits well within MAX_PAGES_PER_USER × 100 activities.
RESYNC_WINDOW_DAYS = int(os.environ.get("RESYNC_WINDOW_DAYS", "45"))


async def resync_all() -> None:
    """Main resync logic — iterate all Strava users in batches."""
    from sqlalchemy import text

    from app.config import TEST_MODE
    from app.db.models import IntegrationAccount
    from app.db.session import SessionLocal
    from app.services.ingest import ingest_activity

    db = SessionLocal()
    try:
        # ── Acquire advisory lock ─────────────────────────────────────────
        lock_acquired = db.execute(
            text(f"SELECT pg_try_advisory_lock({ADVISORY_LOCK_ID})")
        ).scalar()
        if not lock_acquired:
            log.info("Another resync is already running — exiting.")
            return

        # ── Query eligible users ──────────────────────────────────────────
        accounts = (
            db.query(IntegrationAccount)
            .filter(
                IntegrationAccount.provider == "strava",
                IntegrationAccount.refresh_token.isnot(None),
                IntegrationAccount.sync_failures < 3,
            )
            .all()
        )
        log.info("Found %d eligible Strava accounts to resync", len(accounts))

        if not accounts:
            return

        total_synced = 0
        total_skipped = 0
        total_failed = 0
        total_new_activities = 0

        # ── Process in batches ────────────────────────────────────────────
        for batch_idx in range(0, len(accounts), BATCH_SIZE):
            batch = accounts[batch_idx : batch_idx + BATCH_SIZE]
            if batch_idx > 0:
                log.info("Sleeping %ds between batches…", BATCH_DELAY_SECONDS)
                await asyncio.sleep(BATCH_DELAY_SECONDS)

            for acct in batch:
                try:
                    new_count = await _resync_user(acct, db, TEST_MODE, ingest_activity)
                    total_synced += 1
                    total_new_activities += new_count
                except _SkipUser as e:
                    log.warning("Skipping user %s: %s", acct.user_id, e)
                    total_skipped += 1
                except Exception as exc:
                    log.error("Failed resync for user %s: %s", acct.user_id, exc)
                    total_failed += 1

            log.info(
                "Batch %d/%d done — synced=%d skipped=%d failed=%d new_activities=%d",
                batch_idx // BATCH_SIZE + 1,
                (len(accounts) + BATCH_SIZE - 1) // BATCH_SIZE,
                total_synced, total_skipped, total_failed, total_new_activities,
            )

        log.info(
            "RESYNC COMPLETE: %d synced, %d skipped, %d failed, %d new activities",
            total_synced, total_skipped, total_failed, total_new_activities,
        )
    finally:
        # Advisory lock released automatically on session close
        db.close()


class _SkipUser(Exception):
    """Raised when a user should be skipped (token revoked, etc.)."""


async def _resync_user(
    acct: IntegrationAccount,
    db: SessionLocal,
    test_mode: bool,
    ingest_activity_fn,
) -> int:
    """Resync a single user. Returns count of new activities imported."""
    import uuid

    # STRAVA_SPORT_MAP + decode_polyline live in `strava_utils`, not
    # `integrations_strava` — the previous wildcard-style re-import worked
    # by accident only because `integrations_strava` happens to import
    # those names from `strava_utils` at module load. Audit 2026-05-25
    # caught that the symbol `_decode_polyline` (with the leading
    # underscore) does NOT exist anywhere, so the FIRST time Cloud
    # Scheduler fires this on the 1st of the month it would raise
    # ImportError and silently miss the resync.
    from app.api.integrations_strava import refresh_strava_token
    from app.db.models import ImportJob
    from app.services.provenance import STRAVA_API_SOURCE
    from app.services.strava_utils import classify_strava_sport_or_skip, decode_polyline

    # ── Refresh token ─────────────────────────────────────────────────
    # `refresh_strava_token` now owns the sync_failures accounting for a
    # DEFINITIVE auth failure (Strava 400/401 → revoked/expired refresh
    # token) and emits the one-shot `strava_reconnect_required`
    # notification at the threshold crossing (FIX 2b). We no longer
    # increment here — doing so would double-count and mis-fire the
    # reconnect prompt. Transient failures (network, lock contention)
    # return None WITHOUT incrementing, so a flaky night doesn't nudge a
    # healthy account toward the reconnect banner.
    access_token = await refresh_strava_token(acct, db)
    if not access_token:
        raise _SkipUser("Token refresh failed")

    # ── Create ImportJob for tracking ─────────────────────────────────
    job = ImportJob(
        id=str(uuid.uuid4()),
        user_id=acct.user_id,
        provider="strava",
        status="RUNNING",
        cursor=json.dumps({"source": "resync"}),
    )
    db.add(job)
    db.commit()

    if test_mode:
        job.status = "COMPLETED"
        job.imported_count = 0
        acct.last_synced_at = datetime.now(UTC)
        acct.sync_failures = 0
        db.commit()
        return 0

    # ── Fetch the trailing reconciliation window ──────────────────────
    # NOT `after = last_synced_at`. A cursor permanently skips an activity
    # that was DROPPED (webhook lost during a cold start / 5xx / dedup
    # race) once a NEWER activity advanced the cursor past it:
    #
    #   t0  ride A created on Strava, webhook LOST (instance cold-start)
    #   t1  ride B created, webhook delivered → ingested → last_synced_at = t1
    #   ——  a cursor scan uses after=t1, so it NEVER re-sees A (A < t1).
    #
    # A fixed trailing window (`after = now - RESYNC_WINDOW_DAYS`) re-scans
    # the whole recent window every run, so A is re-fetched and ingested;
    # B (already present) is absorbed by ingest_activity's
    # provider_activity_id dedup ("skipped"). This is ALSO the sole
    # recovery path for cold-start webhook losses — min-instances=1 was
    # costed-and-rejected (see PR body, FIX 3).
    window_start = datetime.now(UTC) - timedelta(days=RESYNC_WINDOW_DAYS)
    after_epoch = int(window_start.timestamp())
    log.info(
        "Resync user %s: trailing-window scan after=%s (%d days), max %d pages",
        acct.user_id, window_start.isoformat(), RESYNC_WINDOW_DAYS, MAX_PAGES_PER_USER,
    )

    try:
        import httpx
        all_activities: list[dict] = []

        async with httpx.AsyncClient() as client:
            page = 1
            retries_429 = 0
            while page <= MAX_PAGES_PER_USER:
                resp = await client.get(
                    f"{STRAVA_API_BASE}/athlete/activities",
                    params={"page": page, "per_page": 100, "after": after_epoch},
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=20.0,
                )

                if resp.status_code == 401:
                    # 401 on the activities list even after a successful
                    # refresh = the grant was revoked mid-flight. Route
                    # through the shared recorder so it feeds the same
                    # reconnect-required threshold as a refresh 401.
                    from app.api.integrations_strava import record_refresh_failure
                    record_refresh_failure(acct, db)
                    job.status = "FAILED"
                    job.last_error = "Token expired after refresh"
                    db.commit()
                    raise _SkipUser("401 after token refresh")

                if resp.status_code == 429:
                    retries_429 += 1
                    if retries_429 > MAX_429_RETRIES:
                        log.warning("Rate limited %d times — giving up for user %s", retries_429, acct.user_id)
                        break
                    retry_after = int(resp.headers.get("Retry-After", "900"))
                    log.warning("Rate limited — sleeping %ds (attempt %d/%d)", retry_after, retries_429, MAX_429_RETRIES)
                    await asyncio.sleep(retry_after)
                    continue  # retry same page

                retries_429 = 0  # reset on success
                if not resp.is_success:
                    log.warning("Strava API %s on page %d for user %s", resp.status_code, page, acct.user_id)
                    break

                activities = resp.json()
                if not activities:
                    break

                all_activities.extend(activities)
                job.total_count = len(all_activities)
                db.commit()

                if len(activities) < 100:
                    break
                page += 1

        # ── Ingest activities ─────────────────────────────────────────
        # Heatmap-contribution preference lives on the IntegrationAccount
        # row (PR #348 / audit S2.7). `acct` is already loaded above. The
        # old per-Activity query is gone — the column is the single
        # source of truth maintained at OAuth + every /import_all click.
        contribute_heatmap = bool(acct.contribute_heatmap)

        imported = 0
        skipped = 0
        failed = 0
        private_skipped = 0
        sport_skipped = 0

        for act in all_activities:
            # Skip private + out-of-scope BEFORE the try block — these
            # are deterministic skips, not failures. Matches the bulk
            # paths' approach. See [[feedback_skip_beats_pollute_heatmap]].
            if act.get("private") is True:
                private_skipped += 1
                continue

            sport_type = act.get("sport_type") or act.get("type", "")
            # Pass the activity NAME for the SSOT gravel/mtb refinement
            # (a generic "Ride" named "Gravel ..." / "VTT ..." → gravel/mtb).
            sport = classify_strava_sport_or_skip(sport_type, act.get("name"))
            if sport is None:
                sport_skipped += 1
                continue

            try:
                geometry_geojson: str | None = None
                polyline = act.get("map", {}).get("summary_polyline") or ""
                if polyline:
                    coords = decode_polyline(polyline)
                    if len(coords) >= 2:
                        geometry_geojson = json.dumps({
                            "type": "LineString",
                            "coordinates": coords,
                        })

                result = ingest_activity_fn(
                    user_id=acct.user_id,
                    activity_data={
                        "provider": "strava",
                        "provider_activity_id": str(act["id"]),
                        # Strava-API provenance — personal-only (never community).
                        "source": STRAVA_API_SOURCE,
                        "sport": sport,
                        "name": act.get("name"),
                        "geometry_geojson": geometry_geojson,
                        "distance_m": act.get("distance"),
                        "elevation_gain_m": act.get("total_elevation_gain"),
                        "moving_time": act.get("moving_time"),
                        "activity_date": act.get("start_date_local") or act.get("start_date"),
                    },
                    contribute_heatmap=contribute_heatmap,
                )
                if result["status"] == "created":
                    imported += 1
                    # Per-activity "ride synced (personal view)" toast — the resync's new
                    # rows are webhook events dropped during a cold start,
                    # so each genuinely-new one deserves the same toast the
                    # webhook worker emits. Dedup no-ops return status
                    # "already_exists" (→ else branch), sport skips `continue`
                    # above the try — neither reaches here. Fail-soft.
                    try:
                        from app.services.notifications import emit_activity_synced
                        emit_activity_synced(
                            user_id=acct.user_id,
                            activity_name=act.get("name"),
                            sport=sport,
                            provider_activity_id=str(act["id"]),
                        )
                    except Exception:  # noqa: BLE001 — notification is best-effort
                        log.warning(
                            "activity_synced notification failed user=%s strava_id=%s",
                            acct.user_id, act.get("id"), exc_info=True,
                        )
                else:
                    skipped += 1
            except Exception as exc:
                failed += 1
                # Sentry capture so failed ingests during monthly
                # resync aren't invisible. The except above used to
                # swallow the exception silently — exactly the kind
                # of blind spot the 2026-05-25 audit was supposed to
                # close.
                import sentry_sdk
                sentry_sdk.set_tag("strava.phase", "resync_ingest")
                sentry_sdk.set_tag("strava.user_id", acct.user_id)
                sentry_sdk.capture_exception(exc)
                log.warning(
                    "Resync ingest failure user=%s strava_id=%s: %s",
                    acct.user_id, act.get("id"), exc, exc_info=True,
                )

        if private_skipped or sport_skipped:
            log.info(
                "Resync user=%s: skipped %d private + %d out-of-scope sport (kept %d)",
                acct.user_id, private_skipped, sport_skipped, len(all_activities) - private_skipped - sport_skipped,
            )

        job.imported_count = imported
        job.skipped_count = skipped
        job.failed_count = failed
        job.status = "COMPLETED"
        acct.last_synced_at = datetime.now(UTC)
        acct.sync_failures = 0
        db.commit()

        log.info(
            "User %s: imported=%d skipped=%d failed=%d",
            acct.user_id, imported, skipped, failed,
        )

        # Notify the user that the monthly resync finished — surfaces a
        # toast at their next app load even though the job runs unattended.
        if imported > 0 or failed > 0:
            from app.services.notifications import emit_notification
            emit_notification(
                user_id=acct.user_id,
                kind="strava_resync_complete",
                title="Resynchronisation Strava terminée",
                body=(
                    f"{imported} nouvelle{'s' if imported != 1 else ''} activité{'s' if imported != 1 else ''}"
                    + (f", {failed} erreur{'s' if failed != 1 else ''}" if failed else "")
                ),
                meta={
                    "job_id": job.id,
                    "imported": imported,
                    "skipped": skipped,
                    "failed": failed,
                },
            )
        return imported

    except _SkipUser:
        raise
    except Exception as exc:
        import sentry_sdk
        sentry_sdk.set_tag("strava.phase", "monthly_resync")
        sentry_sdk.set_tag("strava.user_id", acct.user_id)
        sentry_sdk.capture_exception(exc)
        job.status = "FAILED"
        job.last_error = str(exc)[:500]
        db.commit()
        log.exception("Resync failed for user %s", acct.user_id)
        return 0


def main() -> None:
    log.info("Starting monthly Strava resync job")
    asyncio.run(resync_all())


if __name__ == "__main__":
    main()
