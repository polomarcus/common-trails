#!/usr/bin/env python3
"""Import DFCI references from open data into OpenStreetMap.

Matches departmental DFCI tracks to existing OSM ways using geometric
similarity (Hausdorff distance + overlap ratio), then tags matched ways
with `ref:FR:DFCI`.

Supports multiple data sources:
  - herault (default): Hérault open data API (data.herault.fr)
  - gard-cd30: Gard CD30 from local dfci_ign_edges.json cache
  - cache:<path>: arbitrary local JSON cache file

Usage:
    # Dry-run on Hérault (default source)
    python scripts/osm_dfci_import.py --dry-run --bbox 3.3,43.3,4.2,43.9

    # Dry-run on Gard CD30, limited to 10 tracks
    python scripts/osm_dfci_import.py --source gard-cd30 --dry-run --limit 10

    # Apply changes (requires OSM OAuth2 credentials)
    python scripts/osm_dfci_import.py --source gard-cd30 --apply --limit 10

Requirements:
    pip install httpx shapely pyproj osmapi

Sources:
  - Hérault: https://www.herault-data.fr/explore/dataset/pistes-dfci-herault/
  - Gard CD30: opendfci.fr (Licence Ouverte v2.0)

See scripts/osm_dfci_import_wiki.md for the import documentation template.
"""

import argparse
import csv
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path

import httpx
from pyproj import Transformer
from shapely.geometry import LineString
from shapely.ops import transform

logger = logging.getLogger(__name__)

# ── Hérault Data API ──────────────────────────────────────────────────────

_API_BASE = (
    "https://www.herault-data.fr/api/explore/v2.1"
    "/catalog/datasets/pistes-dfci-herault/records"
)
_PAGE_SIZE = 100
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# ── Projection helpers ────────────────────────────────────────────────────

# WGS84 → Lambert-93 (EPSG:2154) for metric distance calculations in France
_to_metric = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)
_to_wgs84 = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)


def _project_to_metric(geom: LineString) -> LineString:
    """Project a WGS84 LineString to Lambert-93 (metres)."""
    return transform(_to_metric.transform, geom)


# ── Geometric matching ────────────────────────────────────────────────────


def hausdorff_match(
    dfci_geom: LineString,
    osm_geom: LineString,
    directed: bool = False,
) -> float:
    """Hausdorff distance in metres between two LineStrings.

    Both geometries must be in WGS84 (lon, lat). They are projected to
    Lambert-93 (EPSG:2154) for metric calculation.

    If directed=True, computes one-directional Hausdorff (DFCI→OSM):
    max distance from any DFCI point to the nearest point on the OSM way.
    This is tolerant of OSM ways that extend beyond the DFCI edge.
    """
    a = _project_to_metric(dfci_geom)
    b = _project_to_metric(osm_geom)
    if directed:
        from shapely.geometry import Point

        return max(b.distance(Point(p)) for p in a.coords)
    return a.hausdorff_distance(b)


def overlap_ratio(
    dfci_geom: LineString,
    osm_geom: LineString,
    buffer_m: float = 30.0,
) -> float:
    """Proportion of the DFCI geometry covered by a buffer around the OSM way.

    Returns a float between 0.0 and 1.0.
    """
    a = _project_to_metric(dfci_geom)
    b = _project_to_metric(osm_geom)
    buffered = b.buffer(buffer_m)
    intersection = a.intersection(buffered)
    if a.length == 0:
        return 0.0
    return intersection.length / a.length


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class DfciRecord:
    """A DFCI track from a departmental dataset."""

    ref: str
    geometry: LineString  # WGS84
    statut: str


# ── Source presets ────────────────────────────────────────────────────────

