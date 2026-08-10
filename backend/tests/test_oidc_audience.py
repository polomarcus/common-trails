"""OIDC audience / signer enforcement on /internal/* endpoints.

``test_oidc_rejection.py`` covers the malformed / missing / unsigned-token
cases. This file closes the two gaps that matter once a token actually
*verifies* against Google's keys:

  (a) a token that PASSES ``id_token.verify_oauth2_token`` but carries the
      WRONG audience (or a verify call that itself rejects on audience) → 401.
  (b) correct audience + correct signer email → NOT 401 (the dependency lets
      the request through to the handler body).

We patch ``google.oauth2.id_token.verify_oauth2_token`` AT THE IMPORT SITE —
the function is imported lazily inside ``verify_oidc_token`` as
``from google.oauth2 import id_token`` then called ``id_token.verify_oauth2_token``,
so patching ``google.oauth2.id_token.verify_oauth2_token`` is the correct target.

Driven at the FastAPI client level so the whole dependency chain runs. The
artefact-rebuild endpoint is used because its body has no request payload and
the accepted-case assertion is "status != 401", which holds whether the
rebuild succeeds (200) or raises (500); either way OIDC let the request past
the gate.
"""
from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

_AUDIENCE = "https://example.invalid/internal/artefacts/rebuild"
_INVOKER_SA = "common-trails-tasks@example.iam.gserviceaccount.com"
# A syntactically valid 3-segment JWT shape; the signature is irrelevant
# because verify_oauth2_token is mocked. The split(" ",1) + bearer check
# in the real handler still must accept it.
_TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2ln"


@pytest.fixture
def prod_oidc_client(client, monkeypatch) -> Iterator:
    """TEST_MODE=false so the real verify path runs, with the artefact-rebuild
    audience and invoker SA configured."""
    monkeypatch.setenv("TEST_MODE", "false")
    monkeypatch.setenv("INTERNAL_ARTEFACT_HANDLER_URL", _AUDIENCE)
    monkeypatch.setenv("CLOUD_TASKS_INVOKER_SA", _INVOKER_SA)
    yield client


def test_wrong_audience_rejected(prod_oidc_client):
    """verify_oauth2_token raises (as the real lib does when audience mismatches)
    → the broad except → 401. This is the audience-enforcement guarantee."""

    def _verify(token, request, audience):  # noqa: ARG001
        # The real google-auth raises ValueError("Token has wrong audience ...")
        # when the token's aud != the audience passed in. Mirror that.
        assert audience == _AUDIENCE, "handler must pass its configured audience"
        raise ValueError(f"Token has wrong audience attacker.invalid, expected {audience}")

    with patch("google.oauth2.id_token.verify_oauth2_token", side_effect=_verify):
        resp = prod_oidc_client.post(
            "/internal/artefacts/rebuild",
            json={},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    assert resp.status_code == 401, (
        f"wrong-audience token must be 401, got {resp.status_code}: {resp.text[:200]}"
    )
    assert "OIDC" in resp.json().get("detail", "") or \
           "token" in resp.json().get("detail", "").lower()


def test_wrong_signer_email_rejected(prod_oidc_client):
    """Audience verifies fine but the token's ``email`` claim is NOT the
    configured invoker SA → 401 (signer not authorised)."""

    def _verify(token, request, audience):  # noqa: ARG001
        return {"iss": "https://accounts.google.com", "aud": audience,
                "email": "attacker@evil.iam.gserviceaccount.com"}

    with patch("google.oauth2.id_token.verify_oauth2_token", side_effect=_verify):
        resp = prod_oidc_client.post(
            "/internal/artefacts/rebuild",
            json={},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    assert resp.status_code == 401, (
        f"wrong-signer token must be 401, got {resp.status_code}: {resp.text[:200]}"
    )
    assert "authoris" in resp.json().get("detail", "").lower() or \
           "token" in resp.json().get("detail", "").lower()


def test_correct_audience_and_signer_accepted(prod_oidc_client):
    """Correct audience + correct issuer + correct signer email → OIDC passes,
    request reaches the handler body. We assert NOT 401 (and not 403) — the
    handler may 200 or 500 depending on the rebuild, but the gate let it
    through, which is the security guarantee under test."""

    def _verify(token, request, audience):  # noqa: ARG001
        assert audience == _AUDIENCE
        return {
            "iss": "https://accounts.google.com",
            "aud": audience,
            "email": _INVOKER_SA,
        }

    with patch("google.oauth2.id_token.verify_oauth2_token", side_effect=_verify):
        resp = prod_oidc_client.post(
            "/internal/artefacts/rebuild",
            json={},
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    assert resp.status_code != 401, (
        f"valid OIDC token must pass the gate (not 401), got {resp.status_code}: "
        f"{resp.text[:200]}"
    )
    assert resp.status_code != 403
