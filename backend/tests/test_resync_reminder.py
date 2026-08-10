"""Real execute-and-assert tests for the 6-monthly re-sync reminder job
(``app.jobs.resync_reminder``).

Drives the REAL selection query + ``send_resync_reminders`` against the seeded
PostGIS DB (``send_email`` is monkeypatched so no mail leaves). Asserts the
staleness / cadence / synthetic-address gating and the stamp-on-success
semantics. Non-destructive: synthetic user_ids + a ``finally`` deleting exactly
what it inserted. Skips cleanly without the DB.
"""
import uuid

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.jobs import resync_reminder
from app.services.email import render_resync_reminder_email

_GEO = '{"type":"LineString","coordinates":[[3.0,43.6],[3.1,43.6]]}'


def _db_available() -> bool:
    try:
        db = SessionLocal()
        try:
            db.execute(sa_text("SELECT last_resync_reminder_at FROM users LIMIT 1"))
            db.execute(sa_text("SELECT 1 FROM activities LIMIT 1"))
            return True
        finally:
            db.close()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="needs the PostGIS DB with migration 0064 (users.last_resync_reminder_at)",
)


def _mk_user(db, email: str, reminded_days_ago: int | None) -> str:
    uid = str(uuid.uuid4())
    db.execute(
        sa_text(
            """
            INSERT INTO users (id, email, username, is_admin, created_at,
                               last_resync_reminder_at)
            VALUES (:id, :email, :u, false, now(),
                    CASE WHEN :rd IS NULL THEN NULL
                         ELSE now() - make_interval(days => :rd) END)
            """
        ),
        {"id": uid, "email": email, "u": "t_" + uid[:8], "rd": reminded_days_ago},
    )
    return uid


def _mk_activity(db, user_id: str, *, uploaded_days_ago: int,
                 source: str = "manual_upload", contribute: bool = True,
                 geo: str | None = _GEO) -> None:
    db.execute(
        sa_text(
            """
            INSERT INTO activities (id, user_id, provider, sport, source,
                                    geometry_geojson, contribute_heatmap,
                                    distance_m, created_at)
            VALUES (:id, :uid, 'test', 'road', :src, :geo, :contrib, 10000,
                    now() - make_interval(days => :d))
            """
        ),
        {"id": str(uuid.uuid4()), "uid": user_id, "src": source, "geo": geo,
         "contrib": contribute, "d": uploaded_days_ago},
    )


def _cleanup(db, uids: list[str]) -> None:
    for uid in uids:
        db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": uid})
        db.execute(sa_text("DELETE FROM users WHERE id = :u"), {"u": uid})
    db.commit()


def test_selection_gates_stale_fresh_reminded_and_synthetic(monkeypatch):
    """Only a stale, not-recently-reminded, real-email contributor is selected."""
    db = SessionLocal()
    uids = []
    try:
        stale = _mk_user(db, f"stale-{uuid.uuid4().hex[:8]}@example.com", reminded_days_ago=None)
        fresh = _mk_user(db, f"fresh-{uuid.uuid4().hex[:8]}@example.com", reminded_days_ago=None)
        reminded = _mk_user(db, f"rem-{uuid.uuid4().hex[:8]}@example.com", reminded_days_ago=30)
        synth = _mk_user(db, f"strava_{uuid.uuid4().hex[:8]}@strava.local", reminded_days_ago=None)
        noncommunity = _mk_user(db, f"api-{uuid.uuid4().hex[:8]}@example.com", reminded_days_ago=None)
        uids = [stale, fresh, reminded, synth, noncommunity]

        _mk_activity(db, stale, uploaded_days_ago=210)       # 7 mo → stale ✓
        _mk_activity(db, fresh, uploaded_days_ago=10)        # fresh → excluded
        _mk_activity(db, reminded, uploaded_days_ago=210)    # stale but reminded 30d ago → excluded
        _mk_activity(db, synth, uploaded_days_ago=210)       # stale but @strava.local → excluded
        _mk_activity(db, noncommunity, uploaded_days_ago=210, source="strava_api")  # not eligible
        db.commit()

        selected = {e for _id, e in resync_reminder._select_stale_contributors(db, stale_months=6)}
        stale_email = db.execute(sa_text("SELECT email FROM users WHERE id = :i"), {"i": stale}).scalar()
        assert stale_email in selected
        for other in (fresh, reminded, synth, noncommunity):
            e = db.execute(sa_text("SELECT email FROM users WHERE id = :i"), {"i": other}).scalar()
            assert e not in selected, f"{e} should not be selected"
    finally:
        _cleanup(db, uids)
        db.close()


