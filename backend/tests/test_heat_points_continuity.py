"""Non-regression: a lone trace must render as a CONTINUOUS heatmap ribbon at
every PMTiles zoom, never as a string of "beads" (fix/heatmap-beads, 2026-11).

Bug (prod /map, Camargue, z12–14): along 1–2-user traces the community heatmap
drew regularly spaced round blobs instead of a ribbon. Root cause: tippecanoe's
DEFAULT dot-dropping (``-r 2.5``) kept 1/2.5 of the ``heat_points`` per zoom
below z15, chosen along its spatial index → measured on a synthetic corpus, a
single trace's 20 m points had max gaps of 750 m at z13 (108 px) and 1250 m at
z12 (90 px) against a heatmap radius of 8 / 6.7 px. Busy corridors hid it.

Fix (z6–z14): the export gives each point a tippecanoe ``minzoom`` so the spacing kept at
each zoom tracks the kernel radius (``heat_point_levels``), and ``run_tippecanoe``
passes ``-r1``. These tests drive the REAL export (``export_raw_geojson``, its
DB stream monkeypatched with a synthetic corpus) and the REAL ``run_tippecanoe``
command, then decode the tiles and measure the gaps along the trace.

The radius comes from ``heatmap_radius_ramp.json`` — the SSOT the frontend
``HEAT_RADIUS_RAMP`` is pinned to (vitest community-heatmap-layers.test.ts).
"""

import itertools
import json
import math
import shutil
import subprocess

import pytest

import app.jobs.build_pmtiles as bp
import app.services.raw_trace_display as rtd
from app.services.raw_trace_display import (
    DEFAULT_POINT_STRIDE_M,
    HEAT_POINT_MAX_GAP_RATIO,
    heat_point_levels,
    heat_point_minzoom,
    heat_radius_m,
    metres_per_css_px,
)

MIN_ZOOM, MAX_ZOOM = 6, 15  # the prod build_pmtiles defaults
# Continuity is guaranteed through z14 (where the owner saw the beads). At z15+
# the radius (~14–16 m) is below the 20 m stride: accepted residual — the
# heatmap fades there and the crisp line carries the read; a finer stride would
# multiply the /tmp (= RAM) points file of the prod job.
CHECK_MAX_ZOOM = 14
LAT = 43.55  # Camargue
LON0 = 4.42
LENGTH_M = 15_000.0
M_LAT = 111_320.0
M_LON = M_LAT * math.cos(math.radians(LAT))


# ── pure level-of-detail invariants (no tippecanoe needed) ───────────────────


@pytest.mark.parametrize("lat", [42.4, 43.55, 51.1])  # France's latitude span
def test_levels_keep_gap_below_radius_at_every_zoom(lat):
    levels = heat_point_levels(lat, DEFAULT_POINT_STRIDE_M)
    assert all(a >= b for a, b in itertools.pairwise(levels)), "levels must nest"
    step = DEFAULT_POINT_STRIDE_M + 3.0  # stride + densify overshoot
    for z in range(MIN_ZOOM, CHECK_MAX_ZOOM + 1):
        worst_gap = (1 << levels[z]) * step
        assert worst_gap <= heat_radius_m(z, lat), (lat, z, worst_gap)
        if levels[z] > 0:  # a decimated zoom honours the design ratio exactly
            assert worst_gap <= HEAT_POINT_MAX_GAP_RATIO * heat_radius_m(z, lat)


def test_minzoom_is_nested_and_first_point_always_kept():
    levels = heat_point_levels(LAT, DEFAULT_POINT_STRIDE_M)
    assert heat_point_minzoom(0, levels) == 0
    for i in range(1, 600):
        mz = heat_point_minzoom(i, levels)
        # kept at mz and at every zoom above (nested), not below
        assert all(i % (1 << levels[z]) == 0 for z in range(mz, len(levels)))
        assert mz == 0 or i % (1 << levels[mz - 1]) != 0


# ── real export + real tippecanoe + decode ───────────────────────────────────


