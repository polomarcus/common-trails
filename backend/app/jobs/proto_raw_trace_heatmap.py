"""Raw-trace heatmap — visual prototype + ingestion-cost benchmark.

Renders the DECISION visuals for docs/raw-trace-heatmap-prototype.md (Paul,
2026-07-21): a precise raw-GPS-trace density raster (the Strava
law-of-large-numbers look) with home/work protected by ENDPOINT MASKING
(``trace_privacy.mask_endpoints``) instead of K-anonymity.

Two PNGs, both over the Hérault/Montpellier bbox used by the grid prototype
(directly comparable to docs/img/grid-vs-osm-*.png):

  1. docs/img/raw-trace-heatmap.png       — full-bbox masked raw density (~5 m)
  2. docs/img/raw-trace-endpoint-mask.png — before/after mask around a
                                            SYNTHETIC home (invented rides,
                                            no DB, no real coordinates)

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
    python -m app.jobs.proto_raw_trace_heatmap --mask-only   # PNG 2 only, no DB
"""
from __future__ import annotations

import json
import math
import os
import time

import numpy as np
from PIL import Image, ImageDraw

from app.db.session import SessionLocal
from app.services.raw_trace_display import mask_then_densify
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
    """RAW fixes (mask BEFORE densify — see ``mask_then_densify``)."""
    try:
        coords = json.loads(geojson_str).get("coordinates", [])
    except (json.JSONDecodeError, AttributeError, TypeError):
        return []
    if not coords or len(coords) < 2:
        return []
    return coords


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
        all_runs.append(mask_then_densify(coords, m))
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



# ── Endpoint-mask illustration: SYNTHETIC data only ─────────────────────────
#
# This PNG is committed to the PUBLIC repo, so it must never be rendered from
# real traces: the previous version zoomed on the densest real endpoint
# cluster (= a contributor's home), and its "after" panel still showed that
# home under the old along-track mask (data audit C1/L1, 2026-11). It is now
# drawn from invented rides around an invented home in the middle of the
# Atlantic, through the real ``mask_endpoints``.

_SYN_HOME = (-30.0, 45.0)  # open ocean — deliberately nobody's address
_SYN_SEED = 20261107


def _syn_offset(east_m: float, north_m: float) -> list:
    lon0, lat0 = _SYN_HOME
    return [lon0 + east_m / (111_195.0 * math.cos(math.radians(lat0))),
            lat0 + north_m / 111_195.0]


def _syn_path(waypoints: list[tuple[float, float]], step: float = 3.0) -> list:
    out: list = []
    for (e0, n0), (e1, n1) in zip(waypoints, waypoints[1:], strict=False):
        n = max(1, int(math.hypot(e1 - e0, n1 - n0) / step))
        out.extend(_syn_offset(e0 + (e1 - e0) * i / n, n0 + (n1 - n0) * i / n)
                   for i in range(n))
    out.append(_syn_offset(*waypoints[-1]))
    return out


def _syn_jitter(rng, seconds: int, amp: float) -> list:
    return [_syn_offset(rng.uniform(-amp, amp), rng.uniform(-amp, amp))
            for _ in range(seconds)]


