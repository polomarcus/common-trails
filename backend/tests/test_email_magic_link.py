"""Tests for passwordless magic-link login (POST /auth/email/{request,verify}).

Drives the REAL handlers. The Resend send is stubbed at the module boundary
(``app.api.auth.send_email``) so we assert on send behaviour without any
network. Each request carries a UNIQUE X-Forwarded-For so the per-IP DB
rate-limiter doesn't bleed across tests; the rate-limit test deliberately
pins one IP + one email to trip the limit.
"""
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from jose import jwt

from app.db.models import MagicLinkToken, User
from app.db.session import SessionLocal

JWT_SECRET = os.environ.get("JWT_SECRET", "test-secret-do-not-use-in-prod")
JWT_ALGORITHM = "HS256"


def _unique_email() -> str:
    return f"magic_{uuid.uuid4().hex[:8]}@example.com"


def _unique_ip() -> str:
    # Distinct per call so the per-IP rate-limit never trips unintentionally.
    return f"10.{uuid.uuid4().int % 256}.{uuid.uuid4().int % 256}.{uuid.uuid4().int % 256}"


def _extract_token(html: str) -> str:
    m = re.search(r"token=([A-Za-z0-9._\-]+)", html)
    assert m, f"no token in email html: {html[:200]}"
    return m.group(1)


@pytest.fixture
def capture_send(monkeypatch):
    """Replace app.api.auth.send_email with a MagicMock; return it."""
    mock = MagicMock(return_value=True)
    monkeypatch.setattr("app.api.auth.send_email", mock)
    return mock


class TestRequest:
    def test_request_creates_user_and_sends(self, client, capture_send):
        email = _unique_email()
        resp = client.post(
            "/auth/email/request",
            json={"email": email},
            headers={"X-Forwarded-For": _unique_ip()},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # User was find-or-created.
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email).first()
            assert user is not None
            assert user.hashed_password is None  # passwordless account
        finally:
            db.close()
        # Email was sent exactly once.
        assert capture_send.call_count == 1
        to_arg = capture_send.call_args.args[0]
        assert to_arg == email

    def test_request_normalizes_email(self, client, capture_send):
        base = _unique_email()
        mixed = base.upper()
        resp = client.post(
            "/auth/email/request",
            json={"email": mixed},
            headers={"X-Forwarded-For": _unique_ip()},
        )
        assert resp.status_code == 200
        db = SessionLocal()
        try:
            # Stored lower-cased (no duplicate account for the upper variant).
            assert db.query(User).filter(User.email == base).first() is not None
        finally:
            db.close()

    def test_request_generic_response_no_enumeration(self, client, capture_send):
        """Existing and non-existing emails return byte-identical responses."""
        existing = _unique_email()
        client.post("/auth/email/request", json={"email": existing},
                    headers={"X-Forwarded-For": _unique_ip()})
        r_existing = client.post("/auth/email/request", json={"email": existing},
                                 headers={"X-Forwarded-For": _unique_ip()})
        r_new = client.post("/auth/email/request", json={"email": _unique_email()},
                            headers={"X-Forwarded-For": _unique_ip()})
        assert r_existing.status_code == r_new.status_code == 200
        assert r_existing.json() == r_new.json()

    def test_request_rate_limited_per_email(self, client, capture_send):
        """4th request for the same email in the window → HTTP 429 and NO send
        (the DB rate-limiter blocks it). It used to be a silent generic 200:
        the user waited for a link that never came. A 429 leaks nothing —
        accounts are auto-created, so every address behaves the same."""
        email = _unique_email()
        ip = _unique_ip()  # fixed IP + fixed email → deterministic limit
        for _ in range(3):
            r = client.post("/auth/email/request", json={"email": email},
                            headers={"X-Forwarded-For": ip})
            assert r.status_code == 200
        r4 = client.post("/auth/email/request", json={"email": email},
                         headers={"X-Forwarded-For": ip})
        assert r4.status_code == 429
        assert r4.headers.get("Retry-After")
        assert capture_send.call_count == 3  # 4th was suppressed

    def test_shared_ip_distinct_emails_are_not_locked_out(self, client, capture_send):
        """A club / campus NAT shares one IP: 4 riders with distinct emails
        must ALL get their link (the old per-IP cap of 3 locked out the 4th)."""
        ip = _unique_ip()
        for _ in range(4):
            r = client.post("/auth/email/request", json={"email": _unique_email()},
                            headers={"X-Forwarded-For": ip})
            assert r.status_code == 200
        assert capture_send.call_count == 4

    def test_request_rate_limited_per_ip_after_20(self, client, capture_send):
        """Same IP, 20 distinct emails OK; the 21st → 429. The answer depends on
        the request rate, never on account existence."""
        from app.api.auth import _MAGIC_LINK_MAX_PER_IP

        assert _MAGIC_LINK_MAX_PER_IP == 20
        ip = _unique_ip()
        for _ in range(20):
            r = client.post("/auth/email/request", json={"email": _unique_email()},
                            headers={"X-Forwarded-For": ip})
            assert r.status_code == 200
        r21 = client.post("/auth/email/request", json={"email": _unique_email()},
                          headers={"X-Forwarded-For": ip})
        assert r21.status_code == 429
        assert capture_send.call_count == 20

    def _sent_html(self, capture_send) -> str:
        html = capture_send.call_args.kwargs.get("html")
        return html if html is not None else capture_send.call_args.args[2]

    def test_request_redirect_is_carried_into_the_link(self, client, capture_send):
        resp = client.post(
            "/auth/email/request",
            json={"email": _unique_email(), "redirect": "/map"},
            headers={"X-Forwarded-For": _unique_ip()},
        )
        assert resp.status_code == 200
        html = self._sent_html(capture_send)
        assert "/auth/verify?token=" in html
        assert "&redirect=%2Fmap" in html
        # The token is still extractable (the redirect is a separate param).
        assert _extract_token(html)

    @pytest.mark.parametrize("evil", [
        "//evil.com", "https://evil.com", "javascript:alert(1)", "/\\evil.com",
        "evil.com", "/map\nSet-Cookie:x",
    ])
    def test_request_unsafe_redirect_is_dropped(self, client, capture_send, evil):
        resp = client.post(
            "/auth/email/request",
            json={"email": _unique_email(), "redirect": evil},
            headers={"X-Forwarded-For": _unique_ip()},
        )
        assert resp.status_code == 200
        html = self._sent_html(capture_send)
        assert "redirect=" not in html
        assert "evil" not in html

    def test_request_invalid_email_422(self, client, capture_send):
        resp = client.post("/auth/email/request", json={"email": "not-an-email"},
                           headers={"X-Forwarded-For": _unique_ip()})
        assert resp.status_code == 422
        assert capture_send.call_count == 0


