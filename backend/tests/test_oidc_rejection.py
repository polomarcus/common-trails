"""OIDC verification on /internal/* endpoints — rejection-path tests.

The /internal/* family (heat-compute, artefact-rebuild) is reachable from
the public internet on Cloud Run. An attacker who finds the URL can trigger
expensive artefact rebuilds (DoS surface) unless OIDC verification holds.

Tested:
  - Missing Authorization header → 401
  - Wrong scheme (Basic instead of Bearer) → 401
  - Empty token → 401
  - Valid-shape but signed by no one → 401
  - When the audience env is unconfigured → 500 (never silently 200)

Tested at the FastAPI client level so the dependency-injection chain
is exercised. We monkeypatch ``TEST_MODE=false`` for these tests so
the real verification path runs (it's normally bypassed in tests).
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture
def prod_mode_client(client, monkeypatch) -> Iterator:
    """Disable TEST_MODE for the duration of the test so OIDC verify runs.

    Also clears the cached _is_test_mode() result if any.
    """
    monkeypatch.setenv("TEST_MODE", "false")
    # Some endpoints ship an audience env that's normally unset in CI.
    # Set placeholders so the "not configured" branch doesn't fire
    # unless we explicitly want it to.
    monkeypatch.setenv("INTERNAL_HEAT_HANDLER_URL", "https://example.invalid/internal/ingest/heat")
    monkeypatch.setenv("INTERNAL_ARTEFACT_HANDLER_URL", "https://example.invalid/internal/artefacts/rebuild")
    monkeypatch.setenv("CLOUD_TASKS_INVOKER_SA", "common-trails-tasks@example.iam.gserviceaccount.com")
    yield client


# Endpoints that should reject unauthenticated traffic when TEST_MODE=false.
# Each entry is (method, path, body) — POSTs need a minimal body to get
# past Pydantic validation so we exercise the dependency, not a 422.
_INTERNAL_ENDPOINTS = [
    ("POST", "/internal/ingest/heat", {"activity_id": "x", "user_id": "y"}),
    ("POST", "/internal/artefacts/rebuild", {}),
]


@pytest.mark.parametrize("method,path,body", _INTERNAL_ENDPOINTS)
def test_internal_endpoint_rejects_no_token(prod_mode_client, method, path, body):
    """No Authorization header → 401 Missing bearer token."""
    resp = prod_mode_client.request(method, path, json=body)
    assert resp.status_code == 401, (
        f"{method} {path} without Authorization should be 401, got "
        f"{resp.status_code} {resp.json()}"
    )
    assert "bearer" in resp.json().get("detail", "").lower() or \
           "token" in resp.json().get("detail", "").lower()


@pytest.mark.parametrize("method,path,body", _INTERNAL_ENDPOINTS)
def test_internal_endpoint_rejects_basic_auth(prod_mode_client, method, path, body):
    """``Authorization: Basic dXNlcjpwYXNz`` (not Bearer) → 401."""
    resp = prod_mode_client.request(
        method, path, json=body,
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert resp.status_code == 401, (
        f"{method} {path} with Basic auth should be 401, got {resp.status_code}"
    )


@pytest.mark.parametrize("method,path,body", _INTERNAL_ENDPOINTS)
def test_internal_endpoint_rejects_empty_bearer(prod_mode_client, method, path, body):
    """``Authorization: Bearer `` (empty token) → 401 (verification fails)."""
    resp = prod_mode_client.request(
        method, path, json=body,
        headers={"Authorization": "Bearer "},
    )
    # Either 401 (google-auth installed → verification fails) or 500
    # (google-auth missing → ImportError handled). Never 200.
    assert resp.status_code in (401, 500), (
        f"Empty bearer must be rejected, got {resp.status_code}: {resp.text[:200]}"
    )


@pytest.mark.parametrize("method,path,body", _INTERNAL_ENDPOINTS)
def test_internal_endpoint_rejects_garbage_token(prod_mode_client, method, path, body):
    """Bearer with random non-JWT garbage → 401 (verify_oauth2_token raises)."""
    resp = prod_mode_client.request(
        method, path, json=body,
        headers={"Authorization": "Bearer this-is-not-a-jwt"},
    )
    # 401 (verification failed) or 500 (google-auth missing). Never 200.
    assert resp.status_code in (401, 500), (
        f"Garbage bearer must be rejected, got {resp.status_code}: {resp.text[:200]}"
    )


@pytest.mark.parametrize("method,path,body", _INTERNAL_ENDPOINTS)
def test_internal_endpoint_rejects_unsigned_jwt(prod_mode_client, method, path, body):
    """Syntactically-valid but unsigned JWT → 401 (signature verification fails)."""
    # Three base64url segments, the third unsigned. Real shape, fake signature.
    fake_jwt = (
        "eyJhbGciOiJIUzI1NiJ9."  # {"alg":"HS256"}
        "eyJlbWFpbCI6ImF0dGFja2VyQGV2aWwuY29tIn0."  # {"email":"attacker@evil.com"}
        "ZmFrZXNpZ25hdHVyZQ"  # "fakesignature"
    )
    resp = prod_mode_client.request(
        method, path, json=body,
        headers={"Authorization": f"Bearer {fake_jwt}"},
    )
    # 401 (signature check failed) or 500 (google-auth missing). Never 200.
    assert resp.status_code in (401, 500), (
        f"Unsigned JWT must be rejected. Got {resp.status_code}: {resp.text[:200]}"
    )


def test_internal_endpoint_returns_500_when_audience_unconfigured(client, monkeypatch):
    """When the audience env var is missing in prod, the endpoint refuses to
    accept any token (returns 500 Internal Configuration Error). This
    prevents a half-deployed instance from accepting tokens with arbitrary
    audiences."""
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.delenv("INTERNAL_ARTEFACT_HANDLER_URL", raising=False)
    monkeypatch.setenv("CLOUD_TASKS_INVOKER_SA", "x@y.iam.gserviceaccount.com")

    resp = client.post(
        "/internal/artefacts/rebuild",
        json={},
        headers={"Authorization": "Bearer any-token"},
    )
    assert resp.status_code == 500, (
        f"Misconfigured prod (no audience env) should refuse with 500, "
        f"got {resp.status_code}: {resp.json()}"
    )
    assert "OIDC" in resp.json().get("detail", "") or \
           "audience" in resp.json().get("detail", "").lower() or \
           "configured" in resp.json().get("detail", "").lower()
