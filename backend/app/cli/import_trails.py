"""Import marked trails (GR, GT, PR, EV, GRP) from OpenStreetMap via Overpass API.

Marked trails are hiking/cycling route relations that carry a ref tag
matching known French trail networks:
  - GR  (Grande Randonnée)         → trail_score 1.0
  - GRP (GR de Pays)               → trail_score 0.85
  - GT  (Grande Traversée VTT)     → trail_score 0.9   (includes GTMC)
  - EV  (EuroVelo)                 → trail_score 0.95
  - PR  (Promenade et Randonnée)   → trail_score 0.7

These are stored in-memory as trail edges (same format as DFCI edges) and
injected into the routing graph for all sport profiles.

Caching: parsed edges are saved to a local JSON file so subsequent startups
don't need Overpass. Delete the cache file to force a refresh.
"""

import json
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_TIMEOUT_S = 180.0

# South of France bounding box (same as DFCI)
_BBOX = (42.0, 1.0, 45.0, 7.5)  # (south, west, north, east)

# Two-pass Overpass query:
#   1. Fetch route relations (hiking/bicycle/mtb/foot) with a ref tag
#   2. Fetch their member ways + nodes
_QUERY = (
    "[out:json][timeout:180];"
    "("
    # GR / GRP / PR hiking trails
    f'relation["route"="hiking"]["ref"~"^GR|^PR"]({_BBOX[0]},{_BBOX[1]},{_BBOX[2]},{_BBOX[3]});'
    # GT trails (Grande Traversée VTT, GTMC, etc.)
    f'relation["route"~"bicycle|mtb"]["ref"~"^GT"]({_BBOX[0]},{_BBOX[1]},{_BBOX[2]},{_BBOX[3]});'
    f'relation["route"~"hiking|bicycle|mtb"]["name"~"Grande Traversée",i]({_BBOX[0]},{_BBOX[1]},{_BBOX[2]},{_BBOX[3]});'
    # EuroVelo
    f'relation["route"="bicycle"]["ref"~"^EV"]({_BBOX[0]},{_BBOX[1]},{_BBOX[2]},{_BBOX[3]});'
    ") -> .rels;"
    ".rels out body;"
    "way(r.rels);"
    "(._;>;);"
    "out body;"
)

# Classify trail type from relation ref/name tags
_REF_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^GRP", re.IGNORECASE), "GRP"),
    (re.compile(r"^GR\s?\d|^GR$", re.IGNORECASE), "GR"),
    (re.compile(r"^GTMC", re.IGNORECASE), "GT"),
    (re.compile(r"^GT", re.IGNORECASE), "GT"),
    (re.compile(r"^EV", re.IGNORECASE), "EV"),
    (re.compile(r"^PR", re.IGNORECASE), "PR"),
]

# Cache file path
_DATA_DIR = os.environ.get("DATA_DIR", "")
_CACHE_FILE = (
    os.path.join(_DATA_DIR, "trail_edges.json") if _DATA_DIR else "/tmp/trail_edges.json"
)


def _classify_trail_type(ref: str, name: str = "", route: str = "") -> str | None:
    """Determine trail type from relation tags. Returns None if unrecognized."""
    for pattern, trail_type in _REF_PATTERNS:
        if pattern.search(ref):
            return trail_type
    # Fallback: check name for "Grande Traversée"
    if "grande traversée" in name.lower():
        return "GT"
    return None