class TestVerify:
    def _request_and_get_token(self, client, capture_send) -> str:
        email = _unique_email()
        client.post("/auth/email/request", json={"email": email},
                    headers={"X-Forwarded-For": _unique_ip()})
        html = capture_send.call_args.kwargs.get("html")
        if html is None:
            # send_email(to, subject, html) — positional
            html = capture_send.call_args.args[2]
        return _extract_token(html)

    def test_verify_valid_sets_cookie(self, client, capture_send):
        token = self._request_and_get_token(client, capture_send)
        resp = client.post("/auth/email/verify", json={"token": token})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert "user_id" in data
        assert "@" in data["email"]
        # A real session cookie was issued. (We decode it rather than round-trip
        # /auth/me: the cookie is Secure and TestClient speaks http, so it isn't
        # resent — but the Set-Cookie value itself is the session JWT.)
        cookie = resp.cookies.get("auth_token")
        assert cookie
        session = jwt.decode(cookie, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert session["sub"] == data["user_id"]
        assert session["email"] == data["email"]
        assert session.get("purpose") is None  # a session token, not a magic-link one

    def test_verify_new_account_lands_on_import_page(self, client, capture_send):
        token = self._request_and_get_token(client, capture_send)
        resp = client.post("/auth/email/verify", json={"token": token})
        assert resp.status_code == 200, resp.text
        assert resp.json()["redirect"] == "/strava"

    def test_verify_returning_rider_lands_on_the_map(self, client, capture_send):
        """Non-regression (2026-11-11): a user who had already imported traces
        was sent to the import page (/strava) after logging in."""
        from sqlalchemy import text as sa_text

        token = self._request_and_get_token(client, capture_send)
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        db = SessionLocal()
        try:
            db.execute(sa_text("""
                INSERT INTO activities (id, user_id, provider, sport, name,
                    distance_m, elevation_gain_m, contribute_heatmap, created_at)
                VALUES (:id, :uid, 'file', 'gravel', 'returning-rider-activity',
                    12345, 234, true, NOW())
            """), {"id": str(uuid.uuid4()), "uid": payload["sub"]})
            db.commit()
        finally:
            db.close()
        resp = client.post("/auth/email/verify", json={"token": token})
        assert resp.status_code == 200, resp.text
        assert resp.json()["redirect"] == "/map"

    def test_first_login_notifies_the_admin_once(self, client, capture_send, monkeypatch):
        """Paul, 2026-11-11: an email to the admin when a new rider creates an
        account. Sent at the FIRST successful login (the address is confirmed —
        the row itself is created for any typed address), and only then."""
        from app.services import email as email_service

        monkeypatch.setattr(email_service, "ADMIN_NOTIFY_EMAIL", "admin@example.com")
        # The notification goes through the email module's own send_email.
        monkeypatch.setattr(email_service, "send_email", capture_send)
        email = _unique_email()
        ip = {"X-Forwarded-For": _unique_ip()}

        def login() -> None:
            client.post("/auth/email/request", json={"email": email}, headers=ip)
            html = capture_send.call_args.kwargs.get("html") or capture_send.call_args.args[2]
            assert client.post("/auth/email/verify", json={"token": _extract_token(html)}).status_code == 200

        def admin_mails() -> list:
            return [c for c in capture_send.call_args_list
                    if (c.kwargs.get("to") or c.args[0]) == "admin@example.com"]

        # Requesting a link creates the row but tells nobody.
        client.post("/auth/email/request", json={"email": email}, headers=ip)
        assert admin_mails() == []
        html = capture_send.call_args.kwargs.get("html") or capture_send.call_args.args[2]
        assert client.post("/auth/email/verify", json={"token": _extract_token(html)}).status_code == 200
        sent = admin_mails()
        assert len(sent) == 1
        subject = sent[0].kwargs.get("subject") or sent[0].args[1]
        body = sent[0].kwargs.get("html") or sent[0].args[2]
        assert email in subject and email in body and "/admin" in body

        # A returning rider logging in again is not "new".
        login()
        assert len(admin_mails()) == 1

    def test_no_admin_address_configured_sends_nothing(self, client, capture_send, monkeypatch):
        from app.services import email as email_service

        monkeypatch.setattr(email_service, "ADMIN_NOTIFY_EMAIL", "")
        monkeypatch.setattr(email_service, "send_email", capture_send)
        token = self._request_and_get_token(client, capture_send)
        before = capture_send.call_count
        assert client.post("/auth/email/verify", json={"token": token}).status_code == 200
        assert capture_send.call_count == before

    def test_verify_single_use_reuse_401(self, client, capture_send):
        token = self._request_and_get_token(client, capture_send)
        first = client.post("/auth/email/verify", json={"token": token})
        assert first.status_code == 200
        client.cookies.clear()
        second = client.post("/auth/email/verify", json={"token": token})
        assert second.status_code == 401

    def test_verify_garbage_token_401(self, client):
        resp = client.post("/auth/email/verify", json={"token": "not.a.jwt"})
        assert resp.status_code == 401

    def test_verify_wrong_purpose_401(self, client, capture_send):
        """A session-style JWT (no magic_link purpose) must be rejected even
        with a valid jti row present."""
        # Create a valid jti row via a real request, then forge a token that
        # reuses the jti but omits purpose.
        email = _unique_email()
        client.post("/auth/email/request", json={"email": email},
                    headers={"X-Forwarded-For": _unique_ip()})
        db = SessionLocal()
        try:
            row = db.query(MagicLinkToken).filter(MagicLinkToken.email == email).first()
            assert row is not None
            forged = jwt.encode(
                {"sub": row.user_id, "jti": row.jti,
                 "exp": datetime.now(UTC) + timedelta(minutes=10)},
                JWT_SECRET, algorithm=JWT_ALGORITHM,
            )
        finally:
            db.close()
        resp = client.post("/auth/email/verify", json={"token": forged})
        assert resp.status_code == 401

    def test_verify_expired_jwt_401(self, client, capture_send):
        """A magic-link JWT whose exp is in the past is rejected (signature
        layer), even though the jti row is valid."""
        email = _unique_email()
        client.post("/auth/email/request", json={"email": email},
                    headers={"X-Forwarded-For": _unique_ip()})
        db = SessionLocal()
        try:
            row = db.query(MagicLinkToken).filter(MagicLinkToken.email == email).first()
            forged = jwt.encode(
                {"sub": row.user_id, "purpose": "magic_link", "jti": row.jti,
                 "exp": datetime.now(UTC) - timedelta(minutes=1)},
                JWT_SECRET, algorithm=JWT_ALGORITHM,
            )
        finally:
            db.close()
        resp = client.post("/auth/email/verify", json={"token": forged})
        assert resp.status_code == 401

    def test_verify_expired_db_row_401(self, client, capture_send):
        """JWT valid but the DB row's expires_at is in the past → rejected."""
        token = self._request_and_get_token(client, capture_send)
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        db = SessionLocal()
        try:
            row = db.query(MagicLinkToken).filter(MagicLinkToken.jti == payload["jti"]).first()
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            db.commit()
        finally:
            db.close()
        resp = client.post("/auth/email/verify", json={"token": token})
        assert resp.status_code == 401

    def test_verify_unknown_jti_401(self, client, capture_send):
        """A validly-signed magic-link token whose jti has no row is rejected."""
        # Real user so sub resolves, but jti never stored.
        email = _unique_email()
        client.post("/auth/email/request", json={"email": email},
                    headers={"X-Forwarded-For": _unique_ip()})
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email).first()
            uid = user.id
        finally:
            db.close()
        forged = jwt.encode(
            {"sub": uid, "purpose": "magic_link", "jti": str(uuid.uuid4()),
             "exp": datetime.now(UTC) + timedelta(minutes=10)},
            JWT_SECRET, algorithm=JWT_ALGORITHM,
        )
        resp = client.post("/auth/email/verify", json={"token": forged})
        assert resp.status_code == 401


