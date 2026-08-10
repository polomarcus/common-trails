"""Daily Strava-workflow health check — runs as a Cloud Scheduler cron.

Hit by `google_cloud_scheduler_job.strava_health_check_daily` once a day
(09:00 Europe/Paris). Runs cheap SQL queries against the local DB to
detect known failure modes that would otherwise go unnoticed:

  1. **Zombie ImportJob rows** — RUNNING with no progress in > 3 h.
     Should be caught by the bootstrap wrapper (PR #338) but we add a
     belt-and-suspenders alert. 3 h = resync Job timeout (2 h) + buffer.
     For each zombie row we ALSO flip status RUNNING → FAILED and emit a
     `strava_import_failed` notification to the affected user. This is
     the safety net for signal-9 / OOM kills, where Python's `except`
     blocks (and therefore the in-process failure handler at
     `integrations_strava.py:712`) never run.
  2. **Webhook subscription dead** — no Strava-provider activities
     created in the last 30 days. Either no friends are riding (unlikely)
     OR the Strava push subscription expired / our endpoint started
     5xx-ing (silent failure mode — see
     [[project_strava_webhook_architecture]]).
  3. **Strava accounts with persistent sync failures** —
     `IntegrationAccount.sync_failures > 5` AND no successful sync
     in the last 7 days. The `last_synced_at` gate prevents false-
     tripping on counters that didn't decrement after a recent success.
     Indicates a token revoke race or persistent 401/403.
  4. **Webhook subscription liveness (proactive)** — asks Strava's
     `push_subscriptions` API directly whether our subscription still
     exists and still points at us. Catches a dead / mis-pointed
     subscription within a DAY, vs check #2's reactive 30-day window.
     See `check_subscription_liveness`.

Any check that fires triggers `sentry_sdk.capture_message(level=warning)`
with structured tags. Sentry's own alert rules (configured in the UI)
fan out to email/Slack — no in-code alert routing needed.

Auth: same OIDC pattern as the other `/internal/*` endpoints. The
Cloud Scheduler is set up in terraform with an OIDC token whose
audience is this endpoint's URL.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text as sa_text

from app.api.internal_ingest import verify_oidc_token
from app.services.strava_client import STRAVA_API_BASE

router = APIRouter(prefix="/internal/strava", tags=["internal"])
log = logging.getLogger(__name__)


def _is_test_mode() -> bool:
    return os.environ.get("TEST_MODE", "false").lower() == "true"


def check_subscription_liveness() -> dict[str, Any]:
    """Assert the Strava push-subscription is alive and points at US.

    Calls Strava `GET /push_subscriptions` (the same endpoint as
    `python -m app.cli.strava_subscribe list`) and classifies the result:

      - ``ok``            — exactly the expected callback URL is registered.
      - ``missing``       — Strava has NO subscription for this app. Every
                            new activity is silently lost; the daily
                            "no activity in 30d" check only notices this
                            30 days too late.
      - ``url_mismatch``  — a subscription exists but points somewhere else
                            (stale URL after a redeploy / domain change).
                            Events go to the wrong endpoint → also silent loss.
      - ``unknown``       — creds/callback not configured (local/dev, or the
                            secret isn't mounted). We SKIP rather than alert.
      - ``error``         — the Strava call itself failed (network / non-200).

    Reported LOUDLY (as a health-check finding + Sentry warning) for
    ``missing`` / ``url_mismatch`` so ops learns within a day, not a month.

    Reads creds from env so the function is drivable in tests with a
    monkeypatched ``httpx.get`` — no real Strava call and no TEST_MODE
    short-circuit inside the function itself (the caller decides whether to
    invoke it). Only the query params below carry the secret, matching the
    CLI; we never log the URL.
    """
    client_id = os.environ.get("STRAVA_CLIENT_ID", "").strip()
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "").strip()
    expected_url = os.environ.get("STRAVA_WEBHOOK_CALLBACK_URL", "").strip()

    if not (client_id and client_secret and expected_url):
        return {
            "verdict": "unknown",
            "detail": "Strava webhook creds/callback not configured — skipped.",
        }

    import httpx

    try:
        resp = httpx.get(
            f"{STRAVA_API_BASE}/push_subscriptions",
            params={"client_id": client_id, "client_secret": client_secret},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        # Log only the class name — the URL carries client_secret.
        log.warning("Strava subscription liveness transport error: %s", type(exc).__name__)
        return {"verdict": "error", "detail": f"transport error: {type(exc).__name__}"}

    if resp.status_code != 200:
        log.warning("Strava subscription liveness non-200: %s", resp.status_code)
        return {"verdict": "error", "detail": f"status={resp.status_code}"}

    try:
        subs = resp.json()
    except ValueError:
        return {"verdict": "error", "detail": "non-JSON body"}

    if not subs:
        return {
            "verdict": "missing",
            "detail": "Strava reports NO active push subscription for this app.",
            "registered_urls": [],
        }

    registered = [s.get("callback_url") for s in subs if isinstance(s, dict)]
    if any(u == expected_url for u in registered):
        return {"verdict": "ok", "detail": "subscription present", "registered_urls": registered}
    return {
        "verdict": "url_mismatch",
        "detail": (
            f"Subscription exists but callback_url does not match expected "
            f"{expected_url!r} (registered: {registered!r})."
        ),
        "registered_urls": registered,
    }


@router.post("/health-check")
async def strava_health_check(request: Request) -> dict[str, Any]:
    """Run all Strava-workflow sanity checks. Returns structured outcome.

    Cloud Scheduler ignores the body but we return useful JSON for
    manual invocation (`curl -X POST .../internal/strava/health-check
    -H 'Authorization: Bearer $(gcloud auth print-identity-token)'`).
    """
    if not _is_test_mode():
        verify_oidc_token(request, audience_env="INTERNAL_STRAVA_HEALTH_HANDLER_URL")

    from app.db.session import SessionLocal

    db = SessionLocal()
    findings: list[dict[str, Any]] = []
    try:
        # Check 1 — zombie ImportJob rows.
        # 3 h threshold = `google_cloud_run_v2_job.import_strava.timeout`
        # (7200s / 2h in `infra/terraform/main.tf`) + 1h buffer. Friends-
        # beta users with 5k+ activities + Strava rate-limit (100/15min)
        # can legitimately take ~2h for a full re-import; anything
        # beyond 3h is genuinely stuck. Mirrors the 30-min threshold the
        # import job uses internally for its OWN cleanup
        # (`import_strava.py`) — this is the SECOND-level catch via cron
        # when the first-level reactive cleanup didn't fire (no new Job
        # launched). Audit 2026-05-27 S3.5: **if you ever bump the
        # terraform Job timeout past 7200s, bump this INTERVAL to match
        # (timeout + 1h buffer) — otherwise the cron will start
        # false-tripping on healthy long-running imports.**
        zombie_rows = db.execute(sa_text("""
            SELECT id, user_id, updated_at, last_error
            FROM import_jobs
            WHERE status = 'RUNNING'
              AND updated_at < NOW() - INTERVAL '3 hours'
            ORDER BY updated_at ASC
            LIMIT 20
        """)).fetchall()
        if zombie_rows:
            findings.append({
                "check": "zombie_import_jobs",
                "severity": "warning",
                "count": len(zombie_rows),
                "sample": [
                    {"id": str(r[0]), "user_id": r[1], "updated_at": r[2].isoformat() if r[2] else None}
                    for r in zombie_rows[:5]
                ],
                "message": (
                    f"{len(zombie_rows)} ImportJob row(s) stuck on status='RUNNING' "
                    "with no progress > 3 h. Reactive cleanup at next Job launch "
                    "should fix automatically; if recurring, investigate."
                ),
            })

            # OOM-kill safety net (signal-9 bypasses Python `except` blocks,
            # so the in-process failure handler at
            # `integrations_strava.py:712` never gets to call
            # `emit_notification(kind="strava_import_failed", ...)`). The
            # frontend promises "Vous recevrez une notification à votre
            # prochaine connexion" — we keep that promise here.
            #
            # Idempotency: we atomically flip status RUNNING → FAILED with a
            # WHERE clause guarding on status='RUNNING'. A row that's been
            # flipped on a previous run (or by reactive cleanup since the
            # 3h-old SELECT) returns 0 affected rows from the UPDATE — we
            # skip the notification in that branch. Sentry is fed by the
            # SELECT regardless (ops-facing signal: this row was a zombie
            # at SELECT time, even if cleaned up between SELECT and UPDATE).
            # Two layers of deduplication:
            #
            # 1. Per-row: the `UPDATE ... WHERE status='RUNNING'` is
            #    atomic. A row already flipped by reactive cleanup or by
            #    a concurrent cron run returns 0 affected rows; we skip
            #    the notification on that path.
            # 2. Per-user-per-cron: a friends-beta user retrying a
            #    5k-activity import 20× would otherwise get 20 toasts.
            #    Notify only the first zombie row per user; the rest are
            #    still flipped to FAILED so they drain from the table.
            from app.services.notifications import emit_notification
            notified_users: set = set()
            for r in zombie_rows:
                job_id, user_id_z = str(r[0]), r[1]
                updated_rows = db.execute(
                    sa_text("""
                        UPDATE import_jobs
                        SET status = 'FAILED',
                            last_error = :err,
                            updated_at = NOW()
                        WHERE id = :id
                          AND status = 'RUNNING'
                    """),
                    {
                        "id": job_id,
                        "err": (
                            "job died without reporting outcome "
                            "(likely OOM or container kill)"
                        ),
                    },
                ).rowcount
                db.commit()
                if not updated_rows:
                    # Lost the race — already flipped. Skip notify.
                    continue
                if user_id_z in notified_users:
                    # User already got a toast for an older zombie row
                    # earlier in this loop. Status is flipped (above);
                    # don't fan out more notifications.
                    continue
                emit_notification(
                    user_id=user_id_z,
                    kind="strava_import_failed",
                    title="Import Strava interrompu",
                    body=(
                        "Votre import Strava a été interrompu — "
                        "vous pouvez le relancer depuis la page Strava."
                    ),
                    meta={
                        "job_id": job_id,
                        "reason": "oom_or_container_kill",
                        "detected_by": "strava_health_check",
                    },
                )
                notified_users.add(user_id_z)

        # Check 2 — webhook subscription liveness.
        # Count Strava-provider activities created in the last N days.
        # 30-day window because friends-beta is small (5-10 users) —
        # a single quiet 7-day stretch (vacation, bad weather) would
        # have false-tripped the alert. A 30-day silence is genuine
        # signal that the subscription died. Bump back to 7 days once
        # active-user count > 30.
        webhook_count = db.execute(sa_text("""
            SELECT COUNT(*)
            FROM activities
            WHERE provider = 'strava'
              AND created_at > NOW() - INTERVAL '30 days'
        """)).scalar() or 0
        if webhook_count == 0:
            findings.append({
                "check": "webhook_subscription_silent",
                "severity": "warning",
                "count": 0,
                "message": (
                    "No Strava-provider activities created in 30 days. "
                    "Either friends-beta is dormant OR our Strava push "
                    "subscription died silently (see "
                    "`python -m app.cli.strava_subscribe list`)."
                ),
            })

        # Check 3 — persistent sync failures per user.
        # `sync_failures` is incremented on each failure but isn't
        # currently reset on the next success (open bug). To avoid
        # false-tripping on users who had ONE failure months ago but
        # have been syncing fine since, gate the alert on
        # `last_synced_at < NOW() - 7d` so a recently-successful sync
        # is treated as "the failures cleared" even if the counter
        # didn't decrement. Also catch the case where last_synced_at
        # is NULL (account that has never synced + has failures).
        failing_accts = db.execute(sa_text("""
            SELECT id, user_id, sync_failures, last_synced_at
            FROM integration_accounts
            WHERE provider = 'strava'
              AND sync_failures > 5
              AND (last_synced_at IS NULL
                   OR last_synced_at < NOW() - INTERVAL '7 days')
            ORDER BY sync_failures DESC
            LIMIT 20
        """)).fetchall()
        if failing_accts:
            findings.append({
                "check": "strava_sync_failures",
                "severity": "warning",
                "count": len(failing_accts),
                "sample": [
                    {
                        "account_id": str(r[0]),
                        "user_id": r[1],
                        "sync_failures": r[2],
                        "last_synced_at": r[3].isoformat() if r[3] else None,
                    }
                    for r in failing_accts[:5]
                ],
                "message": (
                    f"{len(failing_accts)} Strava account(s) have sync_failures > 5. "
                    "Likely token-revoke race or persistent 401/403. "
                    "Ops should check Sentry for `strava.deauth_detected` events."
                ),
            })

        # Check 4 — webhook subscription LIVENESS (proactive).
        # Check 2 above is reactive and 30 days slow: it only fires once
        # a full month of silence has accrued. This check asks Strava
        # directly whether our push subscription still exists and still
        # points at us — catching a dead / mis-pointed subscription within
        # a day of the daily cron. Skipped (verdict=unknown) when creds
        # aren't configured, e.g. local/TEST_MODE, so it never needs a
        # network call there.
        if not _is_test_mode():
            liveness = check_subscription_liveness()
            if liveness["verdict"] in ("missing", "url_mismatch"):
                findings.append({
                    "check": "webhook_subscription_liveness",
                    "severity": "warning",
                    "verdict": liveness["verdict"],
                    "message": (
                        f"Strava push subscription {liveness['verdict']}: "
                        f"{liveness['detail']} Recreate with "
                        "`python -m app.cli.strava_subscribe create`."
                    ),
                    "registered_urls": liveness.get("registered_urls", []),
                })

        # Daily heatmap evolution snapshot — piggy-back on this already-scheduled
        # daily cron (cheapest place to get a once-a-day data point without a
        # NEW scheduler). Fully fail-soft inside the helper; a snapshot hiccup
        # never affects the health-check outcome.
        try:
            from app.jobs.build_pmtiles import capture_heatmap_metrics_snapshot
            capture_heatmap_metrics_snapshot(db, source="daily", min_uc=1)
        except Exception as snap_exc:  # noqa: BLE001 — belt-and-suspenders
            log.warning("daily heatmap_metrics snapshot failed: %s", snap_exc)

        # Emit each finding to Sentry as a warning so Sentry's UI
        # alert rules can fan out to email/Slack. We use
        # capture_message (not capture_exception) because these are
        # planned-detection signals, not exceptions.
        if findings:
            try:
                import sentry_sdk
                for f in findings:
                    sentry_sdk.set_tag("strava.health_check", f["check"])
                    sentry_sdk.set_tag("strava.severity", f["severity"])
                    sentry_sdk.capture_message(
                        f"Strava health-check: {f['check']} — {f['message']}",
                        level="warning",
                    )
            except Exception as sentry_exc:
                # Capture-message failing must not break the health check.
                log.warning("Sentry capture from health check failed: %s", sentry_exc)

        log.info(
            "Strava health-check complete: %d finding(s) at %s",
            len(findings), datetime.now(UTC).isoformat(),
        )
        return {"checks_run": 4, "findings_count": len(findings), "findings": findings}
    except Exception as exc:
        log.exception("Strava health-check crashed")
        # 5xx → Cloud Scheduler will retry (it has its own retry policy
        # configured in terraform). A persistent failure surfaces as a
        # Sentry exception capture from the FastAPI exception handler.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        db.close()