def _parse_response(data: dict) -> list[dict]:
    """Parse Overpass JSON into trail edge dicts.

    1. Build relation_id → trail_type mapping from relation elements
    2. Build way_id → best trail_type from relation members
    3. Build edges from way geometries
    """
    # Phase 1: parse relations → trail_type + member way IDs
    relation_trail_types: dict[int, str] = {}  # relation_id → trail_type
    way_to_trail_types: dict[int, set[str]] = {}  # way_id → set of trail_types

    for el in data.get("elements", []):
        if el.get("type") != "relation":
            continue
        tags = el.get("tags", {})
        ref = tags.get("ref", "")
        name = tags.get("name", "")
        route = tags.get("route", "")
        trail_type = _classify_trail_type(ref, name, route)
        if not trail_type:
            continue
        relation_trail_types[el["id"]] = trail_type
        # Map member ways to this trail type
        for member in el.get("members", []):
            if member.get("type") == "way":
                way_id = member["ref"]
                way_to_trail_types.setdefault(way_id, set()).add(trail_type)

    if not way_to_trail_types:
        return []

    # Phase 2: parse nodes
    nodes: dict[int, tuple[float, float]] = {}
    for el in data.get("elements", []):
        if el.get("type") == "node":
            nodes[el["id"]] = (el["lon"], el["lat"])

    # Phase 3: parse ways → edges
    # Trail type priority: GR > EV > GT > GRP > PR (pick highest score)
    _PRIORITY = {"GR": 5, "EV": 4, "GT": 3, "GRP": 2, "PR": 1}

    edges: list[dict] = []
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        way_id = el["id"]
        trail_types = way_to_trail_types.get(way_id)
        if not trail_types:
            continue

        # Pick best trail type
        best_type = max(trail_types, key=lambda t: _PRIORITY.get(t, 0))

        # Build geometry
        node_ids = el.get("nodes", [])
        coords = []
        for nid in node_ids:
            if nid in nodes:
                coords.append(list(nodes[nid]))  # [lon, lat]
        if len(coords) < 2:
            continue

        tags = el.get("tags", {})
        edges.append({
            "geometry": {"type": "LineString", "coordinates": coords},
            "surface": tags.get("surface", "unknown"),
            "highway": tags.get("highway", "path"),
            "ref": tags.get("ref", ""),
            "user_count": 0,
            "pass_count": 0,
            "trail_network": True,
            "trail_type": best_type,
        })

    return edges


def _load_cache() -> list[dict] | None:
    """Load cached edges from local JSON file. Returns None if no cache."""
    if not os.path.exists(_CACHE_FILE):
        return None
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            edges = json.load(f)
        if isinstance(edges, list) and len(edges) > 0:
            logger.info(
                "Trails: loaded %d edges from cache (%s)", len(edges), _CACHE_FILE
            )
            return edges
    except Exception:
        logger.warning("Trails: cache file corrupt, will re-fetch", exc_info=True)
    return None


def _save_cache(edges: list[dict]) -> None:
    """Save parsed edges to local JSON file for fast subsequent startups."""
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE) or ".", exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(edges, f, separators=(",", ":"))
        logger.info("Trails: cached %d edges to %s", len(edges), _CACHE_FILE)
    except Exception:
        logger.warning("Trails: failed to write cache", exc_info=True)


async def import_trails() -> int:
    """Fetch marked trail ways from Overpass (or cache) and store them in-memory.

    Returns the number of edges imported.
    Disable with TRAILS_ENABLED=false if Overpass is unreachable (CI).
    """
    if os.environ.get("TRAILS_ENABLED", "true").lower() == "false":
        logger.info("Trails import disabled (TRAILS_ENABLED=false)")
        return 0

    from app.services.ingest import get_trail_edge_count, store_trail_edges

    existing = get_trail_edge_count()
    if existing > 0:
        logger.info("Trails: %d edges already in DB, skipping import", existing)
        return 0

    # 1. Try local cache first (slope_grade pre-baked via scripts/enrich_cache_dem.py)
    cached = _load_cache()
    if cached:
        return store_trail_edges(cached)

    # 2. Fetch from Overpass
    logger.info("Trails: querying Overpass API for GR/GT/PR/EV/GRP trails...")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(
                _OVERPASS_URL,
                data={"data": _QUERY},
            )
            resp.raise_for_status()
            raw = resp.json()
    except Exception:
        logger.warning("Trails: Overpass query failed", exc_info=True)
        return 0

    edges = _parse_response(raw)
    if edges:
        _save_cache(edges)
        # Log breakdown by trail type
        by_type: dict[str, int] = {}
        for e in edges:
            tt = e.get("trail_type", "?")
            by_type[tt] = by_type.get(tt, 0) + 1
        logger.info("Trails breakdown: %s", by_type)
    count = store_trail_edges(edges)
    logger.info("Trails: imported %d edges from Overpass", count)
    return count
