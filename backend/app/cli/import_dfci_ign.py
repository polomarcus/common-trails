"""Import DFCI fire-prevention tracks from IGN BD TOPO (pre-extracted cache).

Source: IGN Géoplateforme WFS — BDTOPO_V3:troncon_de_route
Extraction tool: scripts/extract_dfci_ign.py

The IGN WFS has no working server-side spatial filter (CQL BBOX returns 0),
so a full download (~160K features) takes ~20 minutes.  This module only
loads from a pre-built cache file.  To populate the cache, run:

    # From a venv with geopandas, requests, shapely:
    python scripts/extract_dfci_ign.py --bbox sud_france -o /data/dfci_ign_edges.json

Or with Docker volume mount:
    python scripts/extract_dfci_ign.py --bbox sud_france -o backend/data/dfci_ign_edges.json

The cache file is a JSON list of edge dicts (same format as store_dfci_edges).

Disable with DFCI_IGN_ENABLED=false (default: true).
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

# Cache file path — DATA_DIR if available, else check a few default locations
_DATA_DIR = os.environ.get("DATA_DIR", "")

_CACHE_PATHS = [
    # 1. Explicit DATA_DIR
    os.path.join(_DATA_DIR, "dfci_ign_edges.json") if _DATA_DIR else "",
    # 2. Backend data dir (for dev with Docker volume mount)
    os.path.join(os.path.dirname(__file__), "..", "data", "dfci_ign_edges.json"),
    # 3. /tmp fallback
    "/tmp/dfci_ign_edges.json",
]


def _find_cache() -> str | None:
    """Find the first existing cache file."""
    for path in _CACHE_PATHS:
        if path and os.path.exists(os.path.normpath(path)):
            return os.path.normpath(path)
    return None


def _load_cache(path: str) -> list[dict] | None:
    """Load cached edges from local JSON file."""
    try:
        with open(path, encoding="utf-8") as f:
            edges = json.load(f)
        if isinstance(edges, list) and len(edges) > 0:
            logger.info("DFCI IGN: loaded %d edges from cache (%s)", len(edges), path)
            return edges
    except Exception:
        logger.warning("DFCI IGN: cache file corrupt at %s", path, exc_info=True)
    return None


async def import_dfci_ign() -> int:
    """Load DFCI tracks from pre-extracted IGN cache and store in-memory.

    Returns the number of edges imported.
    Disable with DFCI_IGN_ENABLED=false.
    """
    if os.environ.get("DFCI_IGN_ENABLED", "true").lower() == "false":
        logger.info("DFCI IGN import disabled (DFCI_IGN_ENABLED=false)")
        return 0

    cache_path = _find_cache()
    if not cache_path:
        logger.info(
            "DFCI IGN: no cache file found. Run: python scripts/extract_dfci_ign.py --bbox sud_france -o <DATA_DIR>/dfci_ign_edges.json"
        )
        return 0

    cached = _load_cache(cache_path)
    if not cached:
        return 0

    from app.services.ingest import append_dfci_edges, get_dfci_edge_count

    # Always append (idempotent: main.py guard skips if already loaded).
    # slope_grade comes pre-baked in the cache file (via scripts/enrich_cache_dem.py).
    append_dfci_edges(cached)

    logger.info(
        "DFCI IGN: imported %d IGN edges (%d total)",
        len(cached),
        get_dfci_edge_count(),
    )
    return len(cached)
