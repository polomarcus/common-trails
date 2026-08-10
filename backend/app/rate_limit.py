"""Rate limiting configuration — isolated module to avoid circular imports."""
import os

from slowapi import Limiter


def _get_real_ip(request) -> str:
    """Extract the TRUSTWORTHY client IP for rate-limit keying.

    SECURITY (2026-08-09): keying on ``X-Forwarded-For.split(",")[0]`` (the
    FIRST hop) is client-spoofable — the client fully controls the leading
    entries of XFF, so rotating that header gives a fresh limiter bucket every
    request, neutering every per-IP throttle (magic-link email-bomb, login
    brute-force). On Cloud Run the real client IP is the value Google appends
    at the END of the chain (the rightmost hop it did not receive from the
    client). So key on the LAST XFF entry when present, else the direct socket
    peer (``request.client.host``). An attacker prepending fake entries can no
    longer change the derived key.

    ⚠️ The rightmost hop is the real client IP ONLY because prod is a Cloud Run
    DOMAIN MAPPING (Google's front end appends the client IP last). If the
    service is ever fronted by an external HTTPS Load Balancer, the last hop
    becomes the LB's IP → every user collapses into ONE bucket. Revisit this
    (e.g. a trusted-proxy hop count) before adding an external LB.
    """
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "127.0.0.1"


def user_or_ip_key(request) -> str:
    """Rate-limit key for AUTHENTICATED cost-bearing endpoints (uploads).

    Prefer a stable per-USER key derived from the session JWT (Bearer header or
    the ``auth_token`` cookie) so an abuser cannot dodge a per-user upload cap
    by rotating source IPs; fall back to the trustworthy client IP for
    anonymous / unauthenticated callers. Decoding is best-effort — any failure
    (missing/invalid/expired token) degrades cleanly to the IP key.

    The ``app.api.auth`` import is deferred to call-time (this runs per request,
    never at import) so the ``auth -> rate_limit`` module dependency stays
    one-directional and no circular import is introduced.
    """
    token = None
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.cookies.get("auth_token")
    if token:
        try:
            from app.api.auth import _decode_token

            payload = _decode_token(token)
            sub = payload.get("sub") or payload.get("user_id")
            if sub:
                return f"user:{sub}"
        except Exception:
            pass
    return f"ip:{_get_real_ip(request)}"


_rate_limit_enabled = os.environ.get("RATELIMIT_ENABLED", "true").lower() != "false"

limiter = Limiter(key_func=_get_real_ip, enabled=_rate_limit_enabled)
# slowapi reads RATELIMIT_ENABLED from env as string "false" (truthy in Python)
# Force the boolean value back after construction
limiter.enabled = _rate_limit_enabled