def test_send_stamps_on_success_only(monkeypatch):
    """A successful send stamps last_resync_reminder_at; a failure does not."""
    calls = []
    monkeypatch.setattr(resync_reminder, "send_email",
                        lambda to, subject, html: (calls.append(to), True)[1])
    db = SessionLocal()
    uids = []
    try:
        u = _mk_user(db, f"stale-{uuid.uuid4().hex[:8]}@example.com", reminded_days_ago=None)
        uids = [u]
        _mk_activity(db, u, uploaded_days_ago=240)
        db.commit()

        res = resync_reminder.send_resync_reminders(stale_months=6)
        assert res["sent"] >= 1
        stamped = db.execute(
            sa_text("SELECT last_resync_reminder_at FROM users WHERE id = :i"), {"i": u}
        ).scalar()
        assert stamped is not None, "successful send must stamp last_resync_reminder_at"
        # Now stale-but-just-reminded → no longer selected.
        again = {e for _id, e in resync_reminder._select_stale_contributors(db, stale_months=6)}
        my_email = db.execute(sa_text("SELECT email FROM users WHERE id = :i"), {"i": u}).scalar()
        assert my_email not in again
    finally:
        _cleanup(db, uids)
        db.close()


def test_send_failure_does_not_stamp(monkeypatch):
    monkeypatch.setattr(resync_reminder, "send_email", lambda to, subject, html: False)
    db = SessionLocal()
    uids = []
    try:
        u = _mk_user(db, f"stale-{uuid.uuid4().hex[:8]}@example.com", reminded_days_ago=None)
        uids = [u]
        _mk_activity(db, u, uploaded_days_ago=240)
        db.commit()

        res = resync_reminder.send_resync_reminders(stale_months=6)
        assert res["failed"] >= 1 and res["sent"] == 0
        stamped = db.execute(
            sa_text("SELECT last_resync_reminder_at FROM users WHERE id = :i"), {"i": u}
        ).scalar()
        assert stamped is None, "failed send must NOT stamp (so it retries next run)"
    finally:
        _cleanup(db, uids)
        db.close()


def test_dry_run_neither_sends_nor_stamps(monkeypatch):
    calls = []
    monkeypatch.setattr(resync_reminder, "send_email",
                        lambda to, subject, html: (calls.append(to), True)[1])
    db = SessionLocal()
    uids = []
    try:
        u = _mk_user(db, f"stale-{uuid.uuid4().hex[:8]}@example.com", reminded_days_ago=None)
        uids = [u]
        _mk_activity(db, u, uploaded_days_ago=240)
        db.commit()

        res = resync_reminder.send_resync_reminders(stale_months=6, dry_run=True)
        assert res["dry_run"] is True and not calls
        stamped = db.execute(
            sa_text("SELECT last_resync_reminder_at FROM users WHERE id = :i"), {"i": u}
        ).scalar()
        assert stamped is None
    finally:
        _cleanup(db, uids)
        db.close()


def test_reminder_email_is_bilingual():
    """One email, FR then EN — subject + both bodies present, self-contained."""
    subject, html = render_resync_reminder_email()
    assert "🚴" in subject and "missing" in subject.lower()
    assert "carte communautaire" in html          # FR body
    assert "community map" in html                 # EN body
    assert "Strava" in html and "Garmin" in html   # export links
    assert "chemins-communs.fr/strava" in html     # CTA
    assert "http://" not in html.replace("https://", "")  # no non-https assets
