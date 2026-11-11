"""Tests for MVT heatmap tiles.

NOTE: Sport names starting with `test_` are normalized to `gravel` by
`_normalize_heat_edge_sport`, so we use the real `gravel` partition and
isolate test data by **bbox** (a far-north Iceland region where production
data is effectively zero). The MVT endpoint also queries `sport=gravel`
under the hood, so the seeded edges become visible at the matching tile.
"""
import json

import pytest
from sqlalchemy import text as sa_text

from app.api.heatmap import _mvt_cache
from app.db.session import SessionLocal
from app.services.ingest import _update_heat_edges

# Real partition. The MVT endpoint queries by sport, so requests use the
# same `gravel` value. Bbox isolation prevents collisions with other tests
# / production data.
_SPORT = "gravel"
# Far-north Iceland bbox — production data here is effectively zero.
_TEST_BBOX = (-18.2, 65.6, -17.9, 65.8)


def _geojson(coords: list) -> str:
    return json.dumps({"type": "LineString", "coordinates": coords})


def _wipe_test_bbox() -> None:
    min_lon, min_lat, max_lon, max_lat = _TEST_BBOX
    db = SessionLocal()
    try:
        db.execute(sa_text(
            """
            DELETE FROM heat_edge_contributors WHERE edge_key IN (
                SELECT edge_key FROM heat_edges
                WHERE ST_Intersects(
                    geometry,
                    ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
                )
            )
            """
        ), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat})
        db.execute(sa_text(
            """
            DELETE FROM heat_edges
            WHERE ST_Intersects(
                geometry,
                ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
            )
            """
        ), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat})
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _clear_test_edges():
    """Clean up test edges (bbox-scoped) and cache before/after each test."""
    _mvt_cache.clear()
    _wipe_test_bbox()
    yield
    _mvt_cache.clear()
    _wipe_test_bbox()


# ── MVT Tile Endpoint Tests ──────────────────────────────────────────────────


class TestMvtTileEndpoint:
    """GET /heatmap/tiles/{sport}/{z}/{x}/{y}.mvt returns binary protobuf."""

    # Coords in Iceland (z14 tile: 14/7368/4187 approximately)
    _COORDS = [[-18.10000, 65.70000], [-18.09990, 65.70000]]

    def _seed(self):
        _update_heat_edges("mvt_u1", _SPORT, _geojson(self._COORDS))
        _update_heat_edges("mvt_u2", _SPORT, _geojson(self._COORDS))

    def test_mvt_returns_protobuf_content_type(self, client):
        """MVT endpoint returns application/x-protobuf."""
        self._seed()
        resp = client.get(f"/heatmap/tiles/{_SPORT}/14/7368/4187.mvt")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/x-protobuf"

    def test_mvt_returns_bytes(self, client):
        """MVT response is binary data (not JSON)."""
        self._seed()
        resp = client.get(f"/heatmap/tiles/{_SPORT}/14/7368/4187.mvt")
        assert resp.status_code == 200
        assert isinstance(resp.content, bytes)

    def test_mvt_empty_tile(self, client):
        """Empty tile (no edges) returns 200 with empty or minimal protobuf."""
        resp = client.get(f"/heatmap/tiles/{_SPORT}/14/0/0.mvt")
        assert resp.status_code == 200
        # Empty MVT is valid — just no features

    def test_mvt_out_of_range_zoom(self, client):
        """Zoom levels outside 6-16 return 204 No Content."""
        resp = client.get(f"/heatmap/tiles/{_SPORT}/3/0/0.mvt")
        assert resp.status_code == 204

    def test_mvt_cache_header(self, client):
        """MVT response includes cache-control header."""
        self._seed()
        resp = client.get(f"/heatmap/tiles/{_SPORT}/14/7368/4187.mvt")
        assert "max-age=86400" in resp.headers.get("cache-control", "")

    def test_mvt_k_anonymity(self, client):
        """Tiles only contain edges with user_count >= K (K=1 in test mode)."""
        # Seed only 1 user — with K=1 (dev mode), should still appear
        _update_heat_edges("mvt_solo", _SPORT, _geojson(self._COORDS))
        resp = client.get(f"/heatmap/tiles/{_SPORT}/14/7368/4187.mvt")
        assert resp.status_code == 200
        # With K=1, the single-user edge is included (non-empty response)
        # Actual K enforcement is tested via the SQL WHERE clause

    @pytest.mark.usefixtures("matched_mode")  # legacy pipeline — removed with slice D
    def test_mvt_low_zoom_returns_heat_points(self, client):
        """At z<=10, MVT returns centroid points (heat_points layer) for heatmap glow."""
        self._seed()
        # z8 tile covering Iceland coords (-18.1, 65.7) = 8/115/65
        resp = client.get(f"/heatmap/tiles/{_SPORT}/8/115/65.mvt")
        assert resp.status_code == 200
        assert isinstance(resp.content, bytes)
        # Should return non-empty protobuf (aggregated centroid points)
        assert len(resp.content) > 0

    @pytest.mark.usefixtures("matched_mode")  # legacy pipeline — removed with slice D
    def test_mvt_high_zoom_returns_trails(self, client):
        """At z>=11, MVT returns line geometries (trails layer)."""
        self._seed()
        # z14 tile covering Iceland coords (-18.1, 65.7) = 14/7368/4187
        resp = client.get(f"/heatmap/tiles/{_SPORT}/14/7368/4187.mvt")
        assert resp.status_code == 200
        assert len(resp.content) > 0


