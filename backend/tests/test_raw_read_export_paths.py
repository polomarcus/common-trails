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

# NOTE: The community-EXPORT raw-vs-matched tests that used to live here were
# removed when the export surface was reduced to PRE-COMPUTED artifacts only
# (no on-demand /export/heatmap.{geojson,gpx,kml} builds). The live MVT and
# /heatmap/trails pins went with those routes; the summary + build-metrics
# pins below remain.


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


def test_build_matched_mode_runs_heat_quality_metrics(monkeypatch, tmp_path, matched_mode):
    bp = _patch_build_side_effects(monkeypatch)
    monkeypatch.setattr(bp, "export_geojson", lambda *a, **k: 5)
    calls = {"n": 0}
    monkeypatch.setattr(bp, "_emit_heat_quality_metrics",
                        lambda _db: calls.__setitem__("n", calls["n"] + 1))
    bp.main(str(tmp_path), 6, 14, 1)
    assert calls["n"] == 1  # matched build still emits the quality audit
