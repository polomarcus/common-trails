"""Non-regression: `_reap_stuck_archives` terminally-fails pending_archives
stranded in 'processing' by a killed drain, freeing the owner's open slot.

Bug: a drain killed mid-run (job timeout/OOM — no except transitions the row)
leaves it 'processing'; the claim query only re-picks it while attempts <
_MAX_DRAIN_ATTEMPTS, so after that many kills it sat 'processing' FOREVER,
permanently consuming the per-user open-archive quota and never emailing the
owner. The reaper marks such rows 'failed'. Drives the REAL function; fails on
pre-fix code (which had no reaper → stuck stays 'processing').
"""
import uuid

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.jobs.ingest_pending_archives import _MAX_DRAIN_ATTEMPTS, _reap_stuck_archives


def _db_available() -> bool:
    try:
        db = SessionLocal()
        try:
            db.execute(sa_text("SELECT 1 FROM pending_archives LIMIT 1"))
            return True
        finally:
            db.close()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="needs the PostGIS DB")


def _mk_user(db) -> str:
    uid = str(uuid.uuid4())
    db.execute(
        sa_text("INSERT INTO users (id, email, username, is_admin, created_at) "
                "VALUES (:id, :e, :u, false, now())"),
        {"id": uid, "e": f"reap-{uid[:8]}@example.com", "u": "r_" + uid[:8]},
    )
    return uid


def _mk_archive(db, uid: str, *, status: str, attempts: int, age_hours: float) -> str:
    aid = str(uuid.uuid4())
    db.execute(
        sa_text("""
            INSERT INTO pending_archives (id, user_id, storage_backend, bucket_key,
                status, attempts, updated_at)
            VALUES (:id, :uid, 'local', :bk, :st, :att,
                    now() - make_interval(hours => :age))
        """),
        {"id": aid, "uid": uid, "bk": f"archive-intake/{aid}.zip",
         "st": status, "att": attempts, "age": age_hours},
    )
    return aid


def _status(db, aid: str) -> str:
    return db.execute(sa_text("SELECT status FROM pending_archives WHERE id = :i"),
                      {"i": aid}).scalar()


def test_reaper_fails_only_stuck_exhausted_processing_rows():
    db = SessionLocal()
    ids = []
    try:
        uid = _mk_user(db)
        stuck = _mk_archive(db, uid, status="processing", attempts=_MAX_DRAIN_ATTEMPTS, age_hours=4)
        fresh = _mk_archive(db, uid, status="processing", attempts=_MAX_DRAIN_ATTEMPTS, age_hours=0)   # recent → not stale
        retryable = _mk_archive(db, uid, status="processing", attempts=1, age_hours=4)                 # attempts<max → claim re-picks it
        uploaded = _mk_archive(db, uid, status="uploaded", attempts=0, age_hours=4)                    # not processing
        ids = [stuck, fresh, retryable, uploaded]
        db.commit()

        reaped = _reap_stuck_archives(db)

        assert stuck in reaped, "the stuck+exhausted row must be reaped"
        assert _status(db, stuck) == "failed"
        for other in (fresh, retryable, uploaded):
            assert other not in reaped
            assert _status(db, other) != "failed", f"{other} must NOT be reaped"
    finally:
        for aid in ids:
            db.execute(sa_text("DELETE FROM pending_archives WHERE id = :i"), {"i": aid})
        db.execute(sa_text("DELETE FROM users WHERE email LIKE 'reap-%@example.com'"))
        db.commit()
        db.close()
