"""Performance regression tests — assert key endpoints respond within time budgets.

These tests run against the test client (TEST_MODE=true, in-memory data).
They verify that caching works and responses stay within acceptable time budgets.
"""
import pytest

pytestmark = pytest.mark.slow
import time

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _timed_get(client: TestClient, url: str, **kwargs) -> tuple[float, int, int]:
    """Return (elapsed_seconds, status_code, response_size_bytes)."""
    t0 = time.monotonic()
    resp = client.get(url, **kwargs)
    elapsed = time.monotonic() - t0
    return elapsed, resp.status_code, len(resp.content)


def _timed_post(client: TestClient, url: str, **kwargs) -> tuple[float, int, int]:
    t0 = time.monotonic()
    resp = client.post(url, **kwargs)
    elapsed = time.monotonic() - t0
    return elapsed, resp.status_code, len(resp.content)


# ---------------------------------------------------------------------------
# Heatmap endpoints
# ---------------------------------------------------------------------------

class TestHeatmapPerformance:
    """Heatmap endpoints should cache after first call."""

    def test_summary_cache(self, client: TestClient):
        """Second call to /heatmap/summary should be near-instant (cached)."""
        # Cold call
        elapsed1, status1, _ = _timed_get(client, "/heatmap/summary")
        assert status1 == 200

        # Warm call (should be cached)
        elapsed2, status2, _ = _timed_get(client, "/heatmap/summary")
        assert status2 == 200
        assert elapsed2 < 0.1, f"Cached /heatmap/summary took {elapsed2:.3f}s (expected <0.1s)"

    def test_trails_bbox_cache(self, client: TestClient):
        """Bbox-filtered /heatmap/trails should cache on second call."""
        url = "/heatmap/trails?min_lon=3.8&min_lat=43.5&max_lon=4.0&max_lat=43.7"

        # Cold call
        elapsed1, status1, _ = _timed_get(url=url, client=client, headers={"Accept-Encoding": "gzip"})
        assert status1 == 200

        # Warm call (should be cached)
        elapsed2, status2, _ = _timed_get(url=url, client=client, headers={"Accept-Encoding": "gzip"})
        assert status2 == 200
        assert elapsed2 < 0.1, f"Cached /heatmap/trails bbox took {elapsed2:.3f}s (expected <0.1s)"

    def test_dfci_cache(self, client: TestClient):
        """Second call to /heatmap/dfci should be near-instant (cached)."""
        elapsed1, status1, _ = _timed_get(client, "/heatmap/dfci", headers={"Accept-Encoding": "gzip"})
        assert status1 == 200

        elapsed2, status2, _ = _timed_get(client, "/heatmap/dfci", headers={"Accept-Encoding": "gzip"})
        assert status2 == 200
        assert elapsed2 < 2.0, f"Cached /heatmap/dfci took {elapsed2:.3f}s (expected <2.0s)"


# ---------------------------------------------------------------------------
# Auth + user endpoints
# ---------------------------------------------------------------------------

class TestUserEndpointPerformance:

    def test_healthz_fast(self, client: TestClient):
        """/healthz must respond in under 200ms."""
        elapsed, status, _ = _timed_get(client, "/healthz")
        assert status == 200
        assert elapsed < 0.2, f"/healthz took {elapsed:.3f}s (expected <0.2s)"

    def test_login_fast(self, client: TestClient):
        """Login should respond within 1s (bcrypt is slow by design)."""
        t0 = time.monotonic()
        client.post(
            "/auth/login",
            data={"username": "admin@admin", "password": "admin"},
        )
        elapsed = time.monotonic() - t0
        # Admin user may not exist in test mode — just check timing
        assert elapsed < 1.0, f"/auth/login took {elapsed:.3f}s (expected <1.0s)"

    def test_routes_public_fast(self, client: TestClient):
        """/routes?visibility=public must not do heavy PER-ROUTE work.

        The endpoint returns EVERY public route (frozen social feature — no
        pagination), so wall-clock scales with the corpus. The old fixed
        ``< 1.0s`` budget assumed a tiny local ``routes`` table and rotted as
        the seeded corpus grew (thousands of public routes → ~1.9s just to
        serialize them all). Re-pin the thing the test actually guards — the
        PER-ROUTE serialization cost (an N+1 blow-up in ``_route_to_out`` /
        the version fetch would spike it) — so the budget is robust to corpus
        size AND still fails on a real per-route regression. A small floor
        keeps a near-empty table (CI/fresh DB) from tripping on fixed request
        overhead.
        """
        t0 = time.monotonic()
        resp = client.get("/routes?visibility=public")
        elapsed = time.monotonic() - t0
        assert resp.status_code == 200
        count = len(resp.json())
        if count >= 50:
            per_route_ms = elapsed / count * 1000.0
            assert per_route_ms < 2.0, (
                f"/routes?visibility=public per-route cost {per_route_ms:.2f}ms "
                f"over {count} routes ({elapsed:.3f}s total) — expected "
                f"<2ms/route (N+1 / heavy geometry regression guard)"
            )
        else:
            assert elapsed < 1.0, (
                f"/routes?visibility=public took {elapsed:.3f}s for {count} "
                f"routes (expected <1.0s on a small corpus)"
            )


# ---------------------------------------------------------------------------
# Routing endpoints — REMOVED (routing subsystem decommissioned,
# chore/decommission-wasm-routing). The /routing/* endpoints no longer exist.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Response size sanity checks
# ---------------------------------------------------------------------------

class TestResponseSizes:
    """Ensure responses are not unexpectedly large (regression guard)."""

    def test_summary_is_small(self, client: TestClient):
        resp = client.get("/heatmap/summary")
        assert resp.status_code == 200
        assert len(resp.content) < 10_000, f"/heatmap/summary is {len(resp.content)} bytes (expected <10KB)"

    def test_healthz_is_small(self, client: TestClient):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert len(resp.content) < 1_000, f"/healthz is {len(resp.content)} bytes (expected <1KB)"
