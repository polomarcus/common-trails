"""Benchmark "Ma heatmap": legacy JSON (/me/activities?limit=5000) vs the
per-user PMTiles file (docs/ma-heatmap-fichier-prive.md §8).

LOCAL / THROWAWAY DB ONLY — it inserts N synthetic activities for a fresh user.

    python -m scripts.bench_my_heatmap seed --n 2300            # → prints user id
    python -m scripts.bench_my_heatmap measure --user-id U [--out /tmp/bench]

Synthetic corpus: rides of ~40 km (≈600 display points with elevation — the
heaviest real account averages ~15 KB of display_geojson per ride, i.e. its
36 MB / 2,300), random-walked from a handful of "home" starts around
Montpellier so traces overlap like a real rider's. ``display_geojson`` is the
compact cache the file is built from; ``geometry_geojson`` gets the same text
(the map filter only needs it non-empty).

``measure`` reports: JSON body size raw/gzip (level 6, as perf/me-activities-fast
ships; and the current default 9) + server time; file build time, PMTiles and
summary sizes; and writes the PMTiles to --out so the browser-side byte count
of a /map view can be measured with frontend/scripts/bench-my-heatmap-view.mjs.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import random
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text as sa_text

from app.db.session import SessionLocal

_HOMES = [(3.8767, 43.6108), (3.70, 43.40), (3.95, 43.75), (3.60, 43.65), (4.10, 43.68)]


def _ride(rng: random.Random, n_points: int = 600) -> str:
    lon, lat = rng.choice(_HOMES)
    lon += rng.uniform(-0.01, 0.01)
    lat += rng.uniform(-0.01, 0.01)
    heading = rng.uniform(0, 2 * math.pi)
    ele = rng.uniform(20, 300)
    coords = []
    for i in range(n_points):
        heading += rng.gauss(0, 0.25)
        if i == n_points // 2:
            heading += math.pi  # out-and-back-ish loop home
        step = rng.uniform(45, 90)  # metres
        lon += step * math.cos(heading) / (111_320 * math.cos(math.radians(lat)))
        lat += step * math.sin(heading) / 110_540
        ele = max(0.0, ele + rng.gauss(0, 2))
        coords.append([round(lon, 5), round(lat, 5), round(ele, 1)])
    return json.dumps({"type": "LineString", "coordinates": coords}, separators=(",", ":"))


def seed(n: int, seed_value: int = 42) -> str:
    rng = random.Random(seed_value)
    uid = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.execute(
            sa_text("INSERT INTO users (id, email, username) VALUES (:u, :e, 'bench')"),
            {"u": uid, "e": f"bench_{uid[:8]}@example.com"},
        )
        t0 = datetime(2019, 1, 1, tzinfo=UTC)
        rows = []
        for i in range(n):
            g = _ride(rng)
            rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "u": uid,
                    "g": g,
                    "n": f"Sortie {i}",
                    "s": rng.choice(["road", "gravel", "mtb"]),
                    "d": t0 + timedelta(hours=12 * i),
                }
            )
            if len(rows) == 500:
                _insert(db, rows)
                rows = []
        if rows:
            _insert(db, rows)
        db.commit()
    finally:
        db.close()
    return uid


def _insert(db, rows):
    db.execute(
        sa_text(
            "INSERT INTO activities (id, user_id, provider, source, sport, name, geometry_geojson, "
            "display_geojson, distance_m, activity_date, created_at, contribute_heatmap) "
            "VALUES (:id, :u, 'file', 'manual_upload', :s, :n, :g, :g, 40000, :d, :d, false)"
        ),
        rows,
    )


def measure(user_id: str, out_dir: str | None) -> dict:
    from fastapi.testclient import TestClient

    from app.api.auth import _create_token
    from app.main import app
    from app.services import my_heatmap_files as mhf

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {_create_token(user_id, 'bench@example.com')}"}
    res: dict = {}

    t0 = time.perf_counter()
    r = client.get("/me/activities?limit=5000", headers={**headers, "Accept-Encoding": "identity"})
    res["json_server_s"] = round(time.perf_counter() - t0, 2)
    body = r.content
    res["json_features"] = len(json.loads(body)["features"])
    res["json_raw_mb"] = round(len(body) / 1e6, 2)
    t0 = time.perf_counter()
    res["json_gzip6_mb"] = round(len(gzip.compress(body, 6)) / 1e6, 2)
    res["json_gzip6_s"] = round(time.perf_counter() - t0, 2)
    res["json_gzip9_mb"] = round(len(gzip.compress(body, 9)) / 1e6, 2)

    os.environ.setdefault("MY_HEATMAP_FILES_ENABLED", "true")
    os.environ.setdefault("MY_HEATMAP_LOCAL_DIR", out_dir or "/tmp/bench-my-heatmap")
    b = mhf.build_for_user(user_id, force=True)
    res["file_build_s"] = b.get("seconds")
    res["file_activities"] = b.get("activity_count")
    res["pmtiles_mb"] = round((b.get("pmtiles_bytes") or 0) / 1e6, 2)
    res["summary_gz_kb"] = round((b.get("summary_bytes") or 0) / 1e3, 1)

    t0 = time.perf_counter()
    m = client.get("/me/heatmap/manifest", headers=headers).json()
    s = client.get(m["summary_url"], headers=headers)
    res["manifest_plus_summary_server_s"] = round(time.perf_counter() - t0, 3)
    t0 = time.perf_counter()
    for _ in range(20):
        client.get(m["tiles_url"], headers={**headers, "Range": "bytes=0-16383"})
    res["range_16k_server_ms"] = round((time.perf_counter() - t0) / 20 * 1000, 1)
    res["summary_raw_kb"] = round(len(s.content) / 1e3, 1)
    res["pmtiles_path"] = os.path.join(
        os.environ["MY_HEATMAP_LOCAL_DIR"], mhf.pmtiles_key(user_id, m["version"])
    )
    print(json.dumps(res, indent=2))
    return res


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--n", type=int, required=True)
    m = sub.add_parser("measure")
    m.add_argument("--user-id", required=True)
    m.add_argument("--out", default=None)
    a = p.parse_args()
    if a.cmd == "seed":
        print(seed(a.n))
    else:
        measure(a.user_id, a.out)
