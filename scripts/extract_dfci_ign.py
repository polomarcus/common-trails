#!/usr/bin/env python3
"""Extract DFCI fire-prevention tracks from IGN BD TOPO via WFS (Géoplateforme).

Source: IGN Géoplateforme WFS — BDTOPO_V3:troncon_de_route
Docs:   https://geoservices.ign.fr/services-geoplateforme-diffusion

~160 000 pistes DFCI en France avec attributs opérationnels (gabarit, vitesse,
terrain, débroussaillement, etc.).

Usage:
    # France entière (~160K features, ~3 min with parallel fetches)
    python scripts/extract_dfci_ign.py

    # Hérault uniquement
    python scripts/extract_dfci_ign.py --bbox 3.3,43.2,4.2,44.0

    # Sortie personnalisée
    python scripts/extract_dfci_ign.py --bbox 3.3,43.2,4.2,44.0 -o herault_dfci.geojson

    # Injecter directement dans le routeur Common Trails
    python scripts/extract_dfci_ign.py --bbox 3.3,43.2,4.2,44.0 --inject

Deps: pip install geopandas requests shapely pyproj
"""

import argparse
import asyncio
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import box, shape

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WFS endpoint
# ---------------------------------------------------------------------------
WFS_URL = "https://data.geopf.fr/wfs/ows"
TYPENAME = "BDTOPO_V3:troncon_de_route"
PAGE_SIZE = 5000  # IGN default max = 5000
MAX_RETRIES = 3
RETRY_DELAY_S = 5

# ---------------------------------------------------------------------------
# DFCI attribute mapping: IGN BD TOPO name → output name (user-friendly)
#
# IGN attributes (DescribeFeatureType):
#   piste_dfci, vitesse_moyenne_dfci, tout_terrain_dfci, gabarit_dfci,
#   impasse_dfci, piste_dfci_debroussaillee, piste_dfci_fosses,
#   ouvrage_d_art_limitant_dfci, pente_maximale_dfci,
#   sens_de_circulation_dfci, zone_de_croisement_dfci,
#   categorie_dfci, nature_detaillee_dfci, aire_de_retournement_dfci
# ---------------------------------------------------------------------------
DFCI_ATTRS = {
    "piste_dfci": "piste_dfci",
    "vitesse_moyenne_dfci": "vit_dfci",
    "tout_terrain_dfci": "terr_dfci",
    "gabarit_dfci": "gab_dfci",
    "impasse_dfci": "impas_dfci",
    "piste_dfci_debroussaillee": "dfci_debro",
    "piste_dfci_fosses": "dfci_fosse",
    "ouvrage_d_art_limitant_dfci": "oalim_dfci",
    # Extra attributes available in BD TOPO
    "pente_maximale_dfci": "pente_max_dfci",
    "sens_de_circulation_dfci": "sens_dfci",
    "zone_de_croisement_dfci": "croisement_dfci",
    "categorie_dfci": "categorie_dfci",
    "nature_detaillee_dfci": "nature_detail_dfci",
    "aire_de_retournement_dfci": "retournement_dfci",
}

# Road context attributes to keep
CONTEXT_ATTRS = {
    "nature": "nature",
    "largeur_de_chaussee": "largeur_m",
    "nom_collaboratif_gauche": "nom",
    "cleabs": "cleabs",
}

# Example bbox values (WGS84 lon_min,lat_min,lon_max,lat_max)
BBOX_EXAMPLES = {
    "herault": (3.3, 43.2, 4.2, 44.0),
    "gard": (3.8, 43.4, 4.9, 44.3),
    "bouches_du_rhone": (4.2, 43.1, 5.8, 43.9),
    "var": (5.6, 43.0, 6.9, 43.8),
    "corse": (8.5, 41.3, 9.6, 43.1),
    "landes": (-1.5, 43.5, 0.2, 44.5),
    "sud_france": (1.0, 42.0, 7.5, 45.0),
}


def _build_cql_filter(include_categorie: bool = False) -> str:
    """Build CQL filter for DFCI tracks.

    Args:
        include_categorie: Also fetch roads with categorie_dfci set but
            piste_dfci not true (adds ~11K Alps roads).

    Note: the IGN WFS CQL BBOX filter is unreliable (returns 0 for many valid
    bboxes). Spatial filtering is done client-side with geopandas instead.
    """
    if include_categorie:
        return "piste_dfci=true OR categorie_dfci IS NOT NULL"
    return "piste_dfci=true"


