"""Import OSM road segments from a Geofabrik PBF extract.

Downloads a regional PBF file, filters routable highways, and bulk-inserts
node-to-node segments into the region's ``osm_road_edges`` partition. No
Overpass API dependency.

## Storage model (post-0056 substrate slim)

- ``osm_road_edges`` is partitioned ``BY LIST (region)``. Each import
  creates the region's partition on demand, TRUNCATEs it (idempotent
  reimport — also fixes the historical stale-tile-accumulation limitation:
  tiles absent from a newer PBF no longer survive), and streams the new
  rows in. Region removal is ``DROP TABLE osm_road_edges_<region>`` O(1).
- The full way polyline goes to the ``osm_ways`` side-table ONCE per way
  (pre-0056 it was duplicated onto every segment row = ×13.5 = 15 GB).
  Boundary ways present in two regional PBFs are upserted — last import
  wins (identical geometry either way).
- ``tile_key`` is a BIGINT (``x*100000+y`` at z14 — see
  ``app.services.tile_keys``). Boundary tiles of NEIGHBOUR regions are
  lazily cleared during the stream (``DELETE ... WHERE tile_key = ANY``
  ``AND region <> :region``) so a tile is always owned by its most recent
  importer — no cross-partition duplicates.
- After a successful import the guard records ``(region, imported_at,
  row_count)`` in ``osm_import_meta`` and FAILS LOUDLY (non-zero exit)
  when the row count is implausible (see ``_run_import_guard``).

## Memory model

The original implementation accumulated every segment in a Python list
before INSERTing — which OOMed at 16.7 GB on `france-south.osm.pbf`
(2.5 GB PBF → 9 M ways → ~50 M segments × WKT strings + Python overhead).

This version streams: the osmium `way()` callback appends to a small
in-memory buffer (default 50k segments ≈ 10 MB) and flushes to DB
when full. Memory stays bounded at ~30 MB regardless of PBF size.

Usage:
    python -m app.cli.import_osm_roads occitanie [--no-download]
    python -m app.cli.import_osm_roads france
    python -m app.cli.import_osm_roads /path/to/file.osm.pbf --region my-region
    python -m app.cli.import_osm_roads /path/to/file.osm.pbf --batch-size 100000
"""
import argparse
import logging
import os
import re
import sys
import time

import osmium
from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Region → Geofabrik URL mapping ──────────────────────────────────────────

