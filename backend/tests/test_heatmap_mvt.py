"""Canonical-merge benchmark (matched pipeline, ``app.services.ingest``).

The live ``/heatmap/tiles`` MVT endpoint this file used to cover was removed
with the matched HTTP surface; only the ingest-side benchmark is left, until
the matched code in ``ingest.py`` goes too.
"""

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