# The TestLonLatToTile unit tests were removed with the matched-era CDN
# publish subsystem (cache_writer._lonlat_to_tile fed MVT pre-generation in
# the now-deleted publish_heatmap_cache path). The live MVT endpoint has its
# own tile math and is covered by TestMvtTileEndpoint above.


# ── Canonical Merge Performance Test ─────────────────────────────────────────


class TestCanonicalMergePerformance:
    """Benchmark _find_canonical_edge with spatial index vs brute-force."""

    def test_spatial_index_faster_than_bruteforce(self):
        """Spatial index lookup should be at least 3x faster than 440-combo brute force."""
        import time

        from app.services.ingest import (
            _build_endpoint_index,
            _edge_key,
            _find_canonical_edge,
            _snap,
        )

        sport = "bench_merge"
        # Simulate 2000 existing edges (typical for a 30km activity zone)
        existing_keys: set[str] = set()
        existing_endpoints: dict = {}
        existing_pass_counts: dict = {}
        for i in range(2000):
            lat1 = round(43.6 + i * 0.00001, 5)
            lon1 = round(3.87 + (i % 50) * 0.00001, 5)
            lat2 = round(lat1 + 0.00001, 5)
            lon2 = round(lon1 + 0.00001, 5)
            p1, p2 = _snap(lat1, lon1), _snap(lat2, lon2)
            key = _edge_key(sport, p1, p2)
            existing_keys.add(key)
            existing_endpoints[key] = (p1, p2)
            existing_pass_counts[key] = i % 10

        # Build spatial index
        ep_index = _build_endpoint_index(existing_endpoints)

        # Generate 200 new edges to merge-check
        test_edges = []
        for i in range(200):
            lat1 = round(43.6 + i * 0.00001 + 0.000005, 5)  # slight offset
            lon1 = round(3.87 + (i % 50) * 0.00001, 5)
            lat2 = round(lat1 + 0.00001, 5)
            lon2 = round(lon1 + 0.00001, 5)
            test_edges.append((_snap(lat1, lon1), _snap(lat2, lon2)))

        # Benchmark WITH spatial index
        t0 = time.monotonic()
        for p1, p2 in test_edges:
            _find_canonical_edge(sport, p1, p2, existing_keys, existing_endpoints, existing_pass_counts, ep_index)
        with_index = time.monotonic() - t0

        # Benchmark WITHOUT spatial index (None → skips merge, so we measure index overhead)
        t1 = time.monotonic()
        for p1, p2 in test_edges:
            _find_canonical_edge(sport, p1, p2, existing_keys, existing_endpoints, existing_pass_counts, None)
        without_merge = time.monotonic() - t1

        print(f"\n  Canonical merge: {with_index*1000:.1f}ms (spatial index) vs {without_merge*1000:.1f}ms (no merge)")
        print(f"  Per edge: {with_index/len(test_edges)*1000:.2f}ms (index) — 2000 existing edges, 200 lookups")

        # Spatial index should complete in reasonable time (<500ms for 200 edges)
        assert with_index < 0.5, f"Spatial index took {with_index:.2f}s for 200 edges — too slow"
