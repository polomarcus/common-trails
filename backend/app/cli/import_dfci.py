"""Import DFCI fire-prevention tracks from OpenStreetMap via Overpass API.

DFCI = Défense des Forêts Contre les Incendies.
~3 300 ways in south of France with tag `ref:FR:DFCI` OR `ref` containing "DFCI".

These tracks are stored in-memory as trail edges (same format as heat_edges)
and injected into the routing graph for MTB/gravel/offroad routing.

Caching: parsed edges are saved to a local JSON file so subsequent startups
don't need Overpass. Delete the cache file to force a refresh.
"""

import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_TIMEOUT_S = 60.0

# South of France bounding box: Pyrénées → Alpes, Méditerranée → Massif Central
_BBOX = (42.0, 1.0, 45.0, 7.5)  # (south, west, north, east)

_QUERY = (
    "[out:json][timeout:60];"
    "("
    f'way["ref:FR:DFCI"]({_BBOX[0]},{_BBOX[1]},{_BBOX[2]},{_BBOX[3]});'
    f'way["ref"~"DFCI",i]({_BBOX[0]},{_BBOX[1]},{_BBOX[2]},{_BBOX[3]});'
    ");"
    "(._;>;);"
    "out body;"
)

# Cache file path — always write to /tmp (writable on Cloud Run);
# read from DATA_DIR first (GCS FUSE, read-only) then /tmp fallback.
_DATA_DIR = os.environ.get("DATA_DIR", "")
_CACHE_READ_PATHS = [
    p for p in [
        os.path.join(_DATA_DIR, "dfci_edges.json") if _DATA_DIR else "",
        "/tmp/dfci_edges.json",
    ] if p
]
_CACHE_WRITE_FILE = "/tmp/dfci_edges.json"


def _parse_ways(data: dict) -> list[dict]:
    """Parse Overpass JSON response into DFCI edge dicts.

    Resolves node IDs to coordinates and builds LineString geometries.
    """
    # Build node lookup: id → (lon, lat)
    nodes: dict[int, tuple[float, float]] = {}
    for el in data.get("elements", []):
        if el.get("type") == "node":
            nodes[el["id"]] = (el["lon"], el["lat"])

    edges: list[dict] = []
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        node_ids = el.get("nodes", [])
        coords = []
        for nid in node_ids:
            if nid in nodes:
                coords.append(list(nodes[nid]))  # [lon, lat]
        if len(coords) < 2:
            continue

        tags = el.get("tags", {})
        # Prefer ref:FR:DFCI, fall back to ref (e.g. "DFCI E1")
        dfci_ref = tags.get("ref:FR:DFCI", "")
        if not dfci_ref:
            raw_ref = tags.get("ref", "")
            if "dfci" in raw_ref.lower():
                dfci_ref = raw_ref
        edges.append({
            "geometry": {"type": "LineString", "coordinates": coords},
            "surface": tags.get("surface", "unknown"),
            "highway": tags.get("highway", "track"),
            "ref": dfci_ref,
            "user_count": 0,
            "pass_count": 0,
            "trail_network": True,
            "trail_type": "DFCI",
        })

    return edges


def _load_cache() -> list[dict] | None:
    """Load cached edges from local JSON file. Returns None if no cache."""
    for path in _CACHE_READ_PATHS:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                edges = json.load(f)
            if isinstance(edges, list) and len(edges) > 0:
                logger.info("DFCI: loaded %d edges from cache (%s)", len(edges), path)
                return edges
        except Exception:
            logger.warning("DFCI: cache file corrupt (%s), trying next", path)
    return None


def _save_cache(edges: list[dict]) -> None:
    """Save parsed edges to /tmp (always writable, even on Cloud Run)."""
    try:
        os.makedirs(os.path.dirname(_CACHE_WRITE_FILE) or ".", exist_ok=True)
        with open(_CACHE_WRITE_FILE, "w", encoding="utf-8") as f:
            json.dump(edges, f, separators=(",", ":"))
        logger.info("DFCI: cached %d edges to %s", len(edges), _CACHE_WRITE_FILE)
    except Exception:
        logger.warning("DFCI: failed to write cache", exc_info=True)


async def import_dfci() -> int:
    """Fetch DFCI ways from Overpass (or cache) and store them in-memory.

    Returns the number of edges imported.
    Disable with DFCI_ENABLED=false if Overpass is unreachable (CI).
    """
    if os.environ.get("DFCI_ENABLED", "true").lower() == "false":
        logger.info("DFCI import disabled (DFCI_ENABLED=false)")
        return 0

    from app.services.ingest import store_dfci_edges

    # 1. Try local cache first
    cached = _load_cache()
    if cached:
        return store_dfci_edges(cached)

    # 2. Fetch from Overpass
    logger.info("DFCI: querying Overpass API for DFCI ways...")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(
                _OVERPASS_URL,
                data={"data": _QUERY},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.warning("DFCI: Overpass query failed", exc_info=True)
        return 0

    edges = _parse_ways(data)
    if edges:
        _save_cache(edges)
    count = store_dfci_edges(edges)
    logger.info("DFCI: imported %d edges from Overpass", count)
    return count
