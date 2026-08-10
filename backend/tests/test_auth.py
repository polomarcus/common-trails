"""Tests for authentication endpoints."""
import os
import uuid
from datetime import UTC, datetime, timedelta

from jose import jwt

JWT_SECRET = os.environ.get("JWT_SECRET", "test-secret-do-not-use-in-prod")
JWT_ALGORITHM = "HS256"


def _unique_email() -> str:
    return f"test_{uuid.uuid4().hex[:8]}@example.com"


class TestRegister:
    def test_register_success(self, client):
        resp = client.post(
            "/auth/register",
            json={
                "email": _unique_email(),
                "password": "strongpass123",
                "username": "testuser",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "user_id" in data
        assert "@" in data["email"]

    def test_register_duplicate_email(self, client):
        email = _unique_email()
        client.post(
            "/auth/register",
            json={"email": email, "password": "password1234", "username": "u1"},
        )
        resp = client.post(
            "/auth/register",
            json={"email": email, "password": "password5678", "username": "u2"},
        )
        assert resp.status_code == 409

    def test_register_invalid_email(self, client):
        resp = client.post(
            "/auth/register",
            json={"email": "not-an-email", "password": "password1234", "username": "u"},
        )
        assert resp.status_code == 422

    def test_register_short_password(self, client):
        resp = client.post(
            "/auth/register",
            json={"email": _unique_email(), "password": "short", "username": "u"},
        )
        assert resp.status_code == 422


class TestRegisterClosedBeta:
    """Closed-beta gate via BETA_INVITE_CODE env var.

    When the env is set (non-empty), registration requires a matching
    invite_code. When unset (default), registration is open.
    """

    def test_no_invite_code_required_when_env_unset(self, client, monkeypatch):
        monkeypatch.delenv("BETA_INVITE_CODE", raising=False)
        resp = client.post(
            "/auth/register",
            json={"email": _unique_email(), "password": "strongpass123", "username": "u"},
        )
        assert resp.status_code == 201

    def test_register_blocked_without_invite_code(self, client, monkeypatch):
        monkeypatch.setenv("BETA_INVITE_CODE", "secret-2026")
        resp = client.post(
            "/auth/register",
            json={"email": _unique_email(), "password": "strongpass123", "username": "u"},
        )
        assert resp.status_code == 403
        assert "invite" in resp.json()["detail"].lower()

    def test_register_blocked_with_wrong_invite_code(self, client, monkeypatch):
        monkeypatch.setenv("BETA_INVITE_CODE", "secret-2026")
        resp = client.post(
            "/auth/register",
            json={
                "email": _unique_email(),
                "password": "strongpass123",
                "username": "u",
                "invite_code": "wrong-code",
            },
        )
        assert resp.status_code == 403

    def test_register_succeeds_with_correct_invite_code(self, client, monkeypatch):
        monkeypatch.setenv("BETA_INVITE_CODE", "secret-2026")
        resp = client.post(
            "/auth/register",
            json={
                "email": _unique_email(),
                "password": "strongpass123",
                "username": "u",
                "invite_code": "secret-2026",
            },
        )
        assert resp.status_code == 201

    def test_register_invite_code_trims_whitespace(self, client, monkeypatch):
        monkeypatch.setenv("BETA_INVITE_CODE", "  secret-2026  ")
        resp = client.post(
            "/auth/register",
            json={
                "email": _unique_email(),
                "password": "strongpass123",
                "username": "u",
                "invite_code": "secret-2026",
            },
        )
        assert resp.status_code == 201


class TestLogin:
    def test_login_success(self, client):
        email = _unique_email()
        password = "mypassword456"
        client.post(
            "/auth/register",
            json={"email": email, "password": password, "username": "loginuser"},
        )
        resp = client.post(
            "/auth/login",
            data={"username": email, "password": password},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password(self, client):
        email = _unique_email()
        client.post(
            "/auth/register",
            json={"email": email, "password": "correctpass123", "username": "u"},
        )
        resp = client.post(
            "/auth/login",
            data={"username": email, "password": "wrongpassword"},
        )
        assert resp.status_code == 401

    def test_login_unknown_email(self, client):
        resp = client.post(
            "/auth/login",
            data={"username": "nobody@example.com", "password": "whatever"},
        )
        assert resp.status_code == 401


class TestMe:
    def test_me_authenticated(self, client, auth_headers):
        resp = client.get("/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "user_id" in data
        assert "email" in data
        assert "username" in data

    def test_me_unauthenticated(self, client):
        # Clear any cookies from previous tests (TestClient carries cookies)
        client.cookies.clear()
        resp = client.get("/auth/me")
        assert resp.status_code == 401

    def test_me_invalid_token(self, client):
        resp = client.get("/auth/me", headers={"Authorization": "Bearer invalid.token.here"})
        assert resp.status_code == 401


class TestJWTExpiration:
    """Verify expired and tampered tokens are rejected."""

    def test_expired_token_returns_401(self, client):
        """A token with a past `exp` claim must be rejected."""
        # Register a real user so the sub claim is valid
        email = _unique_email()
        reg = client.post(
            "/auth/register",
            json={"email": email, "password": "password1234", "username": "expuser"},
        )
        user_id = reg.json()["user_id"]

        # Forge token that expired 1 hour ago
        expired_payload = {
            "sub": user_id,
            "email": email,
            "exp": datetime.now(UTC) - timedelta(hours=1),
        }
        expired_token = jwt.encode(expired_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

        resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401
        assert "expired" in resp.json()["detail"].lower() or "invalid" in resp.json()["detail"].lower()

    def test_token_wrong_secret_returns_401(self, client):
        """A token signed with the wrong secret must be rejected."""
        email = _unique_email()
        reg = client.post(
            "/auth/register",
            json={"email": email, "password": "password1234", "username": "wrongsec"},
        )
        user_id = reg.json()["user_id"]

        tampered_token = jwt.encode(
            {"sub": user_id, "email": email, "exp": datetime.now(UTC) + timedelta(hours=1)},
            "wrong-secret-key",
            algorithm=JWT_ALGORITHM,
        )

        resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {tampered_token}"},
        )
        assert resp.status_code == 401

    def test_token_missing_sub_returns_401(self, client):
        """A token without a `sub` claim should fail (user not found)."""
        no_sub_token = jwt.encode(
            {"email": "no@sub.com", "exp": datetime.now(UTC) + timedelta(hours=1)},
            JWT_SECRET,
            algorithm=JWT_ALGORITHM,
        )
        resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {no_sub_token}"},
        )
        assert resp.status_code == 401

    def test_token_nonexistent_user_returns_401(self, client):
        """A validly signed token for a deleted/nonexistent user_id must return 401."""
        fake_user_id = str(uuid.uuid4())
        token = jwt.encode(
            {"sub": fake_user_id, "email": "ghost@example.com",
             "exp": datetime.now(UTC) + timedelta(hours=1)},
            JWT_SECRET,
            algorithm=JWT_ALGORITHM,
        )
        resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401
