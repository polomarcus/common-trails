"""Import DFCI fire-prevention tracks from Hérault open data.

Source: https://www.herault-data.fr/explore/dataset/pistes-dfci-herault/
Licence Ouverte v2.0 (Etalab) — compatible ODbL.

4 238 DFCI tracks with GeoJSON geometry, ref, and operational status.
Complements the Overpass-based DFCI import (import_dfci.py) with
authoritative departmental data.

These tracks are stored in-memory as trail edges (same format as heat_edges)
and injected into the routing graph for MTB/gravel/offroad routing.

Caching: parsed edges are saved to a local JSON file so subsequent startups
don't need the API. Delete the cache file to force a refresh.
"""

import json
import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_API_BASE = (
    "https://www.herault-data.fr/api/explore/v2.1"
    "/catalog/datasets/pistes-dfci-herault/records"
)
_PAGE_SIZE = 100
_TIMEOUT_S = 30.0

# Cache file path — always write to /tmp (writable on Cloud Run);
# read from DATA_DIR first (GCS FUSE, read-only) then /tmp fallback.
_DATA_DIR = os.environ.get("DATA_DIR", "")
_CACHE_READ_PATHS = [
    p for p in [
        os.path.join(_DATA_DIR, "dfci_herault_edges.json") if _DATA_DIR else "",
        "/tmp/dfci_herault_edges.json",
    ] if p
]
_CACHE_WRITE_FILE = "/tmp/dfci_herault_edges.json"

# Surface mapping from cat_act codes to OSM surface values
# Hérault uses numeric codes: 1=Route, 2=Piste, 3=Chemin/Sentier + letter suffix
_SURFACE_MAP: dict[str, str] = {
    # Code-based (actual API format: digit + letter suffix)
    "1": "asphalt",
    "1A": "asphalt",
    "1B": "asphalt",
    "2": "compacted",
    "2A": "compacted",
    "2B": "dirt",
    "2C": "dirt",
    "3": "dirt",
    "3A": "dirt",
    "3B": "ground",
    "3C": "ground",
    "NR": "dirt",  # Non renseigné
    # Legacy label-based (in case format changes)
    "Route": "asphalt",
    "Piste revêtue": "asphalt",
    "Piste non revêtue": "dirt",
    "Chemin": "dirt",
    "Sentier": "ground",
}


@dataclass
class DfciTrack:
    """A single DFCI track from the Hérault dataset."""

    ref: str  # e.g. "D34-A1-T2"
    troncon: str  # secondary ref (n_dfci_2)
    coords: list[list[float]]  # [[lon, lat], ...]
    statut: str  # "Opérationnel", "Non opérationnel", etc.
    surface: str  # derived from cat_act


def parse_dfci_track(record: dict) -> DfciTrack | None:
    """Parse a single API record into a DfciTrack.

    Returns None if the record has no usable geometry.
    """
    fields = record.get("fields") or record  # v2.1 uses flat records

    # Geometry: geo_shape field contains GeoJSON (may be a Feature wrapper)
    geo = fields.get("geo_shape") or record.get("geo_shape")
    if not geo:
        return None

    # Unwrap Feature wrapper if present
    if geo.get("type") == "Feature":
        geo = geo.get("geometry") or {}

    geom_type = geo.get("type", "")
    coords_raw = geo.get("coordinates")
    if not coords_raw:
        return None

    # Normalize to list of [lon, lat]
    if geom_type == "MultiLineString":
        # Flatten multi-line into single coordinate list
        coords = []
        for line in coords_raw:
            coords.extend(line)
    elif geom_type == "LineString":
        coords = coords_raw
    else:
        return None

    if len(coords) < 2:
        return None

    # Ensure coords are [lon, lat] (API returns [lon, lat])
    coords = [[float(c[0]), float(c[1])] for c in coords]

    ref = str(fields.get("n_dfci_1") or fields.get("n_dfci_2") or "")
    troncon = str(fields.get("n_dfci_2") or "")
    statut = str(fields.get("statut_operationnel") or "")
    cat_act = str(fields.get("cat_act") or "")
    surface = _SURFACE_MAP.get(cat_act, "dirt")

    return DfciTrack(
        ref=ref,
        troncon=troncon,
        coords=coords,
        statut=statut,
        surface=surface,
    )


