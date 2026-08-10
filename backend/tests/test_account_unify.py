"""Account unification — Strava-connect ⇄ email login.

Drives the REAL handlers through the FastAPI test client:

  (b) LINK-ON-CONNECT — a logged-in user who connects Strava gets the athlete
      attached to THEIR account (no synthetic strava_<id>@strava.local user),
      and a logged-in user can NEVER steal an athlete already linked to someone
      else (the old "ghost migration" hijack is gone → conflict redirect).

  (a) POST /auth/me/email — set/change the account email with a confirm link,
      refusing (409) any address already owned by another account (no takeover),
      and finalizing only on POST /auth/me/email/confirm.

  purpose guard — the email_change token can't authenticate a session and can't
      be replayed at the magic-link login verify; a magic-link token can't
      finalize an email change.

The Strava callback stub always uses external_user_id == "strava_stub_user_42";
we wipe it around each test so a leftover binding can't mask a conflict path.
"""
from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text as sa_text

from app.db.models import IntegrationAccount, User
from app.db.session import SessionLocal

_STUB_STRAVA_ID = "strava_stub_user_42"
_SYNTHETIC_EMAIL = f"strava_{_STUB_STRAVA_ID}@strava.local"


@pytest.fixture(autouse=True)
def _strava_enabled(monkeypatch):
    import app.api.integrations_strava as strava_mod
    monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", True)
    monkeypatch.setattr(strava_mod, "TEST_MODE", True)
    monkeypatch.setattr(strava_mod, "FRONTEND_URL", "http://localhost:3787")
    yield


@pytest.fixture(autouse=True)
def _wipe_stub_binding():
    """Remove any IntegrationAccount / synthetic user for the stub athlete
    before AND after each test so bindings never leak across tests."""
    def _wipe():
        db = SessionLocal()
        try:
            db.execute(sa_text(
                "DELETE FROM integration_accounts WHERE external_user_id = :x"
            ), {"x": _STUB_STRAVA_ID})
            db.execute(sa_text(
                "DELETE FROM users WHERE email = :e"
            ), {"e": _SYNTHETIC_EMAIL})
            db.commit()
        finally:
            db.close()
    _wipe()
    yield
    _wipe()


@pytest.fixture
def capture_send(monkeypatch):
    """Capture app.api.auth.send_email calls (no network)."""
    from unittest.mock import MagicMock
    mock = MagicMock(return_value=True)
    monkeypatch.setattr("app.api.auth.send_email", mock)
    return mock


def _unique_email() -> str:
    return f"unify_{uuid.uuid4().hex[:8]}@example.com"


def _register(client) -> tuple[str, str, str]:
    """Register a fresh real-email user. Returns (user_id, email, bearer)."""
    email = _unique_email()
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "testpass123", "username": f"u_{uuid.uuid4().hex[:6]}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return data["user_id"], email, data["access_token"]


def _create_user_directly(email: str) -> str:
    db = SessionLocal()
    try:
        uid = str(uuid.uuid4())
        db.add(User(id=uid, email=email, username="direct", hashed_password=None))
        db.commit()
        return uid
    finally:
        db.close()


def _link_strava_to(user_id: str) -> None:
    db = SessionLocal()
    try:
        db.add(IntegrationAccount(
            id=str(uuid.uuid4()),
            user_id=user_id,
            provider="strava",
            access_token="x",
            refresh_token="y",
            expires_at=9_999_999_999,
            external_user_id=_STUB_STRAVA_ID,
            athlete_name="Existing",
        ))
        db.commit()
    finally:
        db.close()


def _connect_and_callback(client, bearer: str):
    """Drive GET /connect (logged-in) → follow to /callback. Returns callback resp."""
    connect = client.get(
        "/integrations/strava/connect",
        headers={"Authorization": f"Bearer {bearer}"},
        follow_redirects=False,
    )
    assert connect.status_code in (302, 307), connect.text
    callback_url = connect.headers["location"]
    return client.get(callback_url, follow_redirects=False)