class TestMagicLinkNotASession:
    """A magic-link token must NEVER authenticate a session.

    The token travels in a URL query param (high-leak: history, Referer, logs).
    If get_current_user accepted it, an attacker holding the raw token could
    skip /email/verify and replay it as the auth_token cookie / Bearer for the
    full TTL, reusably — defeating the single-use ledger. These fail on code
    that doesn't reject a ``purpose`` claim in the session-decode path.
    """

    def _mint_magic_token(self, client, capture_send) -> str:
        email = _unique_email()
        client.post("/auth/email/request", json={"email": email},
                    headers={"X-Forwarded-For": _unique_ip()})
        html = capture_send.call_args.args[2]
        return _extract_token(html)

    def test_magic_link_token_rejected_as_cookie(self, client, capture_send):
        token = self._mint_magic_token(client, capture_send)
        client.cookies.clear()
        client.cookies.set("auth_token", token)  # attacker-set cookie
        resp = client.get("/auth/me")
        assert resp.status_code == 401, "magic-link token must not authenticate via cookie"
        client.cookies.clear()

    def test_magic_link_token_rejected_as_bearer(self, client, capture_send):
        token = self._mint_magic_token(client, capture_send)
        client.cookies.clear()
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401, "magic-link token must not authenticate via Bearer"

    def test_session_token_still_authenticates(self, client, capture_send):
        """Regression guard the other way: a real magic-link LOGIN still yields a
        session cookie whose token DOES authenticate (no purpose claim)."""
        token = self._mint_magic_token(client, capture_send)
        verify = client.post("/auth/email/verify", json={"token": token})
        assert verify.status_code == 200
        session_cookie = verify.cookies.get("auth_token")
        client.cookies.clear()
        client.cookies.set("auth_token", session_cookie)
        me = client.get("/auth/me")
        assert me.status_code == 200
        client.cookies.clear()


class TestEmailService:
    def test_send_email_noop_in_test_mode(self, monkeypatch):
        """send_email must NOT hit the network in TEST_MODE and return True."""
        import app.services.email as email_mod

        post_spy = MagicMock(side_effect=AssertionError("httpx.post must not be called in TEST_MODE"))
        monkeypatch.setattr(email_mod.httpx, "post", post_spy)
        assert email_mod.TEST_MODE is True
        result = email_mod.send_email("a@b.com", "hi", "<p>hi</p>")
        assert result is True
        post_spy.assert_not_called()

    def test_render_magic_link_email_bilingual(self):
        from app.services.email import render_magic_link_email

        url = "https://example.com/auth/verify?token=abc"
        subj_fr, html_fr = render_magic_link_email(url, locale="fr")
        subj_en, html_en = render_magic_link_email(url, locale="en")
        assert url in html_fr and url in html_en
        assert subj_fr != subj_en  # actually localized
        assert "Se connecter" in html_fr
        assert "Sign in" in html_en
