"""Raw-trace heatmap — visual prototype + ingestion-cost benchmark.

Renders the DECISION visuals for docs/raw-trace-heatmap-prototype.md (Paul,
2026-07-21): a precise raw-GPS-trace density raster (the Strava
law-of-large-numbers look) with home/work protected by ENDPOINT MASKING
(``trace_privacy.mask_endpoints``) instead of K-anonymity.

Two PNGs, both over the Hérault/Montpellier bbox used by the grid prototype
(directly comparable to docs/img/grid-vs-osm-*.png):

  1. docs/img/raw-trace-heatmap.png       — full-bbox masked raw density (~5 m)
  2. docs/img/raw-trace-endpoint-mask.png — zoom on the densest home cluster,
                                            before-mask vs after-mask panels

Read-only: parses ``activities.geometry_geojson`` (Crouzet invariant #1 — the
stored trace is never mutated). Touches no other table. numpy + PIL only (no
matplotlib in the image).

NOTE — style illustration, NOT the publish path. This renders LOCAL PNGs for
Paul's eyes to judge the raw+masking VISUAL STYLE; it does NOT enforce the
``manual_upload`` provenance gate that the PRODUCTION build path
(``raw_trace_display.export_raw_geojson``) applies. The real public map only
publishes ``source='manual_upload'`` traces (compliance SSOT
``provenance.is_community_source``); these dev PNGs use all consented traces so
the density is representative enough to assess the look.

    python -m app.jobs.proto_raw_trace_heatmap
    TRACE_MASK_METERS=300 python -m app.jobs.proto_raw_trace_heatmap
"""
from __future__ import annotations

import json
import math
import os
import time

import numpy as np
from PIL import Image, ImageDraw

from app.db.session import SessionLocal
from app.services.trace_privacy import mask_endpoints, mask_meters

# lon_min, lat_min, lon_max, lat_max — same window as the grid prototype.
BBOX = (3.70, 43.50, 4.05, 43.75)
OUT_DIR = os.environ.get("PROTO_OUT_DIR", "docs/img")
PIXEL_M = float(os.environ.get("PROTO_PIXEL_M", "5.0"))

# Piecewise Strava-style hot ramp on a black background.
_RAMP = [
    (0.00, (0, 0, 0)),
    (0.12, (28, 8, 52)),
    (0.30, (120, 20, 60)),
    (0.50, (200, 55, 30)),
    (0.70, (245, 140, 25)),
    (0.88, (255, 215, 85)),
    (1.00, (255, 255, 232)),
]


def _grid_dims(bbox: tuple[float, float, float, float], pixel_m: float) -> tuple[int, int]:
    lon0, lat0, lon1, lat1 = bbox
    mean_lat = (lat0 + lat1) / 2
    w_m = (lon1 - lon0) * 111_320 * math.cos(math.radians(mean_lat))
    h_m = (lat1 - lat0) * 110_574
    return max(1, int(w_m / pixel_m)), max(1, int(h_m / pixel_m))


def _load_activities(db) -> list[tuple[int, str]]:
    """(id, geometry_geojson) for consented activities that touch the bbox."""
    from sqlalchemy import text as sa_text
    lon0, lat0, lon1, lat1 = BBOX
    rows = db.execute(sa_text(
        """
        SELECT id, geometry_geojson
        FROM activities
        WHERE geometry_geojson IS NOT NULL AND contribute_heatmap = true
          AND geometry && ST_MakeEnvelope(:lon0, :lat0, :lon1, :lat1, 4326)
        """
    ), {"lon0": lon0, "lat0": lat0, "lon1": lon1, "lat1": lat1}).fetchall()
    return [(r[0], r[1]) for r in rows]


def _parse_coords(geojson_str: str) -> list:
    from app.services.ingest import _densify_coords
    try:
        coords = json.loads(geojson_str).get("coordinates", [])
    except (json.JSONDecodeError, AttributeError, TypeError):
        return []
    if not coords or len(coords) < 2:
        return []
    return _densify_coords(coords, max_gap=3.0)


