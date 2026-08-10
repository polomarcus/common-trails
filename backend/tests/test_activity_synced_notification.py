"""`activity_synced` notification — "ride synced to your personal view" toast.

Drives the REAL Strava sync paths (no inline mirrors, per
[[feedback_real_handler_tests_not_inline_mirror]]) against a real DB and the
real `emit_activity_synced` → `notifications` table:

  * webhook worker (`_ingest_strava_activity`): a genuinely-NEW activity emits
    exactly ONE `activity_synced`; a dedup re-ingest of the SAME activity emits
    ZERO more; a classifier-skipped sport (Workout) emits ZERO.
  * resync (`_resync_user`): one `activity_synced` per genuinely-new row, none
    for dedup no-ops or sport skips.
  * initial bulk import (`ingest_activities_bulk`, the function `_run_strava_import`
    drives): emits ZERO — the anti-spam guard. The toast lives only in the two
    incremental callers, never in the shared bulk function.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models import Activity, IntegrationAccount, Notification, User
from app.db.session import SessionLocal

# ── DB helpers ───────────────────────────────────────────────────────


def _make_user() -> str:
    user_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(User(
            id=user_id,
            email=f"synced_{user_id[:8]}@example.com",
            username=f"u_{user_id[:6]}",
            hashed_password="x",
        ))
        db.commit()
        return user_id
    finally:
        db.close()


def _seed_account(user_id: str, external_user_id: str) -> str:
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
            external_user_id=external_user_id,
            contribute_heatmap=True,
            last_synced_at=datetime.now(UTC),
        ))
        db.commit()
        return acct_id
    finally:
        db.close()


def _synced_notifs(user_id: str) -> list[Notification]:
    db = SessionLocal()
    try:
        return (
            db.query(Notification)
            .filter(Notification.user_id == user_id, Notification.kind == "activity_synced")
            .all()
        )
    finally:
        db.close()


def _cleanup(user_id: str) -> None:
    from app.db.models import ImportJob
    db = SessionLocal()
    try:
        db.query(Notification).filter(Notification.user_id == user_id).delete()
        db.query(ImportJob).filter(ImportJob.user_id == user_id).delete()
        db.query(Activity).filter(Activity.user_id == user_id).delete()
        db.query(IntegrationAccount).filter(IntegrationAccount.user_id == user_id).delete()
        db.query(User).filter(User.id == user_id).delete()
        db.commit()
    finally:
        db.close()


# ── Webhook fakes (Strava fetch boundary only — ingest is REAL) ──────


class _FakeResp:
    def __init__(self, activity: dict):
        self._activity = activity

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._activity


class _FakeHttpClient:
    def __init__(self, activity: dict):
        self._activity = activity

    async def get(self, url, headers=None, timeout=None):  # noqa: ANN001
        return _FakeResp(self._activity)


def _patch_webhook_boundary(monkeypatch, activity: dict) -> None:
    """Patch ONLY the Strava network boundary; ingest + notify stay real."""
    monkeypatch.setenv("TEST_MODE", "false")  # reach the real ingest path
    monkeypatch.delenv("STRAVA_WEBHOOK_SUBSCRIPTION_ID", raising=False)
    monkeypatch.setattr(
        "app.api.integrations_strava.refresh_strava_token",
        AsyncMock(return_value="fake-access-token"),
    )

    async def _fake_get_client():
        return _FakeHttpClient(activity)

    monkeypatch.setattr("app.services.strava_client.get_http_client", _fake_get_client)


def _strava_activity(activity_id: int, sport_type: str, name: str) -> dict:
    return {
        "id": activity_id,
        "name": name,
        "type": sport_type,
        "sport_type": sport_type,
        "private": False,
        "distance": 42000.0,
        "total_elevation_gain": 500.0,
        "moving_time": 7200,
        "start_date": datetime.now(UTC).isoformat(),
        "start_date_local": datetime.now(UTC).isoformat(),
        "map": {},  # no polyline → geometry None; bulk still creates the row
    }


# ── Webhook worker ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_new_activity_emits_one_notification(monkeypatch):
    from app.api.internal_strava_webhook import _ingest_strava_activity

    user_id = _make_user()
    external_user_id = f"SYNC_NEW_{uuid.uuid4().int % 10_000_000}"
    _seed_account(user_id, external_user_id)
    strava_id = uuid.uuid4().int % 1_000_000_000

    try:
        _patch_webhook_boundary(
            monkeypatch,
            _strava_activity(strava_id, "GravelRide", "Sortie du matin"),
        )
        out = await _ingest_strava_activity(external_user_id, strava_id)
        assert out["status"] == "ingested"
        assert out["created"] == 1

        notifs = _synced_notifs(user_id)
        assert len(notifs) == 1
        n = notifs[0]
        assert n.kind == "activity_synced"
        assert n.meta["name"] == "Sortie du matin"
        assert n.meta["sport"] == "gravel"
        assert n.meta["provider_activity_id"] == str(strava_id)
        # PERSONAL-only copy (compliance pivot #453): Strava-API sync stays in
        # the private view and must NOT claim the community/common map.
        assert n.title == "Sortie synchronisée"
        assert n.body == "Ta sortie « Sortie du matin » est synchronisée dans ta vue perso ✓"
        assert "sur la carte" not in n.body and "sur la carte" not in n.title
    finally:
        _cleanup(user_id)


@pytest.mark.asyncio
async def test_webhook_dedup_reingest_emits_no_new_notification(monkeypatch):
    from app.api.internal_strava_webhook import _ingest_strava_activity

    user_id = _make_user()
    external_user_id = f"SYNC_DUP_{uuid.uuid4().int % 10_000_000}"
    _seed_account(user_id, external_user_id)
    strava_id = uuid.uuid4().int % 1_000_000_000

    try:
        _patch_webhook_boundary(
            monkeypatch,
            _strava_activity(strava_id, "Ride", "Rando"),
        )
        first = await _ingest_strava_activity(external_user_id, strava_id)
        assert first["created"] == 1
        assert len(_synced_notifs(user_id)) == 1

        # Same event re-delivered (Strava retry / out-of-order create+update).
        second = await _ingest_strava_activity(external_user_id, strava_id)
        assert second["created"] == 0
        assert len(_synced_notifs(user_id)) == 1, "dedup re-ingest must not emit again"
    finally:
        _cleanup(user_id)


@pytest.mark.asyncio
async def test_webhook_skipped_sport_emits_nothing(monkeypatch):
    from app.api.internal_strava_webhook import _ingest_strava_activity

    user_id = _make_user()
    external_user_id = f"SYNC_SKIP_{uuid.uuid4().int % 10_000_000}"
    _seed_account(user_id, external_user_id)
    strava_id = uuid.uuid4().int % 1_000_000_000

    try:
        # "Workout" classifies to None → skip-beats-pollute → no ingest.
        _patch_webhook_boundary(
            monkeypatch,
            _strava_activity(strava_id, "Workout", "Gym session"),
        )
        out = await _ingest_strava_activity(external_user_id, strava_id)
        assert out["status"] == "skipped"
        assert _synced_notifs(user_id) == []
    finally:
        _cleanup(user_id)


# ── Initial bulk import guard ────────────────────────────────────────


def test_bulk_initial_import_does_not_emit(monkeypatch):
    """`_run_strava_import` drives `ingest_activities_bulk` for the first-connect
    backfill. Emitting per-activity there would spam hundreds of toasts, so the
    shared function must stay silent — the toast lives only in the incremental
    callers."""
    from app.services.ingest import ingest_activities_bulk

    user_id = _make_user()
    try:
        activities = [
            {
                "provider": "strava",
                "provider_activity_id": str(uuid.uuid4().int % 1_000_000_000),
                "sport": "road",
                "name": f"Backfilled ride {i}",
                "geometry_geojson": None,
                "distance_m": 10000.0 + i,
                "elevation_gain_m": 100.0,
                "activity_date": datetime.now(UTC) - timedelta(days=i + 1),
            }
            for i in range(3)
        ]
        result = ingest_activities_bulk(
            user_id=user_id, activities_data=activities, contribute_heatmap=True,
        )
        assert result["created"] == 3
        assert _synced_notifs(user_id) == [], "bulk import must NOT emit activity_synced"
    finally:
        _cleanup(user_id)


# ── Resync ───────────────────────────────────────────────────────────


class _FakeResyncResp:
    def __init__(self, payload: list[dict]):
        self._payload = payload

    @property
    def is_success(self) -> bool:
        return True

    @property
    def headers(self) -> dict:
        return {}

    @property
    def status_code(self) -> int:
        return 200

    def json(self) -> list[dict]:
        return self._payload


class _FakeResyncClient:
    def __init__(self, pages: list[list[dict]]):
        self._pages = pages

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None, timeout=None):  # noqa: ANN001
        idx = (params or {}).get("page", 1) - 1
        return _FakeResyncResp(self._pages[idx] if idx < len(self._pages) else [])


@pytest.mark.asyncio
async def test_resync_emits_one_per_new_activity_only(monkeypatch):
    import httpx

    import app.jobs.resync_strava as resync_mod

    user_id = _make_user()
    external_user_id = f"SYNC_RESYNC_{uuid.uuid4().int % 10_000_000}"
    acct_id = _seed_account(user_id, external_user_id)

    now = datetime.now(UTC)
    new_id, dup_id = "700000001", "700000002"
    page = [[
        {  # genuinely new → created → 1 notif
            "id": int(new_id), "name": "Sortie récupérée", "type": "Ride",
            "sport_type": "Ride", "private": False, "distance": 42000.0,
            "total_elevation_gain": 500.0, "moving_time": 7200,
            "start_date": now.isoformat(), "start_date_local": now.isoformat(), "map": {},
        },
        {  # dedup no-op → skipped → 0 notif
            "id": int(dup_id), "name": "Déjà présente", "type": "Ride",
            "sport_type": "Ride", "private": False, "distance": 30000.0,
            "total_elevation_gain": 300.0, "moving_time": 5400,
            "start_date": now.isoformat(), "start_date_local": now.isoformat(), "map": {},
        },
        {  # out-of-scope sport → classify None → continue before ingest → 0 notif
            "id": 700000003, "name": "Gym", "type": "Workout",
            "sport_type": "Workout", "private": False, "distance": 0.0,
            "total_elevation_gain": 0.0, "moving_time": 3600,
            "start_date": now.isoformat(), "start_date_local": now.isoformat(), "map": {},
        },
    ]]

    def _fake_ingest(*, user_id, activity_data, contribute_heatmap):  # noqa: ANN001
        pid = activity_data["provider_activity_id"]
        return {"status": "created" if pid == new_id else "skipped"}

    ingest_mock = MagicMock(side_effect=_fake_ingest)

    db = SessionLocal()
    try:
        acct = db.query(IntegrationAccount).filter(IntegrationAccount.id == acct_id).first()
        monkeypatch.setattr(
            "app.api.integrations_strava.refresh_strava_token",
            AsyncMock(return_value="fake-access-token"),
        )
        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeResyncClient(page))

        new_count = await resync_mod._resync_user(
            acct, db, test_mode=False, ingest_activity_fn=ingest_mock,
        )
        assert new_count == 1
    finally:
        db.close()

    try:
        notifs = _synced_notifs(user_id)
        assert len(notifs) == 1, "exactly one toast for the single genuinely-new ride"
        assert notifs[0].meta["provider_activity_id"] == new_id
        assert notifs[0].meta["name"] == "Sortie récupérée"
    finally:
        _cleanup(user_id)