def synthetic_home_rides(n: int = 40) -> list[list]:
    """Invented rides from one home: 1–3 min of ±6 m GPS jitter at the door,
    out along one of four streets, back along another (some pass the door
    mid-ride), 1–3 min of jitter at the end. Plus through-traffic on a road
    that never touches the house."""
    import random
    rng = random.Random(_SYN_SEED)
    streets = [  # (east, north) polylines leaving the house
        [(0, 0), (40, 60), (90, 200), (120, 450), (150, 900)],
        [(0, 0), (-70, 20), (-260, 60), (-520, 40), (-900, 80)],
        [(0, 0), (30, -80), (60, -250), (40, -500), (80, -900)],
        [(0, 0), (90, -10), (280, -60), (540, -40), (900, -90)],
    ]
    def far(street):  # close the loop on a 3 km arc, outside the rendered box
        e, n_ = street[-1]
        return math.atan2(n_, e)

    def arc(a0: float, a1: float) -> list[tuple[float, float]]:
        steps = 24
        da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        return [(3000 * math.cos(a0 + da * k / steps), 3000 * math.sin(a0 + da * k / steps))
                for k in range(steps + 1)]

    rides = []
    for i in range(n):
        out_s, back_s = rng.sample(streets, 2)
        wps = out_s + arc(far(out_s), far(back_s)) + list(reversed(back_s))
        if i % 5 == 0:  # passes the door mid-ride, then leaves again
            last = streets[(i // 5) % 4]
            wps = out_s + list(reversed(out_s))[1:] + last[1:]
        trace = (_syn_jitter(rng, rng.randint(60, 180), 6.0) + _syn_path(wps)
                 + _syn_jitter(rng, rng.randint(60, 180), 6.0))
        rides.append(trace)
    for _ in range(n // 2):  # through-traffic, 320 m north of the house
        y = 320 + rng.uniform(-4, 4)
        rides.append(_syn_path([(-900, y), (-300, y + 30), (300, y - 20), (900, y)]))
    return rides


def render_endpoint_mask() -> dict:
    clon, clat = _SYN_HOME
    half_m = 400.0
    dlat = half_m / 110_574
    dlon = half_m / (111_320 * math.cos(math.radians(clat)))
    zbox = (clon - dlon, clat - dlat, clon + dlon, clat + dlat)
    pixel_m = 1.5
    nx, ny = _grid_dims(zbox, pixel_m)
    m = mask_meters()

    rides = synthetic_home_rides()
    unmasked_runs = [[r] for r in rides]
    masked_runs = [mask_endpoints(r, m) for r in rides]
    acc_before = _accumulate(unmasked_runs, zbox, nx, ny)
    acc_after = _accumulate(masked_runs, zbox, nx, ny)
    left = _colorize(acc_before, 100.0)
    right = _colorize(acc_after, 100.0)

    # Faint outline of the R circle on the "after" panel.
    rd = ImageDraw.Draw(right)
    r_px = m / pixel_m
    cx, cy = nx / 2, ny / 2
    rd.ellipse((cx - r_px, cy - r_px, cx + r_px, cy + r_px), outline=(70, 110, 130))

    gap = 16
    canvas = Image.new("RGB", (nx * 2 + gap, ny + 34), (0, 0, 0))
    canvas.paste(left, (0, 34))
    canvas.paste(right, (nx + gap, 34))
    d = ImageDraw.Draw(canvas)
    d.text((6, 4), "AVANT masquage (donnees synthetiques)", fill=(255, 255, 255))
    d.text((6, 18), "attente GPS au depart = pelote au domicile", fill=(200, 200, 200))
    d.text((nx + gap + 6, 4), f"APRES masquage radial {int(m)} m", fill=(120, 230, 255))
    d.text((nx + gap + 6, 18), "aucun point a moins de R du depart/arrivee",
           fill=(120, 230, 255))
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "raw-trace-endpoint-mask.png")
    canvas.save(path, optimize=True)

    return {
        "path": path, "px_per_panel": f"{nx}x{ny}", "pixel_m": pixel_m,
        "mask_m": m, "rides": len(rides),
        "occupied_px_before": int((acc_before > 0).sum()),
        "occupied_px_after": int((acc_after > 0).sum()),
    }


def main() -> None:
    import sys
    if "--mask-only" not in sys.argv:
        db = SessionLocal()
        try:
            print("=== RAW-TRACE HEATMAP (full bbox) ===")
            r = render_full(db)
            for k, v in r.items():
                print(f"  {k}: {v}")
        finally:
            db.close()
    print("\n=== ENDPOINT MASKING (synthetic home, no real data) ===")
    e = render_endpoint_mask()
    for k, v in e.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
