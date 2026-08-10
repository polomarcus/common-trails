#!/usr/bin/env python3
"""Seed GT collections from local GPX files via the API.

Usage: python scripts/seed_gt_collections.py
"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from math import atan2, radians, sin, cos, sqrt
from pathlib import Path

from urllib.request import Request, urlopen
from urllib.error import HTTPError


def _post(url, data, headers=None):
    """POST JSON, return (status, json_body)."""
    body = json.dumps(data).encode()
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = Request(url, data=body, headers=hdrs, method="POST")
    try:
        resp = urlopen(req)
        return resp.status, json.loads(resp.read())
    except HTTPError as e:
        return e.code, {"detail": e.read().decode()[:200]}

API = os.environ.get("API_URL", "http://localhost:8787")
GPX_DIR = Path(__file__).resolve().parent.parent / "backend" / "data" / "gt-routes"

# ── GPX parsing ─────────────────────────────────────────────────────────────

def parse_gpx(path: Path) -> dict | None:
    """Parse a GPX file, return {name, coords, distance_m, elevation_gain_m}."""
    try:
        tree = ET.parse(path)
    except Exception as e:
        print(f"  SKIP {path.name}: {e}")
        return None

    root = tree.getroot()
    ns = re.match(r"\{.*\}", root.tag)
    ns = ns.group(0) if ns else ""

    coords = []
    for trkpt in root.iter(f"{ns}trkpt"):
        lat = float(trkpt.attrib["lat"])
        lon = float(trkpt.attrib["lon"])
        ele_el = trkpt.find(f"{ns}ele")
        if ele_el is not None and ele_el.text:
            coords.append([lon, lat, float(ele_el.text)])
        else:
            coords.append([lon, lat])

    # Also check <rtept> (route points)
    if not coords:
        for rtept in root.iter(f"{ns}rtept"):
            lat = float(rtept.attrib["lat"])
            lon = float(rtept.attrib["lon"])
            coords.append([lon, lat])

    if len(coords) < 2:
        print(f"  SKIP {path.name}: only {len(coords)} points")
        return None

    # Simplify if too many points (keep every Nth point, always keep first/last)
    if len(coords) > 2000:
        step = max(1, len(coords) // 2000)
        simplified = [coords[0]]
        for i in range(step, len(coords) - 1, step):
            simplified.append(coords[i])
        simplified.append(coords[-1])
        coords = simplified

    # Compute distance and elevation gain
    dist = 0.0
    dplus = 0.0
    for i in range(1, len(coords)):
        a, b = coords[i - 1], coords[i]
        dist += _haversine(a[1], a[0], b[1], b[0])
        if len(a) > 2 and len(b) > 2:
            delta = b[2] - a[2]
            if delta > 0:
                dplus += delta

    name_el = root.find(f".//{ns}name")
    name = name_el.text.strip() if name_el is not None and name_el.text else path.stem

    return {
        "name": name,
        "coords": coords,
        "distance_m": round(dist),
        "elevation_gain_m": round(dplus),
    }


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


# ── Collection definitions ──────────────────────────────────────────────────

COLLECTIONS = [
    {
        "name": "GTMC — Grande Traversée du Massif Central",
        "sport": "mtb",
        "description": "1 360 km de VTT de Clermont-Ferrand à Sète. Balisage VTT-FFC.",
        "region": "Massif Central",
        "files": ["GT_grande-traversee-du-massif-central-a-vtt.gpx"],
    },
    {
        "name": "GT VTT Ardèche",
        "sport": "mtb",
        "description": "Grande Traversée de l'Ardèche en VTT.",
        "region": "Ardèche",
        "files": ["GT_grande-traversee-de-l-ardeche-vtt.gpx"],
    },
    {
        "name": "GT VTT Hérault — Passa Meridia",
        "sport": "mtb",
        "description": "Traversée VTT de l'Hérault, Nord et Sud.",
        "region": "Hérault",
        "files": [
            "GT_VTT_HÉRAULT_PASSA_MERIDIA_INTÉGRALE NORD_LUNEL_SALVETAT.gpx",
            "GT_VTT_HÉRAULT_PASSA_MERIDIA_INTÉGRALE_SUD_LUNEL_MINERVOIS.gpx",
        ],
    },
    {
        "name": "GT Alpes-Provence VTT",
        "sport": "mtb",
        "description": "Grande Traversée des Alpes à la Provence en VTT.",
        "region": "Alpes-Provence",
        "files": ["GT_Alpes-Provence-VTT.gpx"],
    },
    {
        "name": "GT Jura VTT",
        "sport": "mtb",
        "description": "Grande Traversée du Jura en VTT.",
        "region": "Jura",
        "files": ["GT_Jura-VTT.gpx"],
    },
    {
        "name": "GT Morvan VTT",
        "sport": "mtb",
        "description": "Grande Traversée du Morvan en VTT.",
        "region": "Morvan",
        "files": ["GT_Morvan-VTT.gpx"],
    },
    {
        "name": "GT Rhône VTT",
        "sport": "mtb",
        "description": "Grande Traversée du Rhône en VTT.",
        "region": "Rhône",
        "files": ["GT_Rhone-VTT.gpx"],
    },
    {
        "name": "GT Transverdon VTT",
        "sport": "mtb",
        "description": "Grande Traversée du Verdon en VTT.",
        "region": "Verdon",
        "files": ["GT_Transverdon-VTT.gpx"],
    },
    {
        "name": "Grand Tour des Écrins",
        "sport": "mtb",
        "description": "Tour VTT du massif des Écrins.",
        "region": "Écrins, Hautes-Alpes",
        "files": ["GT_Grand-Tour-des-Ecrins.gpx"],
    },
    {
        "name": "Gravel Grands Causses",
        "sport": "gravel",
        "description": "31 itinéraires gravel dans les Grands Causses — Larzac, Sévérac, Millau, Lodève.",
        "region": "Grands Causses, Aveyron",
        "files": sorted([f for f in os.listdir(GPX_DIR) if f.startswith("GRAVEL_Grands-Causses")]),
    },
    {
        "name": "Gravel Lozère",
        "sport": "gravel",
        "description": "Itinéraires gravel en Lozère — Aubrac, Mont Lozère, Causse Méjean.",
        "region": "Lozère",
        "files": sorted([f for f in os.listdir(GPX_DIR) if f.startswith("GRAVEL_Lozere")]),
    },
    {
        "name": "GT VTT Luberon FFC",
        "sport": "mtb",
        "description": "63 itinéraires VTT labellisés FFC dans le Luberon.",
        "region": "Luberon, Vaucluse",
        "files": sorted([f for f in os.listdir(GPX_DIR) if "Luberon" in f])[:10],  # first 10 to keep manageable
    },
]


def main():
    if not GPX_DIR.is_dir():
        print(f"ERROR: GPX dir not found: {GPX_DIR}")
        sys.exit(1)

    # Register or login
    print("Authenticating...")
    status, data = _post(f"{API}/auth/register", {
        "email": "gt-seed@chemins-communs.fr",
        "password": "gtseed2024",
        "username": "gt-seed",
    })
    if status == 409:
        status, data = _post(f"{API}/auth/login", {
            "email": "gt-seed@chemins-communs.fr",
            "password": "gtseed2024",
        })
    if status not in (200, 201):
        print(f"Auth failed: {status} {data}")
        sys.exit(1)

    token = data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("Authenticated.")

    created = 0
    for coll in COLLECTIONS:
        print(f"\n{'='*60}")
        print(f"Collection: {coll['name']}")
        print(f"  Files: {len(coll['files'])}")

        # Parse all GPX files for this collection
        stages = []
        for fname in coll["files"]:
            fpath = GPX_DIR / fname
            if not fpath.exists():
                print(f"  MISSING: {fname}")
                continue
            parsed = parse_gpx(fpath)
            if parsed:
                stages.append(parsed)
                print(f"  OK: {parsed['name']} ({parsed['distance_m']/1000:.0f}km, D+{parsed['elevation_gain_m']}m, {len(parsed['coords'])} pts)")

        if not stages:
            print("  SKIP: no valid stages")
            continue

        # Create routes
        route_ids = []
        for stage in stages:
            geojson = json.dumps({"type": "LineString", "coordinates": stage["coords"]})
            status, rdata = _post(f"{API}/routes", {
                "name": stage["name"],
                "sport": coll["sport"],
                "visibility": "public",
                "geometry_geojson": geojson,
                "distance_m": stage["distance_m"],
                "elevation_gain_m": stage["elevation_gain_m"],
            }, headers)
            if status == 201:
                route_ids.append(rdata["id"])
            else:
                print(f"  Route create failed: {status}")
                route_ids.append(None)

        # Create collection
        status, tdata = _post(f"{API}/trips", {
            "name": coll["name"],
            "sport": coll["sport"],
            "visibility": "public",
            "status": "planned",
            "description": coll.get("description"),
            "region": coll.get("region"),
        }, headers)
        if status != 201:
            print(f"  Collection create failed: {status} {tdata}")
            continue

        trip_id = tdata["id"]

        # Add stages
        for i, (stage, route_id) in enumerate(zip(stages, route_ids)):
            if not route_id:
                continue
            _post(f"{API}/trips/{trip_id}/stages", {
                "route_id": route_id,
                "day_index": i,
                "title": stage["name"],
            }, headers)

        print(f"  CREATED: {coll['name']} ({len(route_ids)} traces)")
        created += 1

    print(f"\n{'='*60}")
    print(f"Done: {created} collections created.")


if __name__ == "__main__":
    main()
