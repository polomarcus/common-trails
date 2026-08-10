"""Raster/density ``heat_points`` layer emission (feat/raster-heatmap).

The vector-line raw display rendered dense repeated routes as a diffuse orange
blob ("pâté"). The fix is a maplibre ``heatmap`` layer type over trace POINTS,
which needs the build to emit a companion ``heat_points`` GeoJSONL (weighted
density points) alongside the existing ``trails`` lines, fed to tippecanoe as a
second named layer.

Pins:
  * ``point_stride_m`` env resolution (default 20 m, floors at the default).
  * ``export_raw_geojson(..., points_path=...)`` emits Point features carrying
    ``w`` (density weight ∈ [0,1]) + ``sport`` — from the SAME surviving
    sub-runs the ``trails`` lines are drawn from, so the raster and the lines
    describe the same corridors.
  * point emission is STREAMED to disk (memory bounded by geography + one run,
    not by corpus size) even though point COUNT grows with overlap.
  * ``run_tippecanoe`` builds a MULTI-LAYER command (``-L trails:… -L
    heat_points:…``) when a points file is supplied, and the single-layer
    (``-l trails`` + positional) form otherwise — no tippecanoe binary needed.
"""
from __future__ import annotations

import json
import os
import tracemalloc

import pytest
from sqlalchemy import text as sa_text

from app.db.models import Activity
from app.db.session import SessionLocal
from app.services import raw_trace_display as rtd
from app.services.raw_trace_display import (
    DEFAULT_POINT_STRIDE_M,
    export_raw_geojson,
    point_stride_m,
)

LAT = 43.61
STEP = 10.0 / 110_574  # ~10 m per point
_MANUAL = "manual_upload"
_TEST_USER = "heatpoints-test-user"
_LON_BASE = -43.0  # empty mid-Atlantic corridor → isolated from the real corpus


def _read_lines(path: str) -> list[str]:
    with open(path) as fh:
        return [line for line in fh if line.strip()]


def _ours(feature: dict) -> bool:
    g = feature["geometry"]
    coords = [g["coordinates"]] if g["type"] == "Point" else g["coordinates"]
    return all(-43.5 < c[0] < -42.5 for c in coords)


def _line_geojson(n: int, lon: float) -> str:
    return json.dumps({
        "type": "LineString",
        "coordinates": [[lon, LAT + i * STEP] for i in range(n)],
    })


@pytest.fixture()
def seeded_line():
    db = SessionLocal()
    db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _TEST_USER})
    db.commit()
    # ~2 km manual_upload gravel line → survives 200 m endpoint masking on both
    # ends, leaving ~1.6 km of drawable geometry.
    db.add(Activity(user_id=_TEST_USER, provider="file", sport="gravel",
                    source=_MANUAL, geometry_geojson=_line_geojson(200, _LON_BASE),
                    contribute_heatmap=True))
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.execute(sa_text("DELETE FROM activities WHERE user_id = :u"), {"u": _TEST_USER})
    db.commit()
    db.close()


def test_point_stride_m_env(monkeypatch):
    monkeypatch.delenv("HEATMAP_RAW_POINT_STRIDE_M", raising=False)
    assert point_stride_m() == DEFAULT_POINT_STRIDE_M
    monkeypatch.setenv("HEATMAP_RAW_POINT_STRIDE_M", "15")
    assert point_stride_m() == 15.0
    monkeypatch.setenv("HEATMAP_RAW_POINT_STRIDE_M", "garbage")
    assert point_stride_m() == DEFAULT_POINT_STRIDE_M
    monkeypatch.setenv("HEATMAP_RAW_POINT_STRIDE_M", "0")
    assert point_stride_m() == DEFAULT_POINT_STRIDE_M  # never 0 (would emit every vertex)


def test_export_emits_heat_points_with_weight_and_sport(tmp_path, monkeypatch, seeded_line):
    monkeypatch.setenv("TRACE_MASK_METERS", "200")
    monkeypatch.setenv("HEATMAP_MIN_USERS", "1")
    monkeypatch.setenv("HEATMAP_RAW_POINT_STRIDE_M", "20")
    lines_path = os.path.join(tmp_path, "trails.geojsonl")
    points_path = os.path.join(tmp_path, "heat_points.geojsonl")
    db = SessionLocal()
    try:
        written = export_raw_geojson(db, lines_path, points_path=points_path)
    finally:
        db.close()

    # trails layer unchanged: LineString features returned + counted.
    assert written > 0
    lines = [json.loads(x) for x in _read_lines(lines_path)]
    ours_lines = [f for f in lines if _ours(f)]
    assert ours_lines
    assert all(f["geometry"]["type"] == "LineString" for f in ours_lines)

    # heat_points layer: Point features with exactly {w, sport}.
    pts = [json.loads(x) for x in _read_lines(points_path)]
    ours_pts = [f for f in pts if _ours(f)]
    assert ours_pts, "expected density points for the seeded corridor"
    for f in ours_pts:
        assert f["type"] == "Feature"
        assert f["geometry"]["type"] == "Point"
        assert set(f["properties"]) == {"w", "sport"}
        assert f["properties"]["sport"] == "gravel"
        w = f["properties"]["w"]
        assert isinstance(w, (int, float)) and 0.0 <= w <= 1.0
    # Density sampling: many more points than line features (one line here).
    assert len(ours_pts) > len(ours_lines)


