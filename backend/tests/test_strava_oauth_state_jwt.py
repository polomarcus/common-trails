"""Audit 2026-05-27 S3.3 — JWT-based OAuth state.

The CSRF guard for the Strava OAuth round-trip used to be backed by a
DB table (`oauth_states`) keyed by random tokens. PR #349 replaces it
with a self-encoding signed JWT carrying the user_id (or login
sentinel) directly. These tests pin the security-critical properties:

- Valid round-trip returns the original value
- Expired tokens are rejected
- Tokens signed with a foreign key are rejected
- Tokens with a foreign `aud` (e.g. a user-auth JWT) are rejected
- Empty / None / malformed input returns None (no crash)

If any of these fail in the future, the OAuth /callback CSRF guard
silently breaks — either by accepting forged states or by rejecting
legitimate ones.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.api.integrations_strava import (
    _OAUTH_STATE_AUD,
    _STRAVA_LOGIN_SENTINEL,
    _decode_oauth_state,
    _encode_oauth_state,
)


def test_round_trip_user_id():
    """Encoding a user_id and decoding produces the same value."""
    user_id = "11111111-2222-3333-4444-555555555555"
    state = _encode_oauth_state(user_id)
    assert _decode_oauth_state(state) == user_id


def test_round_trip_login_sentinel():
    """The login-flow sentinel must round-trip identically — the
    /callback branches on `state_value == _STRAVA_LOGIN_SENTINEL`."""
    state = _encode_oauth_state(_STRAVA_LOGIN_SENTINEL)
    assert _decode_oauth_state(state) == _STRAVA_LOGIN_SENTINEL


def test_none_returns_none():
    """An absent state param must return None, not crash."""
    assert _decode_oauth_state(None) is None


def test_empty_returns_none():
    """Empty string is treated as absent."""
    assert _decode_oauth_state("") is None


def test_garbage_returns_none():
    """Non-JWT garbage in the state param must return None."""
    assert _decode_oauth_state("not-a-jwt-at-all") is None
    assert _decode_oauth_state("a.b.c") is None  # JWT-shaped but no signature


def test_tampered_signature_returns_none():
    """Flipping a char inside the signature must invalidate.

    Note: we flip a char ~5 from the end rather than the very last
    char. Base64url-encoded HS256 signatures are 43 chars = 258 bits,
    leaving 2 trailing bits unused — flipping those bits doesn't
    change the decoded signature bytes. A middle-of-signature flip is
    guaranteed to mutate the actual signature value."""
    state = _encode_oauth_state("user-abc")
    # state is `header.payload.signature` — the signature is everything
    # after the last `.`. Flip a char comfortably in the middle of it.
    head, _, sig = state.rpartition(".")
    assert sig, "expected a signature segment"
    pivot = len(sig) // 2
    flipped_char = "A" if sig[pivot] != "A" else "B"
    tampered_sig = sig[:pivot] + flipped_char + sig[pivot + 1:]
    tampered = f"{head}.{tampered_sig}"
    assert _decode_oauth_state(tampered) is None


def test_expired_token_returns_none():
    """A token whose `exp` is in the past must be rejected.

    Build a JWT directly (bypassing `_encode_oauth_state`) so we can
    set `exp` to yesterday."""
    from jose import jwt

    from app.api.auth import JWT_ALGORITHM, JWT_SECRET
    payload = {
        "sub": "user-abc",
        "aud": _OAUTH_STATE_AUD,
        "iat": int((datetime.now(UTC) - timedelta(days=1)).timestamp()),
        "exp": int((datetime.now(UTC) - timedelta(minutes=5)).timestamp()),
    }
    expired = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    assert _decode_oauth_state(expired) is None


def test_wrong_audience_returns_none():
    """A JWT signed by our key but with a different `aud` claim must
    be rejected — the audience check is the security wall between
    OAuth-state JWTs and the user-auth JWTs (also HS256-signed by
    JWT_SECRET).
    """
    from jose import jwt

    from app.api.auth import JWT_ALGORITHM, JWT_SECRET
    payload = {
        "sub": "user-abc",
        "aud": "some-other-purpose",
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(minutes=30)).timestamp()),
    }
    foreign_aud = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    assert _decode_oauth_state(foreign_aud) is None


def test_user_auth_jwt_is_not_a_valid_oauth_state():
    """A real user-auth JWT (produced by `_create_token`) MUST NOT be
    accepted as OAuth state — it lacks the `aud` claim entirely.

    This is the critical security guarantee: stealing a user's session
    JWT and feeding it to /callback as `state` must not result in a
    successful Strava-account bind. The audience check (no `aud` !=
    `strava-oauth-state`) is what catches this.
    """
    from app.api.auth import _create_token
    user_auth = _create_token("user-abc", "a@b.com")
    assert _decode_oauth_state(user_auth) is None


def test_foreign_key_signed_token_returns_none():
    """A JWT with our claim shape but signed by a DIFFERENT key must
    be rejected. Proves the signature check is the wall, not just the
    shape check."""
    from jose import jwt
    payload = {
        "sub": "user-abc",
        "aud": _OAUTH_STATE_AUD,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(minutes=30)).timestamp()),
    }
    foreign_signed = jwt.encode(payload, "attacker-key-12345", algorithm="HS256")
    assert _decode_oauth_state(foreign_signed) is None


def test_subject_not_a_string_returns_none():
    """A token whose `sub` is e.g. a number must not propagate as the
    user_id — downstream code expects a string and an int comparison
    against the login sentinel string would silently always be False."""
    from jose import jwt

    from app.api.auth import JWT_ALGORITHM, JWT_SECRET
    payload = {
        "sub": 12345,  # int, not str
        "aud": _OAUTH_STATE_AUD,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(minutes=30)).timestamp()),
    }
    weird_sub = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    assert _decode_oauth_state(weird_sub) is None


def test_state_param_is_short_enough_for_strava():
    """Strava's documented `state` parameter limit is ~500 chars. A
    signed JWT for a UUID user_id should fit well under that — this
    test catches a future expansion (extra claims, longer alg name)
    that would silently break the OAuth handshake at Strava's side."""
    state = _encode_oauth_state("11111111-2222-3333-4444-555555555555")
    assert len(state) < 500, (
        f"OAuth state JWT length {len(state)} exceeds Strava's 500-char "
        f"limit. Trim the payload or switch to a shorter alg."
    )


@pytest.mark.parametrize("value", ["", "user-x", _STRAVA_LOGIN_SENTINEL])
def test_round_trip_various_subjects(value: str):
    """Round-trip multiple subject values to prove the helper isn't
    sensitive to subject content."""
    state = _encode_oauth_state(value)
    assert _decode_oauth_state(state) == value