def _line(lat0, n_m, jitter_seed=None):
    import random

    rnd = random.Random(jitter_seed)
    j = (lambda: rnd.gauss(0, 2.0)) if jitter_seed is not None else (lambda: 0.0)
    return [[LON0 + (i * 5.0 + j()) / M_LON, lat0 + j() / M_LAT] for i in range(int(n_m / 5.0) + 1)]


def _points(obj, out):
    """Collect Point features from tippecanoe-decode's nested collections."""
    if isinstance(obj, dict):
        if obj.get("type") == "Feature" and obj.get("geometry", {}).get("type") == "Point":
            out.append(obj)
        for v in obj.get("features", []) or []:
            _points(v, out)
    return out


@pytest.mark.skipif(
    not (shutil.which("tippecanoe") and shutil.which("tippecanoe-decode")), reason="tippecanoe not installed"
)
def test_single_trace_heat_points_continuous_at_every_zoom(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_MASK_METERS", "200")
    monkeypatch.setenv("HEATMAP_MIN_USERS", "1")
    monkeypatch.delenv("HEATMAP_RAW_POINT_STRIDE_M", raising=False)
    monkeypatch.delenv("HEATMAP_RAW_BBOX", raising=False)

    busy_lat = LAT + 1000.0 / M_LAT  # a 10-user corridor 1 km north
    rows = [(1, 1, "running", json.dumps({"type": "LineString", "coordinates": _line(LAT, LENGTH_M)}), None)]
    for k in range(20):
        rows.append(
            (
                2 + k,
                100 + k % 10,
                "gravel",
                json.dumps({"type": "LineString", "coordinates": _line(busy_lat, LENGTH_M, k)}),
                None,
            )
        )
    monkeypatch.setattr(rtd, "_stream_activities", lambda db, **kw: iter(rows))

    trails, pts, out = (str(tmp_path / n) for n in ("t.geojsonl", "p.geojsonl", "o.pmtiles"))
    assert rtd.export_raw_geojson(None, trails, points_path=pts) > 0
    bp.run_tippecanoe(trails, out, MIN_ZOOM, MAX_ZOOM, points_path=pts)

    for z in range(MIN_ZOOM, CHECK_MAX_ZOOM + 1):
        r = subprocess.run(
            ["tippecanoe-decode", "-l", "heat_points", "-Z", str(z), "-z", str(z), out],
            capture_output=True,
            text=True,
            check=True,
        )
        feats = _points(json.loads(r.stdout), [])
        single = sorted(
            (f["geometry"]["coordinates"][0] - LON0) * M_LON for f in feats if f["properties"]["sport"] == "running"
        )
        busy = [f for f in feats if f["properties"]["sport"] == "gravel"]
        assert len(single) >= 2, (z, single)
        # Tiles cut at their edges + buffer → a point can be decoded twice.
        single = sorted({round(x, 1) for x in single})
        max_gap = max(b - a for a, b in itertools.pairwise(single))
        radius = heat_radius_m(z, LAT)
        quant = 2 * metres_per_css_px(z, LAT) * 512 / 4096  # tile coord rounding
        assert max_gap <= radius, f"z{z}: max gap {max_gap:.0f} m > heatmap radius {radius:.0f} m → beads"
        # Design target, unless even the undecimated stride exceeds it (z14);
        # +2 m for the 5-decimal (~1 m) coordinate rounding of both endpoints.
        target = max(HEAT_POINT_MAX_GAP_RATIO * radius, DEFAULT_POINT_STRIDE_M + 3.0) + 2.0
        assert max_gap <= target + quant, (z, max_gap, radius)
        # The ribbon spans the masked trace (length minus the endpoint masks and
        # at most one LOD spacing at the tail), not a stub.
        assert single[-1] - single[0] >= 0.9 * LENGTH_M, (z, single[0], single[-1])
        # Density stays honest: 20 stacked passes stay far denser than 1 trace.
        assert len(busy) >= 10 * len(single), (z, len(busy), len(single))
