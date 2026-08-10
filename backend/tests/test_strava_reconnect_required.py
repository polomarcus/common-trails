"""FIX 2b coverage — sustained-refresh-failure → reconnect-required plumbing.

Drives the REAL handlers (no inline mirrors — see
`feedback_real_handler_tests_not_inline_mirror`):

  * ``app.api.integrations_strava.refresh_strava_token``
  * ``app.api.integrations_strava.record_refresh_failure``
  * ``GET /integrations/strava/status``

What we prove, against a real ``IntegrationAccount`` row + real Fernet
encryption + mocked Strava HTTP (no network):

  1. REGRESSION — a rotated refresh_token from Strava is persisted AND
     committed (re-read from a fresh session), and the per-user advisory
     lock SQL runs on the refresh path.
  2. Strava 400/401 (dead refresh_token) → ``refresh_strava_token``
     returns None → ``sync_failures`` increments → after N consecutive
     definitive failures the one-shot ``strava_reconnect_required``
     notification is emitted exactly once.
  3. Transient failures (5xx, network) do NOT increment ``sync_failures``
     and never emit — a flaky Strava night must not nag the user.
  4. ``GET /integrations/strava/status`` surfaces ``reconnect_required``
     + ``sync_failures`` once the threshold is crossed.

These paths were previously exercised ONLY by the TEST_MODE stub (which
short-circuits before any Strava call). Here TEST_MODE is monkeypatched
OFF so the real refresh/rotation/failure-accounting code runs, with httpx
mocked.
"""
from __future__ import annotations

import importlib
import time
import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.db.models import IntegrationAccount, Notification, User
from app.db.session import SessionLocal


@pytest.fixture
def fernet_secrets(monkeypatch) -> Iterator[object]:
    """Reload secrets + integrations_strava with a real Fernet key so
    encrypt/decrypt actually transform. Restore afterwards."""
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("STRAVA_TOKEN_ENC_KEY", key)

    import app.config as config_mod
    monkeypatch.setattr(config_mod, "STRAVA_TOKEN_ENC_KEY", key, raising=False)

    import app.services.secrets as secrets_mod
    importlib.reload(secrets_mod)

    import app.api.integrations_strava as strava_mod
    importlib.reload(strava_mod)

    try:
        yield strava_mod
    finally:
        monkeypatch.undo()
        importlib.reload(secrets_mod)
        importlib.reload(strava_mod)


def _make_user() -> str:
    """Insert a real User row (Notification.user_id has a FK). Returns id."""
    user_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(User(
            id=user_id,
            email=f"reconnect_{user_id[:8]}@example.com",
            username=f"u_{user_id[:6]}",
            hashed_password=None,
        ))
        db.commit()
        return user_id
    finally:
        db.close()


def _seed_account(strava_mod, user_id: str, *, expires_at: int | None,
                  sync_failures: int = 0) -> str:
    encrypt_token = strava_mod.encrypt_token
    db = SessionLocal()
    try:
        acct_id = str(uuid.uuid4())
        db.add(IntegrationAccount(
            id=acct_id,
            user_id=user_id,
            provider="strava",
            access_token=encrypt_token("at-original"),
            refresh_token=encrypt_token("rt-original"),
            expires_at=expires_at,
            external_user_id=str(uuid.uuid4().int % 10_000_000),
            sync_failures=sync_failures,
        ))
        db.commit()
        return acct_id
    finally:
        db.close()


def _cleanup(user_id: str, acct_id: str) -> None:
    db = SessionLocal()
    try:
        db.query(IntegrationAccount).filter(IntegrationAccount.id == acct_id).delete()
        db.query(Notification).filter(Notification.user_id == user_id).delete()
        db.query(User).filter(User.id == user_id).delete()
        db.commit()
    finally:
        db.close()


def _reconnect_notifs(user_id: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(Notification)
            .filter(Notification.user_id == user_id)
            .filter(Notification.kind == "strava_reconnect_required")
            .count()
        )
    finally:
        db.close()


# ── 1. REGRESSION: rotation persisted + committed + advisory lock runs ────────

