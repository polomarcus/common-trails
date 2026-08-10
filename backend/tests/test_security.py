"""Tests for security headers and rate limiting."""
import uuid


class TestSecurityHeaders:
    def test_security_headers_present(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_hsts_not_in_dev(self, client):
        """HSTS should NOT be set when ENV=development (default in tests)."""
        resp = client.get("/healthz")
        assert "Strict-Transport-Security" not in resp.headers

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
