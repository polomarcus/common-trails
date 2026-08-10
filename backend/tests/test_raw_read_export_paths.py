"""Raw-trace cutover — the READ / EXPORT / ROUTING paths must not 500 / OOM
when the OSM substrate (``osm_ways`` / ``osm_road_edges``) and
``heat_edge_contributors`` are DROPPED in prod (raw mode), while ``matched``
mode (the default) stays byte-identical.

Each test drives the REAL handler via the FastAPI TestClient. The dropped-table
failure is reproduced by monkeypatching the exact matched-pipeline callable the
old code reached (``build_heat_aggregation_sql`` / ``get_heat_edges_public`` /
``get_heatmap_summary`` / ``_build_bbox_graph`` / ``compute_multi_route`` /
``compute_proposals``) to RAISE — the same effect a ``relation ... does not
exist`` gives at runtime. On the OLD code these paths call the raising callable →
500; on the FIXED code raw mode never reaches it (early return / 410) → the
raising callable is never called. So every test FAILS on old code and PASSES on
the fix, and runs identically in CI (no real DROP needed).

Manually reproduced end-to-end (2026-08-04) by RENAMING osm_road_edges /
osm_ways / heat_edge_contributors on the local DB: raw mode 500s → all green
after the fix; matched mode unchanged.
"""
from __future__ import annotations

BBOX = "3.80,43.55,3.92,43.65"  # ~11 km Montpellier box the local corpus covers
BBOX_Q = "min_lon=3.80&min_lat=43.55&max_lon=3.92&max_lat=43.65"


# NOTE: The community-EXPORT raw-vs-matched tests that used to live here were
# removed when the export surface was reduced to PRE-COMPUTED artifacts only
# (no on-demand /export/heatmap.{geojson,gpx,kml} builds). The READ (MVT /
# trails / summary) + build-metrics raw/matched pins below are unaffected.


# ── #2 Live MVT time-filter tile ──────────────────────────────────────────────


def _clear_tile_caches():
    import app.api.heatmap as hm
    hm._reset_tile_caches()


def test_mvt_time_filter_raw_mode_no_500(client, monkeypatch):
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")
    _clear_tile_caches()

    # The time-filtered z>11 branch calls build_heat_aggregation_sql (OSM join)
    # → 500 under a dropped substrate. Raw mode must drop the days filter and
    # serve the safe all-time tile instead.
    def _boom(*a, **k):
        raise RuntimeError('relation "osm_road_edges" does not exist')

    monkeypatch.setattr(
        "app.services.heat_aggregation.build_heat_aggregation_sql", _boom
    )
    resp = client.get("/heatmap/tiles/gravel/14/8305/5905.mvt?days=30")
    assert resp.status_code == 200, resp.text


def test_mvt_time_filter_matched_mode_still_uses_osm_join(client, monkeypatch):
    """Matched mode with ?days= still routes through the live OSM-join builder."""
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "matched")
    _clear_tile_caches()

    import app.services.heat_aggregation as ha
    real = ha.build_heat_aggregation_sql
    calls = {"n": 0}

    def _spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(ha, "build_heat_aggregation_sql", _spy)
    resp = client.get("/heatmap/tiles/gravel/14/8307/5907.mvt?days=30")
    assert resp.status_code == 200
    assert calls["n"] >= 1  # matched days-tile still hit the OSM-join builder


# ── #3 /heatmap/trails full-bbox OOM ──────────────────────────────────────────


def test_trails_raw_mode_returns_410_without_scanning(client, monkeypatch):
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")

    def _must_not_run(*a, **k):
        raise AssertionError("get_heat_edges_public scanned in raw mode")

    monkeypatch.setattr(
        "app.services.ingest.get_heat_edges_public", _must_not_run
    )
    resp = client.get("/heatmap/trails")  # default = whole-world bbox
    assert resp.status_code == 410


