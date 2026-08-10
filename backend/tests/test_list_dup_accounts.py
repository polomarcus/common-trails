"""Read-only diagnostic: ``app.cli.list_dup_accounts.collect_report``.

Drives the REAL ``collect_report`` against a seeded synthetic-Strava dup +
a temporal collision, asserting it SURFACES both signals without mutating.
Non-destructive on the shared DB: unique synthetic ids + a ``finally`` that
deletes exactly what it inserted. Skips cleanly if the PostGIS DB is absent.
"""
import uuid

import pytest
from sqlalchemy import text as sa_text

from app.cli.list_dup_accounts import collect_report
from app.db.session import SessionLocal


def _db_available() -> bool:
    try:
        db = SessionLocal()
        try:
            db.execute(sa_text("SELECT 1 FROM users LIMIT 1"))
            db.execute(sa_text("SELECT 1 FROM integration_accounts LIMIT 1"))
            return True
        finally:
            db.close()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="needs the PostGIS DB (users + integration_accounts + activities)",
)


def _insert_user(db, uid: str, email: str, has_pw: bool) -> None:
    db.execute(
        sa_text(
            "INSERT INTO users (id, email, username, hashed_password) "
            "VALUES (:id, :email, :email, :pw)"
        ),
        {"id": uid, "email": email, "pw": ("x" if has_pw else None)},
    )


def _insert_activity(db, aid: str, uid: str, when) -> None:
    db.execute(
        sa_text(
            "INSERT INTO activities (id, user_id, provider, sport, source, "
            " geometry_geojson, contribute_heatmap, activity_date) "
            "VALUES (:id, :uid, 'test', 'road', 'manual_upload', "
            " '{\"type\":\"LineString\",\"coordinates\":[[3,43],[3.01,43]]}', true, :d)"
        ),
        {"id": aid, "uid": uid, "d": when},
    )


def test_detects_synthetic_account_and_temporal_collision():
    db = SessionLocal()
    athlete = f"testath_{uuid.uuid4().hex[:8]}"
    email_uid = str(uuid.uuid4())
    strava_uid = str(uuid.uuid4())
    email_addr = f"person_{uuid.uuid4().hex[:8]}@example.com"
    synth_addr = f"strava_{athlete}@strava.local"
    act_email = str(uuid.uuid4())
    act_strava = str(uuid.uuid4())
    acct_id = str(uuid.uuid4())
    try:
        _insert_user(db, email_uid, email_addr, has_pw=False)   # email/magic-link
        _insert_user(db, strava_uid, synth_addr, has_pw=False)  # synthetic strava
        db.execute(
            sa_text(
                "INSERT INTO integration_accounts "
                "(id, user_id, provider, access_token, external_user_id) "
                "VALUES (:id, :uid, 'strava', 'stub', :ath)"
            ),
            {"id": acct_id, "uid": strava_uid, "ath": athlete},
        )
        # Same start time under the TWO different users → collision.
        ts = "2026-05-01 09:00:00+00"
        _insert_activity(db, act_email, email_uid, ts)
        _insert_activity(db, act_strava, strava_uid, ts)
        db.commit()

        report = collect_report(db, tolerance_min=5)

        synth_ids = {u["user_id"] for u in report["synthetic_strava_accounts"]}
        assert strava_uid in synth_ids
        assert email_uid not in synth_ids

        # The synthetic row carries the athlete id it was linked by.
        synth_row = next(u for u in report["synthetic_strava_accounts"] if u["user_id"] == strava_uid)
        assert synth_row["strava_athlete_id"] == athlete

        # The two accounts collide at the same start time → flagged as a pair.
        pair = {email_uid, strava_uid}
        assert any(
            {c["user_a"], c["user_b"]} == pair and c["colliding_activities"] >= 1
            for c in report["temporal_collisions"]
        ), "expected the two seeded users to be reported as a temporal-collision pair"
    finally:
        db.rollback()  # clear any aborted tx so cleanup DELETEs run
        db.execute(sa_text("DELETE FROM activities WHERE id IN (:a, :b)"),
                   {"a": act_email, "b": act_strava})
        db.execute(sa_text("DELETE FROM integration_accounts WHERE id = :id"), {"id": acct_id})
        db.execute(sa_text("DELETE FROM users WHERE id IN (:a, :b)"),
                   {"a": email_uid, "b": strava_uid})
        db.commit()
        db.close()
