"""Tests for the Strava push-webhook endpoints (public + internal worker).

Coverage focus per [[project_strava_webhook_architecture]]:

  * Handshake: missing verify_token → 503, mismatch → 403, match → echo.
  * POST event: unknown owner → 200 "unknown-owner" (so Strava stops
    retrying a forged event).
  * POST event: invalid JSON → 200 "ignored".
  * POST event: known owner → 200 "ok" (+ enqueue or inline run).
  * POST event: ack-fast — handler must not actually fetch from Strava.
  * Worker: athlete deauthorize → removes IntegrationAccount.
  * Worker: unknown owner → skipped.
  * Worker: TEST_MODE returns a stub status without DB / network.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.internal_strava_webhook import process_strava_webhook_event
from app.db.models import IntegrationAccount, User
from app.db.session import SessionLocal
from app.main import app

client = TestClient(app)


# ── GET handshake ────────────────────────────────────────────────────


def test_handshake_missing_verify_token_env_returns_503(monkeypatch):
    monkeypatch.delenv("STRAVA_WEBHOOK_VERIFY_TOKEN", raising=False)
    resp = client.get(
        "/integrations/strava/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "anything",
            "hub.challenge": "abc",
        },
    )
    assert resp.status_code == 503


def test_handshake_token_mismatch_returns_403(monkeypatch):
    monkeypatch.setenv("STRAVA_WEBHOOK_VERIFY_TOKEN", "expected-secret")
    resp = client.get(
        "/integrations/strava/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong-secret",
            "hub.challenge": "abc",
        },
    )
    assert resp.status_code == 403


def test_handshake_match_echoes_challenge(monkeypatch):
    monkeypatch.setenv("STRAVA_WEBHOOK_VERIFY_TOKEN", "the-secret")
    resp = client.get(
        "/integrations/strava/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "the-secret",
            "hub.challenge": "STRAVA_CHALLENGE_XYZ",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # Strava REQUIRES the dotted key — verify it's preserved verbatim.
    assert body == {"hub.challenge": "STRAVA_CHALLENGE_XYZ"}


def test_handshake_wrong_mode_returns_400(monkeypatch):
    monkeypatch.setenv("STRAVA_WEBHOOK_VERIFY_TOKEN", "tok")
    resp = client.get(
        "/integrations/strava/webhook",
        params={"hub.mode": "unsubscribe", "hub.verify_token": "tok", "hub.challenge": "x"},
    )
    assert resp.status_code == 400


# ── POST event receiver ──────────────────────────────────────────────


def _make_strava_account(user_id: str, external_user_id: str) -> None:
    db = SessionLocal()
    try:
        db.add(User(
            id=user_id, email=f"{user_id}@test.local",
            username=f"u{user_id[:8]}", hashed_password="x",
        ))
        db.add(IntegrationAccount(
            id=str(uuid.uuid4()),
            user_id=user_id,
            provider="strava",
            access_token="encrypted-stub",
            refresh_token="encrypted-stub",
            expires_at=9999999999,
            external_user_id=external_user_id,
            athlete_name="Test",
        ))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _ensure_test_mode(monkeypatch):
    """Force TEST_MODE so the worker skips the DB + Strava fetch path."""
    monkeypatch.setenv("TEST_MODE", "true")


def test_post_event_unknown_owner_returns_200_unknown(monkeypatch):
    """A forged event for an owner_id we don't know must NOT 4xx —
    that would make Strava retry the same forged event. Ack 200 with
    a marker body so dedup is silent."""
    monkeypatch.setenv("STRAVA_WEBHOOK_VERIFY_TOKEN", "tok")
    resp = client.post(
        "/integrations/strava/webhook",
        json={
            "object_type": "activity",
            "aspect_type": "create",
            "owner_id": 999999999,
            "object_id": 12345,
            "subscription_id": 1,
            "event_time": 1690000000,
        },
    )
    assert resp.status_code == 200
    assert resp.text == "unknown-owner"


def test_post_event_invalid_json_returns_200_ignored(monkeypatch):
    monkeypatch.setenv("STRAVA_WEBHOOK_VERIFY_TOKEN", "tok")
    resp = client.post(
        "/integrations/strava/webhook",
        content=b"not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.text == "ignored"


def test_post_event_known_owner_acks_ok(monkeypatch):
    monkeypatch.setenv("STRAVA_WEBHOOK_VERIFY_TOKEN", "tok")
    user_id = str(uuid.uuid4())
    external_user_id = "STRAVA_WEBHOOK_KNOWN_USER"
    _make_strava_account(user_id, external_user_id)

    resp = client.post(
        "/integrations/strava/webhook",
        json={
            "object_type": "activity",
            "aspect_type": "create",
            "owner_id": external_user_id,
            "object_id": 12345,
            "subscription_id": 1,
            "event_time": 1690000000,
        },
    )
    assert resp.status_code == 200
    assert resp.text == "ok"


# ── Worker pure-function path (process_strava_webhook_event) ─────────


@pytest.mark.asyncio
async def test_worker_missing_owner_skipped() -> None:
    out = await process_strava_webhook_event({"object_type": "activity", "owner_id": ""})
    assert out["status"] == "skipped"


@pytest.mark.asyncio
async def test_worker_unknown_object_type_skipped() -> None:
    out = await process_strava_webhook_event({
        "object_type": "future_type",
        "owner_id": "1",
        "aspect_type": "create",
    })
    assert out["status"] == "skipped"


@pytest.mark.asyncio
async def test_worker_athlete_deauthorize_removes_account() -> None:
    user_id = str(uuid.uuid4())
    external_user_id = "STRAVA_WEBHOOK_DEAUTH_USER"
    _make_strava_account(user_id, external_user_id)

    # Snapshot the user's activities before deauthorize so we can prove
    # the cascade does NOT touch them — see Sev-1 #6 in the PR #341 review.
    from app.db.models import Activity
    db = SessionLocal()
    try:
        activity_count_before = db.query(Activity).filter(Activity.user_id == user_id).count()
    finally:
        db.close()

    out = await process_strava_webhook_event({
        "object_type": "athlete",
        "owner_id": external_user_id,
        "aspect_type": "update",
        "updates": {"authorized": "false"},
    })
    assert out["status"] == "deauthorized"
    assert out["user_id"] == user_id

    # Verify the IntegrationAccount row is gone AND the user's
    # activities are untouched — disconnecting the OAuth link doesn't
    # retract the user's choice to have imported those activities.
    db = SessionLocal()
    try:
        acct = db.query(IntegrationAccount).filter(
            IntegrationAccount.external_user_id == external_user_id,
            IntegrationAccount.provider == "strava",
        ).first()
        assert acct is None
        activity_count_after = db.query(Activity).filter(Activity.user_id == user_id).count()
        assert activity_count_after == activity_count_before, (
            "deauthorize must NOT cascade-delete user activities"
        )
    finally:
        db.close()


@pytest.mark.asyncio
async def test_worker_athlete_update_without_deauth_is_skipped() -> None:
    """Strava might emit athlete updates for fields other than
    `authorized`. We only act on the deauthorize signal — anything
    else is a no-op."""
    out = await process_strava_webhook_event({
        "object_type": "athlete",
        "owner_id": "1",
        "aspect_type": "update",
        "updates": {"some_other_field": "true"},
    })
    assert out["status"] == "skipped"


@pytest.mark.asyncio
async def test_worker_activity_create_in_test_mode_returns_stub() -> None:
    """TEST_MODE short-circuits Strava + DB, returning a stub. Lets
    the public endpoint test pass without needing the full fetch path."""
    os.environ["TEST_MODE"] = "true"
    out = await process_strava_webhook_event({
        "object_type": "activity",
        "owner_id": "1",
        "aspect_type": "create",
        "object_id": 12345,
    })
    assert out["status"] == "ingested"
    assert out.get("stub") is True


@pytest.mark.asyncio
async def test_worker_activity_delete_skipped_when_no_owner() -> None:
    """Delete event for an owner we don't have → silent skip."""
    out = await process_strava_webhook_event({
        "object_type": "activity",
        "owner_id": "999999",
        "aspect_type": "delete",
        "object_id": 12345,
    })
    assert out["status"] == "skipped"


