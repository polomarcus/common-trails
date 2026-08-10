"""FIX 1 coverage — resync catch-up is a FIXED TRAILING WINDOW, not a cursor.

Drives the REAL ``app.jobs.resync_strava._resync_user`` with a mocked Strava
``/athlete/activities`` (no network) and a controllable ``ingest_activity``.

The load-bearing property (and the exact bug this closes):

  A webhook event is dropped (Cloud Run cold start / 5xx). The dropped ride A
  is OLDER than a ride B that WAS delivered and advanced ``last_synced_at``
  past A. A cursor scan (``after = last_synced_at``) then queries Strava with
  ``after > A.start`` and NEVER re-sees A — permanent silent loss. A trailing
  window (``after = now - RESYNC_WINDOW_DAYS``) re-scans the whole recent
  window, so A is re-fetched and recovered; B (already present) is absorbed by
  ingest dedup.

We assert:
  1. The ``after`` param SENT to Strava equals ``now - RESYNC_WINDOW_DAYS``
     (within a small tolerance) — NOT ``last_synced_at``.
  2. That window is early enough to include the dropped ride A, whereas the
     OLD cursor (``last_synced_at``) would have excluded it.
  3. ``ingest_activity`` is invoked for A (recovered) and B (dedup-skipped).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models import IntegrationAccount, Notification, User
from app.db.session import SessionLocal


class _FakeResponse:
    def __init__(self, status_code: int, payload: list[dict]):
        self.status_code = status_code
        self._payload = payload

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def headers(self) -> dict:
        return {}

    def json(self) -> list[dict]:
        return self._payload


class _FakeAsyncClient:
    """Stand-in for ``httpx.AsyncClient`` used as an async context manager.

    Records the ``after`` query param of every /athlete/activities GET and
    serves one page then EOF (empty second page).
    """

    def __init__(self, captured: dict, pages: list[list[dict]]):
        self._captured = captured
        self._pages = pages

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None, timeout=None):
        params = params or {}
        self._captured.setdefault("after_values", []).append(params.get("after"))
        page = params.get("page", 1)
        idx = page - 1
        payload = self._pages[idx] if idx < len(self._pages) else []
        return _FakeResponse(200, payload)


def _make_user() -> str:
    user_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(User(
            id=user_id,
            email=f"resync_{user_id[:8]}@example.com",
            username=f"u_{user_id[:6]}",
            hashed_password=None,
        ))
        db.commit()
        return user_id
    finally:
        db.close()


def _seed_account(user_id: str, last_synced_at: datetime) -> str:
    acct_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(IntegrationAccount(
            id=acct_id,
            user_id=user_id,
            provider="strava",
            access_token="stub",
            refresh_token="stub-rt",
            expires_at=9_999_999_999,
            external_user_id=str(uuid.uuid4().int % 10_000_000),
            last_synced_at=last_synced_at,
            contribute_heatmap=True,
        ))
        db.commit()
        return acct_id
    finally:
        db.close()


def _cleanup(user_id: str, acct_id: str) -> None:
    from app.db.models import ImportJob
    db = SessionLocal()
    try:
        db.query(ImportJob).filter(ImportJob.user_id == user_id).delete()
        db.query(IntegrationAccount).filter(IntegrationAccount.id == acct_id).delete()
        db.query(Notification).filter(Notification.user_id == user_id).delete()
        db.query(User).filter(User.id == user_id).delete()
        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_trailing_window_recovers_dropped_older_activity(monkeypatch):
    import app.jobs.resync_strava as resync_mod

    now = datetime.now(UTC)
    # last_synced_at is RECENT (2 days ago) — a cursor scan would use this.
    last_synced = now - timedelta(days=2)
    # Dropped ride A is OLDER than last_synced (10 days ago) → a cursor would
    # never re-fetch it. It sits INSIDE the 45-day trailing window.
    ride_a_start = now - timedelta(days=10)
    ride_b_start = now - timedelta(days=1)  # newer, already ingested

    user_id = _make_user()
    acct_id = _seed_account(user_id, last_synced_at=last_synced)

    captured: dict = {}
    pages = [[
        {
            "id": 111111,  # dropped ride A — we do NOT have it
            "name": "Sortie gravel perdue",
            "type": "Ride",
            "sport_type": "Ride",
            "private": False,
            "distance": 42000.0,
            "total_elevation_gain": 500.0,
            "moving_time": 7200,
            "start_date": ride_a_start.isoformat(),
            "start_date_local": ride_a_start.isoformat(),
            "map": {},
        },
        {
            "id": 222222,  # ride B — already in our DB
            "name": "Sortie récente",
            "type": "Ride",
            "sport_type": "Ride",
            "private": False,
            "distance": 30000.0,
            "total_elevation_gain": 300.0,
            "moving_time": 5400,
            "start_date": ride_b_start.isoformat(),
            "start_date_local": ride_b_start.isoformat(),
            "map": {},
        },
    ]]

    # ingest_activity: A is new ("created"), B is a dedup hit ("skipped").
    def _fake_ingest(*, user_id, activity_data, contribute_heatmap):
        pid = activity_data["provider_activity_id"]
        return {"status": "created" if pid == "111111" else "skipped"}

    ingest_mock = MagicMock(side_effect=_fake_ingest)

    db = SessionLocal()
    try:
        acct = db.query(IntegrationAccount).filter(
            IntegrationAccount.id == acct_id
        ).first()

        # Patch refresh (return a token) + httpx.AsyncClient (fake pages).
        monkeypatch.setattr(
            "app.api.integrations_strava.refresh_strava_token",
            AsyncMock(return_value="fake-access-token"),
        )
        import httpx
        monkeypatch.setattr(
            httpx, "AsyncClient",
            lambda *a, **k: _FakeAsyncClient(captured, pages),
        )

        new_count = await resync_mod._resync_user(
            acct, db, test_mode=False, ingest_activity_fn=ingest_mock,
        )
    finally:
        db.close()

    try:
        after_values = captured["after_values"]
        assert after_values, "resync must have queried Strava with an `after` param"
        after_sent = after_values[0]

        expected_window_epoch = int(
            (now - timedelta(days=resync_mod.RESYNC_WINDOW_DAYS)).timestamp()
        )
        cursor_epoch = int(last_synced.timestamp())
        ride_a_epoch = int(ride_a_start.timestamp())

        # 1. The scan uses the trailing window, not the cursor.
        assert abs(after_sent - expected_window_epoch) < 120, (
            f"after={after_sent} should be ~now-{resync_mod.RESYNC_WINDOW_DAYS}d "
            f"({expected_window_epoch}), not the cursor ({cursor_epoch})"
        )
        assert after_sent < cursor_epoch, "trailing window must reach further back than the cursor"

        # 2. Window includes dropped ride A; the OLD cursor would have excluded it.
        assert after_sent < ride_a_epoch, "window must include the dropped older ride"
        assert ride_a_epoch < cursor_epoch, (
            "the dropped ride is older than last_synced_at — a cursor scan would skip it"
        )

        # 3. Both rides reached ingest; A recovered (created), B deduped (skipped).
        ingested_pids = {
            c.kwargs["activity_data"]["provider_activity_id"]
            for c in ingest_mock.call_args_list
        }
        assert ingested_pids == {"111111", "222222"}
        assert new_count == 1, "exactly the dropped ride A is newly created"
    finally:
        _cleanup(user_id, acct_id)