_SOURCE_PRESETS = {
    "herault": {
        "changeset_comment": (
            "Ajout ref:FR:DFCI depuis données ouvertes Hérault (data.herault.fr)"
        ),
        "changeset_source": "data.herault.fr (Licence Ouverte v2.0)",
        "tag_source": "data.herault.fr",
        "bbox": (3.3, 43.3, 4.2, 43.9),
        "directed_hausdorff": True,
        "hausdorff_threshold_m": 50.0,
        "overlap_threshold": 0.6,
    },
    "gard-cd30": {
        "changeset_comment": (
            "Ajout ref:FR:DFCI depuis données ouvertes Gard CD30 (opendfci.fr)"
        ),
        "changeset_source": "CD30 open data, opendfci.fr (Licence Ouverte v2.0)",
        "tag_source": "CD30 open data (opendfci.fr)",
        "bbox": (3.2, 43.4, 4.7, 44.5),
        # IGN edges are short segments; OSM ways extend beyond → use directed
        # Hausdorff (DFCI→OSM) with relaxed threshold
        "directed_hausdorff": True,
        "hausdorff_threshold_m": 50.0,
        "overlap_threshold": 0.6,
    },
}


@dataclass
class MatchResult:
    """Result of matching a single DFCI track to OSM."""

    dfci_ref: str
    status: str  # "matched", "already_tagged", "no_match", "ambiguous"
    osm_way_id: int | None = None
    hausdorff_m: float | None = None
    overlap_pct: float | None = None
    existing_tag: str | None = None


@dataclass
class ImportReport:
    """Aggregated import statistics."""

    results: list[MatchResult] = field(default_factory=list)

    @property
    def matched(self) -> int:
        return sum(1 for r in self.results if r.status == "matched")

    @property
    def already_tagged(self) -> int:
        return sum(1 for r in self.results if r.status == "already_tagged")

    @property
    def no_match(self) -> int:
        return sum(1 for r in self.results if r.status == "no_match")

    @property
    def ambiguous(self) -> int:
        return sum(1 for r in self.results if r.status == "ambiguous")

    def to_csv(self) -> str:
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "dfci_ref",
            "status",
            "osm_way_id",
            "hausdorff_m",
            "overlap_pct",
            "existing_tag",
        ])
        for r in self.results:
            writer.writerow([
                r.dfci_ref,
                r.status,
                r.osm_way_id or "",
                f"{r.hausdorff_m:.1f}" if r.hausdorff_m is not None else "",
                f"{r.overlap_pct:.1f}" if r.overlap_pct is not None else "",
                r.existing_tag or "",
            ])
        return output.getvalue()

    def summary(self) -> str:
        total = len(self.results)
        return (
            f"Total: {total} | "
            f"Matched: {self.matched} | "
            f"Already tagged: {self.already_tagged} | "
            f"No match: {self.no_match} | "
            f"Ambiguous: {self.ambiguous}"
        )


# ── Hérault Data API ──────────────────────────────────────────────────────