def test_trails_matched_mode_still_serves(client, monkeypatch):
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "matched")
    resp = client.get(f"/heatmap/trails?{BBOX_Q}")
    assert resp.status_code == 200
    assert resp.headers.get("X-License") == "ODbL-1.0"


# ── #4 /heatmap/summary heavy + dropped heat_edge_contributors ────────────────


def test_summary_raw_mode_no_500_without_contributors(client, monkeypatch):
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")
    import app.api.heatmap as hm
    hm._summary_cache = None

    def _boom(*a, **k):
        raise RuntimeError('relation "heat_edge_contributors" does not exist')

    monkeypatch.setattr("app.services.ingest.get_heatmap_summary", _boom)
    resp = client.get("/heatmap/summary")
    assert resp.status_code == 200, resp.text
    d = resp.json()
    # legacy shape the frontend parseCommunityStats() consumes.
    assert "total_contributors" in d
    assert "total_activities" in d
    assert "total_km" in d


def test_summary_matched_mode_uses_ingest(client, monkeypatch):
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "matched")
    import app.api.heatmap as hm
    hm._summary_cache = None
    real = hm.ingest_service.get_heatmap_summary
    calls = {"n": 0}

    def _spy():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(hm.ingest_service, "get_heatmap_summary", _spy)
    resp = client.get("/heatmap/summary")
    assert resp.status_code == 200
    assert calls["n"] == 1


# ── #5 Routing / graph endpoints — REMOVED ────────────────────────────────────
# The internal routing subsystem (app/api/routing.py, app/api/graph_tiles.py,
# app/services/routing.py) was decommissioned in the WASM-routing removal
# (chore/decommission-wasm-routing). The /routing/* endpoints no longer exist,
# so the raw-vs-matched 410 assertions that lived here are obsolete.


# ── #6 build_pmtiles heat-quality scan skipped in raw mode ─────────────────────


def _patch_build_side_effects(monkeypatch):
    import app.jobs.build_pmtiles as bp
    monkeypatch.setattr(bp.shutil, "which", lambda _n: "/usr/bin/tippecanoe")
    monkeypatch.setattr(bp, "run_tippecanoe", lambda *a, **k: None)
    monkeypatch.setattr(bp, "_upload_to_export_bucket", lambda *a, **k: None)
    monkeypatch.setattr(bp, "publish_stats_json", lambda *a, **k: {})
    monkeypatch.setattr(bp, "capture_heatmap_metrics_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(bp.os.path, "getsize", lambda _p: 0)
    monkeypatch.setattr(bp.os, "unlink", lambda _p: None)
    return bp


def test_build_raw_mode_skips_heat_quality_metrics(monkeypatch, tmp_path):
    monkeypatch.setenv("HEATMAP_DISPLAY_SOURCE", "raw")
    bp = _patch_build_side_effects(monkeypatch)
    monkeypatch.setattr(
        "app.services.raw_trace_display.export_raw_geojson", lambda _db, _p, **_kw: 5
    )
    calls = {"n": 0}
    monkeypatch.setattr(bp, "_emit_heat_quality_metrics",
                        lambda _db: calls.__setitem__("n", calls["n"] + 1))
    bp.main(str(tmp_path), 6, 14, 1)
    assert calls["n"] == 0  # no raw-heat_edges audit in raw mode


def test_build_matched_mode_runs_heat_quality_metrics(monkeypatch, tmp_path):
    monkeypatch.delenv("HEATMAP_DISPLAY_SOURCE", raising=False)
    bp = _patch_build_side_effects(monkeypatch)
    monkeypatch.setattr(bp, "export_geojson", lambda *a, **k: 5)
    calls = {"n": 0}
    monkeypatch.setattr(bp, "_emit_heat_quality_metrics",
                        lambda _db: calls.__setitem__("n", calls["n"] + 1))
    bp.main(str(tmp_path), 6, 14, 1)
    assert calls["n"] == 1  # matched build still emits the quality audit
