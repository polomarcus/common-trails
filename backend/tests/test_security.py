"""Tests for security headers and rate limiting."""
import uuid


class TestSecurityHeaders:
    def test_security_headers_present(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_hsts_not_in_dev(self, client, monkeypatch):
        """Plain-http dev (http FRONTEND_URL, no forwarded https): no HSTS."""
        monkeypatch.setattr("app.main._frontend_is_https", False)
        resp = client.get("/healthz")
        assert "Strict-Transport-Security" not in resp.headers

    def test_hsts_when_request_arrived_over_https(self, client, monkeypatch):
        """Cloud Run terminates TLS and sets X-Forwarded-Proto: https — ENV is
        not set in prod, so this signal alone must turn HSTS on."""
        monkeypatch.setattr("app.main._frontend_is_https", False)
        monkeypatch.setattr("app.main._is_prod", False)
        resp = client.get("/healthz", headers={"X-Forwarded-Proto": "https"})
        assert resp.headers["Strict-Transport-Security"].startswith("max-age=31536000")

    def test_hsts_when_public_origin_is_https(self, client, monkeypatch):
        monkeypatch.setattr("app.main._frontend_is_https", True)
        monkeypatch.setattr("app.main._is_prod", False)
        resp = client.get("/healthz")
        assert "Strict-Transport-Security" in resp.headers

    def test_frontend_https_flag_follows_frontend_url(self):
        import app.main as main
        from app import config

        assert main._frontend_is_https == config.FRONTEND_URL.lower().startswith("https://")

    def test_api_responses_keep_the_strict_csp(self, client):
        resp = client.get("/healthz")
        assert resp.headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"

    def test_html_responses_get_a_minimal_csp(self, client, auth_headers):
        """HTML (share pages, the static export) gets a CSP that cannot break
        the Next.js export: framing / plugins / <base> only."""
        route = client.post(
            "/routes", json={"name": "CSP", "sport": "road", "visibility": "public"}, headers=auth_headers,
        ).json()
        resp = client.get(f"/share/{route['id']}")
        assert "text/html" in resp.headers["content-type"]
        csp = resp.headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp
        assert "object-src 'none'" in csp
        assert "base-uri 'self'" in csp
        assert "script-src" not in csp and "default-src" not in csp
        assert resp.headers["X-Frame-Options"] == "DENY"

    def test_security_headers_middleware_is_outermost(self):
        """Registered after register_frontend → wraps every other middleware,
        including the static-export page short-circuit (frontend_html_priority),
        which answers browser navigations without calling the inner stack."""
        import app.main as main

        first = main.app.user_middleware[0]
        assert getattr(first.kwargs.get("dispatch"), "__name__", "") == "add_security_headers"

    def test_rate_limit_disabled_in_tests(self, client):
        """With limiter disabled, no 429 even after many requests."""
        from app.rate_limit import limiter
        limiter.enabled = False
        limiter.reset()
        for _ in range(20):
            resp = client.post(
                "/auth/login",
                data={"username": "nobody@example.com", "password": "wrong"},
            )
            assert resp.status_code != 429


class TestRateLimiting:
    def test_rate_limit_login(self, client):
        """With rate limiting enabled, 6th login attempt within a minute returns 429."""
        from app.rate_limit import limiter
        limiter.enabled = True
        limiter.reset()
        try:
            for i in range(6):
                resp = client.post(
                    "/auth/login",
                    data={"username": f"rl_{uuid.uuid4().hex[:6]}@example.com", "password": "wrong"},
                )
                if i < 5:
                    assert resp.status_code in (401, 422), f"Request {i+1} got {resp.status_code}"
                else:
                    assert resp.status_code == 429, f"Request {i+1} should be rate-limited"
                    assert "Trop de requêtes" in resp.json()["detail"]
                    assert "Retry-After" in resp.headers
        finally:
            limiter.enabled = False
            limiter.reset()

    def test_rate_limit_api_endpoint(self, client):
        """Rate limiting works on non-auth endpoints too (60/minute on /routes).

        Verifies the Retry-After header is present when rate-limited.
        Uses /auth/login (5/minute) as a fast proxy since testing 60/minute
        would require >60s and cross the sliding window boundary.
        """
        from app.rate_limit import limiter
        limiter.enabled = True
        limiter.reset()
        try:
            # Exhaust the 5/min login limit
            for _i in range(6):
                resp = client.post(
                    "/auth/login",
                    data={"username": f"api_rl_{uuid.uuid4().hex[:6]}@example.com", "password": "wrong"},
                )
            assert resp.status_code == 429
            assert "Retry-After" in resp.headers

            # Verify that GET endpoints still work (separate rate limit counter)
            resp = client.get("/routes?visibility=public")
            assert resp.status_code == 200, "GET /routes should not be affected by login rate limit"
        finally:
            limiter.enabled = False
            limiter.reset()
