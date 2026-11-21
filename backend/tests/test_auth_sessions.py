"""Session lifecycle — password endpoints limited to TEST_MODE, session ends.

Drives the REAL handlers through the FastAPI test client:

* ``POST /auth/register`` and ``POST /auth/login`` answer 404 outside
  TEST_MODE (flag switched at request time), whatever the body;
* ``users.token_version`` (migration 0073) is copied into the session JWT as
  ``tv``: a token whose ``tv`` no longer matches is refused. The version moves
  on logout, on a confirmed email change, and when a magic-link login lands on
  an account that still has a password (the password is dropped);
* confirming an email change opens no session.

Session tokens are sent as Bearer: the auth cookie is ``Secure`` and the
TestClient speaks http, so it is never sent back (see test_email_magic_link).
"""
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from jose import jwt
from sqlalchemy import inspect as sa_inspect

from app.db.models import User
from app.db.session import SessionLocal, engine

JWT_SECRET = os.environ.get("JWT_SECRET", "test-secret-do-not-use-in-prod")
JWT_ALGORITHM = "HS256"
PASSWORD = "testpass123"


def _unique_email() -> str:
    return f"sess_{uuid.uuid4().hex[:8]}@example.com"


def _unique_ip() -> str:
    return f"10.{uuid.uuid4().int % 256}.{uuid.uuid4().int % 256}.{uuid.uuid4().int % 256}"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _extract_token(html: str) -> str:
    m = re.search(r"token=([A-Za-z0-9._\-]+)", html)
    assert m, f"no token in email html: {html[:200]}"
    return m.group(1)


def _register(client, email: str | None = None) -> tuple[str, str, str]:
    """Password account (TEST_MODE). Returns (user_id, email, session token)."""
    email = email or _unique_email()
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "username": f"s_{uuid.uuid4().hex[:6]}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return data["user_id"], email, data["access_token"]