def _extract_token(html: str) -> str:
    m = re.search(r"token=([A-Za-z0-9._\-]+)", html)
    assert m, f"no token in email html: {html[:200]}"
    return m.group(1)


def _strava_account_owner() -> str | None:
    db = SessionLocal()
    try:
        acct = db.query(IntegrationAccount).filter(
            IntegrationAccount.external_user_id == _STUB_STRAVA_ID,
            IntegrationAccount.provider == "strava",
        ).first()
        return acct.user_id if acct else None
    finally:
        db.close()


def _user_email(user_id: str) -> str | None:
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == user_id).first()
        return u.email if u else None
    finally:
        db.close()


# ── (b) LINK-ON-CONNECT ────────────────────────────────────────────────

class TestLinkOnConnect:
    def test_logged_in_connect_attaches_to_current_user(self, client):
        """A logged-in email user who connects Strava gets the athlete bound to
        THEIR account — no synthetic strava_<id>@strava.local user is created."""
        user_id, email, bearer = _register(client)

        resp = _connect_and_callback(client, bearer)
        assert resp.status_code in (302, 307), resp.text
        assert "status=connected" in resp.headers.get("location", "")

        # The athlete is bound to the logged-in user, not a fresh synthetic one.
        assert _strava_account_owner() == user_id
        # No synthetic user was created for this athlete.
        db = SessionLocal()
        try:
            synth = db.query(User).filter(User.email == _SYNTHETIC_EMAIL).first()
            assert synth is None, "connect flow wrongly created a synthetic strava_ user"
        finally:
            db.close()
        # The user's real email is untouched.
        assert _user_email(user_id) == email

    def test_connect_does_not_steal_athlete_linked_to_another_user(self, client):
        """User A connecting an athlete already linked to user B must NOT relink
        or migrate — B keeps the link, A is told there is a conflict."""
        b_id = _create_user_directly(_unique_email())
        _link_strava_to(b_id)

        a_id, _a_email, a_bearer = _register(client)
        resp = _connect_and_callback(client, a_bearer)

        # Refused via a conflict redirect (never a silent success bind).
        assert resp.status_code in (302, 307), resp.text
        loc = resp.headers.get("location", "")
        assert "status=conflict" in loc, f"expected conflict redirect, got {loc}"

        # The athlete still belongs to B; A has no Strava account.
        assert _strava_account_owner() == b_id
        db = SessionLocal()
        try:
            a_acct = db.query(IntegrationAccount).filter(
                IntegrationAccount.user_id == a_id,
                IntegrationAccount.provider == "strava",
            ).first()
            assert a_acct is None
            # B's user row is intact (old ghost-migration would delete it).
            assert db.query(User).filter(User.id == b_id).first() is not None
        finally:
            db.close()


# ── (a) POST /auth/me/email ────────────────────────────────────────────