@pytest.mark.asyncio
async def test_rotated_refresh_token_persisted_and_advisory_lock_runs(
    fernet_secrets, monkeypatch,
):
    strava_mod = fernet_secrets
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setattr(strava_mod, "TEST_MODE", False, raising=False)

    user_id = _make_user()
    new_expiry = int(time.time()) + 21600
    acct_id = _seed_account(strava_mod, user_id, expires_at=int(time.time()) - 100)
    db = SessionLocal()
    try:
        acct = db.query(IntegrationAccount).filter(IntegrationAccount.id == acct_id).first()
        stored_rt_before = acct.refresh_token

        resp = httpx.Response(
            200,
            json={
                "access_token": "at-rotated",
                "refresh_token": "rt-ROTATED-by-strava",
                "expires_at": new_expiry,
            },
            request=httpx.Request("POST", strava_mod.STRAVA_TOKEN_URL),
        )
        fake_client = AsyncMock()
        fake_client.post = AsyncMock(return_value=resp)

        # Spy on db.execute so we can prove the per-user advisory lock SQL
        # actually runs on the refresh path (serialises concurrent refreshes).
        real_execute = db.execute
        exec_spy = MagicMock(side_effect=real_execute)
        monkeypatch.setattr(db, "execute", exec_spy)

        with patch.object(strava_mod, "get_http_client", AsyncMock(return_value=fake_client)):
            result = await strava_mod.refresh_strava_token(acct, db)

        assert result == "at-rotated"
        executed_sql = " ".join(str(c.args[0]) for c in exec_spy.call_args_list if c.args)
        assert "pg_try_advisory_xact_lock" in executed_sql, (
            "refresh must serialise via the per-user advisory lock"
        )

        # Re-read from a FRESH session → proves the rotation was committed.
        db2 = SessionLocal()
        try:
            reread = db2.query(IntegrationAccount).filter(
                IntegrationAccount.id == acct_id
            ).first()
            assert reread.expires_at == new_expiry
            assert reread.refresh_token != stored_rt_before
            assert strava_mod.decrypt_token(reread.refresh_token) == "rt-ROTATED-by-strava"
            assert reread.sync_failures == 0, "successful rotation must not touch sync_failures"
        finally:
            db2.close()
    finally:
        db.close()
        _cleanup(user_id, acct_id)


# ── 2. Definitive failure → increments + one-shot reconnect notification ──────

@pytest.mark.asyncio
async def test_401_increments_failures_and_emits_reconnect_at_threshold(
    fernet_secrets, monkeypatch,
):
    strava_mod = fernet_secrets
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setattr(strava_mod, "TEST_MODE", False, raising=False)
    threshold = strava_mod.STRAVA_RECONNECT_THRESHOLD

    user_id = _make_user()
    acct_id = _seed_account(strava_mod, user_id, expires_at=int(time.time()) - 100)
    try:
        # Drive the real async function `threshold` times. Reset expiry into
        # the past before each call so refresh always attempts the Strava
        # POST (a prior rotation could otherwise leave a valid token).
        for i in range(1, threshold + 1):
            db = SessionLocal()
            try:
                acct = db.query(IntegrationAccount).filter(
                    IntegrationAccount.id == acct_id
                ).first()
                acct.expires_at = int(time.time()) - 100
                db.commit()

                resp = httpx.Response(
                    401,
                    json={"message": "Bad Request",
                          "errors": [{"field": "refresh_token", "code": "invalid"}]},
                    request=httpx.Request("POST", strava_mod.STRAVA_TOKEN_URL),
                )
                fake_client = AsyncMock()
                fake_client.post = AsyncMock(return_value=resp)
                with patch.object(strava_mod, "get_http_client",
                                  AsyncMock(return_value=fake_client)):
                    result = await strava_mod.refresh_strava_token(acct, db)
                assert result is None, "dead refresh_token must return None"
            finally:
                db.close()

            # Failures accumulate; notification only at the exact crossing.
            db = SessionLocal()
            try:
                reread = db.query(IntegrationAccount).filter(
                    IntegrationAccount.id == acct_id
                ).first()
                assert reread.sync_failures == i
            finally:
                db.close()

            expected_notifs = 1 if i >= threshold else 0
            assert _reconnect_notifs(user_id) == expected_notifs, (
                f"after {i} failures expected {expected_notifs} reconnect notif(s)"
            )

        # One more failure past the threshold — still exactly ONE notification.
        db = SessionLocal()
        try:
            acct = db.query(IntegrationAccount).filter(
                IntegrationAccount.id == acct_id
            ).first()
            acct.expires_at = int(time.time()) - 100
            db.commit()
            resp = httpx.Response(
                401,
                json={"errors": [{"code": "invalid"}]},
                request=httpx.Request("POST", strava_mod.STRAVA_TOKEN_URL),
            )
            fake_client = AsyncMock()
            fake_client.post = AsyncMock(return_value=resp)
            with patch.object(strava_mod, "get_http_client",
                              AsyncMock(return_value=fake_client)):
                await strava_mod.refresh_strava_token(acct, db)
        finally:
            db.close()
        assert _reconnect_notifs(user_id) == 1, "reconnect prompt must be one-shot"
    finally:
        _cleanup(user_id, acct_id)


# ── 3. Transient failure → NO increment, NO notification ──────────────────────

