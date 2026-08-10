"""Import GT/EV/GR trail routes as public routes.

Sources (in priority order):
  1. Local GPX files in data/gt-routes/ directory
  2. Overpass API (fallback if no local files)

GPX file naming convention:
  {trail_type}_{name}.gpx  →  e.g. GT_GTA-Ardeche.gpx, EV_EV17-Rhone.gpx

  trail_type determines default sport:
    GT → mtb, EV → road, GR → gravel, GRP → gravel

Usage:
    python -m app.cli.import_gt_routes
"""
from __future__ import annotations

import json
import logging
import math
import os
import uuid
from glob import glob
from pathlib import Path

# Harden stdlib XML before importing ET. `python -m app.cli.import_gt_routes`
# bypasses `app.main` (and therefore `app.services.gpx`), so the defuse call
# in gpx.py is not triggered. GT/EV/GR seed files are operator-controlled
# (low risk), but the same parser hardening should apply on every entry
# point. Audit 2026-05-29 GPX-S2.3 (S3.4).
import defusedxml

defusedxml.defuse_stdlib()

import xml.etree.ElementTree as ET  # noqa: E402  — must follow defuse_stdlib()

logger = logging.getLogger(__name__)

_DATA_DIR = os.environ.get("DATA_DIR", "")
_GPX_DIR = os.path.join(_DATA_DIR, "gt-routes") if _DATA_DIR else "data/gt-routes"

_SPORT_MAP = {
    "GT": "mtb",       # Grande Traversée — MTB, sometimes gravel
    "EV": "gravel",    # EuroVelo — all bikes (road/gravel/touring)
    "GR": "offroad",   # Grande Randonnée — hiking + MTB
    "GRP": "offroad",  # GR de Pays — hiking + MTB
    "PR": "offroad",   # Promenade et Randonnée — hiking + MTB
    "GRAVEL": "gravel", # Gravel routes
}

_GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    R = 6371000
    p = math.pi / 180
    a = (
        0.5
        - math.cos((lat2 - lat1) * p) / 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def _compute_distance(coords: list[list[float]]) -> float:
    total = 0.0
    for i in range(1, len(coords)):
        total += _haversine_m(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1])
    return total


