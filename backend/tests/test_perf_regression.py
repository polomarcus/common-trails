"""Performance regression tests — non-regression guards for fixes in perf/area-pb-initial-load.

Each test targets a specific issue that caused OOM, slow startup, or degraded UX:
1. DFCI endpoint limit  — GET /heatmap/dfci no longer OOMs (limit=100_000 cap)
2. MVT tile generation  — GET /heatmap/tiles/.../...mvt completes without OOM
3. Backend startup speed — /healthz responds quickly (was 195s, now <5s)
4. DFCI edge count fast  — get_dfci_edge_count(fast=True) uses bounded scan
5. Enrichment removed   — _enrich_edges no longer runs at startup (moved to CLI)
6. area.pb edge cap     — /routing/graph/{sport}/area.pb caps + UNION-ALL on
                          per-partition (sport, user_count) index (was 129s
                          unbounded, now <5s).
"""
import inspect
import time

from fastapi.testclient import TestClient

from app.services.ingest import get_dfci_edge_count

# ---------------------------------------------------------------------------
# 1. DFCI endpoint returns valid GeoJSON and does not crash
# ---------------------------------------------------------------------------


class TestDfciEndpointLimit:
    """GET /heatmap/dfci returns valid GeoJSON with OOM-safe limit."""

    def test_dfci_returns_geojson(self, client: TestClient):
        """Endpoint responds 200 with a valid FeatureCollection."""
        resp = client.get("/heatmap/dfci")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert isinstance(data["features"], list)

    def test_dfci_has_metadata(self, client: TestClient):
        """Response includes metadata block (license, source)."""
        resp = client.get("/heatmap/dfci")
        assert resp.status_code == 200
        data = resp.json()
        assert "metadata" in data
        assert data["metadata"]["license"] == "ODbL-1.0"

    def test_dfci_limit_applied_in_source(self):
        """The API handler passes limit=100_000 to get_dfci_geojson (OOM guard)."""
        from app.api import heatmap as heatmap_module

        source = inspect.getsource(heatmap_module.heatmap_dfci)
        assert "limit=100_000" in source or "limit=100000" in source, (
            "DFCI endpoint must pass a limit to get_dfci_geojson to prevent OOM"
        )


# ---------------------------------------------------------------------------
# 2. MVT tile generation completes without hanging
# ---------------------------------------------------------------------------


class TestMvtTileRegression:
    """GET /heatmap/tiles/{sport}/{z}/{x}/{y}.mvt completes quickly."""

    def test_mvt_tile_responds(self, client: TestClient):
        """MVT endpoint returns 200 with protobuf content type."""
        resp = client.get("/heatmap/tiles/road/13/4183/2990.mvt")
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "protobuf" in ct or "mvt" in ct, f"Unexpected content-type: {ct}"

    def test_mvt_tile_completes_in_time(self, client: TestClient):
        """MVT tile must complete within 5 seconds (was OOMing / hanging)."""
        t0 = time.monotonic()
        resp = client.get("/heatmap/tiles/road/13/4183/2990.mvt")
        elapsed = time.monotonic() - t0
        assert resp.status_code == 200
        assert elapsed < 5.0, f"MVT tile took {elapsed:.2f}s (budget: 5s)"

    def test_mvt_empty_tile_fast(self, client: TestClient):
        """Empty tile (no data) should be near-instant, not scan the whole table."""
        t0 = time.monotonic()
        resp = client.get("/heatmap/tiles/road/14/0/0.mvt")
        elapsed = time.monotonic() - t0
        assert resp.status_code in (200, 204)
        assert elapsed < 2.0, f"Empty MVT tile took {elapsed:.2f}s (budget: 2s)"


# ---------------------------------------------------------------------------
# 3. Backend startup speed — /healthz responds quickly
# ---------------------------------------------------------------------------


class TestStartupSpeed:
    """/healthz must respond promptly (was 195s before perf fixes)."""

    def test_healthz_under_5s(self, client: TestClient):
        """TestClient lifespan already ran; healthz must respond quickly."""
        t0 = time.monotonic()
        resp = client.get("/healthz")
        elapsed = time.monotonic() - t0
        assert resp.status_code == 200
        assert elapsed < 5.0, f"/healthz took {elapsed:.2f}s (budget: 5s)"
        assert resp.json()["status"] == "ok"

    def test_readyz_under_5s(self, client: TestClient):
        """/readyz must also respond quickly post-startup."""
        t0 = time.monotonic()
        resp = client.get("/readyz")
        elapsed = time.monotonic() - t0
        assert resp.status_code == 200
        assert elapsed < 5.0, f"/readyz took {elapsed:.2f}s (budget: 5s)"


# ---------------------------------------------------------------------------
# 4. DFCI edge count — fast=True uses bounded scan (not full COUNT(*))
# ---------------------------------------------------------------------------


class TestDfciEdgeCountFast:
    """get_dfci_edge_count(fast=True) uses LIMIT-bounded subquery."""

    def test_fast_count_returns_int(self):
        """fast=True returns an integer (may be 0 in test DB)."""
        result = get_dfci_edge_count(fast=True)
        assert isinstance(result, int)
        assert result >= 0

    def test_fast_count_completes_quickly(self):
        """fast=True must complete in under 2 seconds (was 6.3s with COUNT(*))."""
        t0 = time.monotonic()
        get_dfci_edge_count(fast=True)
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0, f"get_dfci_edge_count(fast=True) took {elapsed:.2f}s (budget: 2s)"

    def test_fast_count_uses_limit_in_source(self):
        """Implementation must use LIMIT in the SQL (bounded scan guard)."""
        source = inspect.getsource(get_dfci_edge_count)
        assert "LIMIT" in source, (
            "get_dfci_edge_count fast path must use LIMIT to avoid full table scan"
        )

    def test_fast_count_caps_at_1001(self):
        """fast=True returns at most 1001 (by design — bounded scan)."""
        result = get_dfci_edge_count(fast=True)
        assert result <= 1001, f"fast count returned {result}, expected <= 1001"


# ---------------------------------------------------------------------------
# 5. Enrichment NOT at startup — _enrich_edges removed from main.py
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 6. area.pb endpoint — REMOVED with the routing subsystem decommission
#    (chore/decommission-wasm-routing). The /routing/graph/*/area.pb endpoint
#    and app/api/graph_tiles.py no longer exist.
# ---------------------------------------------------------------------------


class TestEnrichmentNotAtStartup:
    """_enrich_edges must not exist in main.py (was matched-era OSM enrichment)."""

    def test_no_enrich_edges_function_in_main(self):
        """main.py must not define _enrich_edges (was causing 194s startup)."""
        import app.main as main_module

        assert not hasattr(main_module, "_enrich_edges"), (
            "_enrich_edges still exists in main.py — matched-era enrichment was "
            "removed with the raw-trace pivot and must not run at startup"
        )

    def test_no_enrich_edges_call_in_main(self):
        """main.py must not call _enrich_edges anywhere."""
        import app.main as main_module

        source = inspect.getsource(main_module)
        # Allow comments referencing it, but no actual function call
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "_enrich_edges(" not in stripped, (
                f"main.py still calls _enrich_edges: {stripped}"
            )