REGIONS = {
    # France by region (full France PBF too large for in-memory parsing)
    "occitanie": "https://download.geofabrik.de/europe/france/languedoc-roussillon-latest.osm.pbf",
    "paca": "https://download.geofabrik.de/europe/france/provence-alpes-cote-d-azur-latest.osm.pbf",
    "ara": "https://download.geofabrik.de/europe/france/rhone-alpes-latest.osm.pbf",
    "auvergne": "https://download.geofabrik.de/europe/france/auvergne-latest.osm.pbf",
    "midi-pyrenees": "https://download.geofabrik.de/europe/france/midi-pyrenees-latest.osm.pbf",
    "aquitaine": "https://download.geofabrik.de/europe/france/aquitaine-latest.osm.pbf",
    "ile-de-france": "https://download.geofabrik.de/europe/france/ile-de-france-latest.osm.pbf",
    "bretagne": "https://download.geofabrik.de/europe/france/bretagne-latest.osm.pbf",
    "normandie": "https://download.geofabrik.de/europe/france/basse-normandie-latest.osm.pbf",
    "haute-normandie": "https://download.geofabrik.de/europe/france/haute-normandie-latest.osm.pbf",
    "centre": "https://download.geofabrik.de/europe/france/centre-latest.osm.pbf",
    "bourgogne": "https://download.geofabrik.de/europe/france/bourgogne-latest.osm.pbf",
    "franche-comte": "https://download.geofabrik.de/europe/france/franche-comte-latest.osm.pbf",
    "alsace": "https://download.geofabrik.de/europe/france/alsace-latest.osm.pbf",
    "lorraine": "https://download.geofabrik.de/europe/france/lorraine-latest.osm.pbf",
    "champagne-ardenne": "https://download.geofabrik.de/europe/france/champagne-ardenne-latest.osm.pbf",
    "picardie": "https://download.geofabrik.de/europe/france/picardie-latest.osm.pbf",
    "nord-pas-de-calais": "https://download.geofabrik.de/europe/france/nord-pas-de-calais-latest.osm.pbf",
    "pays-de-la-loire": "https://download.geofabrik.de/europe/france/pays-de-la-loire-latest.osm.pbf",
    "poitou-charentes": "https://download.geofabrik.de/europe/france/poitou-charentes-latest.osm.pbf",
    "limousin": "https://download.geofabrik.de/europe/france/limousin-latest.osm.pbf",
    "corse": "https://download.geofabrik.de/europe/france/corse-latest.osm.pbf",
    "france": "https://download.geofabrik.de/europe/france-latest.osm.pbf",
    # Switzerland
    "switzerland": "https://download.geofabrik.de/europe/switzerland-latest.osm.pbf",
    # Spain — NE regions (full Spain PBF too large)
    "cataluna": "https://download.geofabrik.de/europe/spain/cataluna-latest.osm.pbf",
    "aragon": "https://download.geofabrik.de/europe/spain/aragon-latest.osm.pbf",
    "navarra": "https://download.geofabrik.de/europe/spain/navarra-latest.osm.pbf",
    "pais-vasco": "https://download.geofabrik.de/europe/spain/pais-vasco-latest.osm.pbf",
    # Italy — NW (full Italy PBF too large)
    "italia-nord-ovest": "https://download.geofabrik.de/europe/italy/nord-ovest-latest.osm.pbf",
}

# ── Import guard thresholds ─────────────────────────────────────────────────
#
# Any Geofabrik regional extract yields well over 100k routable segments
# (the smallest, corse, imported ~600k in 2026-05). A count below the floor
# means a truncated PBF, a broken highway filter, or a mid-import crash —
# fail LOUDLY instead of silently serving a hollowed-out substrate.
# For arbitrary local PBF paths (test extracts can be tiny) the floor is 0
# unless --min-rows is passed.
GUARD_MIN_ROWS_GEOFABRIK = 100_000
# A reimport that produces < 60 % of the region's previous row count is
# suspicious regardless of the absolute floor (OSM regions only grow).
GUARD_MIN_FRACTION_OF_PREVIOUS = 0.6

_REGION_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ImportGuardError(RuntimeError):
    """Raised when the post-import sanity check fails — exit non-zero."""


# ── Highway filter ──────────────────────────────────────────────────────────
#
# Matches the routing-graph filter at `graph_tiles.py:_ROUTABLE_HIGHWAYS`,
# which has always excluded `service`. Importing service roads here was
# pure waste — they bloat `osm_road_edges` (~5-10% of rows in a typical
# region) and they're never used downstream:
#   - the routing graph (graph_tiles.py) skips them
#   - cyclists almost never ride service roads as actual routes
#     (parking lot access, gas-station driveways, etc.)
#   - GPX traces transiting one snap to grid-fallback, which is fine
#
# `pedestrian`, `footway`, `bridleway` STAY: walking-sport users + MTB
# single-tracks often tagged as `footway` in OSM (especially in
# Mediterranean/Spanish municipalities where any non-paved path defaults
# to footway).
ROUTABLE_HIGHWAYS = frozenset({
    "residential", "tertiary", "tertiary_link", "secondary", "secondary_link",
    "primary", "primary_link", "unclassified", "track", "path", "cycleway",
    "footway", "bridleway", "living_street", "pedestrian",
})

