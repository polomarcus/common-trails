"""Periodic "re-sync your data" reminder email (backlog P4 #13, 2026-08-07).

The community heatmap is fed by MANUAL uploads ONLY — Strava-API activities stay
personal (§5.4/§5.10). So a contributor's rides SINCE their last export never
reach the map: they go stale silently. This job emails each contributor, at most
every ~6 months, to re-export from Strava/Garmin and re-upload.

Selection (all three must hold):
  * the user has ≥1 COMMUNITY-ELIGIBLE activity (the provenance SSOT —
    manual_upload + geometry + contribute_heatmap), i.e. they have contributed;
  * their most RECENT such upload (``MAX(activities.created_at)``) is older than
    ``--stale-months`` (default 6) — they've gone stale, not a fresh uploader;
  * they were not reminded within the last ``--stale-months`` months
    (``users.last_resync_reminder_at`` NULL or older) — never double-send.

Synthetic Strava addresses (``…@strava.local``) are excluded — they can't
receive mail. On a successful send we stamp ``last_resync_reminder_at = now`` so
a monthly schedule reminds each stale contributor at most every 6 months.

Run as a Cloud Run job on a monthly Cloud Scheduler. ``--dry-run`` lists the
recipients without sending or stamping. Fail-soft per recipient: one bad send
never aborts the batch.
"""
from __future__ import annotations

import argparse
import logging

from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.email import render_resync_reminder_email, send_email
from app.services.provenance import community_eligible_conditions

log = logging.getLogger(__name__)

# Approximate a month as 30 days — the cadence is deliberately coarse (a reminder
# is not time-critical), and this keeps the SQL a plain interval arithmetic.
_DAYS_PER_MONTH = 30


def _select_stale_contributors(db, stale_months: int) -> list[tuple[str, str]]:
    """Return ``[(user_id, email), …]`` for stale contributors due a reminder."""
    conds, params = community_eligible_conditions()
    elig = " AND ".join(conds)
    cutoff_days = stale_months * _DAYS_PER_MONTH
    params = {**params, "cutoff_days": cutoff_days}
    rows = db.execute(
        sa_text(
            f"""
            SELECT u.id, u.email
            FROM users u
            JOIN activities a ON a.user_id = u.id::text
            WHERE {elig}
              AND u.email NOT LIKE '%@strava.local'
              AND (
                u.last_resync_reminder_at IS NULL
                OR u.last_resync_reminder_at < now() - make_interval(days => :cutoff_days)
              )
            GROUP BY u.id, u.email
            HAVING MAX(a.created_at) < now() - make_interval(days => :cutoff_days)
            """
        ),
        params,
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def send_resync_reminders(*, stale_months: int = 6, dry_run: bool = False,
                          limit: int | None = None) -> dict:
    """Send the reminder to every stale contributor due one. Returns a summary."""
    db = SessionLocal()
    sent = failed = 0
    try:
        recipients = _select_stale_contributors(db, stale_months)
        if limit is not None:
            recipients = recipients[:limit]
        log.info("resync-reminder: %d stale contributor(s) due (stale_months=%d, dry_run=%s)",
                 len(recipients), stale_months, dry_run)
        subject, html = render_resync_reminder_email()
        for user_id, email in recipients:
            if dry_run:
                log.info("resync-reminder DRY-RUN: would email %s", email)
                continue
            if send_email(email, subject, html):
                db.execute(
                    sa_text("UPDATE users SET last_resync_reminder_at = now() WHERE id = :uid"),
                    {"uid": user_id},
                )
                db.commit()
                sent += 1
            else:
                # Do NOT stamp on failure → retried next run (never lost, never
                # double-sent on success).
                failed += 1
                log.warning("resync-reminder: send failed for %s — will retry next run", email)
        summary = {"due": len(recipients), "sent": sent, "failed": failed, "dry_run": dry_run}
        log.info("resync-reminder done: %s", summary)
        return summary
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Send the 6-monthly re-sync reminder email")
    parser.add_argument("--stale-months", type=int, default=6,
                        help="Remind contributors with no upload in this many months (default 6).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap recipients this run (default: no cap).")
    parser.add_argument("--dry-run", action="store_true",
                        help="List recipients without sending or stamping.")
    args = parser.parse_args()
    send_resync_reminders(stale_months=args.stale_months, dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()