def _login(client, email: str) -> str:
    resp = client.post("/auth/login", data={"username": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _me_status(client, token: str) -> int:
    return client.get("/auth/me", headers=_bearer(token)).status_code


def _user(user_id: str) -> User:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        assert user is not None
        db.expunge(user)
        return user
    finally:
        db.close()


def _magic_link_login(client, capture_send, email: str):
    """Request + verify a magic link for ``email``; returns the verify response."""
    capture_send.reset_mock()
    r = client.post("/auth/email/request", json={"email": email},
                    headers={"X-Forwarded-For": _unique_ip()})
    assert r.status_code == 200, r.text
    token = _extract_token(capture_send.call_args.args[2])
    return client.post("/auth/email/verify", json={"token": token})


@pytest.fixture
def capture_send(monkeypatch):
    mock = MagicMock(return_value=True)
    monkeypatch.setattr("app.api.auth.send_email", mock)
    return mock


# ── Password endpoints: TEST_MODE only ──────────────────────────────────


class TestPasswordEndpointsTestModeOnly:
    def test_register_is_404_outside_test_mode(self, client, monkeypatch):
        monkeypatch.setattr("app.config.TEST_MODE", False)
        email = _unique_email()
        resp = client.post(
            "/auth/register",
            json={"email": email, "password": PASSWORD, "username": "nope"},
        )
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Not Found"}
        # Nothing was created.
        db = SessionLocal()
        try:
            assert db.query(User).filter(User.email == email).first() is None
        finally:
            db.close()

    def test_register_is_404_even_with_an_invalid_body(self, client, monkeypatch):
        """Same answer as an unknown route — not a 422 that would describe it."""
        monkeypatch.setattr("app.config.TEST_MODE", False)
        assert client.post("/auth/register", json={}).status_code == 404

    def test_login_is_404_outside_test_mode(self, client, monkeypatch):
        _uid, email, _token = _register(client)
        monkeypatch.setattr("app.config.TEST_MODE", False)
        resp = client.post("/auth/login", data={"username": email, "password": PASSWORD})
        assert resp.status_code == 404
        assert "access_token" not in resp.text
        assert "auth_token" not in resp.cookies
        assert client.post("/auth/login", data={}).status_code == 404

    def test_both_answer_in_test_mode(self, client):
        _uid, email, token = _register(client)
        assert _me_status(client, token) == 200
        assert _me_status(client, _login(client, email)) == 200


# ── token_version: tv claim + revocation ────────────────────────────────


class TestTokenVersion:
    def test_session_token_carries_the_account_version(self, client):
        uid, _email, token = _register(client)
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert payload["tv"] == _user(uid).token_version == 0

    def test_token_without_tv_is_accepted_while_version_is_zero(self, client):
        """Sessions issued before the claim existed stay valid after deploy."""
        uid, email, _token = _register(client)
        legacy = jwt.encode(
            {"sub": uid, "email": email, "exp": datetime.now(UTC) + timedelta(hours=1)},
            JWT_SECRET, algorithm=JWT_ALGORITHM,
        )
        assert _me_status(client, legacy) == 200

    def test_token_with_another_version_is_refused(self, client):
        uid, email, _token = _register(client)
        other = jwt.encode(
            {"sub": uid, "email": email, "tv": 1,
             "exp": datetime.now(UTC) + timedelta(hours=1)},
            JWT_SECRET, algorithm=JWT_ALGORITHM,
        )
        assert _me_status(client, other) == 401


class TestLogoutEndsSessions:
    def test_logout_refuses_the_token_afterwards(self, client):
        uid, email, token = _register(client)
        legacy = jwt.encode(
            {"sub": uid, "email": email, "exp": datetime.now(UTC) + timedelta(hours=1)},
            JWT_SECRET, algorithm=JWT_ALGORITHM,
        )
        other_device = _login(client, email)
        assert _me_status(client, token) == 200

        resp = client.delete("/auth/logout", headers=_bearer(token))
        assert resp.status_code == 204

        assert _me_status(client, token) == 401
        assert _me_status(client, other_device) == 401
        assert _me_status(client, legacy) == 401  # no tv = 0, now stale
        assert _user(uid).token_version == 1
        # Signing in again works.
        assert _me_status(client, _login(client, email)) == 200

    def test_logout_without_session_just_clears_the_cookie(self, client):
        resp = client.delete("/auth/logout")
        assert resp.status_code == 204
        set_cookie = resp.headers.get("set-cookie", "")
        assert "auth_token=" in set_cookie and "Max-Age=0" in set_cookie

    def test_logout_with_a_stale_token_changes_nothing(self, client):
        uid, email, token = _register(client)
        assert client.delete("/auth/logout", headers=_bearer(token)).status_code == 204
        fresh = _login(client, email)
        # Replaying the ended session's logout does not end the new one.
        assert client.delete("/auth/logout", headers=_bearer(token)).status_code == 204
        assert _me_status(client, fresh) == 200
        assert _user(uid).token_version == 1


# ── Magic-link login on an account that has a password ──────────────────


class TestMagicLinkDropsPassword:
    def test_password_cleared_and_password_sessions_ended(self, client, capture_send):
        uid, email, password_session = _register(client)
        assert _user(uid).hashed_password is not None

        resp = _magic_link_login(client, capture_send, email)
        assert resp.status_code == 200, resp.text
        new_session = resp.cookies.get("auth_token")
        assert new_session

        user = _user(uid)
        assert user.hashed_password is None
        assert user.token_version == 1
        assert _me_status(client, password_session) == 401
        assert _me_status(client, new_session) == 200
        # The old password no longer signs in.
        r = client.post("/auth/login", data={"username": email, "password": PASSWORD})
        assert r.status_code == 401

    def test_passwordless_account_keeps_its_other_sessions(self, client, capture_send):
        """No password to drop → no version bump: logging in on a second
        device does not end the first one."""
        email = _unique_email()
        first = _magic_link_login(client, capture_send, email)
        assert first.status_code == 200, first.text
        second = _magic_link_login(client, capture_send, email)
        assert second.status_code == 200, second.text
        assert _me_status(client, first.cookies.get("auth_token")) == 200
        assert _me_status(client, second.cookies.get("auth_token")) == 200
        assert _user(first.json()["user_id"]).token_version == 0


# ── Email change confirmation ───────────────────────────────────────────


class TestEmailChangeConfirm:
    def _request_change(self, client, capture_send, token: str, new_email: str) -> str:
        capture_send.reset_mock()
        r = client.post("/auth/me/email", json={"email": new_email}, headers=_bearer(token))
        assert r.status_code == 200, r.text
        return _extract_token(capture_send.call_args.args[2])

    def test_confirm_opens_no_session_and_ends_existing_ones(self, client, capture_send):
        uid, _email, token = _register(client)
        new_email = _unique_email()
        link_token = self._request_change(client, capture_send, token, new_email)

        resp = client.post("/auth/me/email/confirm", json={"token": link_token})
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True, "user_id": uid, "email": new_email}
        assert "auth_token" not in resp.cookies
        assert "auth_token" not in resp.headers.get("set-cookie", "")

        user = _user(uid)
        assert user.email == new_email
        assert user.token_version == 1
        assert _me_status(client, token) == 401

    def test_user_signs_in_again_with_the_new_address(self, client, capture_send):
        uid, _email, token = _register(client)
        new_email = _unique_email()
        link_token = self._request_change(client, capture_send, token, new_email)
        assert client.post("/auth/me/email/confirm", json={"token": link_token}).status_code == 200

        resp = _magic_link_login(client, capture_send, new_email)
        assert resp.status_code == 200, resp.text
        assert resp.json()["user_id"] == uid
        assert _me_status(client, resp.cookies.get("auth_token")) == 200


# ── Migration 0073 ──────────────────────────────────────────────────────


def test_migration_0073_chain_and_column():
    # Read as text: importing it would resolve ``alembic`` to backend/alembic/.
    src = (
        Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0073_users_token_version.py"
    ).read_text()
    assert 'revision = "0073"' in src and 'down_revision = "0072"' in src

    col = {c["name"]: c for c in sa_inspect(engine).get_columns("users")}["token_version"]
    assert col["nullable"] is False
    assert str(col["default"]).strip("'") == "0"