# Imports placed after module-level constants so they're easy to scan;
# noqa codes silence the related lint warnings.
#
# - SURFACE_NORMALIZE is the SSOT class-map (2026-05-15) re-exported
#   here for backward compat with test_surface_normalize_ssot.py;
#   classify_surface is the full cascade (surface → tracktype → highway
#   with smoothness downgrade) called per-way in the parse loop.
# - _dem_slope_grade is the pure-numpy HGT lookup. Returns
#   (e_start, e_end, slope_pct) or (None, None, None) when the tile is
#   missing on disk. No external runtime dep — HGT tiles staged in
#   DEM_DIR at deploy time. See app/services/local_dem.py.
from app.services.local_dem import slope_grade as _dem_slope_grade  # noqa: E402
from app.services.surface_classification import SURFACE_NORMALIZE, classify_surface  # noqa: E402, F401
from app.services.tile_keys import tile_key_from_latlon  # noqa: E402

HIGHWAY_KNOWN = frozenset({
    # The matched-era routing graph keyed these highway classes (the encoder
    # lived in the removed `services/graph_builder.py`). Kept here for the
    # matched OSM import path: drift means service-tagged OSM ways get imported
    # with highway='unknown', and the routing cost model in `services/ch.py`
    # then looks up the WRONG cost factor.
    "residential", "tertiary", "secondary", "primary",
    "unclassified", "track", "path", "cycleway", "steps",
    "service", "unknown",
})


def normalize_surface(raw: str) -> str:
    return SURFACE_NORMALIZE.get(raw.lower(), "unknown") if raw else "unknown"


def normalize_highway(raw: str) -> str:
    h = raw.lower() if raw else "unknown"
    if h.endswith("_link"):
        h = h[:-5]
    if h in ("footway", "bridleway", "pedestrian", "living_street"):
        h = "path"
    return h if h in HIGHWAY_KNOWN else "unknown"


# OSM `bridge` / `tunnel` tag values that mark a way as a physical
# crossing. `no` and missing tag → false. Drives the critical-connectors
# layer in `_build_bbox_graph` (PR for the Clapiers Lez-bridge bug,
# 2026-05-31). Keep these sets aligned with the same enum used by any
# Overpass-side query if we ever add one — drift would mean the
# critical-connectors layer disagrees with the live data.
BRIDGE_TAG_VALUES = frozenset({"yes", "viaduct", "aqueduct", "boardwalk",
                                "cantilever", "covered", "movable", "trestle"})
TUNNEL_TAG_VALUES = frozenset({"yes", "building_passage", "culvert", "passage"})


def is_bridge(raw: str) -> bool:
    return bool(raw) and raw.lower() in BRIDGE_TAG_VALUES


def is_tunnel(raw: str) -> bool:
    return bool(raw) and raw.lower() in TUNNEL_TAG_VALUES


# ── Region partition helpers ────────────────────────────────────────────────

def region_partition_name(region: str) -> str:
    """``osm_road_edges_<region>`` with dashes mapped to underscores."""
    if not _REGION_NAME_RE.match(region):
        raise ValueError(
            f"invalid region name {region!r} — must match {_REGION_NAME_RE.pattern}"
        )
    return f"osm_road_edges_{region.replace('-', '_')}"


def ensure_region_partition(db, region: str) -> str:
    """Create the region's LIST partition if it doesn't exist yet.

    Region names are regex-validated (see ``region_partition_name``) so the
    inlined literal is safe — DDL can't take bind parameters.
    """
    part = region_partition_name(region)
    db.execute(sa_text(
        f"CREATE TABLE IF NOT EXISTS {part} "
        f"PARTITION OF osm_road_edges FOR VALUES IN ('{region}')"
    ))
    db.commit()
    return part


# ── PBF parsing with pyosmium (streaming) ───────────────────────────────────

