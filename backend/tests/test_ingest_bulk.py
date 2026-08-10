"""Tests for ingest_activities_bulk (PR #310 — db-pool-pressure bundle).

Pins three behaviours of the per-batch session refactor:
1. The function opens / closes a fresh session for every batch instead
   of holding one session for the whole job (the connection-starvation
   bug at the root of the Strava 429 + stats hang incident).
2. SAVEPOINT-per-activity preserves the legacy "one bad row in a batch
   doesn't kill the rest" semantics now that we commit per batch.
3. Re-running with the same provider_activity_ids is a no-op (idempotency).
"""
import json
import uuid

import pytest

from app.db.models import Activity, User
from app.db.session import SessionLocal
from app.services import ingest as ingest_service


def _make_activity_data(idx: int, with_geom: bool = True) -> dict:
    """Build a Strava-shaped activity_data dict (matches what
    integrations_strava._run_strava_import would pass)."""
    geom = None
    if with_geom:
        # Two-point LineString — enough to pass _geojson_to_cells.
        geom = json.dumps({
            "type": "LineString",
            "coordinates": [
                [3.87 + idx * 0.001, 43.62 + idx * 0.001],
                [3.87 + idx * 0.001 + 0.005, 43.62 + idx * 0.001 + 0.005],
            ],
        })
    return {
        "provider": "strava",
        "provider_activity_id": f"strava-bulk-test-{idx}-{uuid.uuid4().hex[:8]}",
        "sport": "road",
        "name": f"Bulk activity {idx}",
        "geometry_geojson": geom,
        "distance_m": 1500 + idx,
        "elevation_gain_m": 50,
        "moving_time": 600,
        "total_photo_count": 0,
    }


@pytest.fixture
def fresh_user() -> str:
    """Create a fresh user via the User model, return user_id."""
    db = SessionLocal()
    try:
        user = User(
            id=str(uuid.uuid4()),
            email=f"bulk_{uuid.uuid4().hex[:8]}@example.com",
            username=f"bulk_{uuid.uuid4().hex[:6]}",
            hashed_password=None,
        )
        db.add(user)
        db.commit()
        return user.id
    finally:
        db.close()


class TestBulkPerBatchSession:
    """Verify the per-batch session pattern actually releases connections."""

    def test_releases_session_between_batches(self, fresh_user, monkeypatch):
        """SessionLocal must be called ≥ (N_batches + 1) times: one
        per batch flush, plus the pre-load + tail flush.

        Before PR #310 the function called SessionLocal() exactly once
        for the entire job — this test would have failed with
        called_count == 1.
        """
        call_count = {"n": 0}
        from app.db import session as session_mod

        original = session_mod.SessionLocal

        def counting_session_local():
            call_count["n"] += 1
            return original()

        monkeypatch.setattr(session_mod, "SessionLocal", counting_session_local)

        # 120 activities at BATCH_SIZE=50 → 3 flush batches (50+50+20)
        # Plus the pre-load session → ≥ 4 SessionLocal() calls.
        activities = [_make_activity_data(i) for i in range(120)]
        result = ingest_service.ingest_activities_bulk(
            user_id=fresh_user,
            activities_data=activities,
        )

        assert result["created"] == 120
        assert result["skipped"] == 0
        # Pre-load (1) + 3 flush batches = 4 minimum. Backfills add more,
        # but with no pre-existing activities there shouldn't be any.
        assert call_count["n"] >= 4, (
            f"Expected ≥ 4 SessionLocal() calls (pre-load + 3 batches), "
            f"got {call_count['n']} — session is being held across batches"
        )
        # Sanity: not absurdly more either.
        assert call_count["n"] < 200, (
            f"Got {call_count['n']} SessionLocal() calls for 120 activities — "
            "looks like we're opening a session per row again"
        )


class TestBulkPartialFailure:
    """SAVEPOINT-per-activity must let one bad row fail without rolling
    back the whole batch."""

    def test_one_bad_row_doesnt_kill_batch(self, fresh_user):
        """A single activity that triggers a DB error must fail in
        isolation; the other rows in the batch must still be committed.

        Trigger path: ``activity_date`` set to a string PostgreSQL can't
        cast to ``timestamp``. SQLAlchemy raises DataError when the
        SAVEPOINT is released; the savepoint rollback contains the
        damage to that one row.

        Before SAVEPOINT-per-activity (single ``commit()`` at the end of
        the job), this would abort the entire transaction and lose ALL
        the good rows in the batch.
        """
        good = [_make_activity_data(i) for i in range(4)]
        bad = _make_activity_data(99)
        bad["activity_date"] = "not-a-date-at-all"

        result = ingest_service.ingest_activities_bulk(
            user_id=fresh_user,
            activities_data=[*good[:2], bad, *good[2:]],
        )

        assert result["created"] == 4, (
            f"Expected 4 good rows committed, got {result}"
        )
        assert result["failed"] == 1, (
            f"Expected 1 row to fail (bad activity_date), got {result}"
        )

        # Verify in DB
        db = SessionLocal()
        try:
            count = db.query(Activity).filter(
                Activity.user_id == fresh_user,
                Activity.provider == "strava",
            ).count()
            assert count == 4, (
                f"Expected 4 activities in DB, found {count} — SAVEPOINT "
                "rollback may have caught more than the one bad row"
            )
        finally:
            db.close()