@pytest.mark.asyncio
async def test_transient_5xx_does_not_increment_or_notify(fernet_secrets, monkeypatch):
    strava_mod = fernet_secrets
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setattr(strava_mod, "TEST_MODE", False, raising=False)

    user_id = _make_user()
    acct_id = _seed_account(strava_mod, user_id, expires_at=int(time.time()) - 100)
    db = SessionLocal()
    try:
        acct = db.query(IntegrationAccount).filter(IntegrationAccount.id == acct_id).first()
        resp = httpx.Response(
            503, text="upstream down",
            request=httpx.Request("POST", strava_mod.STRAVA_TOKEN_URL),
        )
        fake_client = AsyncMock()
        fake_client.post = AsyncMock(return_value=resp)
        with patch.object(strava_mod, "get_http_client", AsyncMock(return_value=fake_client)):
            result = await strava_mod.refresh_strava_token(acct, db)
        assert result is None
    finally:
        db.close()

    db = SessionLocal()
    try:
        reread = db.query(IntegrationAccount).filter(IntegrationAccount.id == acct_id).first()
        assert reread.sync_failures == 0, "transient 5xx must NOT count toward reconnect"
    finally:
        db.close()
    assert _reconnect_notifs(user_id) == 0
    _cleanup(user_id, acct_id)


@pytest.mark.asyncio
async def test_network_error_does_not_increment(fernet_secrets, monkeypatch):
    strava_mod = fernet_secrets
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setattr(strava_mod, "TEST_MODE", False, raising=False)

    user_id = _make_user()
    acct_id = _seed_account(strava_mod, user_id, expires_at=int(time.time()) - 100)
    db = SessionLocal()
    try:
        acct = db.query(IntegrationAccount).filter(IntegrationAccount.id == acct_id).first()
        fake_client = AsyncMock()
        fake_client.post = AsyncMock(side_effect=httpx.ConnectError("dns fail"))
        with patch.object(strava_mod, "get_http_client", AsyncMock(return_value=fake_client)):
            result = await strava_mod.refresh_strava_token(acct, db)
        assert result is None
    finally:
        db.close()

    db = SessionLocal()
    try:
        reread = db.query(IntegrationAccount).filter(IntegrationAccount.id == acct_id).first()
        assert reread.sync_failures == 0
    finally:
        db.close()
    _cleanup(user_id, acct_id)


# ── 4. record_refresh_failure one-shot, directly ──────────────────────────────

def test_record_refresh_failure_one_shot(monkeypatch):
    import app.api.integrations_strava as strava_mod
    threshold = strava_mod.STRAVA_RECONNECT_THRESHOLD

    user_id = _make_user()
    acct_id = _seed_account(strava_mod, user_id, expires_at=int(time.time()) + 100)
    try:
        db = SessionLocal()
        try:
            acct = db.query(IntegrationAccount).filter(
                IntegrationAccount.id == acct_id
            ).first()
            for i in range(1, threshold + 2):
                n = strava_mod.record_refresh_failure(acct, db)
                assert n == i
        finally:
            db.close()
        # Exactly one reconnect notification across all the failures.
        assert _reconnect_notifs(user_id) == 1
    finally:
        _cleanup(user_id, acct_id)


# ── 5. /status surfaces reconnect_required + sync_failures ────────────────────

def test_status_surfaces_reconnect_required(client):
    """Drive the REAL GET /integrations/strava/status endpoint."""
    import app.api.integrations_strava as strava_mod
    threshold = strava_mod.STRAVA_RECONNECT_THRESHOLD

    # Register a user via the API so we get a valid auth token + real row.
    email = f"status_{uuid.uuid4().hex[:8]}@example.com"
    reg = client.post("/auth/register", json={
        "email": email, "password": "testpass123",
        "username": f"u_{uuid.uuid4().hex[:6]}",
    })
    assert reg.status_code == 201, reg.text
    user_id = reg.json()["user_id"]
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}

    acct_id = _seed_account(strava_mod, user_id, expires_at=int(time.time()) + 999,
                            sync_failures=threshold)
    try:
        resp = client.get("/integrations/strava/status", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["connected"] is True
        assert body["sync_failures"] == threshold
        assert body["reconnect_required"] is True

        # Below threshold → not required.
        db = SessionLocal()
        try:
            acct = db.query(IntegrationAccount).filter(
                IntegrationAccount.id == acct_id
            ).first()
            acct.sync_failures = threshold - 1
            db.commit()
        finally:
            db.close()
        body2 = client.get("/integrations/strava/status", headers=headers).json()
        assert body2["reconnect_required"] is False
        assert body2["sync_failures"] == threshold - 1
    finally:
        _cleanup(user_id, acct_id)
