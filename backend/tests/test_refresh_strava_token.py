"""Security coverage for ``refresh_strava_token`` (Strava OAuth token rotation).

Drives the REAL ``app.api.integrations_strava.refresh_strava_token`` against a
real ``IntegrationAccount`` row in the DB. The function is security-sensitive:
it holds the encrypted-at-rest Strava access/refresh tokens, rotates them on
expiry, and must NOT leak / re-POST a still-valid token.

Cases:
  (a) token still valid (expires_at > now + 5min) → returns the plaintext
      access token AND makes NO Strava HTTP call.
  (b) token expired → POSTs to Strava /oauth/token, gets new tokens →
      the DB row's access_token / refresh_token are rewritten (re-encrypted)
      and expires_at is bumped; the returned value is the NEW access token.
  (c) Strava returns 401 invalid_grant → returns None (no crash, no rotation).

These run with TEST_MODE=false (monkeypatched) so the REAL refresh path
executes — in TEST_MODE the function short-circuits to a stub.
"""
from __future__ import annotations

import importlib
import time
import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.db.models import IntegrationAccount
from app.db.session import SessionLocal


@pytest.fixture
def fernet_secrets(monkeypatch) -> Iterator[object]:
    """Reload the secrets module with a real Fernet key set, so encrypt/decrypt
    actually transform (not the unset-key no-op). Reload integrations_strava so
    its module-level ``encrypt_token`` / ``decrypt_token`` bind to the keyed
    versions. Restore both modules afterwards so other tests are unaffected.
    """
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
        # Reload back to the (keyless) default so the rest of the suite sees
        # the original module objects.
        monkeypatch.undo()
        importlib.reload(secrets_mod)
        importlib.reload(strava_mod)


def _seed_account(strava_mod, *, expires_at: int | None, refresh: str = "rt-original") -> str:
    """Insert an IntegrationAccount with encrypted tokens. Returns the row id."""
    encrypt_token = strava_mod.encrypt_token
    db = SessionLocal()
    try:
        acct_id = str(uuid.uuid4())
        acct = IntegrationAccount(
            id=acct_id,
            user_id=str(uuid.uuid4()),
            provider="strava",
            access_token=encrypt_token("at-original"),
            refresh_token=encrypt_token(refresh),
            expires_at=expires_at,
            external_user_id=str(uuid.uuid4().int % 10_000_000),
        )
        db.add(acct)
        db.commit()
        return acct_id
    finally:
        db.close()


def _delete_account(acct_id: str) -> None:
    db = SessionLocal()
    try:
        db.query(IntegrationAccount).filter(IntegrationAccount.id == acct_id).delete()
        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_valid_token_returns_plaintext_no_http(fernet_secrets, monkeypatch):
    """expires_at well in the future → fast path: return decrypted token, no
    Strava POST at all."""
    strava_mod = fernet_secrets
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setattr(strava_mod, "TEST_MODE", False, raising=False)

    acct_id = _seed_account(strava_mod, expires_at=int(time.time()) + 7200)
    db = SessionLocal()
    try:
        acct = db.query(IntegrationAccount).filter(IntegrationAccount.id == acct_id).first()
        assert acct is not None

        # If the real client were called we'd want to know — make it explode.
        fake_client = AsyncMock()
        fake_client.post = AsyncMock(side_effect=AssertionError("must not POST on valid token"))
        with patch.object(strava_mod, "get_http_client", AsyncMock(return_value=fake_client)):
            result = await strava_mod.refresh_strava_token(acct, db)

        assert result == "at-original", "valid token should be returned as plaintext"
        fake_client.post.assert_not_called()
    finally:
        db.close()
        _delete_account(acct_id)


@pytest.mark.asyncio
async def test_expired_token_rotates_and_reencrypts(fernet_secrets, monkeypatch):
    """expired token → Strava issues new tokens → DB row rewritten with new
    encrypted values, expires_at bumped, NEW access token returned."""
    strava_mod = fernet_secrets
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setattr(strava_mod, "TEST_MODE", False, raising=False)

    new_expiry = int(time.time()) + 21600  # +6h
    acct_id = _seed_account(strava_mod, expires_at=int(time.time()) - 100)
    db = SessionLocal()
    try:
        acct = db.query(IntegrationAccount).filter(IntegrationAccount.id == acct_id).first()
        assert acct is not None
        stored_at_before = acct.access_token
        stored_rt_before = acct.refresh_token

        resp = httpx.Response(
            200,
            json={
                "access_token": "at-rotated",
                "refresh_token": "rt-rotated",
                "expires_at": new_expiry,
            },
            request=httpx.Request("POST", strava_mod.STRAVA_TOKEN_URL),
        )
        fake_client = AsyncMock()
        fake_client.post = AsyncMock(return_value=resp)
        with patch.object(strava_mod, "get_http_client", AsyncMock(return_value=fake_client)):
            result = await strava_mod.refresh_strava_token(acct, db)

        fake_client.post.assert_called_once()
        assert result == "at-rotated", "must return the freshly issued access token"

        # Re-read from a fresh session to prove it was committed to the DB.
        db2 = SessionLocal()
        try:
            reread = db2.query(IntegrationAccount).filter(
                IntegrationAccount.id == acct_id
            ).first()
            assert reread.expires_at == new_expiry
            assert reread.access_token != stored_at_before, "access_token must be rewritten"
            assert reread.refresh_token != stored_rt_before, "refresh_token must be rewritten"
            # And the new ciphertext must decrypt to the rotated plaintext.
            assert strava_mod.decrypt_token(reread.access_token) == "at-rotated"
            assert strava_mod.decrypt_token(reread.refresh_token) == "rt-rotated"
        finally:
            db2.close()
    finally:
        db.close()
        _delete_account(acct_id)


@pytest.mark.asyncio
async def test_invalid_grant_returns_none(fernet_secrets, monkeypatch):
    """Strava 401 invalid_grant (revoked refresh token) → return None, no crash,
    no rotation of the stored tokens."""
    strava_mod = fernet_secrets
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setattr(strava_mod, "TEST_MODE", False, raising=False)

    acct_id = _seed_account(strava_mod, expires_at=int(time.time()) - 100)
    db = SessionLocal()
    try:
        acct = db.query(IntegrationAccount).filter(IntegrationAccount.id == acct_id).first()
        assert acct is not None
        stored_at_before = acct.access_token

        req = httpx.Request("POST", strava_mod.STRAVA_TOKEN_URL)
        resp = httpx.Response(
            401,
            json={"message": "Bad Request", "errors": [{"field": "refresh_token", "code": "invalid"}]},
            request=req,
        )
        fake_client = AsyncMock()
        # raise_for_status() raises HTTPStatusError on 401 → caught → None.
        fake_client.post = AsyncMock(return_value=resp)
        with patch.object(strava_mod, "get_http_client", AsyncMock(return_value=fake_client)):
            result = await strava_mod.refresh_strava_token(acct, db)

        assert result is None, "401 invalid_grant must return None, not raise"

        db2 = SessionLocal()
        try:
            reread = db2.query(IntegrationAccount).filter(
                IntegrationAccount.id == acct_id
            ).first()
            assert reread.access_token == stored_at_before, "tokens must NOT rotate on failure"
        finally:
            db2.close()
    finally:
        db.close()
        _delete_account(acct_id)