class TestBulkExistingIdsRollbackOnFlushFailure:
    """Audit 2026-05-27 S2.3.

    The outer ingest loop eagerly registers each row's
    `provider_activity_id` in `existing_ids` *before* `_flush_batch`
    commits. If commit fails, the row didn't persist — but without a
    symmetric rollback, the id stays in `existing_ids` and a later
    re-run sees the row as "already exists" and silently drops it.
    Pair with the existing `batch_new_dd` rollback at L3217-3232.

    This test forces a commit failure and verifies the next ingest call
    actually re-attempts (and succeeds) instead of treating the row as
    a ghost-duplicate.
    """

    def test_failed_flush_does_not_poison_existing_ids(self, fresh_user, monkeypatch):
        """Force `flush_db.commit()` to raise once, verify a follow-up
        ingest with the same provider_activity_id is NOT skipped as a
        ghost duplicate.

        `SessionLocal` is imported lazily inside ingest helpers, so we
        patch `app.db.session.SessionLocal` (the canonical name) — that
        catches every callsite.
        """
        from app.db import session as session_mod

        target = _make_activity_data(0)

        # Wrap SessionLocal so the FIRST flush_db.commit() raises after
        # the row inserts succeed (simulates a transient connection drop
        # right at commit time — pre-fix this would leave prov_id stuck
        # in existing_ids).
        real_session_local = session_mod.SessionLocal
        crashed = {"once": False}

        class _CrashingSession:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def commit(self):
                if not crashed["once"]:
                    crashed["once"] = True
                    # Roll back so SQLAlchemy doesn't yell on session.close()
                    self._inner.rollback()
                    raise RuntimeError("simulated transient commit failure")
                return self._inner.commit()

        def _wrap_session():
            return _CrashingSession(real_session_local())

        monkeypatch.setattr(session_mod, "SessionLocal", _wrap_session)

        # First call: commit fails, _flush_batch re-raises. Pre-fix this
        # would leave prov_id stuck in `existing_ids`.
        with pytest.raises(RuntimeError, match="simulated transient"):
            ingest_service.ingest_activities_bulk(
                user_id=fresh_user,
                activities_data=[target],
            )

        # Restore the real session (only one commit was forced to fail).
        monkeypatch.setattr(session_mod, "SessionLocal", real_session_local)

        # Second call with the SAME provider_activity_id must succeed
        # — without the rollback fix, this would skip as "already exists".
        result = ingest_service.ingest_activities_bulk(
            user_id=fresh_user,
            activities_data=[target],
        )
        assert result["created"] == 1, (
            f"Expected 1 row created on retry after rolled-back flush, got {result}. "
            f"Regression: existing_ids was not popped on flush failure (S2.3)."
        )
        assert result["skipped"] == 0


class TestBulkIdempotency:
    """Re-running with the same provider_activity_ids must not duplicate."""

    def test_recommit_no_duplicates(self, fresh_user):
        activities = [_make_activity_data(i) for i in range(10)]

        first = ingest_service.ingest_activities_bulk(
            user_id=fresh_user,
            activities_data=activities,
        )
        assert first["created"] == 10
        assert first["skipped"] == 0

        # Same payload, second run
        second = ingest_service.ingest_activities_bulk(
            user_id=fresh_user,
            activities_data=activities,
        )
        assert second["created"] == 0, (
            f"Expected no new activities on re-run, got {second}"
        )
        assert second["skipped"] == 10, (
            f"Expected 10 skipped (idempotent), got {second}"
        )

        # DB invariant: still only 10 rows
        db = SessionLocal()
        try:
            count = db.query(Activity).filter(
                Activity.user_id == fresh_user,
                Activity.provider == "strava",
            ).count()
            assert count == 10
        finally:
            db.close()

    def test_progress_callback_fires_per_batch(self, fresh_user):
        """The progress callback must fire once per batch flush, not
        once per activity (was per-batch before; preserve that)."""
        calls: list[tuple[int, int, int]] = []

        # 80 activities at BATCH_SIZE=50 → at least 2 callback fires
        activities = [_make_activity_data(i) for i in range(80)]

        def on_progress(c: int, s: int, f: int) -> None:
            calls.append((c, s, f))

        ingest_service.ingest_activities_bulk(
            user_id=fresh_user,
            activities_data=activities,
            on_progress=on_progress,
        )

        assert len(calls) >= 2, (
            f"Expected ≥ 2 progress callbacks for 80 acts in BATCH_SIZE=50 "
            f"chunks, got {len(calls)}"
        )
        # Callbacks must be monotonically non-decreasing in `created`
        for prev, curr in zip(calls, calls[1:], strict=False):
            assert curr[0] >= prev[0]
