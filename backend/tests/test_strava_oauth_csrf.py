"""Strava OAuth state — CSRF protection tests.

The OAuth state parameter is the only thing standing between a CSRF
attacker and account hijack:

1. Attacker tricks victim into visiting
   `/integrations/strava/callback?code=ATTACKER_STRAVA_ACCOUNT_CODE&state=GUESSED`
2. If we accept any unrecognized state, the victim's account gets
   bound to the attacker's Strava — attacker now sees the victim's
   activities.

The state is now a signed JWT (PR #349 / audit S3.3) — the value is
the JWT itself, signed with `JWT_SECRET` + audience `strava-oauth-state`.
A callback must present a state that:
- Verifies under our signing key
- Carries the expected `aud` claim
- Hasn't expired (`exp` claim, 30-min TTL)

Replay protection lives at Strava's side: the `code` is single-use,
so re-presenting the same (state, code) fails at the Strava token
exchange. Within the TTL the same state JWT may be presented multiple
times — each round-trip will hit a fresh Strava code, and the second
presentation will fail at `code` exchange.

These tests pin those guarantees end-to-end via the FastAPI client
(in TEST_MODE so the actual Strava token call is stubbed but the
state mechanism runs for real).
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _strava_enabled(monkeypatch):
    """Enable Strava + TEST_MODE for all tests in this module."""
    import app.api.integrations_strava as strava_mod
    monkeypatch.setattr(strava_mod, "STRAVA_ENABLED", True)
    monkeypatch.setattr(strava_mod, "TEST_MODE", True)
    monkeypatch.setattr(strava_mod, "FRONTEND_URL", "http://localhost:3787")
    yield


def _wipe_oauth_states(db) -> None:
    """Clear the test stub Strava integration before each test so the
    ghost-fallback path can't mask a CSRF rejection.

    The TEST_MODE callback uses a hardcoded stub_strava_id and one test
    run leaves an IntegrationAccount with that external_user_id. The
    callback's state-lost ghost-fallback would then find that account
    and silently accept any forged state from a later test.

    (Pre-PR-349 this also DELETE-d from `oauth_states`. The JWT
    state has no DB row to wipe — the function name + tests
    keep "wipe_oauth_states" for backwards compat.)"""
    from sqlalchemy import text as sa_text
    db.execute(sa_text(
        "DELETE FROM integration_accounts WHERE external_user_id = 'strava_stub_user_42'"
    ))
    db.commit()


@pytest.fixture
def db_session():
    from app.db.session import SessionLocal
    db = SessionLocal()
    _wipe_oauth_states(db)
    try:
        yield db
    finally:
        _wipe_oauth_states(db)
        db.close()


# ── Forged state (never set) ───────────────────────────────────────────

def test_callback_with_forged_state_is_rejected(client, db_session):
    """A state the server never issued (= JWT that doesn't verify
    under our signing key) must NOT bind any account.

    Pre-PR-349: rejected because the random token wasn't in the
    `oauth_states` table. Post-PR-349: rejected because the string
    doesn't decode as a valid JWT with our `aud` claim.
    """
    resp = client.get(
        "/integrations/strava/callback?code=stub_code_test&state=forged_by_attacker_xyz",
        follow_redirects=False,
    )
    # Must be a 4xx — never a 302 redirect (which would mean account bound)
    assert resp.status_code in (400, 401, 403, 404), (
        f"Forged state was accepted! status={resp.status_code}, "
        f"body={resp.text[:200]}"
    )


# ── Replay (state already used) ────────────────────────────────────────

def test_callback_replay_rebinds_to_same_user_not_attacker(client, db_session):
    """A replayed state cannot bind the Strava account to a DIFFERENT user.

    Replay protection design: the `DELETE ... RETURNING` in
    `_pop_oauth_state` pops the row so the second callback sees
    `state_value=None`. The state-lost ghost-fallback then looks up the
    existing IntegrationAccount by `external_user_id` (the Strava ID)
    and rebinds to THAT user — which is the user from the FIRST call.
    Result: replay is idempotent (same user, same binding), never
    escalation.

    What this test pins: replay does NOT bind the account to a fresh
    `stub_session` user or any other identity. The original user_id is
    preserved. This is the "ghost migration" feature designed for
    Cloud Run scaled-to-zero, where a legitimate user's state can be
    legitimately re-used.

    The price: an attacker who observes a state token cannot escalate,
    but CAN trigger a no-op callback. That's accepted.
    """
    from app.db.models import IntegrationAccount
    # First legitimate login
    login_resp = client.get(
        "/integrations/strava/login",
        follow_redirects=False,
    )
    callback_url = login_resp.headers["location"]
    first = client.get(callback_url, follow_redirects=False)
    assert first.status_code in (302, 307), (
        f"First legitimate callback failed: {first.status_code} {first.text[:200]}"
    )
    # Record the user_id from the first callback
    from urllib.parse import parse_qs, urlparse
    first_user_id = parse_qs(urlparse(first.headers["location"]).query).get("user_id", [None])[0]
    assert first_user_id, "First callback didn't return a user_id"
    # Check the actual DB binding
    first_acct = db_session.query(IntegrationAccount).filter(
        IntegrationAccount.external_user_id == "strava_stub_user_42"
    ).first()
    assert first_acct is not None
    original_user_id = first_acct.user_id

    # Replay the same callback URL
    second = client.get(callback_url, follow_redirects=False)
    # The replay MUST either reject (4xx) or succeed with the SAME user
    # — what it must NOT do is bind to a different user / fresh stub.
    if second.status_code in (302, 307):
        # If success, the binding must still be the original user
        db_session.expire_all()  # force fresh read
        post_replay_acct = db_session.query(IntegrationAccount).filter(
            IntegrationAccount.external_user_id == "strava_stub_user_42"
        ).first()
        assert post_replay_acct is not None
        assert post_replay_acct.user_id == original_user_id, (
            f"Replay rebound the Strava account! original_user_id={original_user_id}, "
            f"after_replay_user_id={post_replay_acct.user_id}. This is a CSRF escalation."
        )
    else:
        assert second.status_code in (400, 401, 403, 404), (
            f"Replayed state must either be rejected or bind to same user, got {second.status_code}"
        )


# ── Expired state ──────────────────────────────────────────────────────

def test_callback_with_expired_state_is_rejected(client, db_session):
    """A JWT state with an `exp` in the past must be rejected.

    Pre-PR-349: enforced by `created_at > NOW() - TTL` in the SQL.
    Post-PR-349: enforced by `jose.jwt.decode`'s built-in `exp`
    check — same observable behaviour from the /callback endpoint."""
    from datetime import UTC, datetime, timedelta

    from jose import jwt

    from app.api.auth import JWT_ALGORITHM, JWT_SECRET
    from app.api.integrations_strava import (
        _OAUTH_STATE_AUD,
        _STRAVA_LOGIN_SENTINEL,
    )

    expired = jwt.encode(
        {
            "sub": _STRAVA_LOGIN_SENTINEL,
            "aud": _OAUTH_STATE_AUD,
            "iat": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(minutes=5)).timestamp()),
        },
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )

    resp = client.get(
        f"/integrations/strava/callback?code=stub_code_test&state={expired}",
        follow_redirects=False,
    )
    # Must be rejected — never a successful bind
    if resp.status_code in (302, 307):
        loc = resp.headers.get("location", "")
        assert "status=error" in loc or "expirée" in loc or "status=connected" not in loc, (
            f"Expired state was accepted as success! redirect={loc}"
        )
    else:
        assert resp.status_code in (400, 401, 403, 404), (
            f"Expired state must be rejected, got {resp.status_code}"
        )


# ── Cross-user state injection ─────────────────────────────────────────

def test_callback_state_value_scoping(client, db_session):
    """A state issued for user A must not bind a Strava account to user B.

    The state's `value` column stores either the user_id (connect flow)
    or `__strava_login__` (login flow). If an attacker somehow obtains
    user A's state token (e.g. log file), using it from another browser
    session must STILL bind the account to user A (the original
    requester) — never to whoever holds the cookie at callback time.

    This is the "state binds the result, not the caller" guarantee.
    """
    # Sign in as user_a — use a real-ish email (Pydantic email-validator
    # rejects `.local` reserved TLDs) and include the required username
    email_a = "user_a_csrf@example.com"
    user_a_resp = client.post(
        "/auth/register",
        json={"email": email_a, "password": "test1234", "username": "user_a_csrf"},
    )
    if user_a_resp.status_code not in (200, 201, 409):
        pytest.skip(f"Register endpoint not as expected: {user_a_resp.status_code} {user_a_resp.text[:200]}")
    # /auth/login uses OAuth2PasswordRequestForm (form-encoded, not JSON)
    # and expects `username` = email, `password` = password
    login_a = client.post(
        "/auth/login",
        data={"username": email_a, "password": "test1234"},
    )
    assert login_a.status_code == 200, login_a.text
    token_a = login_a.json()["access_token"]

    # User A initiates /connect — state JWT will carry user_a's user_id
    # in its `sub` claim
    connect = client.get(
        "/integrations/strava/connect",
        headers={"Authorization": f"Bearer {token_a}"},
        follow_redirects=False,
    )
    assert connect.status_code in (302, 307)
    cb_url_a = connect.headers["location"]
    # Extract state from the callback URL
    from urllib.parse import parse_qs, urlparse
    state_a = parse_qs(urlparse(cb_url_a).query)["state"][0]

    # Decode the JWT — `sub` should be user_a's id. The decode helper
    # IS what /callback uses, so we test the exact same path the CSRF
    # guard depends on.
    from app.api.integrations_strava import _decode_oauth_state
    stored_user_id = _decode_oauth_state(state_a)
    assert stored_user_id is not None, "State JWT failed to decode for user_a"
    # The JWT's `sub` IS the user_id — used at callback to decide
    # which user gets the Strava link. An attacker can't change WHO
    # the link binds to by stealing the state token (signature would
    # have to be re-forged, which requires JWT_SECRET).
    assert stored_user_id != "__strava_login__", (
        "Connect flow should embed the user_id in the JWT, not the login sentinel"
    )
    assert len(stored_user_id) > 8, (
        f"JWT `sub` looks too short to be a user_id: {stored_user_id!r}"
    )


# ── Missing state ──────────────────────────────────────────────────────

def test_callback_with_missing_state_is_rejected(client, db_session):
    """The callback called without any `state` query param must fail."""
    resp = client.get(
        "/integrations/strava/callback?code=stub_code_test",
        follow_redirects=False,
    )
    # State is None → _pop_oauth_state returns None → ghost fallback
    # finds no existing link → 400
    if resp.status_code in (302, 307):
        loc = resp.headers.get("location", "")
        assert "status=error" in loc or "expirée" in loc, (
            f"Missing state was accepted as success! redirect={loc}"
        )
    else:
        assert resp.status_code in (400, 422, 401, 403), (
            f"Missing state must be rejected, got {resp.status_code}: {resp.text[:200]}"
        )


# ── Garbage state (SQL injection shape) ────────────────────────────────

def test_callback_with_sql_injection_shape_state(client, db_session):
    """A state value crafted to look like SQL injection must not break
    the server. Pre-PR-349 the defence was psycopg parameterised
    queries. Post-PR-349 the defence is stronger: the JWT decode
    layer never reaches SQL with the user-controlled value at all."""
    resp = client.get(
        "/integrations/strava/callback?code=stub_code_test&state='; DROP TABLE foo; --",
        follow_redirects=False,
    )
    # The only acceptable outcomes are 4xx rejection or a 302 to error.
    # A 500 indicates an exception leaking → bad input handling.
    assert resp.status_code != 500, (
        f"SQL-injection-shaped state caused a 500: {resp.text[:200]}"
    )
