"""Tests for /internal/strava/health-check — the daily Cloud Scheduler cron.

Most of the checks are pure "look at SQL, emit Sentry warning" — the only
behaviour worth pinning is the OOM-kill safety net: when a zombie
ImportJob row is found, the endpoint MUST:

  1. Flip the row status RUNNING → FAILED (with a descriptive last_error)
     so subsequent runs don't keep re-flagging it.
  2. Emit a `strava_import_failed` notification to the affected user so
     the frontend toast fires on their next page load.
  3. Be idempotent: running the endpoint twice on the same zombie row
     must not emit two notifications.

The 3-hour zombie threshold itself is enforced by the SQL `WHERE` clause
and we don't re-test it here — it's load-bearing for false-positive
suppression and changing it should be a separate, deliberate PR.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text as sa_text

from app.db.models import Notification
from app.db.session import SessionLocal


def _make_user(client) -> str:
    """Register a fresh test user and return their user_id."""
    email = f"healthcheck_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "pw123456789",
            "username": f"u_{uuid.uuid4().hex[:6]}",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["user_id"]


def _make_zombie_import_job(user_id: str, hours_old: int = 4) -> str:
    """Insert an import_jobs row with status=RUNNING and updated_at older
    than the 3-h zombie threshold. Returns the job id.

    We use raw SQL because the ORM's `onupdate=_now` callback would
    rewrite our backdated `updated_at` on flush.
    """
    job_id = str(uuid.uuid4())
    backdated = datetime.now(UTC) - timedelta(hours=hours_old)
    db = SessionLocal()
    try:
        # Raw INSERT listing only the bare minimum columns (others have
        # server defaults). We bypass the ORM because the ORM
        # `onupdate=_now` hook would rewrite our backdated `updated_at`
        # on any subsequent flush, and the column set in the ORM model
        # may drift ahead of local-dev migrations.
        db.execute(
            sa_text("""
                INSERT INTO import_jobs
                    (id, user_id, provider, status, created_at, updated_at)
                VALUES
                    (:id, :uid, 'strava', 'RUNNING', :ts, :ts)
            """),
            {"id": job_id, "uid": user_id, "ts": backdated},
        )
        db.commit()
    finally:
        db.close()
    return job_id


def _cleanup(user_id: str, job_id: str | None = None) -> None:
    db = SessionLocal()
    try:
        db.query(Notification).filter(Notification.user_id == user_id).delete()
        if job_id:
            # Raw SQL — see _make_zombie_import_job for rationale.
            db.execute(
                sa_text("DELETE FROM import_jobs WHERE id = :id"),
                {"id": job_id},
            )
        db.commit()
    finally:
        db.close()


class TestZombieImportJobSafetyNet:
    def test_zombie_row_flips_to_failed_and_emits_notification(self, client):
        """The OOM-kill safety net: signal-9 bypasses Python `except`,
        so we rely on this cron to flip status and tell the user."""
        user_id = _make_user(client)
        job_id = _make_zombie_import_job(user_id, hours_old=4)
        try:
            resp = client.post("/internal/strava/health-check")
            assert resp.status_code == 200, resp.text

            body = resp.json()
            zombie_findings = [
                f for f in body["findings"] if f["check"] == "zombie_import_jobs"
            ]
            assert len(zombie_findings) == 1
            assert zombie_findings[0]["count"] >= 1

            # Row status flipped to FAILED with a descriptive error.
            # Use raw SQL (not ORM) for the import_jobs read — the ORM
            # model may declare columns not present in local-dev DBs
            # at older migration heads.
            db = SessionLocal()
            try:
                row = db.execute(
                    sa_text(
                        "SELECT status, last_error FROM import_jobs WHERE id = :id"
                    ),
                    {"id": job_id},
                ).one()
                assert row[0] == "FAILED"
                assert row[1] is not None
                assert "OOM" in row[1] or "container kill" in row[1]

                notifs = (
                    db.query(Notification)
                    .filter(Notification.user_id == user_id)
                    .filter(Notification.kind == "strava_import_failed")
                    .all()
                )
                assert len(notifs) == 1
                notif = notifs[0]
                assert "interrompu" in notif.title.lower()
                assert "Strava" in notif.body
                assert notif.meta["job_id"] == job_id
                assert notif.meta["reason"] == "oom_or_container_kill"
                assert notif.meta["detected_by"] == "strava_health_check"
            finally:
                db.close()
        finally:
            _cleanup(user_id, job_id)

    def test_idempotent_on_second_run(self, client):
        """The strict idempotency claim: a zombie row gets exactly one
        notification across any number of `/internal/strava/health-check`
        invocations. The endpoint runs daily in prod; if it weren't
        idempotent, every day for a stuck-FAILED user would emit another
        toast. The conditional `UPDATE ... WHERE status='RUNNING'` is
        what enforces this — once flipped to FAILED, the SELECT no
        longer matches the row and no further work happens on it."""
        user_id = _make_user(client)
        job_id = _make_zombie_import_job(user_id, hours_old=4)
        try:
            # Run 1: zombie row detected → flipped + notified.
            resp1 = client.post("/internal/strava/health-check")
            assert resp1.status_code == 200

            db = SessionLocal()
            try:
                notifs_after_1 = (
                    db.query(Notification)
                    .filter(Notification.user_id == user_id)
                    .filter(Notification.kind == "strava_import_failed")
                    .count()
                )
                assert notifs_after_1 == 1
            finally:
                db.close()

            # Run 2: row is now FAILED. SELECT WHERE status='RUNNING'
            # filters it out → no finding, no UPDATE, no notification.
            # State is untouched between runs — this is the cron firing
            # twice on the same zombie.
            resp2 = client.post("/internal/strava/health-check")
            assert resp2.status_code == 200
            body2 = resp2.json()
            zombie_findings = [
                f for f in body2["findings"] if f["check"] == "zombie_import_jobs"
            ]
            assert zombie_findings == []

            db = SessionLocal()
            try:
                notifs_after_2 = (
                    db.query(Notification)
                    .filter(Notification.user_id == user_id)
                    .filter(Notification.kind == "strava_import_failed")
                    .count()
                )
                # Still 1 — no duplicate emitted.
                assert notifs_after_2 == 1
            finally:
                db.close()
        finally:
            _cleanup(user_id, job_id)

    def test_multiple_zombies_same_user_emit_one_notification(self, client):
        """A user retrying an OOM-killed 5k-activity import 5× would
        leave 5 zombie rows in `import_jobs`. The SELECT picks them
        all up (LIMIT 20), the UPDATE loop flips all 5 to FAILED, but
        the user must only get ONE toast on next login — otherwise
        their notification tray turns into spam after a bad-luck day."""
        user_id = _make_user(client)
        job_ids = [
            _make_zombie_import_job(user_id, hours_old=4 + i)
            for i in range(5)
        ]
        try:
            resp = client.post("/internal/strava/health-check")
            assert resp.status_code == 200

            db = SessionLocal()
            try:
                # All 5 rows should be flipped to FAILED.
                failed_count = db.execute(
                    sa_text("""
                        SELECT COUNT(*) FROM import_jobs
                        WHERE user_id = :uid AND status = 'FAILED'
                    """),
                    {"uid": user_id},
                ).scalar()
                assert failed_count == 5

                # But the user only got ONE notification.
                notifs = (
                    db.query(Notification)
                    .filter(Notification.user_id == user_id)
                    .filter(Notification.kind == "strava_import_failed")
                    .count()
                )
                assert notifs == 1, (
                    f"Expected 1 notification per user per cron run, got {notifs}. "
                    f"Multi-zombie-per-user fan-out regression."
                )
            finally:
                db.close()
        finally:
            for jid in job_ids:
                _cleanup(user_id, jid)
