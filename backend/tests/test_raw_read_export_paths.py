"""The READ / BUILD paths never touch the matched-pipeline tables.

``/heatmap/summary`` serves the community stats from ``activities`` only, and
the PMTiles build runs the raw-trace export — there is no other source.
Each test drives the REAL handler / entrypoint.
"""
from __future__ import annotations


def test_summary_serves_community_stats(client):
    import app.api.heatmap as hm
    hm._summary_cache = None

    resp = client.get("/heatmap/summary")
    assert resp.status_code == 200, resp.text
    d = resp.json()
    # legacy shape the frontend parseCommunityStats() consumes.
    assert "total_contributors" in d
    assert "total_activities" in d
    assert "total_km" in d


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


def test_build_runs_the_raw_export_only(monkeypatch, tmp_path):
    bp = _patch_build_side_effects(monkeypatch)
    calls = {"n": 0}

    def _fake_export(_db, _p, **_kw):
        calls["n"] += 1
        return 5

    monkeypatch.setattr("app.services.raw_trace_display.export_raw_geojson", _fake_export)
    bp.main(str(tmp_path), 6, 14, 1)
    assert calls["n"] == 1
    # The matched export + its quality audit are gone for good.
    assert not hasattr(bp, "export_geojson")
    assert not hasattr(bp, "_emit_heat_quality_metrics")