def _accumulate(runs_per_activity, bbox, nx, ny) -> np.ndarray:
    """Accumulate distinct-activity pixel occupancy (popularity)."""
    lon0, lat0, lon1, lat1 = bbox
    acc = np.zeros((ny, nx), dtype=np.float32)
    dlon = lon1 - lon0
    dlat = lat1 - lat0
    for runs in runs_per_activity:
        pix: set[int] = set()
        for run in runs:
            for pt in run:
                lon, lat = pt[0], pt[1]
                if lon < lon0 or lon > lon1 or lat < lat0 or lat > lat1:
                    continue
                px = int((lon - lon0) / dlon * (nx - 1))
                py = int((lat1 - lat) / dlat * (ny - 1))  # row 0 = north
                pix.add(py * nx + px)
        for idx in pix:
            acc[idx // nx, idx % nx] += 1.0
    return acc


def _colorize(acc: np.ndarray, vmax_pct: float = 99.5) -> Image.Image:
    nz = acc[acc > 0]
    vmax = float(np.percentile(nz, vmax_pct)) if nz.size else 1.0
    vmax = max(vmax, 1.0)
    t = np.log1p(acc) / math.log1p(vmax)
    t = np.clip(t, 0.0, 1.0)

    stops = np.array([s[0] for s in _RAMP])
    cols = np.array([s[1] for s in _RAMP], dtype=np.float32)
    r = np.interp(t, stops, cols[:, 0])
    g = np.interp(t, stops, cols[:, 1])
    b = np.interp(t, stops, cols[:, 2])
    rgb = np.dstack([r, g, b]).astype(np.uint8)
    rgb[acc == 0] = (0, 0, 0)
    return Image.fromarray(rgb, mode="RGB")


def render_full(db) -> dict:
    nx, ny = _grid_dims(BBOX, PIXEL_M)
    acts = _load_activities(db)
    m = mask_meters()

    t0 = time.perf_counter()
    all_runs = []
    parsed = 0
    total_pts = 0
    for _aid, geojson_str in acts:
        coords = _parse_coords(geojson_str)
        if not coords:
            continue
        total_pts += len(coords)
        all_runs.append(mask_endpoints(coords, m))
        parsed += 1
    mask_s = time.perf_counter() - t0

    tr = time.perf_counter()
    acc = _accumulate(all_runs, BBOX, nx, ny)
    raster_s = time.perf_counter() - tr

    img = _colorize(acc)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "raw-trace-heatmap.png")
    img.save(path, optimize=True)

    return {
        "path": path, "px": f"{nx}x{ny}", "pixel_m": PIXEL_M,
        "activities": parsed, "total_points": total_pts, "mask_m": m,
        "parse_mask_s": round(mask_s, 3), "raster_s": round(raster_s, 3),
        "ms_per_activity_parse_mask": round(1000 * mask_s / parsed, 3) if parsed else 0,
        "ms_per_activity_total": round(1000 * (mask_s + raster_s) / parsed, 3) if parsed else 0,
        "occupied_px": int((acc > 0).sum()), "max_overlap": int(acc.max()),
    }


def _find_home_cluster(db, acts) -> tuple[float, float]:
    """Densest ~50 m cell of trace endpoints (start + end) inside the bbox."""
    lon0, lat0, lon1, lat1 = BBOX
    cell = 0.0005  # ~55 m
    hist: dict[tuple[int, int], int] = {}
    for _aid, geojson_str in acts:
        try:
            coords = json.loads(geojson_str).get("coordinates", [])
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
        coords = [c for c in coords if c is not None]
        if len(coords) < 2:
            continue
        for pt in (coords[0], coords[-1]):
            lon, lat = pt[0], pt[1]
            if lon0 <= lon <= lon1 and lat0 <= lat <= lat1:
                k = (int(lat / cell), int(lon / cell))
                hist[k] = hist.get(k, 0) + 1
    if not hist:
        return (lon0 + lon1) / 2, (lat0 + lat1) / 2
    (ilat, ilon), _ = max(hist.items(), key=lambda kv: kv[1])
    return (ilon + 0.5) * cell, (ilat + 0.5) * cell


def render_endpoint_mask(db) -> dict:
    acts = _load_activities(db)
    clon, clat = _find_home_cluster(db, acts)
    half_m = 400.0
    dlat = half_m / 110_574
    dlon = half_m / (111_320 * math.cos(math.radians(clat)))
    zbox = (clon - dlon, clat - dlat, clon + dlon, clat + dlat)
    pixel_m = 1.5
    nx, ny = _grid_dims(zbox, pixel_m)
    m = mask_meters()

    unmasked_runs = []
    masked_runs = []
    for _aid, geojson_str in acts:
        coords = _parse_coords(geojson_str)
        if not coords:
            continue
        # "before": treat the whole trace as one display run (drop break sentinels)
        unmasked_runs.append([[c for c in coords if c is not None]])
        masked_runs.append(mask_endpoints(coords, m))

    acc_before = _accumulate(unmasked_runs, zbox, nx, ny)
    acc_after = _accumulate(masked_runs, zbox, nx, ny)
    vmax = max(float(acc_before.max()), 1.0)
    left = _colorize(acc_before, 100.0)
    right = _colorize(acc_after, 100.0)

    gap = 16
    canvas = Image.new("RGB", (nx * 2 + gap, ny + 34), (0, 0, 0))
    canvas.paste(left, (0, 34))
    canvas.paste(right, (nx + gap, 34))
    d = ImageDraw.Draw(canvas)
    d.text((6, 10), "AVANT masquage (trace brute → domicile visible)", fill=(255, 255, 255))
    d.text((nx + gap + 6, 10), f"APRES masquage {int(m)} m (zone privee ouverte)", fill=(120, 230, 255))
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "raw-trace-endpoint-mask.png")
    canvas.save(path, optimize=True)

    return {
        "path": path, "px_per_panel": f"{nx}x{ny}", "pixel_m": pixel_m,
        "home_center": (round(clon, 5), round(clat, 5)), "mask_m": m,
        "endpoints_before_px": int((acc_before > 0).sum()),
        "endpoints_after_px": int((acc_after > 0).sum()),
        "max_overlap_before": int(vmax),
    }


def main() -> None:
    db = SessionLocal()
    try:
        print("=== RAW-TRACE HEATMAP (full bbox) ===")
        r = render_full(db)
        for k, v in r.items():
            print(f"  {k}: {v}")
        print("\n=== ENDPOINT MASKING (home-cluster zoom) ===")
        e = render_endpoint_mask(db)
        for k, v in e.items():
            print(f"  {k}: {v}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