def fetch_dfci_records(
    bbox: tuple[float, float, float, float] | None = None,
) -> list[DfciRecord]:
    """Fetch DFCI records from Hérault Data API (synchronous)."""
    records: list[DfciRecord] = []
    offset = 0

    with httpx.Client(timeout=30.0) as client:
        while True:
            params: dict = {"limit": _PAGE_SIZE, "offset": offset}
            resp = client.get(_API_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()

            page = data.get("results") or data.get("records") or []
            if not page:
                break

            for rec in page:
                dfci = _parse_record(rec, bbox)
                if dfci:
                    records.append(dfci)

            total = data.get("total_count", len(records))
            offset += _PAGE_SIZE
            if offset >= total:
                break

    return records


def _parse_record(
    rec: dict,
    bbox: tuple[float, float, float, float] | None = None,
) -> DfciRecord | None:
    """Parse a single API record. Returns None if invalid or outside bbox."""
    geo = rec.get("geo_shape")
    if not geo:
        return None

    # Handle GeoJSON Feature wrapper (API v2.1 returns Feature, not bare geometry)
    if geo.get("type") == "Feature":
        geo = geo.get("geometry", {})

    geom_type = geo.get("type", "")
    coords_raw = geo.get("coordinates")
    if not coords_raw:
        return None

    if geom_type == "MultiLineString":
        coords = []
        for line in coords_raw:
            coords.extend(line)
    elif geom_type == "LineString":
        coords = coords_raw
    else:
        return None

    if len(coords) < 2:
        return None

    # Bbox filter (west, south, east, north)
    if bbox:
        west, south, east, north = bbox
        in_bbox = any(
            west <= c[0] <= east and south <= c[1] <= north for c in coords
        )
        if not in_bbox:
            return None

    ref = str(rec.get("n_dfci_1") or rec.get("n_dfci_2") or "")
    statut = str(rec.get("statut_operationnel") or "")
    geom = LineString([(c[0], c[1]) for c in coords])

    return DfciRecord(ref=ref, geometry=geom, statut=statut)


# ── Local JSON cache source ──────────────────────────────────────────────

_DEFAULT_CACHE_PATH = (
    Path(__file__).resolve().parent.parent
    / "backend"
    / "app"
    / "data"
    / "dfci_ign_edges.json"
)


def fetch_dfci_from_cache(
    path: Path | str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> list[DfciRecord]:
    """Load DFCI records from a local JSON cache file.

    Filters out entries with IGN internal refs (TRONROUT*) and applies
    optional bbox filtering.
    """
    cache_path = Path(path) if path else _DEFAULT_CACHE_PATH
    logger.info("Loading DFCI records from %s ...", cache_path)

    with open(cache_path, encoding="utf-8") as f:
        data = json.load(f)

    records: list[DfciRecord] = []
    for entry in data:
        ref = entry.get("ref", "")
        if not ref or ref.startswith("TRONROUT"):
            continue
        # Filter invalid refs (nan, nan/nan, empty-looking)
        if ref.lower() in ("nan", "nan/nan", "none", ""):
            continue

        geom_data = entry.get("geometry", {})
        coords_raw = geom_data.get("coordinates", [])
        geom_type = geom_data.get("type", "")

        if geom_type == "MultiLineString":
            coords = []
            for line in coords_raw:
                coords.extend(line)
        elif geom_type == "LineString":
            coords = coords_raw
        else:
            continue

        if len(coords) < 2:
            continue

        # Bbox filter
        if bbox:
            west, south, east, north = bbox
            in_bbox = any(
                west <= c[0] <= east and south <= c[1] <= north for c in coords
            )
            if not in_bbox:
                continue

        geom = LineString([(c[0], c[1]) for c in coords])
        statut = entry.get("surface", "")
        records.append(DfciRecord(ref=ref, geometry=geom, statut=statut))

    logger.info("Loaded %d DFCI records (filtered from %d total)", len(records), len(data))
    return records


# ── Overpass query ────────────────────────────────────────────────────────


def query_osm_candidates(
    dfci_geom: LineString,
    around_m: float = 50.0,
) -> list[dict]:
    """Query Overpass for highway ways near the DFCI geometry.

    Returns list of {id, tags, geometry} dicts.
    """
    # Sample points along the geometry for the around filter
    coords = list(dfci_geom.coords)
    # Use first, middle, last points
    sample_points = [coords[0], coords[len(coords) // 2], coords[-1]]
    # Build around filter: lat,lon pairs (Overpass uses lat,lon)
    around_parts = []
    for lon, lat in sample_points:
        around_parts.append(f"{lat},{lon}")
    around_str = ",".join(around_parts)

    query = (
        f"[out:json][timeout:30];"
        f'way["highway"](around:{around_m},{around_str});'
        f"(._;>;);"
        f"out body;"
    )

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(_OVERPASS_URL, data={"data": query})
        resp.raise_for_status()
        data = resp.json()

    # Build node lookup
    nodes: dict[int, tuple[float, float]] = {}
    for el in data.get("elements", []):
        if el.get("type") == "node":
            nodes[el["id"]] = (el["lon"], el["lat"])

    # Build way geometries
    ways = []
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        way_coords = []
        for nid in el.get("nodes", []):
            if nid in nodes:
                way_coords.append(nodes[nid])
        if len(way_coords) < 2:
            continue
        ways.append({
            "id": el["id"],
            "tags": el.get("tags", {}),
            "geometry": LineString(way_coords),
        })

    return ways


# ── Matching pipeline ─────────────────────────────────────────────────────


def match_dfci_to_osm(
    dfci: DfciRecord,
    candidates: list[dict],
    hausdorff_threshold_m: float = 30.0,
    overlap_threshold: float = 0.7,
    directed_hausdorff: bool = False,
) -> MatchResult:
    """Match a single DFCI track to the best OSM candidate."""
    if not candidates:
        return MatchResult(dfci_ref=dfci.ref, status="no_match")

    best_match = None
    best_score = float("inf")

    for cand in candidates:
        h_dist = hausdorff_match(dfci.geometry, cand["geometry"], directed=directed_hausdorff)
        o_ratio = overlap_ratio(dfci.geometry, cand["geometry"])

        if h_dist < hausdorff_threshold_m and o_ratio >= overlap_threshold:
            if h_dist < best_score:
                best_score = h_dist
                best_match = (cand, h_dist, o_ratio)

    if best_match is None:
        return MatchResult(dfci_ref=dfci.ref, status="no_match")

    cand, h_dist, o_ratio = best_match
    existing = cand["tags"].get("ref:FR:DFCI", "")

    if existing:
        return MatchResult(
            dfci_ref=dfci.ref,
            status="already_tagged",
            osm_way_id=cand["id"],
            hausdorff_m=h_dist,
            overlap_pct=o_ratio * 100,
            existing_tag=existing,
        )

    return MatchResult(
        dfci_ref=dfci.ref,
        status="matched",
        osm_way_id=cand["id"],
        hausdorff_m=h_dist,
        overlap_pct=o_ratio * 100,
    )


# ── OSM API apply ────────────────────────────────────────────────────────


def _build_oauth2_session(client_id: str, client_secret: str, auth_code: str = ""):
    """Build an OAuth2-authenticated requests.Session for OSM API.

    Uses the out-of-band (OOB) flow: prints an authorization URL,
    the user opens it in their browser, authorizes, and pastes back the code.
    If auth_code is provided, skips the interactive prompt.
    """
    from requests_oauthlib import OAuth2Session

    authorize_url = "https://www.openstreetmap.org/oauth2/authorize"
    token_url = "https://www.openstreetmap.org/oauth2/token"
    redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
    scope = ["read_prefs", "write_api"]

    oauth = OAuth2Session(client_id, redirect_uri=redirect_uri, scope=scope)
    auth_url, state = oauth.authorization_url(authorize_url)

    if not auth_code:
        print(f"\n{'='*60}")
        print("Open this URL in your browser to authorize:")
        print(f"\n  {auth_url}\n")
        print("After authorizing, paste the code below.")
        print(f"{'='*60}\n")

        auth_code = input("Authorization code: ").strip()

    # OSM CDN (Varnish) blocks requests without a User-Agent
    oauth.headers.update({
        'User-Agent': 'common-trails-dfci-import/1.0 (+https://github.com/polomarcus/common-trails)',
    })
    token = oauth.fetch_token(
        token_url,
        code=auth_code,
        client_secret=client_secret,
    )
    logger.info("OAuth2 token obtained (expires: %s)", token.get("expires_at", "?"))
    return oauth


def apply_tags(
    results: list[MatchResult],
    changeset_comment: str = "",
    changeset_source: str = "",
    tag_source: str = "",
    osm_user: str = "",
    osm_password: str = "",
    oauth2_session=None,
) -> int:
    """Apply ref:FR:DFCI tags to matched OSM ways.

    Returns the number of ways tagged.
    """
    try:
        import osmapi
    except ImportError:
        logger.error("osmapi not installed. Run: pip install osmapi>=5.0")
        return 0

    if oauth2_session:
        api = osmapi.OsmApi(session=oauth2_session)
    elif osm_user and osm_password:
        api = osmapi.OsmApi(username=osm_user, password=osm_password)
    else:
        logger.error("No authentication provided (need OAuth2 or user/pass)")
        return 0

    matched = [r for r in results if r.status == "matched" and r.osm_way_id]
    if not matched:
        logger.info("No ways to tag.")
        return 0

    comment = changeset_comment or "Ajout ref:FR:DFCI depuis données ouvertes"
    source = changeset_source or "open data (Licence Ouverte v2.0)"
    src_tag = tag_source or "open data"

    # Process in changesets of ~50 ways
    chunk_size = 50
    tagged = 0

    for i in range(0, len(matched), chunk_size):
        chunk = matched[i : i + chunk_size]
        api.ChangesetCreate({
            "comment": comment,
            "source": source,
            "import": "yes",
        })

        for result in chunk:
            try:
                way = api.WayGet(result.osm_way_id)
                way["tag"]["ref:FR:DFCI"] = result.dfci_ref
                way["tag"]["source:ref:FR:DFCI"] = src_tag
                api.WayUpdate(way)
                tagged += 1
            except Exception:
                logger.warning(
                    "Failed to tag way %s", result.osm_way_id, exc_info=True
                )

        api.ChangesetClose()
        # Rate limit: wait between changesets
        if i + chunk_size < len(matched):
            time.sleep(2)

    return tagged


# ── Main ──────────────────────────────────────────────────────────────────


def run(
    source: str = "herault",
    bbox: tuple[float, float, float, float] | None = None,
    limit: int = 0,
    offset: int = 0,
    apply: bool = False,
    output_file: str | None = None,
    osm_user: str = "",
    osm_password: str = "",
    client_id: str = "",
    client_secret: str = "",
    auth_code: str = "",
    changeset_comment: str = "",
    changeset_source: str = "",
    tag_source: str = "",
) -> ImportReport:
    """Run the full DFCI → OSM matching pipeline."""
    report = ImportReport()

    # Resolve source preset
    preset = _SOURCE_PRESETS.get(source, {})
    cs_comment = changeset_comment or preset.get("changeset_comment", "")
    cs_source = changeset_source or preset.get("changeset_source", "")
    t_source = tag_source or preset.get("tag_source", "")
    default_bbox = preset.get("bbox")
    directed_h = preset.get("directed_hausdorff", False)
    h_threshold = preset.get("hausdorff_threshold_m", 30.0)
    o_threshold = preset.get("overlap_threshold", 0.7)

    effective_bbox = bbox or default_bbox

    # 0. Authenticate early (before Overpass queries) so OAuth code doesn't expire
    _oauth_session = None
    if apply and client_id and client_secret:
        logger.info("Authenticating with OSM OAuth2...")
        _oauth_session = _build_oauth2_session(client_id, client_secret, auth_code)
        logger.info("OAuth2 authenticated successfully")

    # 1. Fetch DFCI records
    if source == "herault":
        logger.info("Fetching DFCI records from Hérault Data API...")
        dfci_records = fetch_dfci_records(bbox=effective_bbox)
    elif source == "gard-cd30":
        dfci_records = fetch_dfci_from_cache(
            path=_DEFAULT_CACHE_PATH, bbox=effective_bbox,
        )
    elif source.startswith("cache:"):
        cache_path = source[len("cache:"):]
        dfci_records = fetch_dfci_from_cache(
            path=cache_path, bbox=effective_bbox,
        )
    else:
        logger.error("Unknown source: %s", source)
        return report

    logger.info("Fetched %d DFCI records", len(dfci_records))

    if offset > 0:
        dfci_records = dfci_records[offset:]
        logger.info("Skipped first %d records (offset)", offset)

    if limit > 0:
        dfci_records = dfci_records[:limit]
        logger.info("Limited to %d records", len(dfci_records))

    # 2. Process each record (with retry on 429 and dedup)
    #    In --apply mode, tag matches immediately (incremental apply).
    seen_way_ids: dict[int, str] = {}  # way_id → first ref that matched it
    retry_wait = 2  # seconds, doubles on 429

    # Incremental apply: open OSM API + changeset upfront
    _osm_api = None
    _changeset_open = False
    _changeset_tagged = 0  # ways tagged in current changeset
    _total_tagged = 0
    _CHANGESET_CHUNK = 50  # close/reopen changeset every N ways

    if apply and (_oauth_session or osm_user):
        try:
            import osmapi
            if _oauth_session:
                _osm_api = osmapi.OsmApi(session=_oauth_session)
            else:
                _osm_api = osmapi.OsmApi(username=osm_user, password=osm_password)
        except ImportError:
            logger.error("osmapi not installed. Run: pip install osmapi>=5.0")
        except Exception:
            logger.error("Failed to init OSM API", exc_info=True)

    def _ensure_changeset():
        nonlocal _changeset_open, _changeset_tagged
        if _osm_api and not _changeset_open:
            _osm_api.changeset_create({
                "comment": cs_comment or "Ajout ref:FR:DFCI depuis données ouvertes",
                "source": cs_source or "open data (Licence Ouverte v2.0)",
                "import": "yes",
            })
            _changeset_open = True
            _changeset_tagged = 0

    def _close_changeset_if_needed(force=False):
        nonlocal _changeset_open, _changeset_tagged
        if _osm_api and _changeset_open and (force or _changeset_tagged >= _CHANGESET_CHUNK):
            _osm_api.changeset_close()
            _changeset_open = False
            logger.info("Changeset closed (%d ways tagged in this batch)", _changeset_tagged)

    def _tag_way_now(result: MatchResult):
        nonlocal _changeset_tagged, _total_tagged
        if not _osm_api or not result.osm_way_id:
            return
        try:
            _ensure_changeset()
            way = _osm_api.WayGet(result.osm_way_id)
            way["tag"]["ref:FR:DFCI"] = result.dfci_ref
            way["tag"]["source:ref:FR:DFCI"] = t_source or "open data"
            _osm_api.WayUpdate(way)
            _changeset_tagged += 1
            _total_tagged += 1
            logger.info("✓ Tagged way %s with ref:FR:DFCI=%s (%d total)",
                        result.osm_way_id, result.dfci_ref, _total_tagged)
            _close_changeset_if_needed()
        except Exception:
            logger.warning("Failed to tag way %s", result.osm_way_id, exc_info=True)

    for i, dfci in enumerate(dfci_records):
        if i % 50 == 0 and i > 0:
            logger.info("Processing %d/%d... (%d tagged so far)", i, len(dfci_records), _total_tagged)

        # Rate limit: sleep between every request to avoid 429
        if i > 0:
            time.sleep(retry_wait)

        logger.debug("[%d/%d] Querying OSM for ref=%s", i + 1, len(dfci_records), dfci.ref)

        candidates = None
        for attempt in range(3):
            try:
                candidates = query_osm_candidates(dfci.geometry)
                retry_wait = 2  # reset on success
                break
            except Exception as exc:
                if "429" in str(exc) or "504" in str(exc):
                    wait = retry_wait * (2 ** attempt)
                    logger.info("Rate limited, waiting %ds (attempt %d/3)...", wait, attempt + 1)
                    time.sleep(wait)
                else:
                    logger.warning("Overpass query failed for %s", dfci.ref, exc_info=True)
                    break

        if candidates is None:
            report.results.append(
                MatchResult(dfci_ref=dfci.ref, status="no_match")
            )
            continue

        result = match_dfci_to_osm(
            dfci, candidates,
            hausdorff_threshold_m=h_threshold,
            overlap_threshold=o_threshold,
            directed_hausdorff=directed_h,
        )

        # Dedup: skip if this way was already matched by another ref
        if result.status == "matched" and result.osm_way_id:
            if result.osm_way_id in seen_way_ids:
                prev_ref = seen_way_ids[result.osm_way_id]
                logger.info("Skipping duplicate: way %s already matched by %s (now %s)",
                            result.osm_way_id, prev_ref, result.dfci_ref)
                result = MatchResult(dfci_ref=dfci.ref, status="no_match")
            else:
                seen_way_ids[result.osm_way_id] = result.dfci_ref

        report.results.append(result)
        logger.debug("  → %s (way=%s, hausdorff=%.1fm, overlap=%.0f%%)",
                      result.status,
                      result.osm_way_id or "-",
                      result.hausdorff_m or 0,
                      result.overlap_pct or 0)

        # Incremental apply: tag immediately on match
        if apply and result.status == "matched" and _osm_api:
            _tag_way_now(result)

        # Periodic CSV save (every 100 records)
        if output_file and i > 0 and i % 100 == 0:
            Path(output_file).write_text(report.to_csv(), encoding="utf-8")

    # Close any remaining open changeset
    _close_changeset_if_needed(force=True)

    # 3. Report
    logger.info(report.summary())
    if _total_tagged:
        logger.info("Total tagged this run: %d ways", _total_tagged)

    if output_file:
        Path(output_file).write_text(report.to_csv(), encoding="utf-8")
        logger.info("Report written to %s", output_file)

    # 4. Batch apply (only if incremental didn't run — legacy mode)
    if apply and _osm_api is None:
        if not osm_user:
            logger.error("Need --client-id/--client-secret (OAuth2) or --osm-user/--osm-password")
            return report
        tagged = apply_tags(
            report.results,
            changeset_comment=cs_comment,
            changeset_source=cs_source,
            tag_source=t_source,
            osm_user=osm_user,
            osm_password=osm_password,
            oauth2_session=_oauth_session,
        )
        logger.info("Tagged %d OSM ways", tagged)

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Import DFCI references from open data into OSM.",
        epilog="See scripts/osm_dfci_import_wiki.md for documentation.",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="herault",
        help=(
            "Data source: herault (API), gard-cd30 (local cache), "
            "or cache:<path> (arbitrary JSON). Default: herault"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Generate report without modifying OSM (default)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply tag changes to OSM (requires --osm-user/--osm-password)",
    )
    parser.add_argument(
        "--bbox",
        type=str,
        help="Bounding box: west,south,east,north (overrides source default)",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip first N DFCI records (resume from where you left off)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of DFCI records to process (0 = no limit)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="dfci_import_report.csv",
        help="Output CSV report path (default: dfci_import_report.csv)",
    )
    parser.add_argument(
        "--changeset-comment",
        type=str,
        default="",
        help="Override changeset comment (default: per-source preset)",
    )
    parser.add_argument(
        "--changeset-source",
        type=str,
        default="",
        help="Override changeset source tag (default: per-source preset)",
    )
    parser.add_argument(
        "--client-id",
        type=str,
        default="",
        help="OSM OAuth2 client ID (for --apply)",
    )
    parser.add_argument(
        "--client-secret",
        type=str,
        default="",
        help="OSM OAuth2 client secret (for --apply)",
    )
    parser.add_argument(
        "--auth-code",
        type=str,
        default="",
        help="OSM OAuth2 authorization code (skip interactive prompt)",
    )
    parser.add_argument(
        "--osm-user",
        type=str,
        default="",
        help="OSM username (legacy basic auth, deprecated)",
    )
    parser.add_argument(
        "--osm-password",
        type=str,
        default="",
        help="OSM password (legacy basic auth, deprecated)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    bbox = None
    if args.bbox:
        parts = [float(x) for x in args.bbox.split(",")]
        if len(parts) != 4:
            parser.error("--bbox must have 4 values: west,south,east,north")
        bbox = (parts[0], parts[1], parts[2], parts[3])

    report = run(
        source=args.source,
        bbox=bbox,
        limit=args.limit,
        offset=args.offset,
        apply=args.apply,
        output_file=args.output,
        osm_user=args.osm_user,
        osm_password=args.osm_password,
        client_id=args.client_id,
        client_secret=args.client_secret,
        auth_code=args.auth_code,
        changeset_comment=args.changeset_comment,
        changeset_source=args.changeset_source,
    )

    print(report.summary())
    if not args.apply:
        print("(Dry-run mode — no changes applied to OSM)")


if __name__ == "__main__":
    main()