def test_export_without_points_path_is_backward_compatible(tmp_path, monkeypatch, seeded_line):
    """Existing callers pass no points_path → no companion file is created and
    the trails output is exactly as before."""
    monkeypatch.setenv("TRACE_MASK_METERS", "200")
    lines_path = os.path.join(tmp_path, "trails.geojsonl")
    db = SessionLocal()
    try:
        written = export_raw_geojson(db, lines_path)
    finally:
        db.close()
    assert written > 0
    # No stray heat_points file appeared next to it.
    assert not os.path.exists(os.path.join(tmp_path, "heat_points.geojsonl"))


def _synthetic_iter(k: int, npts: int, lon: float):
    """Fresh generator each call: ``k`` synthetic gravel activities, each an
    ``npts``-point run on the SAME corridor with a distinct activity id and one
    shared user. Same-corridor + distinct aids → occupied cells INVARIANT to k
    while the drawn geometry (and thus point count) scales with k."""
    def _factory(_db):
        for i in range(k):
            run = [[lon, LAT + j * STEP] for j in range(npts)]
            yield i, "syn-user", "gravel", run
    return _factory


def _peak_points_bytes(k: int, tmp_path, name: str, monkeypatch) -> tuple[int, int]:
    """Run export_raw_geojson WITH point emission over a k-activity synthetic
    stream; return (peak_bytes, points_written)."""
    import gc
    monkeypatch.setattr(rtd, "iter_masked_runs", _synthetic_iter(k, 400, -47.0))
    lines_path = os.path.join(tmp_path, f"{name}.trails")
    points_path = os.path.join(tmp_path, f"{name}.points")
    gc.collect()
    tracemalloc.start()
    tracemalloc.clear_traces()
    export_raw_geojson(None, lines_path, points_path=points_path)
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    n_points = len(_read_lines(points_path))
    return peak, n_points


def test_heat_points_emission_is_streamed_not_materialized(tmp_path, monkeypatch):
    """Point COUNT scales with overlap (5x corpus → ~5x points — the density
    signal), but PEAK MEMORY stays ~flat because points are written to disk one
    at a time (never a list). Proves the emission is bounded like the rest of
    the streaming build."""
    monkeypatch.delenv("HEATMAP_MIN_USERS", raising=False)
    monkeypatch.setenv("HEATMAP_RAW_POINT_STRIDE_M", "20")

    peak_small, pts_small = _peak_points_bytes(50, tmp_path, "small", monkeypatch)
    peak_big, pts_big = _peak_points_bytes(250, tmp_path, "big", monkeypatch)

    # Overlapping traces stack points → count grows ~linearly with the corpus.
    assert pts_big > pts_small * 2.5, (pts_small, pts_big)
    # But peak memory barely moves (bounded by geography + one in-flight run).
    assert peak_big < peak_small * 1.6, (peak_small, peak_big)


def test_run_tippecanoe_multi_layer_command(monkeypatch):
    """With a points file, tippecanoe gets TWO named layers (-L trails:… -L
    heat_points:…) — never the single-layer -l form; without it, the historical
    single-layer command is emitted."""
    import app.jobs.build_pmtiles as bp

    captured = {}

    class _R:
        returncode = 0
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _R()

    monkeypatch.setattr(bp.subprocess, "run", _fake_run)

    # Multi-layer
    bp.run_tippecanoe("/tmp/trails.geojsonl", "/tmp/out.pmtiles", 6, 15,
                      points_path="/tmp/pts.geojsonl")
    cmd = captured["cmd"]
    assert "-L" in cmd
    assert "trails:/tmp/trails.geojsonl" in cmd
    assert "heat_points:/tmp/pts.geojsonl" in cmd
    assert "-l" not in cmd  # -l cannot combine with -L

    # Single-layer (matched mode / no points)
    bp.run_tippecanoe("/tmp/trails.geojsonl", "/tmp/out.pmtiles", 6, 15)
    cmd = captured["cmd"]
    assert "-l" in cmd and "trails" in cmd
    assert "/tmp/trails.geojsonl" in cmd
    assert not any(str(a).startswith("heat_points:") for a in cmd)