class TestSetEmail:
    def test_refuses_email_owned_by_another_user(self, client, capture_send):
        """409 when the target email already belongs to a different account —
        never a merge/takeover."""
        _b_id, b_email, _ = _register(client)
        _a_id, a_email, a_bearer = _register(client)

        resp = client.post(
            "/auth/me/email",
            json={"email": b_email},
            headers={"Authorization": f"Bearer {a_bearer}"},
        )
        assert resp.status_code == 409, resp.text
        # No email was sent, no address changed.
        assert capture_send.call_count == 0
        assert _user_email(_a_id) == a_email
        assert _user_email(_b_id) == b_email

    def test_synthetic_to_real_email_confirm_flow(self, client, capture_send):
        """A synthetic-email owner sets a real email; it finalizes only after
        confirming the link mailed to the NEW address."""
        from app.api.auth import _create_token

        synth_id = _create_user_directly(_SYNTHETIC_EMAIL)
        bearer = _create_token(synth_id, _SYNTHETIC_EMAIL)
        new_email = _unique_email()

        resp = client.post(
            "/auth/me/email",
            json={"email": new_email},
            headers={"Authorization": f"Bearer {bearer}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["pending"] is True and body["email"] == new_email
        # Confirmation mailed to the NEW address; account NOT yet changed.
        assert capture_send.call_count == 1
        assert capture_send.call_args.args[0] == new_email
        assert _user_email(synth_id) == _SYNTHETIC_EMAIL

        token = _extract_token(capture_send.call_args.args[2])
        confirm = client.post("/auth/me/email/confirm", json={"token": token})
        assert confirm.status_code == 200, confirm.text
        assert confirm.json()["email"] == new_email
        assert _user_email(synth_id) == new_email

        # Single-use: replaying the confirm token is rejected.
        again = client.post("/auth/me/email/confirm", json={"token": token})
        assert again.status_code == 401

    def test_same_email_is_noop(self, client, capture_send):
        _uid, email, bearer = _register(client)
        resp = client.post(
            "/auth/me/email",
            json={"email": email},
            headers={"Authorization": f"Bearer {bearer}"},
        )
        assert resp.status_code == 200
        assert resp.json()["pending"] is False
        assert capture_send.call_count == 0

    def test_confirm_rechecks_takeover(self, client, capture_send):
        """If the target address gets claimed by another account between request
        and confirm, finalize refuses (409) — never overwrites/merges."""
        from app.api.auth import _create_token

        synth_id = _create_user_directly(_SYNTHETIC_EMAIL)
        bearer = _create_token(synth_id, _SYNTHETIC_EMAIL)
        new_email = _unique_email()

        resp = client.post(
            "/auth/me/email",
            json={"email": new_email},
            headers={"Authorization": f"Bearer {bearer}"},
        )
        assert resp.status_code == 200
        token = _extract_token(capture_send.call_args.args[2])

        # Someone else grabs that email first.
        _create_user_directly(new_email)

        confirm = client.post("/auth/me/email/confirm", json={"token": token})
        assert confirm.status_code == 409, confirm.text
        assert _user_email(synth_id) == _SYNTHETIC_EMAIL

    def test_requires_auth(self, client):
        resp = client.post("/auth/me/email", json={"email": _unique_email()})
        assert resp.status_code == 401


# ── purpose guard (cross-flow token replay) ────────────────────────────

class TestPurposeGuard:
    def _mint_email_change_token(self, client, capture_send) -> str:
        from app.api.auth import _create_token
        synth_id = _create_user_directly(_SYNTHETIC_EMAIL)
        bearer = _create_token(synth_id, _SYNTHETIC_EMAIL)
        client.post(
            "/auth/me/email",
            json={"email": _unique_email()},
            headers={"Authorization": f"Bearer {bearer}"},
        )
        return _extract_token(capture_send.call_args.args[2])

    def test_email_change_token_cannot_authenticate_session(self, client, capture_send):
        """An email_change token carries a `purpose` claim → _decode_token rejects
        it as a session credential (cookie or Bearer)."""
        token = self._mint_email_change_token(client, capture_send)
        # As Bearer.
        r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        # As cookie.
        client.cookies.set("auth_token", token)
        r2 = client.get("/auth/me")
        assert r2.status_code == 401
        client.cookies.clear()

    def test_email_change_token_rejected_at_magic_link_verify(self, client, capture_send):
        """The magic-link LOGIN verify must reject an email_change token
        (wrong purpose) — no cross-flow login."""
        token = self._mint_email_change_token(client, capture_send)
        r = client.post("/auth/email/verify", json={"token": token})
        assert r.status_code == 401

    def test_magic_link_token_cannot_finalize_email_change(self, client, capture_send):
        """A magic_link LOGIN token must not be accepted by the email-change
        confirm endpoint (purpose mismatch)."""
        # Mint a real magic-link login token via the request endpoint.
        capture_send.reset_mock()
        email = _unique_email()
        client.post(
            "/auth/email/request",
            json={"email": email},
            headers={"X-Forwarded-For": f"10.{uuid.uuid4().int % 250}.0.1"},
        )
        assert capture_send.call_count == 1
        login_token = _extract_token(capture_send.call_args.args[2])
        r = client.post("/auth/me/email/confirm", json={"token": login_token})
        assert r.status_code == 401