def _fetch_page(
    session: requests.Session,
    cql_filter: str,
    start_index: int,
) -> dict:
    """Fetch a single WFS page with retries."""
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": TYPENAME,
        "OUTPUTFORMAT": "application/json",
        "SRSNAME": "EPSG:4326",
        "COUNT": str(PAGE_SIZE),
        "STARTINDEX": str(start_index),
        "CQL_FILTER": cql_filter,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(WFS_URL, params=params, timeout=60)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise
            logger.warning(
                "WFS request failed (attempt %d/%d): %s — retrying in %ds",
                attempt,
                MAX_RETRIES,
                exc,
                RETRY_DELAY_S,
            )
            time.sleep(RETRY_DELAY_S)

    raise RuntimeError("unreachable")


MAX_PARALLEL = 4  # Concurrent WFS requests (higher = faster, but IGN may throttle)


def extract_dfci_pistes(
    bbox: tuple[float, float, float, float] | None = None,
    include_categorie: bool = False,
) -> gpd.GeoDataFrame:
    """Extract DFCI tracks from IGN BD TOPO WFS.

    Uses parallel page fetches (4 concurrent) for ~3x speedup vs sequential.

    Args:
        bbox: Optional (lon_min, lat_min, lon_max, lat_max) in WGS84 (EPSG:4326).
              None = France entière (~160K features).
        include_categorie: Also fetch roads with categorie_dfci set but
            piste_dfci not true (adds ~11K Alps/Isère/Savoie roads).

    Returns:
        GeoDataFrame in EPSG:4326 with DFCI attributes and LineString geometry.
    """
    cql_filter = _build_cql_filter(include_categorie=include_categorie)
    logger.info("CQL filter: %s", cql_filter)
    if bbox:
        logger.info("Client-side bbox filter: %s (applied after download)", bbox)

    session = requests.Session()
    session.headers["User-Agent"] = "CheminsCommuns/DFCI-extract/1.0"

    # First page: get total count
    t0 = time.monotonic()
    data = _fetch_page(session, cql_filter, 0)
    total_matched = data.get("totalFeatures", data.get("numberMatched"))
    first_features = data.get("features", [])
    logger.info(
        "Total DFCI features matched: %s (first page: %d, %.1fs)",
        total_matched or "unknown",
        len(first_features),
        time.monotonic() - t0,
    )

    if not first_features or not total_matched:
        all_features = first_features
    else:
        all_features = list(first_features)

        # Build list of remaining page offsets
        offsets = list(range(PAGE_SIZE, total_matched, PAGE_SIZE))
        if offsets:
            logger.info(
                "Fetching %d remaining pages (%d parallel)...",
                len(offsets),
                MAX_PARALLEL,
            )

            def fetch_offset(offset: int) -> list[dict]:
                d = _fetch_page(session, cql_filter, offset)
                feats = d.get("features", [])
                logger.info(
                    "  page %d/%d: %d features",
                    offset // PAGE_SIZE + 1,
                    (total_matched + PAGE_SIZE - 1) // PAGE_SIZE,
                    len(feats),
                )
                return feats

            with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
                results = pool.map(fetch_offset, offsets)

            for page_features in results:
                all_features.extend(page_features)

    if not all_features:
        logger.warning("No DFCI features found.")
        return gpd.GeoDataFrame()

    # Parse into GeoDataFrame
    rows = []
    for feat in all_features:
        geom_raw = feat.get("geometry")
        if not geom_raw:
            continue

        geom = shape(geom_raw)
        props = feat.get("properties", {})

        row: dict = {"geometry": geom}

        # DFCI attributes
        for ign_name, out_name in DFCI_ATTRS.items():
            row[out_name] = props.get(ign_name)

        # Context attributes
        for ign_name, out_name in CONTEXT_ATTRS.items():
            row[out_name] = props.get(ign_name)

        rows.append(row)

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    logger.info(
        "GeoDataFrame: %d pistes DFCI (France entière), bounds: %s",
        len(gdf),
        gdf.total_bounds.tolist() if len(gdf) > 0 else "empty",
    )

    # Client-side bbox filter (IGN WFS CQL BBOX is unreliable)
    if bbox and len(gdf) > 0:
        lon_min, lat_min, lon_max, lat_max = bbox
        bbox_geom = box(lon_min, lat_min, lon_max, lat_max)
        gdf = gdf[gdf.intersects(bbox_geom)].copy()
        logger.info("After bbox filter: %d pistes DFCI", len(gdf))

    return gdf