def _parse_gpx(filepath: str) -> dict | None:
    """Parse a GPX file into a route dict."""
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except Exception as e:
        logger.warning("Failed to parse GPX %s: %s", filepath, e)
        return None

    # Handle both namespaced and non-namespaced GPX
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    # Extract name from GPX metadata or track
    name = None
    name_el = root.find(f"{ns}metadata/{ns}name")
    if name_el is not None and name_el.text:
        name = name_el.text.strip()

    # Extract coordinates from tracks
    coords: list[list[float]] = []
    for trk in root.findall(f"{ns}trk"):
        if not name:
            trk_name = trk.find(f"{ns}name")
            if trk_name is not None and trk_name.text:
                name = trk_name.text.strip()
        for seg in trk.findall(f"{ns}trkseg"):
            for pt in seg.findall(f"{ns}trkpt"):
                lat = float(pt.get("lat", "0"))
                lon = float(pt.get("lon", "0"))
                ele_el = pt.find(f"{ns}ele")
                if ele_el is not None and ele_el.text:
                    coords.append([lon, lat, float(ele_el.text)])
                else:
                    coords.append([lon, lat])

    # Also check routes (rte) if no tracks
    if not coords:
        for rte in root.findall(f"{ns}rte"):
            if not name:
                rte_name = rte.find(f"{ns}name")
                if rte_name is not None and rte_name.text:
                    name = rte_name.text.strip()
            for pt in rte.findall(f"{ns}rtept"):
                lat = float(pt.get("lat", "0"))
                lon = float(pt.get("lon", "0"))
                coords.append([lon, lat])

    if len(coords) < 2:
        logger.warning("GPX %s has too few points (%d)", filepath, len(coords))
        return None

    # Infer trail type from filename
    filename = Path(filepath).stem
    trail_type = "GT"  # default
    for prefix in ("GRAVEL", "GT", "EV", "GRP", "GR", "PR"):  # GRAVEL before GR to avoid false match
        if filename.upper().startswith(prefix):
            trail_type = prefix
            break

    sport = _SPORT_MAP.get(trail_type, "gravel")
    # Use filename as name if GPX name is missing or generic
    if not name or name.lower() in ("activity", "untitled", "track", "route", "newtrack"):
        # Clean filename: GT_grande-traversee-de-l-ardeche-vtt → Grande Traversée de l Ardèche VTT
        clean = filename
        # Remove trail type prefix (GT_, EV_, etc.)
        for prefix in ("GRAVEL_", "GT_", "EV_", "GR_", "GRP_", "PR_"):
            if clean.upper().startswith(prefix):
                clean = clean[len(prefix):]
                break
        name = clean.replace("-", " ").replace("_", " ").strip().title()

    distance_m = _compute_distance(coords)
    center = coords[len(coords) // 2]

    # Determine collection group from filename
    collection = None
    source = None
    fn_upper = filename.upper()
    if "FFC-LUBERON" in fn_upper or "VTTPLL" in fn_upper:
        collection = "VTT Provence Luberon Lure"
        source = "https://sitesvtt.ffc.fr/sites/provence-luberon-lure/"
    elif "GRANDS-CAUSSES" in fn_upper:
        collection = "Gravel Grands Causses"
        source = "https://www.explore-millau.com/activites-nature-loisirs/millau-une-destination-tous-velos/les-grands-causses-une-terre-pour-le-gravel/"
    elif "LOZERE" in fn_upper:
        collection = "Gravel Lozère"
        source = "https://www.lozere-tourisme.com/gravel-itineraires"
    elif "FFC-DEPUIS" in fn_upper or "FFC-COTE" in fn_upper or "FFC-SERRE" in fn_upper:
        collection = "Gravel FFC Sud de France"
        source = "https://cyclosportgravel.ffc.fr/"
    elif fn_upper.startswith("GT_GRANDE-TRAVERSEE") or fn_upper.startswith("GT_JURA") or fn_upper.startswith("GT_ALPES") or fn_upper.startswith("GT_TRANSVERDON") or fn_upper.startswith("GT_RHONE") or fn_upper.startswith("GT_MORVAN"):
        collection = "Grandes Traversées VTT"
        source = "https://www.francevelotourisme.com/conseils/itineraires-vtt-grandes-traversees"
    elif "VAUCLUSE" in fn_upper:
        collection = "GT VTT Vaucluse"
        source = "https://sitesvtt.ffc.fr/grandes-traversees/la-grande-traversee-du-vaucluse/"
    elif "HERAULT" in fn_upper or "PASSA_MERIDIA" in fn_upper:
        collection = "GT VTT Hérault Passa Meridia"
        source = "https://sitesvtt.ffc.fr/grandes-traversees/la-grande-traversee-de-lherault-passa-meridia/"
    elif fn_upper.startswith("GT_GRAND-TOUR"):
        collection = "Grands Tours VTT"
        source = "https://sitesvtt.ffc.fr/sites/provence-luberon-lure/"

    # Reject suspiciously long routes (bad GPS data / corrupted coordinates)
    if distance_m > 2_000_000:  # 2000 km — even GTMC is ~1400 km
        logger.warning("GPX %s rejected: %.0f km is suspiciously long", filepath, distance_m / 1000)
        return None

    return {
        "ref": filename,
        "name": name,
        "trail_type": trail_type,
        "sport": sport,
        "distance_m": round(distance_m),
        "center": center[:2],  # [lon, lat] only
        "coordinates": coords,
        "collection": collection,
        "source": source,
    }


def load_gpx_routes() -> list[dict]:
    """Load routes from GPX files in the gt-routes directory."""
    if not os.path.isdir(_GPX_DIR):
        logger.info("No GPX directory at %s", _GPX_DIR)
        return []

    gpx_files = sorted(glob(os.path.join(_GPX_DIR, "*.gpx")))
    if not gpx_files:
        logger.info("No GPX files in %s", _GPX_DIR)
        return []

    logger.info("Found %d GPX files in %s", len(gpx_files), _GPX_DIR)
    routes = []
    for f in gpx_files:
        r = _parse_gpx(f)
        if r:
            routes.append(r)
            logger.info("  %s: %s (%.1f km, %s)", Path(f).name, r["name"], r["distance_m"] / 1000, r["sport"])

    return routes


async def import_gt_routes() -> int:
    """Import GT/EV/GR routes into the routes table."""
    from app.db.models import Route
    from app.db.session import SessionLocal

    # Load from GPX files
    parsed = load_gpx_routes()

    if not parsed:
        logger.info("No GT routes to import (add GPX files to %s)", _GPX_DIR)
        return 0

    # Get or create system owner ID
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@admin")
    system_owner_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"admin:{admin_email}"))

    db = SessionLocal()
    try:
        count = 0
        for r in parsed:
            # Check if route already exists (by name + sport)
            existing = db.query(Route).filter(
                Route.name == r["name"],
                Route.sport == r["sport"],
                Route.visibility == "public",
                Route.deleted_at.is_(None),
            ).first()

            geojson = json.dumps({
                "type": "LineString",
                "coordinates": r["coordinates"],
            })
            desc = f"Sentier balisé {r['trail_type']}"
            if r["ref"]:
                desc += f" — {r['ref']}"

            if existing:
                existing.geometry_geojson = geojson
                existing.distance_m = r["distance_m"]
                existing.description = desc
                logger.info("Updated: %s (%s, %.1f km)", r["name"], r["sport"], r["distance_m"] / 1000)
            else:
                route = Route(
                    id=str(uuid.uuid4()),
                    owner_id=system_owner_id,
                    name=r["name"],
                    sport=r["sport"],
                    visibility="public",
                    geometry_geojson=geojson,
                    distance_m=r["distance_m"],
                    description=desc,
                    status="published",
                )
                db.add(route)
                count += 1
                logger.info("Created: %s (%s, %.1f km)", r["name"], r["sport"], r["distance_m"] / 1000)

        db.commit()
        logger.info("Imported %d new GT/EV/GR routes (%d total)", count, len(parsed))

        # ── Create collections (Trips) from grouped routes ─────────────────
        from app.db.models import Trip, TripStage

        # Build collection → route_ids + source mapping
        collections: dict[str, list[str]] = {}
        coll_sources: dict[str, str] = {}
        for r in parsed:
            coll = r.get("collection")
            if not coll:
                continue
            if r.get("source"):
                coll_sources[coll] = r["source"]
            # Find the route ID we just created/updated
            route = db.query(Route).filter(
                Route.name == r["name"],
                Route.sport == r["sport"],
                Route.visibility == "public",
                Route.deleted_at.is_(None),
            ).first()
            if route:
                collections.setdefault(coll, []).append(route.id)

        # Determine sport for each collection (majority vote)
        coll_sports: dict[str, str] = {}
        for coll_name, route_ids in collections.items():
            sports: dict[str, int] = {}
            for rid in route_ids:
                route = db.query(Route).filter(Route.id == rid).first()
                if route:
                    sports[route.sport] = sports.get(route.sport, 0) + 1
            coll_sports[coll_name] = max(sports, key=sports.get) if sports else "mtb"

        trip_count = 0
        for coll_name, route_ids in collections.items():
            if len(route_ids) < 2:
                continue  # skip single-route "collections"

            # Check if trip already exists
            existing_trip = db.query(Trip).filter(
                Trip.name == coll_name,
                Trip.owner_id == system_owner_id,
            ).first()

            if existing_trip:
                # Update: clear old stages and recreate
                db.query(TripStage).filter(TripStage.trip_id == existing_trip.id).delete()
                trip = existing_trip
                trip.sport = coll_sports.get(coll_name, "mtb")
            else:
                trip = Trip(
                    id=str(uuid.uuid4()),
                    owner_id=system_owner_id,
                    name=coll_name,
                    sport=coll_sports.get(coll_name, "mtb"),
                    visibility="public",
                    status="completed",
                    description=f"Collection de {len(route_ids)} itinéraires",
                )
                db.add(trip)
                trip_count += 1

            # Add stages
            total_dist = 0.0
            for idx, rid in enumerate(route_ids):
                stage = TripStage(
                    id=str(uuid.uuid4()),
                    trip_id=trip.id,
                    route_id=rid,
                    day_index=idx,
                )
                db.add(stage)
                route = db.query(Route).filter(Route.id == rid).first()
                if route and route.distance_m:
                    total_dist += route.distance_m

            trip.total_distance_m = total_dist
            source_url = coll_sources.get(coll_name, "")
            desc = f"Collection de {len(route_ids)} itinéraires — {total_dist / 1000:.0f} km"
            if source_url:
                desc += f"\n\nSource : {source_url}"
            trip.description = desc
            logger.info("Collection: %s (%d routes, %.0f km)", coll_name, len(route_ids), total_dist / 1000)

        db.commit()
        logger.info("Created %d collections", trip_count)
        return count
    finally:
        db.close()


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    asyncio.run(import_gt_routes())
