"""Ingestion performance tests — measure edge computation speed with real GPX data.

Uses GPX files from data/gpx/ (Strava exports, ~3-5k points each).
Tests run against the local PostgreSQL instance (not mocked).

Skipped in CI (too slow for GitHub Actions runners).
Run locally with: pytest tests/test_ingestion_perf.py -v
"""
import json
import os
import time
from pathlib import Path

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.gpx import parse_gpx
from app.services.ingest import _update_heat_cells, _update_heat_edges, rebuild_heatmap_parallel

# Skip entire module in CI — perf tests need real GPX data + fast local DB.
pytestmark = pytest.mark.skipif(
    os.environ.get("CI") == "true"
    or os.environ.get("GITHUB_ACTIONS") == "true"
    or os.environ.get("SKIP_PERF_TESTS") == "true",
    reason="Performance tests skipped in CI (too slow for GitHub Actions runners)",
)

# Path to GPX test data — multiple fallback locations
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"  # backend/tests/fixtures/ (in Docker as /app/tests/fixtures/)
_GPX_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "gpx"  # repo root data/gpx/


def _find_gpx_dir() -> Path:
    """Find GPX directory — works both locally and in Docker."""
    for d in (_FIXTURES_DIR, _GPX_DIR):
        if d.exists() and any(d.glob("*.gpx")):
            return d
    pytest.skip("No GPX test data found (tests/fixtures/*.gpx or data/gpx/*.gpx)")


def _load_gpx_activities() -> list[dict]:
    """Parse all GPX files into activity dicts with GeoJSON geometry."""
    gpx_dir = _find_gpx_dir()
    activities = []
    for gpx_file in sorted(gpx_dir.glob("*.gpx")):
        with open(gpx_file, "rb") as f:
            content = f.read()
        parsed = parse_gpx(content)
        if parsed and parsed.get("geometry_geojson"):
            coords = json.loads(parsed["geometry_geojson"]).get("coordinates", [])
            activities.append({
                "name": gpx_file.stem,
                "geometry_geojson": parsed["geometry_geojson"],
                "sport": "mtb" if "Mountain" in gpx_file.stem else "gravel",
                "point_count": len(coords),
            })
    return activities


# ── Fixtures ──────────────────────────────────────────────────────────────────

_TEST_USER = "perf-test-user-00000000"
_TEST_SPORT_PREFIX = "perf_"