def tracks_to_edges(tracks: list[DfciTrack]) -> list[dict]:
    """Convert DfciTracks to edge dicts compatible with store_dfci_edges()."""
    edges = []
    for track in tracks:
        edges.append({
            "geometry": {"type": "LineString", "coordinates": track.coords},
            "surface": track.surface,
            "highway": "track",
            "ref": track.ref,
            "user_count": 0,
            "pass_count": 0,
            "trail_network": True,
            "trail_type": "DFCI",
        })
    return edges


async def fetch_all_records() -> list[dict]:
    """Fetch all records from Hérault Data API with pagination."""
    records: list[dict] = []
    offset = 0

    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        while True:
            resp = await client.get(
                _API_BASE,
                params={"limit": _PAGE_SIZE, "offset": offset},
            )
            resp.raise_for_status()
            data = resp.json()

            page_records = data.get("results") or data.get("records") or []
            if not page_records:
                break

            records.extend(page_records)
            total = data.get("total_count", len(records))

            offset += _PAGE_SIZE
            if offset >= total:
                break

    return records


def _load_cache() -> list[dict] | None:
    """Load cached edges from local JSON file. Returns None if no cache."""
    for path in _CACHE_READ_PATHS:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                edges = json.load(f)
            if isinstance(edges, list) and len(edges) > 0:
                logger.info(
                    "DFCI Hérault: loaded %d edges from cache (%s)",
                    len(edges),
                    path,
                )
                return edges
        except Exception:
            logger.warning(
                "DFCI Hérault: cache file corrupt (%s), trying next", path
            )
    return None


def _save_cache(edges: list[dict]) -> None:
    """Save parsed edges to /tmp (always writable, even on Cloud Run)."""
    try:
        os.makedirs(os.path.dirname(_CACHE_WRITE_FILE) or ".", exist_ok=True)
        with open(_CACHE_WRITE_FILE, "w", encoding="utf-8") as f:
            json.dump(edges, f, separators=(",", ":"))
        logger.info(
            "DFCI Hérault: cached %d edges to %s", len(edges), _CACHE_WRITE_FILE
        )
    except Exception:
        logger.warning("DFCI Hérault: failed to write cache", exc_info=True)


async def import_dfci_herault() -> int:
    """Fetch DFCI tracks from Hérault open data (or cache) and store in-memory.

    Returns the number of edges imported.
    Disable with DFCI_HERAULT_ENABLED=false.
    """
    if os.environ.get("DFCI_HERAULT_ENABLED", "true").lower() == "false":
        logger.info("DFCI Hérault import disabled (DFCI_HERAULT_ENABLED=false)")
        return 0

    from app.services.ingest import append_dfci_edges, get_dfci_edge_count, store_dfci_edges

    existing = get_dfci_edge_count()
    if existing > 0:
        logger.info("DFCI Hérault: %d DFCI edges already in DB, skipping import", existing)
        return 0

    # 1. Try local cache first
    cached = _load_cache()
    if cached:
        if get_dfci_edge_count() > 0:
            append_dfci_edges(cached)
            return len(cached)
        return store_dfci_edges(cached)

    # 2. Fetch from API
    logger.info("DFCI Hérault: fetching from Hérault Data API...")
    try:
        records = await fetch_all_records()
    except Exception:
        logger.warning("DFCI Hérault: API fetch failed", exc_info=True)
        return 0

    # 3. Parse records into tracks
    tracks: list[DfciTrack] = []
    skipped = 0
    for record in records:
        track = parse_dfci_track(record)
        if track is None:
            continue
        if track.statut and track.statut.lower() not in (
            "opérationnel",
            "operationnel",
            "",
        ):
            skipped += 1
            continue
        tracks.append(track)

    if skipped:
        logger.info("DFCI Hérault: skipped %d non-operational tracks", skipped)

    # 4. Convert to edges and store
    edges = tracks_to_edges(tracks)
    if edges:
        _save_cache(edges)

    # Append to existing DFCI edges (from IGN or other sources)
    if get_dfci_edge_count() > 0:
        append_dfci_edges(edges)
    else:
        store_dfci_edges(edges)

    logger.info(
        "DFCI Hérault: imported %d edges from %d tracks (%d total)",
        len(edges),
        len(tracks),
        get_dfci_edge_count(),
    )

    return len(edges)