def to_routing_edges(gdf: gpd.GeoDataFrame) -> list[dict]:
    """Convert GeoDataFrame to edge dicts compatible with Common Trails routing.

    Output format matches store_dfci_edges() in backend/app/services/ingest.py.
    """
    edges = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue

        coords = list(geom.coords)
        if len(coords) < 2:
            continue

        # Map nature_detail_dfci → OSM-like surface
        nature = str(row.get("nature_detail_dfci") or "").lower()
        surface_map = {
            "sol naturel": "dirt",
            "revêtue": "asphalt",
            "revetue": "asphalt",
            "empierrée": "gravel",
            "empierree": "gravel",
            "stabilisée": "compacted",
            "stabilisee": "compacted",
        }
        surface = "dirt"  # default for DFCI
        for key, val in surface_map.items():
            if key in nature:
                surface = val
                break

        edges.append({
            "geometry": {
                "type": "LineString",
                "coordinates": [[c[0], c[1]] for c in coords],
            },
            "surface": surface,
            "highway": "track",
            "ref": row.get("cleabs", ""),
            "user_count": 0,
            "pass_count": 0,
            "trail_network": True,
            "trail_type": "DFCI",
        })

    return edges


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract DFCI tracks from IGN BD TOPO WFS (Géoplateforme)",
    )
    parser.add_argument(
        "--bbox",
        type=str,
        default=None,
        help=(
            "Bounding box: lon_min,lat_min,lon_max,lat_max (WGS84). "
            "Ex: 3.3,43.2,4.2,44.0 (Hérault). "
            "Presets: " + ", ".join(BBOX_EXAMPLES.keys())
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default="pistes_dfci.geojson",
        help="Output GeoJSON file (default: pistes_dfci.geojson)",
    )
    parser.add_argument(
        "--edges-cache",
        type=str,
        default=None,
        help=(
            "Export routing edges as JSON cache (loadable by backend startup). "
            "Ex: --edges-cache backend/data/dfci_ign_edges.json"
        ),
    )
    parser.add_argument(
        "--include-categorie",
        action="store_true",
        help="Also fetch roads with categorie_dfci (not piste_dfci) — adds ~11K Alps DFCI roads",
    )
    parser.add_argument(
        "--inject",
        action="store_true",
        help="POST edges to Common Trails API (localhost:8787/heatmap/dfci)",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default="http://localhost:8787",
        help="Common Trails API URL for --inject",
    )
    args = parser.parse_args()

    bbox = None
    if args.bbox:
        # Check presets first
        if args.bbox in BBOX_EXAMPLES:
            bbox = BBOX_EXAMPLES[args.bbox]
            logger.info("Using preset bbox '%s': %s", args.bbox, bbox)
        else:
            try:
                parts = [float(x.strip()) for x in args.bbox.split(",")]
                if len(parts) != 4:
                    raise ValueError
                bbox = (parts[0], parts[1], parts[2], parts[3])
            except (ValueError, IndexError):
                logger.error(
                    "Invalid --bbox format. Expected: lon_min,lat_min,lon_max,lat_max "
                    "or a preset name: %s",
                    ", ".join(BBOX_EXAMPLES.keys()),
                )
                sys.exit(1)

    # Extract
    gdf = extract_dfci_pistes(bbox=bbox, include_categorie=args.include_categorie)
    if gdf.empty:
        logger.error("No DFCI features extracted.")
        sys.exit(1)

    # Export GeoJSON
    output_path = Path(args.output)
    gdf.to_file(output_path, driver="GeoJSON")
    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info("Exported %d features to %s (%.1f MB)", len(gdf), output_path, size_mb)

    # Export routing edges cache (JSON, loadable at backend startup)
    edges = to_routing_edges(gdf)
    if args.edges_cache:
        import json
        cache_path = Path(args.edges_cache)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(edges, f, separators=(",", ":"))
        cache_mb = cache_path.stat().st_size / (1024 * 1024)
        logger.info(
            "Exported %d routing edges to %s (%.1f MB) — loadable by backend startup",
            len(edges),
            cache_path,
            cache_mb,
        )

    # Inject into routing engine
    if args.inject:
        logger.info("Injecting %d DFCI edges into %s ...", len(edges), args.api_url)

        # POST in batches (API might choke on 160K features at once)
        batch_size = 2000
        total_seeded = 0
        for i in range(0, len(edges), batch_size):
            batch = edges[i : i + batch_size]
            try:
                resp = requests.post(
                    f"{args.api_url}/heatmap/dfci",
                    json=batch,
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                total_seeded += data.get("seeded", 0)
                logger.info(
                    "  batch %d-%d: seeded %d",
                    i,
                    i + len(batch),
                    data.get("seeded", 0),
                )
            except requests.RequestException as exc:
                logger.error("  batch %d failed: %s", i, exc)

        logger.info("Total DFCI edges injected: %d", total_seeded)


if __name__ == "__main__":
    main()