class StreamingRoadCollector(osmium.SimpleHandler):
    """Collects routable highway segments and streams them to DB.

    Does NOT accumulate all segments in memory. Instead, buffers up to
    ``batch_size`` segments (+ one full-way WKT per way seen in the batch)
    and flushes to DB when full. Memory usage is bounded at
    ~O(batch_size + n_unique_tiles_in_pbf), typically <50 MB even for
    whole-France imports.

    Segments INSERT into the ``region`` partition of ``osm_road_edges``
    (the caller TRUNCATEd it up-front). Way polylines UPSERT into
    ``osm_ways``. Boundary-tile clearing is lazy: the first time a
    tile_key appears in a flush, rows for that tile in OTHER regions are
    DELETEd (neighbour PBFs overlap at region borders — last import owns
    the tile). ``self.cleared_tiles`` prevents double work.
    """

    def __init__(self, db, region: str = "adhoc", batch_size: int = 50_000):
        super().__init__()
        self.db = db
        self.region = region
        self.batch_size = batch_size
        # (tile_key, way_id, seg_idx, surface, highway, lon1, lat1, lon2, lat2,
        #  ele_start, ele_end, ele_delta, slope_pct, surface_conf,
        #  bridge_yes, tunnel_yes)
        self.buffer: list[tuple] = []
        # way_id → full-way WKT, deduped per flush; upserted into osm_ways.
        self.way_buffer: dict[int, str] = {}
        self.cleared_tiles: set[int] = set()
        self.way_count = 0
        self.skipped = 0
        self.inserted_total = 0
        self.dem_hits = 0
        self.dem_misses = 0
        self.t_start = time.monotonic()

    def way(self, w):
        hw = w.tags.get("highway", "")
        if hw not in ROUTABLE_HIGHWAYS:
            return

        self.way_count += 1
        # Full cascade: surface → tracktype → highway, with smoothness downgrade.
        # ``classify_surface`` returns (class, confidence ∈ [0, 1]). Storing
        # confidence lets the frontend fade overlay opacity rather than binary-drop.
        tags_dict = {
            "surface": w.tags.get("surface", ""),
            "highway": hw,
            "tracktype": w.tags.get("tracktype", ""),
            "smoothness": w.tags.get("smoothness", ""),
        }
        surface, surface_conf = classify_surface(tags_dict)
        highway = normalize_highway(hw)
        # Bridge / tunnel flags propagate to every segment of the way —
        # OSM tags the whole way, not individual segments.
        bridge_yes = is_bridge(w.tags.get("bridge", ""))
        tunnel_yes = is_tunnel(w.tags.get("tunnel", ""))

        nodes = [(n.lon, n.lat) for n in w.nodes if n.location.valid()]
        if len(nodes) < 2:
            self.skipped += 1
            return

        # Full way LineString → osm_ways (ONCE per way; smooth heatmap render)
        self.way_buffer[w.id] = (
            "LINESTRING(" + ",".join(f"{lon} {lat}" for lon, lat in nodes) + ")"
        )

        for k in range(len(nodes) - 1):
            lon1, lat1 = nodes[k]
            lon2, lat2 = nodes[k + 1]

            # Skip degenerate segments
            if abs(lon2 - lon1) < 1e-7 and abs(lat2 - lat1) < 1e-7:
                continue

            # Compute tile_key from segment midpoint
            mid_lat = (lat1 + lat2) / 2
            mid_lon = (lon1 + lon2) / 2
            tile_key = tile_key_from_latlon(mid_lat, mid_lon)

            # Local DEM lookup — returns (None, None, None) when the HGT
            # tile is not on disk or the endpoint falls on a void cell.
            # Cost: 2 numpy indexings + bilinear math per segment, dominated
            # by the cached tile array (~185 MB resident max).
            e1, e2, slope = _dem_slope_grade(lat1, lon1, lat2, lon2)
            if e1 is None:
                self.dem_misses += 1
                ele_delta = None
            else:
                self.dem_hits += 1
                ele_delta = e2 - e1

            self.buffer.append((
                tile_key, w.id, k, surface, highway,
                lon1, lat1, lon2, lat2,
                e1, e2, ele_delta, slope, surface_conf,
                bridge_yes, tunnel_yes,
            ))

            if len(self.buffer) >= self.batch_size:
                self._flush()

    def _flush(self) -> None:
        """Clear neighbour-region tiles, then COPY the buffered rows in.

        Uses PostgreSQL's COPY FROM STDIN — 10-100× faster than batched
        ``INSERT VALUES`` on db-f1-micro over cloud-sql-proxy (PR #251).
        Two staging tables per flush: segments (→ region partition of
        ``osm_road_edges``) and ways (→ ``osm_ways`` upsert).
        """
        if not self.buffer:
            return

        # Lazy boundary-tile clearing: our own partition was TRUNCATEd by
        # run_import, so only rows from OTHER regions (overlapping border
        # tiles of a neighbour PBF) can pre-exist on these tiles.
        new_tiles = {seg[0] for seg in self.buffer} - self.cleared_tiles
        if new_tiles:
            keys = list(new_tiles)
            for i in range(0, len(keys), 500):
                self.db.execute(
                    sa_text("DELETE FROM osm_road_edges "
                            "WHERE tile_key = ANY(:keys) AND region <> :region"),
                    {"keys": keys[i:i + 500], "region": self.region},
                )
            self.cleared_tiles.update(new_tiles)

        # Build TSVs in memory and stream via COPY into temp staging tables.
        # Elevation columns are NULL when DEM is unavailable for the tile —
        # PG ``COPY`` reads ``\N`` as SQL NULL. surface_confidence is always
        # set (0-1) since classify_surface always returns a value.
        # bridge_yes / tunnel_yes are non-null booleans written as ``t``/``f``.
        # The 2-point segment geometry is reconstructed server-side via
        # ST_MakeLine of two points; the multi-point way polyline stays WKT.
        import io
        tsv = io.StringIO()
        for seg in self.buffer:
            (tile_key, way_id, seg_idx, surface, highway,
             lon1, lat1, lon2, lat2,
             e1, e2, e_delta, slope, surf_conf,
             bridge_yes, tunnel_yes) = seg
            # TSV escaping: surface/highway are normalized enums (no specials).
            e1_s = r"\N" if e1 is None else f"{e1:.2f}"
            e2_s = r"\N" if e2 is None else f"{e2:.2f}"
            ed_s = r"\N" if e_delta is None else f"{e_delta:.2f}"
            sl_s = r"\N" if slope is None else f"{slope:.3f}"
            sc_s = f"{surf_conf:.2f}"
            br_s = "t" if bridge_yes else "f"
            tn_s = "t" if tunnel_yes else "f"
            tsv.write(
                f"{tile_key}\t{way_id}\t{seg_idx}\t{surface}\t{highway}\t"
                f"{lon1}\t{lat1}\t{lon2}\t{lat2}\t"
                f"{e1_s}\t{e2_s}\t{ed_s}\t{sl_s}\t{sc_s}\t{br_s}\t{tn_s}\n"
            )
        tsv.seek(0)

        ways_tsv = io.StringIO()
        for way_id, way_wkt in self.way_buffer.items():
            # way_wkt is from our own ",".join — no tabs/newlines.
            ways_tsv.write(f"{way_id}\t{way_wkt}\n")
        ways_tsv.seek(0)

        # Grab the underlying psycopg connection to use copy_expert.
        raw_conn = self.db.connection().connection
        cur = raw_conn.cursor()
        try:
            cur.execute("""
                CREATE TEMP TABLE _osm_stage (
                    tile_key bigint, osm_way_id bigint, segment_idx int,
                    surface text, highway text,
                    lon1 double precision, lat1 double precision,
                    lon2 double precision, lat2 double precision,
                    ele_start_m double precision,
                    ele_end_m double precision,
                    ele_delta_m double precision,
                    slope_grade double precision,
                    surface_confidence double precision,
                    bridge_yes boolean,
                    tunnel_yes boolean
                ) ON COMMIT DROP
            """)
            cur.copy_expert(
                "COPY _osm_stage (tile_key, osm_way_id, segment_idx, surface, highway, "
                "lon1, lat1, lon2, lat2, "
                "ele_start_m, ele_end_m, ele_delta_m, slope_grade, surface_confidence, "
                "bridge_yes, tunnel_yes) FROM STDIN",
                tsv,
            )
            cur.execute("""
                INSERT INTO osm_road_edges (
                    region, tile_key, osm_way_id, segment_idx, surface, highway,
                    geometry,
                    ele_start_m, ele_end_m, ele_delta_m, slope_grade,
                    surface_confidence, bridge_yes, tunnel_yes
                )
                SELECT
                    %s, tile_key, osm_way_id, segment_idx, surface, highway,
                    ST_SetSRID(ST_MakeLine(ST_MakePoint(lon1, lat1), ST_MakePoint(lon2, lat2)), 4326),
                    ele_start_m, ele_end_m, ele_delta_m, slope_grade,
                    surface_confidence, bridge_yes, tunnel_yes
                FROM _osm_stage
            """, (self.region,))
            cur.execute("DROP TABLE _osm_stage")

            cur.execute("""
                CREATE TEMP TABLE _osm_ways_stage (
                    osm_way_id bigint, way_wkt text
                ) ON COMMIT DROP
            """)
            cur.copy_expert(
                "COPY _osm_ways_stage (osm_way_id, way_wkt) FROM STDIN",
                ways_tsv,
            )
            cur.execute("""
                INSERT INTO osm_ways (osm_way_id, way_geometry, region)
                SELECT osm_way_id, ST_GeomFromText(way_wkt, 4326), %s
                FROM _osm_ways_stage
                ON CONFLICT (osm_way_id) DO UPDATE
                SET way_geometry = EXCLUDED.way_geometry,
                    region = EXCLUDED.region
            """, (self.region,))
            cur.execute("DROP TABLE _osm_ways_stage")
        finally:
            cur.close()
        self.db.commit()

        self.inserted_total += len(self.buffer)
        elapsed = time.monotonic() - self.t_start
        logger.info(
            "  %d inserted (%.0fs, %d ways in batch, %d tiles cleared)",
            self.inserted_total, elapsed, len(self.way_buffer), len(self.cleared_tiles),
        )
        self.buffer.clear()
        self.way_buffer.clear()

    def finalize(self) -> None:
        """Flush the remaining buffer at end of parse."""
        if self.buffer or self.way_buffer:
            self._flush()