def test_post_event_does_not_touch_strava_api(monkeypatch) -> None:
    """Ack-fast contract — the public handler must NOT call Strava
    (no fetch of activity streams etc.) on the request path. All real
    work happens in the Cloud Task worker.

    We don't patch `httpx.AsyncClient.request` globally because the
    FastAPI TestClient itself uses httpx internally (anyio + httpx
    transport) — that would break the test infrastructure. Instead we
    patch the specific `get_http_client` function the Strava code
    uses; the test TestClient never goes through it."""
    monkeypatch.setenv("STRAVA_WEBHOOK_VERIFY_TOKEN", "tok")
    user_id = str(uuid.uuid4())
    external_user_id = "STRAVA_WEBHOOK_ACKFAST_USER"
    _make_strava_account(user_id, external_user_id)

    # Track invocations of the shared strava httpx client. If the
    # public handler called it during the request, we'd see the count
    # > 0. The worker uses it but TEST_MODE short-circuits before
    # `get_http_client` is invoked.
    from app.services import strava_client as sc
    call_count = {"n": 0}
    real_get_http_client = sc.get_http_client

    async def _counting_get_client():
        call_count["n"] += 1
        return await real_get_http_client()

    monkeypatch.setattr(sc, "get_http_client", _counting_get_client)

    resp = client.post(
        "/integrations/strava/webhook",
        json={
            "object_type": "activity",
            "aspect_type": "create",
            "owner_id": external_user_id,
            "object_id": 12345,
            "subscription_id": 1,
            "event_time": 1690000000,
        },
    )
    assert resp.status_code == 200
    assert resp.text == "ok"
    assert call_count["n"] == 0, (
        "Public webhook handler called Strava httpx client — violates ack-fast contract"
    )


def test_handshake_uses_constant_time_compare(monkeypatch) -> None:
    """Smoke test that hmac.compare_digest is on the path — verify_token
    must be compared in constant time. We can't directly measure timing
    here, just assert the import was used so a future refactor removing
    it shows up in the diff review."""
    import app.api.integrations_strava_webhook as mod
    assert "hmac" in mod.__dict__ or "compare_digest" in str(mod.__dict__), (
        "hmac module must be imported for constant-time compare"
    )
