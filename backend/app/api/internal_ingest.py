"""Internal endpoints plumbing — OIDC verification for Cloud Tasks callers.

``verify_oidc_token`` is shared by every ``/internal/*`` handler
(artefact rebuild, my-heatmap rebuild…). This module defines no route.

Auth model:
- Cloud Tasks signs each request with an OIDC token whose audience equals
  the handler URL. We verify the token using ``google-auth`` against the
  service-account email configured in ``CLOUD_TASKS_INVOKER_SA``.
- In TEST_MODE we skip verification entirely so the dev loop / unit tests
  can hit the ``/internal/*`` routes directly.
- Anything else (no token, wrong audience, wrong signer, expired) → 401.
"""
from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request, status

log = logging.getLogger(__name__)


def _is_test_mode() -> bool:
    return os.environ.get("TEST_MODE", "false").lower() == "true"


def verify_oidc_token(request: Request, audience_env: str) -> None:
    """Verify the request was signed by Cloud Tasks.

    Cloud Tasks → ``Authorization: Bearer <oidc_jwt>`` with::

        iss = https://accounts.google.com
        email = CLOUD_TASKS_INVOKER_SA
        aud = $audience_env  (the configured handler URL)

    Each ``/internal/<thing>`` endpoint passes its own
    ``audience_env`` (e.g. ``INTERNAL_ARTEFACT_HANDLER_URL``) so token
    leakage between handlers is impossible — a token signed for the
    my-heatmap URL won't pass for the artefact-rebuild URL.

    The ``id_token.verify_oauth2_token`` helper checks the signature
    against Google's public keys, the expiry, the issuer, and the
    audience.
    """
    if _is_test_mode():
        return  # dev/test escape hatch — the endpoint can be hit directly

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = auth_header.split(" ", 1)[1].strip()

    expected_audience = os.environ.get(audience_env, "")
    expected_email = os.environ.get("CLOUD_TASKS_INVOKER_SA", "")
    if not expected_audience or not expected_email:
        # Mis-configured production: refuse rather than accept anything.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal endpoint {audience_env} not configured for OIDC verification",
        )

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError as exc:  # pragma: no cover — prod has google-auth
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="google-auth not installed; cannot verify OIDC token",
        ) from exc

    try:
        claims = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=expected_audience,
        )
    except Exception as exc:  # broad: any verification failure → 401
        log.warning("OIDC verification failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OIDC token") from exc

    email = claims.get("email", "")
    if email != expected_email:
        log.warning("OIDC token email mismatch: got %s expected %s", email, expected_email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token signer not authorised",
        )