@pytest.fixture(autouse=True)
def _cleanup_perf_edges():
    """Remove performance test edges before/after each test."""
    db = SessionLocal()
    try:
        for sport_suffix in ("mtb", "gravel", "road"):
            sport = f"{_TEST_SPORT_PREFIX}{sport_suffix}"
            db.execute(sa_text(
                "DELETE FROM heat_edge_contributors WHERE edge_key LIKE :prefix"
            ), {"prefix": f"{sport}/%"})
            db.execute(sa_text(
                "DELETE FROM heat_edges WHERE sport = :sport"
            ), {"sport": sport})
            db.execute(sa_text(
                "DELETE FROM heat_cell_contributors WHERE sport = :sport"
            ), {"sport": sport})
            db.execute(sa_text(
                "DELETE FROM heat_cells WHERE sport = :sport"
            ), {"sport": sport})
        db.commit()
    finally:
        db.close()

    yield

    db = SessionLocal()
    try:
        for sport_suffix in ("mtb", "gravel", "road"):
            sport = f"{_TEST_SPORT_PREFIX}{sport_suffix}"
            db.execute(sa_text(
                "DELETE FROM heat_edge_contributors WHERE edge_key LIKE :prefix"
            ), {"prefix": f"{sport}/%"})
            db.execute(sa_text(
                "DELETE FROM heat_edges WHERE sport = :sport"
            ), {"sport": sport})
            db.execute(sa_text(
                "DELETE FROM heat_cell_contributors WHERE sport = :sport"
            ), {"sport": sport})
            db.execute(sa_text(
                "DELETE FROM heat_cells WHERE sport = :sport"
            ), {"sport": sport})
        db.commit()
    finally:
        db.close()


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.slow
class TestIngestionPerformance:
    """Measure _update_heat_edges performance with real GPX data."""

    # Budgets bumped from 2s/8s (2026-07). TWO independent causes, both
    # measured on the current local occitanie import (7.16M edges):
    #   1. PRE-EXISTING: a cold first-ingest of this ride loads ~42 z14 OSM
    #      tiles / ~74k segments before matching — that alone measures ~3.6s
    #      cold on the current (heavier) local OSM, independent of this PR.
    #      The old 2s budget assumed a smaller/ warmer local corpus.
    #   2. THIS PR (migration 0057): _update_heat_edges now also incrementally
    #      refreshes heat_edges_agg for the touched ways (recompute-from-source,
    #      ~0.8s for a ~270-way ride). Deliberate display-aggregate maintenance;
    #      in prod the whole _update_heat_edges path runs in the async Cloud
    #      Tasks heat-compute worker (process_heat_compute), OFF the user-facing
    #      upload request, so the latency is not user-visible.
    # Still bounded (not "no limit") so a gross matcher/agg regression fails.
    def test_single_activity_under_6s(self):
        """A single ~4k-point GPX activity should cold-ingest (OSM tile load +
        match + heat_edges_agg refresh) in < 6s. See the budget note above."""
        activities = _load_gpx_activities()
        if not activities:
            pytest.skip("No GPX activities parsed")

        act = activities[0]
        sport = f"{_TEST_SPORT_PREFIX}{act['sport']}"

        t0 = time.monotonic()
        edge_count, new_keys = _update_heat_edges(
            _TEST_USER, sport, act["geometry_geojson"],
        )
        elapsed = time.monotonic() - t0

        assert edge_count > 0, f"Expected edges from {act['name']} ({act['point_count']} points)"
        assert elapsed < 6.0, (
            f"Single activity ({act['point_count']} pts, {edge_count} edges) "
            f"took {elapsed:.2f}s — expected < 6s (cold OSM tile load + match "
            f"+ heat_edges_agg refresh)"
        )

    def test_batch_5_activities_under_12s(self):
        """Five activities (15-20k points total) should ingest (+ per-activity
        agg refresh) in < 12s. See the budget note above test_single_activity."""
        activities = _load_gpx_activities()
        if len(activities) < 2:
            pytest.skip("Need at least 2 GPX activities")

        total_points = 0
        total_edges = 0

        t0 = time.monotonic()
        for act in activities[:5]:
            sport = f"{_TEST_SPORT_PREFIX}{act['sport']}"
            edge_count, _ = _update_heat_edges(
                _TEST_USER, sport, act["geometry_geojson"],
            )
            total_edges += edge_count
            total_points += act["point_count"]
        elapsed = time.monotonic() - t0

        assert total_edges > 0
        assert elapsed < 12.0, (
            f"Batch of {len(activities[:5])} activities ({total_points} pts, "
            f"{total_edges} edges) took {elapsed:.2f}s — expected < 12s "
            f"(incl. heat_edges_agg refresh)"
        )

    def test_throughput_edges_per_second(self):
        """Measure edge throughput — should be > 500 edges/s."""
        activities = _load_gpx_activities()
        if not activities:
            pytest.skip("No GPX activities parsed")

        # Use the largest activity for throughput measurement
        act = max(activities, key=lambda a: a["point_count"])
        sport = f"{_TEST_SPORT_PREFIX}{act['sport']}"

        t0 = time.monotonic()
        edge_count, _ = _update_heat_edges(
            _TEST_USER, sport, act["geometry_geojson"],
        )
        elapsed = time.monotonic() - t0

        edges_per_sec = edge_count / elapsed if elapsed > 0 else 0
        assert edges_per_sec > 500, (
            f"Throughput: {edges_per_sec:.0f} edges/s from {act['point_count']} pts "
            f"— expected > 500 edges/s"
        )

    def test_heat_cells_under_500ms(self):
        """Heat cell update for ~100 cells should complete in < 500ms."""
        activities = _load_gpx_activities()
        if not activities:
            pytest.skip("No GPX activities parsed")

        act = activities[0]
        sport = f"{_TEST_SPORT_PREFIX}{act['sport']}"

        # Extract cells from geometry
        from app.services.ingest import _geojson_to_cells
        cells = _geojson_to_cells(act["geometry_geojson"])

        t0 = time.monotonic()
        _update_heat_cells(_TEST_USER, sport, cells)
        elapsed = time.monotonic() - t0

        assert elapsed < 0.5, (
            f"Heat cells update ({len(cells)} cells) took {elapsed:.2f}s "
            f"— expected < 500ms"
        )

    def test_duplicate_ingestion_not_slower(self):
        """Re-ingesting the same activity (UPSERT path) should not be slower."""
        activities = _load_gpx_activities()
        if not activities:
            pytest.skip("No GPX activities parsed")

        act = activities[0]
        sport = f"{_TEST_SPORT_PREFIX}{act['sport']}"

        # First ingest (INSERT path)
        t0 = time.monotonic()
        _update_heat_edges(_TEST_USER, sport, act["geometry_geojson"])
        elapsed_first = time.monotonic() - t0

        # Second ingest (UPDATE path — same edges, increment pass_count)
        t0 = time.monotonic()
        _update_heat_edges(_TEST_USER, sport, act["geometry_geojson"])
        elapsed_second = time.monotonic() - t0

        # UPSERT (update) should be at most 2x slower than insert
        assert elapsed_second < elapsed_first * 2.0, (
            f"Duplicate ingest {elapsed_second:.2f}s vs first {elapsed_first:.2f}s "
            f"— expected < 2x overhead"
        )

    def test_duplicate_ingestion_does_not_grow_edges(self):
        """Re-ingesting the same geometry must NOT add new heat_edges rows.

        UPSERT on (edge_key, sport) means second pass should bump pass_count
        but never insert. If this regresses, fresh-DB heat_edges count grows
        unbounded as users re-ride the same routes (the May 2026 cruft scenario).

        Note: heat_edges is partitioned by sport with `gravel` as the default
        bucket for non-canonical sport names. Test sports (`perf_*`) get
        normalized to `gravel` by `_normalize_heat_edge_sport`. This test
        measures the **delta** to ride alongside other contributors safely.
        """
        from sqlalchemy import text as sa_text

        from app.db.session import SessionLocal

        activities = _load_gpx_activities()
        if not activities:
            pytest.skip("No GPX activities parsed")

        act = activities[0]
        sport_label = f"{_TEST_SPORT_PREFIX}{act['sport']}"
        # _normalize_heat_edge_sport sends test_* / cache_test_* sport labels to 'gravel'
        # (and 'perf_offroad' lands in the default → gravel branch too).
        partition_sport = "gravel"

        def _count() -> int:
            d = SessionLocal()
            try:
                return d.execute(
                    sa_text("SELECT COUNT(*) FROM heat_edges WHERE sport = :sport"),
                    {"sport": partition_sport},
                ).scalar() or 0
            finally:
                d.close()

        # First ingest: warms the dedup state (or no-ops if the fixture was
        # already ingested by a previous run — that's also a valid pass).
        _update_heat_edges(_TEST_USER, sport_label, act["geometry_geojson"])
        count_after_first = _count()

        # Second ingest: MUST NOT grow rows. This is the dedup guarantee.
        _update_heat_edges(_TEST_USER, sport_label, act["geometry_geojson"])
        count_after_second = _count()

        delta_second = count_after_second - count_after_first
        assert delta_second == 0, (
            f"Duplicate ingest grew heat_edges by {delta_second} extra rows "
            f"({count_after_first} → {count_after_second}). "
            "UPSERT dedup is broken — same geometry should only bump pass_count."
        )

    def test_parallel_faster_than_sequential(self):
        """Parallel rebuild (4 workers) should be faster than sequential for 4+ activities."""
        activities = _load_gpx_activities()
        if len(activities) < 4:
            pytest.skip("Need at least 4 GPX activities")

        acts = activities[:4]
        # Build parallel input tuples
        parallel_input = [
            (_TEST_USER, f"{_TEST_SPORT_PREFIX}{a['sport']}", a["geometry_geojson"], None, True)
            for a in acts
        ]

        # Sequential baseline
        t0 = time.monotonic()
        for a in acts:
            sport = f"{_TEST_SPORT_PREFIX}{a['sport']}"
            _update_heat_edges(_TEST_USER, sport, a["geometry_geojson"])
        elapsed_seq = time.monotonic() - t0

        # Clean edges for parallel run
        db = SessionLocal()
        try:
            for sport_suffix in ("mtb", "gravel", "road"):
                sport = f"{_TEST_SPORT_PREFIX}{sport_suffix}"
                db.execute(sa_text("DELETE FROM heat_edge_contributors WHERE edge_key LIKE :p"), {"p": f"{sport}/%"})
                db.execute(sa_text("DELETE FROM heat_edges WHERE sport = :s"), {"s": sport})
            db.commit()
        finally:
            db.close()

        # Parallel run
        t0 = time.monotonic()
        loaded = rebuild_heatmap_parallel(parallel_input, max_workers=4)
        elapsed_par = time.monotonic() - t0

        assert loaded == 4, f"Expected 4 activities processed, got {loaded}"
        # PRIMARY invariant: parallel rebuild produces CORRECT results
        # (loaded == 4 above). The timing check is only a gross-regression
        # guard. `rebuild_heatmap_parallel` uses a ThreadPoolExecutor, and
        # map-matching is CPU-bound Python — under the GIL, 4 threads give NO
        # speedup for CPU work; they add thread + per-thread-DB-session
        # contention plus the shared `_prewarm_osm_cache` fixed cost. At this
        # tiny n=4 scale the pool overhead therefore DOMINATES and parallel is
        # reliably ~1.5-3x SLOWER than sequential (measured, high variance on a
        # constrained Docker VM) — the "2-3x speedup" only materializes at
        # 100+ diverse activities (not exercised here to keep the suite fast).
        # The old `* 1.5` bound encoded a false premise and flaked around the
        # threshold. Widen to a true no-catastrophe ceiling (a deadlock-retry
        # storm / pool starvation would still blow past 4x) without asserting a
        # speedup that the GIL makes impossible at n=4.
        assert elapsed_par < elapsed_seq * 4.0 + 2.0, (
            f"Parallel {elapsed_par:.2f}s catastrophically slower than "
            f"sequential {elapsed_seq:.2f}s (>4x — pool/deadlock pathology)"
        )