# ── Download ────────────────────────────────────────────────────────────────

def download_pbf(url: str, dest: str) -> None:
    """Download PBF atomically: write to a temp file, then rename.

    ``urllib.request.urlretrieve(url, dest, ...)`` writes straight to
    ``dest``. If the download is interrupted (network drop, OOM-kill,
    Ctrl-C), ``dest`` is left TRUNCATED — and ``run_import``'s freshness
    check (``age_hours < 24`` → skip download) would then happily reuse
    that partial file on the next run, feeding a corrupt PBF to osmium
    (silent under-import or a parse crash mid-stream). Downloading to a
    sibling ``.part`` file and ``os.replace``-ing it into place only when
    the transfer completes means an interrupted download never lands at
    ``dest`` — the partial ``.part`` is cleaned up and the next run
    re-downloads from scratch. (June 2026 audit S1 #7a.)
    """
    import urllib.request

    logger.info("Downloading %s ...", url)

    def reporthook(block_num, block_size, total_size):
        downloaded = block_num * block_size
        if total_size > 0:
            pct = min(100, downloaded * 100 // total_size)
            mb = downloaded / 1024 / 1024
            total_mb = total_size / 1024 / 1024
            if block_num % 200 == 0:
                logger.info("  %.0f / %.0f MB (%d%%)", mb, total_mb, pct)

    # Same directory as dest so os.replace stays on one filesystem (atomic).
    tmp_dest = dest + ".part"
    try:
        urllib.request.urlretrieve(url, tmp_dest, reporthook)
    except BaseException:
        # Includes KeyboardInterrupt / network errors — never leave a
        # half-written .part behind to be mistaken for a complete file.
        if os.path.exists(tmp_dest):
            os.remove(tmp_dest)
        raise
    os.replace(tmp_dest, dest)
    size_mb = os.path.getsize(dest) / 1024 / 1024
    logger.info("Downloaded: %.0f MB → %s", size_mb, dest)


# ── Import guard ────────────────────────────────────────────────────────────

def _run_import_guard(db, region: str, inserted: int,
                      previous_count: int | None, min_rows: int) -> None:
    """Sanity-check the row count, then record the import in osm_import_meta.

    Raises ImportGuardError (→ non-zero exit) on an implausible count. The
    partition has already been TRUNCATEd + refilled at this point — the
    error message says so LOUDLY so the operator reimports instead of
    trusting a hollowed-out region.
    """
    if inserted < min_rows:
        raise ImportGuardError(
            f"IMPORT GUARD FAILED for region '{region}': only {inserted} segments "
            f"inserted (floor {min_rows}). The PBF is likely truncated or the "
            f"highway filter broke. THE REGION PARTITION NOW HOLDS THIS "
            f"IMPLAUSIBLE STATE — reimport before serving traffic."
        )
    if previous_count is not None and inserted < previous_count * GUARD_MIN_FRACTION_OF_PREVIOUS:
        raise ImportGuardError(
            f"IMPORT GUARD FAILED for region '{region}': {inserted} segments "
            f"inserted vs {previous_count} on the previous import "
            f"(< {GUARD_MIN_FRACTION_OF_PREVIOUS:.0%}). OSM regions only grow — "
            f"this smells like a partial parse. THE REGION PARTITION NOW HOLDS "
            f"THIS IMPLAUSIBLE STATE — reimport before serving traffic."
        )
    db.execute(sa_text("""
        INSERT INTO osm_import_meta (region, imported_at, row_count)
        VALUES (:region, now(), :count)
        ON CONFLICT (region) DO UPDATE
        SET imported_at = EXCLUDED.imported_at, row_count = EXCLUDED.row_count
    """), {"region": region, "count": inserted})
    db.commit()


# ── Main import ─────────────────────────────────────────────────────────────

def run_import(pbf_path: str, no_download: bool = False, batch_size: int = 50_000,
               region: str | None = None, min_rows: int | None = None) -> None:
    from app.db.session import SessionLocal

    # Resolve PBF path + region
    is_geofabrik_preset = pbf_path in REGIONS
    if is_geofabrik_preset:
        preset = pbf_path
        region = region or preset
        url = REGIONS[preset]
        data_dir = os.environ.get("DATA_DIR", "/data")
        os.makedirs(data_dir, exist_ok=True)
        pbf_path = os.path.join(data_dir, f"{preset}.osm.pbf")

        # A truncated PBF from an interrupted pre-fix download (or a
        # 0-byte placeholder) must NOT be reused — osmium would silently
        # under-import or crash mid-stream. Geofabrik regional extracts
        # are tens of MB minimum; treat anything implausibly small as
        # incomplete and re-download. (June 2026 audit S1 #7a.)
        _MIN_PLAUSIBLE_PBF_BYTES = 1_000_000  # 1 MB
        existing_ok = (
            os.path.exists(pbf_path)
            and os.path.getsize(pbf_path) >= _MIN_PLAUSIBLE_PBF_BYTES
        )

        if no_download:
            if existing_ok:
                logger.info("Using existing PBF: %s", pbf_path)
            elif os.path.exists(pbf_path):
                logger.error(
                    "Existing PBF looks truncated (%d bytes) and --no-download "
                    "set: %s — re-run without --no-download.",
                    os.path.getsize(pbf_path), pbf_path,
                )
                return
            else:
                logger.error("PBF not found and --no-download set: %s", pbf_path)
                return
        elif existing_ok:
            age_hours = (time.time() - os.path.getmtime(pbf_path)) / 3600
            if age_hours < 24:
                logger.info("PBF is recent (%.0fh old), skipping download: %s", age_hours, pbf_path)
            else:
                download_pbf(url, pbf_path)
        else:
            download_pbf(url, pbf_path)
    else:
        if not os.path.exists(pbf_path):
            logger.error("PBF file not found: %s", pbf_path)
            return
        if region is None:
            # e.g. /data/france-south.osm.pbf → region "france-south"
            region = os.path.basename(pbf_path)
            for suffix in (".osm.pbf", ".pbf"):
                if region.endswith(suffix):
                    region = region[: -len(suffix)]
                    break
            region = region.lower()

    guard_floor = min_rows if min_rows is not None else (
        GUARD_MIN_ROWS_GEOFABRIK if is_geofabrik_preset else 0
    )

    # Parse PBF — streaming INSERTs from inside the way() callback
    logger.info("Parsing PBF (streaming, region=%s, batch_size=%d): %s ...",
                region, batch_size, pbf_path)
    db = SessionLocal()
    try:
        part = ensure_region_partition(db, region)
        previous_count = db.execute(
            sa_text("SELECT row_count FROM osm_import_meta WHERE region = :r"),
            {"r": region},
        ).scalar()
        logger.info("Region '%s' → partition %s (previous import: %s rows). TRUNCATE + refill.",
                    region, part, previous_count if previous_count is not None else "none")
        db.execute(sa_text(f"TRUNCATE TABLE {part}"))
        db.commit()

        t0 = time.monotonic()
        collector = StreamingRoadCollector(db, region=region, batch_size=batch_size)
        collector.apply_file(pbf_path, locations=True)
        collector.finalize()
        elapsed = time.monotonic() - t0

        total_dem = collector.dem_hits + collector.dem_misses
        dem_pct = (100.0 * collector.dem_hits / total_dem) if total_dem else 0.0
        logger.info(
            "Parsed: %d ways → %d segments inserted in %.0fs "
            "(skipped %d, %d unique tiles, DEM hits %d/%d = %.1f%%)",
            collector.way_count, collector.inserted_total, elapsed,
            collector.skipped, len(collector.cleared_tiles),
            collector.dem_hits, total_dem, dem_pct,
        )

        # Import guard: fail LOUDLY on implausible counts, else record the
        # import in osm_import_meta (per-region freshness + next-run baseline).
        _run_import_guard(db, region, collector.inserted_total,
                          previous_count, guard_floor)

        # Stats
        tile_count = db.execute(sa_text("SELECT COUNT(DISTINCT tile_key) FROM osm_road_edges")).scalar()
        logger.info("Coverage: %d z14 tiles total in DB", tile_count)

        # Invalidate in-memory caches so ingestion picks up the new tiles
        try:
            from app.services.ingest import invalidate_osm_tile_cache
            invalidate_osm_tile_cache()
            logger.info("OSM tile cache invalidated")
        except ImportError:
            pass

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import OSM roads from Geofabrik PBF")
    parser.add_argument("region", help="Region name (occitanie, france, ...) or path to .osm.pbf file")
    parser.add_argument("--no-download", action="store_true", help="Skip download, use existing file")
    parser.add_argument(
        "--batch-size", type=int, default=50_000,
        help="INSERT batch size — also bounds peak memory (default 50k ≈ 10MB buffer)",
    )
    parser.add_argument(
        "--region", dest="region_override", default=None,
        help="Region label for the osm_road_edges partition "
             "(default: preset name, or the PBF filename stem for paths)",
    )
    parser.add_argument(
        "--min-rows", type=int, default=None,
        help="Import-guard floor override (default: 100k for Geofabrik presets, 0 for paths)",
    )
    args = parser.parse_args()
    try:
        run_import(args.region, no_download=args.no_download,
                   batch_size=args.batch_size, region=args.region_override,
                   min_rows=args.min_rows)
    except ImportGuardError as exc:
        logger.error("%s", exc)
        sys.exit(2)
