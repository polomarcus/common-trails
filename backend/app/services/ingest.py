"""Activity ingestion service.

Stores activities (private) and updates community heatmap (opt-in, ODbL).
K-anonymity is enforced at read time (endpoints).

Data separation:
- PRIVATE: activities, activity_cells, activity_edges
- COMMON (ODbL): heat_cells, heat_edges (only if contribute_heatmap=True)

Edge data is stored in PostGIS tables (heat_edges, dfci_edges, trail_edges)
with GiST spatial indexes. No in-memory stores.
"""
import hashlib
import json
import logging
import math
import os
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func
from sqlalchemy import text as sa_text

from app.config import TEST_MODE
from app.services.geo import haversine_m as _haversine_m
from app.services.provenance import (
    COMMUNITY_SOURCE,
    is_community_source,
    should_promote_to_community,
)
from app.services.raw_trace_display import raw_display_enabled
from app.services.routing_profiles import compute_heat_score, compute_trail_score

logger = logging.getLogger(__name__)

HEATMAP_K_ANONYMITY = int(os.environ.get("HEATMAP_K_ANONYMITY", "2"))

# Cross-source dedup window: "the same physical ride" iff same user + start
# time within ±DEDUP_START_WINDOW_MIN AND distance within ±DEDUP_DISTANCE_TOL.
# SSOT — the ingest cross-provider dedup (below) AND account-merge dedup
# (app/services/account_merge.py) both key off these so they can never drift.
# Tightened to ±5 min / ±5 % in audit 2026-05-27 S2.6 (see the dedup call site).
DEDUP_START_WINDOW_MIN = 5
DEDUP_DISTANCE_TOL = 0.05


def heat_user_id_hash(user_id: str) -> int:
    """Stable 32-bit hash of a ``user_id`` used as the K-anonymity contributor
    key in ``heat_edge_contributors`` / ``heat_cell_contributors``.

    SSOT — the ingest write path AND account merge (which rewrites the hash of
    an absorbed account's contributions onto the survivor) MUST agree, or a
    merged person would keep counting as two distinct K-anonymity users.
    """
    return int(hashlib.sha256(str(user_id).encode()).hexdigest()[:8], 16)

# Directory for seed GPX data (mounted Docker volume).
DATA_DIR = os.environ.get("DATA_DIR", "")


def _geom_from_geojson_sql(geojson: str | None):
    """Return a SQLAlchemy SQL expression that converts a GeoJSON string
    to `geometry(LineString, 4326)`, or None if the input is unusable.

    Used by the dual-write path in `ingest_activity` (and bulk variants)
    so that `activities.geometry` (binary PostGIS) is populated alongside
    the legacy TEXT `geometry_geojson` column. See migration 0036.

    Cheap structural validation in Python (must be a LineString with at
    least 2 coords) avoids paying the PostGIS parse cost for obvious
    garbage and keeps a single bad row from poisoning bulk inserts. If
    the JSON parses but PostGIS itself rejects it, the surrounding
    try/except in the caller handles the rollback.

    `ST_Force2D` drops the optional Z (elevation) coordinate that GPX
    imports carry in `[lon, lat, ele]` triples. The binary column is 2D
    (`geometry(LineString, 4326)`); elevation stays in the legacy text
    column for now. Without `ST_Force2D` PostGIS rejects 3D inputs into
    the 2D typmod with "Geometry has Z dimension but column does not".
    """
    if not geojson:
        return None
    try:
        parsed = json.loads(geojson)
        if not isinstance(parsed, dict):
            return None
        if parsed.get("type") != "LineString":
            return None
        coords = parsed.get("coordinates")
        if not isinstance(coords, list) or len(coords) < 2:
            return None
    except (ValueError, TypeError):
        return None
    return func.ST_SetSRID(func.ST_Force2D(func.ST_GeomFromGeoJSON(geojson)), 4326)


# ── OSM map-matching constants ────────────────────────────────────────────────

_ROUTABLE_HIGHWAYS = frozenset({
    "residential", "tertiary", "tertiary_link", "secondary", "secondary_link",
    "primary", "primary_link", "unclassified", "track", "path", "cycleway",
    "footway", "bridleway", "living_street", "pedestrian",
})

# SSOT (2026-05-15): re-export from surface_classification.py. Private name
# kept since callers in this module reference it as `_SURFACE_NORMALIZE`.
from app.services.local_dem import slope_grade as _dem_slope_grade  # noqa: E402
from app.services.surface_classification import SURFACE_NORMALIZE as _SURFACE_NORMALIZE  # noqa: F401, E402
from app.services.surface_classification import classify_surface as _classify_surface  # noqa: E402
from app.services.tile_keys import tile_key_from_xy as _tile_key_from_xy  # noqa: E402
from app.services.tile_keys import tile_key_to_xy as _tile_key_to_xy  # noqa: E402

_OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
_OSM_FETCH_TIMEOUT = 5.0
_OSM_FETCH_DELAY = 1.0  # delay between fetches to avoid Overpass rate-limit
_osm_fetch_lock = threading.Semaphore(2)

_OSM_MATCH_RADIUS_M = 15.0
_OSM_GRID_SIZE = 0.0005  # ~55m cells for spatial grid
_OSM_MIN_CONSECUTIVE = 2  # minimum consecutive GPS points on same segment

# Sport-aware OSM match radius. Urban GPS multipath easily produces
# 20–40m drift on road/running activities — we observed 1.6% / 6.6%
# OSM-match rates for road / running vs 45% for gravel/mtb (May 2026
# data, see docs/ingestion-pipeline.md). Wider radius for the urban
# profiles only; gravel/mtb stay tight so single-track precision is
# preserved.
_OSM_MATCH_RADIUS_BY_SPORT: dict[str, float] = {
    "road": 25.0,
    "running": 25.0,
    "gravel": 15.0,
    "mtb": 15.0,
    "offroad": 15.0,
}


def _osm_match_radius_for(sport: str) -> float:
    return _OSM_MATCH_RADIUS_BY_SPORT.get(sport, _OSM_MATCH_RADIUS_M)

# In-memory cache of known tile keys (BIGINT ``x*100000+y`` since migration
# 0056 — see app.services.tile_keys) — avoids a per-tile DB existence check.
# Populated on first _ensure_osm_tiles call, cleared on PBF re-import.
_known_osm_tiles: set[int] | None = None
_known_osm_tiles_lock = threading.Lock()

# In-memory cache of loaded OSM segment grids — keyed by tile_key.
# Avoids reloading the same segments when multiple activities overlap geographically.
_osm_segment_cache: dict[int, list] = {}
_osm_segment_cache_order: list[int] = []
_osm_segment_cache_lock = threading.Lock()

# Cache for built spatial grids — keyed by frozenset of tile keys.
# Avoids rebuilding the grid when multiple activities cover the same tiles.
# A bulk import scattered across France easily produces 200+ distinct
# tile-sets; the default 50 was too tight, every cache miss cost 5–15s
# of segment loading + grid rebuild. Tune up with `OSM_GRID_CACHE_MAX`.
#
# Memory floor: large grids skip the cache entirely (see
# `_osm_grid_cache_max_segs_per_entry`). Each cached grid references the
# `_OsmSegment` objects from `_osm_segment_cache` — and because long
# segments are inserted into ALL grid cells they cover, a single 50k-seg
# grid can hold ~200k list slots. Caching one such grid is ~50 MB; on a
# 512 Mi Cloud Run Job that exhausts the budget in < 10 entries. The
# fast-path matching of small activities doesn't suffer from the cap
# because their grids stay small.
_osm_grid_cache: dict[frozenset, dict] = {}
# Parallel cache: tile_key_set -> way-adjacency index (for the HMM matcher's
# transition term). Keyed identically to _osm_grid_cache, guarded by the same
# lock. Kept separate so the grid cache value shape is unchanged.
_osm_adjacency_cache: dict[frozenset, dict] = {}
_osm_grid_cache_lock = threading.Lock()


# The three OSM-cache caps are read from the environment at USE time (not once
# at import) so a job can size them to its OWN container. The code DEFAULTS stay
# conservative — the 512 Mi web service must never blow up — and only the 8 Gi
# drain / rebuild jobs override them upward (see scripts/deploy-prod.sh
# `job_env_for`). Reading live also makes the knobs unit-testable without a
# module reload.
def _osm_segment_cache_max() -> int:
    """Max z14 tiles held in the segment cache (env ``OSM_TILE_CACHE_MAX``).
    Default 50 (~a few MB/tile of France OSM data; dense-urban worst case
    ~4 MB/tile)."""
    return int(os.environ.get("OSM_TILE_CACHE_MAX", "50"))


def _osm_grid_cache_max() -> int:
    """Max built spatial grids cached (env ``OSM_GRID_CACHE_MAX``, 0 disables).
    Each entry is an INDEX over already-cached ``_OsmSegment`` objects (list
    slots + cell dict), ~8 MB for a big multi-tile grid — the 50 MB figure in
    the comment above counts the segment objects, which live in the segment
    cache and are NOT re-allocated per grid."""
    return int(os.environ.get("OSM_GRID_CACHE_MAX", "200"))


def _osm_grid_cache_max_segs_per_entry() -> int:
    """Grids built from more segments than this skip the cache entirely
    (env ``OSM_GRID_CACHE_MAX_SEGS_PER_ENTRY``, 0 disables grid caching)."""
    return int(os.environ.get("OSM_GRID_CACHE_MAX_SEGS_PER_ENTRY", "10000"))

# Version counter: incremented on any edge mutation. Used by routing graph cache
# for invalidation (cache key includes version → stale entries auto-miss).
#
# Storage: ``edge_version_state`` table (single row, see migration 0040). All
# instances see the same version → routing graph cache is correct at
# ``max_instances ≥ 2``. Per-instance TTL cache below avoids one DB read
# per routing request.
_edge_version_lock = threading.Lock()
_edge_version_cache: tuple[int, float] = (0, 0.0)  # (version, fetched_at_monotonic)
_EDGE_VERSION_TTL_S = float(os.environ.get("EDGE_VERSION_TTL_S", "5.0"))


def _read_edge_version_from_db() -> int:
    """Read version from edge_version_state.

    Returns 0 if the table doesn't exist yet (e.g. fresh DB before migration
    0040 ran) — keeps the routing graph cache key stable rather than crashing.
    """
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        row = db.execute(sa_text(
            "SELECT version FROM edge_version_state WHERE id = 1"
        )).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        # Migration 0040 hasn't run yet, or table dropped; treat as version 0.
        return 0
    finally:
        db.close()


def _bump_edge_version(db) -> int:
    """Atomically increment the persisted version and return the new value.

    Uses the caller-supplied session so the bump joins the surrounding
    ingest transaction — if the ingest commits, the version commits with it;
    if the ingest rolls back, the version stays unchanged. Call this BEFORE
    ``db.commit()`` so the two land atomically.

    Errors propagate — alembic migration 0040 created the singleton row, so
    the UPSERT below should never fail in a healthy deployment. If it does,
    the caller's transaction rolls back and heat_edges stays unchanged.
    """
    global _edge_version_cache
    row = db.execute(sa_text("""
        INSERT INTO edge_version_state (id, version) VALUES (1, 1)
        ON CONFLICT (id) DO UPDATE SET version = edge_version_state.version + 1
        RETURNING version
    """)).fetchone()
    new_version = int(row[0]) if row else 0
    # Refresh the per-instance cache so the next routing read sees the new
    # version immediately (skipping the TTL).
    with _edge_version_lock:
        _edge_version_cache = (new_version, time.monotonic())
    return new_version


def get_edge_version() -> int:
    """Return the current edge mutation version (used by routing graph cache).

    DB-backed (works at ``max_instances ≥ 2``) with a short per-instance TTL
    cache. The TTL means that, after an ingest on instance A, instance B may
    serve up-to-``EDGE_VERSION_TTL_S`` seconds of stale routing-graph cache
    before refreshing — acceptable for an interactive map.
    """
    global _edge_version_cache
    with _edge_version_lock:
        cached_version, fetched_at = _edge_version_cache
        now = time.monotonic()
        if now - fetched_at < _EDGE_VERSION_TTL_S:
            return cached_version
    # Cache miss / expired — refresh from DB outside the lock so we don't
    # block other readers. Multiple readers may race here; that's fine,
    # they'll all read the same value and write it back consistently.
    fresh = _read_edge_version_from_db()
    with _edge_version_lock:
        _edge_version_cache = (fresh, now)
    return fresh


# ── PMTiles rebuild (debounced, longer window) ───────────────────────────
# PMTiles rebuild is the BROWSER-facing display artefact, so we want it fresh
# but not thrash on every single upload. 5 min debounce: a friend
# uploading 50 GPX files in a session triggers ONE rebuild at the end.
_pmtiles_rebuild_timer: threading.Timer | None = None
_pmtiles_rebuild_lock = threading.Lock()
_PMTILES_REBUILD_DELAY = float(os.environ.get("PMTILES_REBUILD_DELAY_S", "300"))
# Bind-mounted host path the frontend container also mounts. When set
# (dev: /frontend-public; prod: configured via env), the in-place write
# preserves the inode so the frontend picks up new bytes without
# restart. When unset (e.g. unit tests), we skip the deploy step.
_FRONTEND_PMTILES_PATH = os.environ.get(
    "FRONTEND_PMTILES_PATH",
    "/frontend-public/heatmap-display.pmtiles",
)


def _schedule_pmtiles_rebuild() -> None:
    """Schedule a debounced PMTiles rebuild + in-place deploy.

    Debounced with a 5 min default window because PMTiles rebuild is heavier
    (10–30 s on dev, longer in prod) and the output is what the
    browser actually reads. A friend uploading 50 GPX files in a
    session triggers ONE rebuild at the end, not 50.

    Bulk-import fast path: ``SKIP_PMTILES_REBUILD=true`` skips the
    schedule entirely (use ``make heatmap-deploy`` after the loop).

    The output is written IN-PLACE to ``FRONTEND_PMTILES_PATH`` via
    truncate-and-write so the frontend container's bind-mount inode
    is preserved — no container restart needed.
    """
    if os.environ.get("SKIP_PMTILES_REBUILD", "").lower() == "true":
        return
    if os.environ.get("TEST_MODE", "").lower() == "true":
        # In tests we don't want subprocess churn after every fixture upload.
        return
    global _pmtiles_rebuild_timer
    with _pmtiles_rebuild_lock:
        if _pmtiles_rebuild_timer is not None:
            _pmtiles_rebuild_timer.cancel()
        _pmtiles_rebuild_timer = threading.Timer(
            _PMTILES_REBUILD_DELAY, _do_pmtiles_rebuild,
        )
        _pmtiles_rebuild_timer.daemon = True
        _pmtiles_rebuild_timer.start()


def _do_pmtiles_rebuild() -> None:
    """Run build_pmtiles in-process, then write IN-PLACE to the
    bind-mounted host path so the frontend picks up new bytes without
    a container restart.

    Errors are logged but never re-raised — this runs on a daemon
    thread post-ingest, and a failure should not affect the ongoing
    request flow.
    """
    import os as _os
    import shutil
    import tempfile
    try:
        from app.jobs import build_pmtiles
    except Exception as exc:  # pragma: no cover
        logger.error("auto-pmtiles: cannot import build_pmtiles: %s", exc)
        return

    target_dir = tempfile.mkdtemp(prefix="autopmtiles_")
    try:
        # Build into a tmp dir; same flags as ``make pmtiles``.
        # min_uc MUST track the API's K-anonymity floor: the static
        # PMTiles binary is published to the browser AND the public GCS
        # export, so hardcoding min_uc=1 here published single-user
        # OSM-matched edges while the API enforced K=2 (K-anon bypass,
        # June 2026 audit S1). HEATMAP_K_ANONYMITY is 1 in dev compose
        # (full visibility) and defaults to 2 in prod.
        try:
            build_pmtiles.main(
                target_dir, min_zoom=6, max_zoom=15,
                min_uc=HEATMAP_K_ANONYMITY,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto-pmtiles: build failed: %s", exc)
            return

        src = _os.path.join(target_dir, "heatmap-display.pmtiles")
        dst = _FRONTEND_PMTILES_PATH
        if not _os.path.exists(src):
            logger.warning("auto-pmtiles: source missing after build: %s", src)
            return
        if not _os.path.exists(_os.path.dirname(dst)):
            logger.info(
                "auto-pmtiles: dest dir %s missing — skipping deploy "
                "(set FRONTEND_PMTILES_PATH or mount /frontend-public)",
                _os.path.dirname(dst),
            )
            return

        # Truncate-and-write IN PLACE so the frontend bind-mount inode
        # is preserved (this is the trap that wasted hours in May 2026
        # — see docs/migration-runbook.md § PMTiles).
        with open(src, "rb") as src_f, open(dst, "r+b") as dst_f:
            dst_f.truncate(0)
            shutil.copyfileobj(src_f, dst_f)
        logger.info("auto-pmtiles: deployed %d bytes to %s",
                    _os.path.getsize(dst), dst)
    finally:
        shutil.rmtree(target_dir, ignore_errors=True)


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _coords_to_linestring(coords: list) -> str | None:
    """Build a WKT LINESTRING from coordinates, validating all values are numeric.

    Returns None if coords has < 2 points or contains non-numeric values.
    """
    if len(coords) < 2:
        return None
    parts: list[str] = []
    for c in coords:
        try:
            parts.append(f"{float(c[0])} {float(c[1])}")
        except (TypeError, ValueError, IndexError):
            logger.warning("Skipping invalid coordinate: %s", c)
            continue
    if len(parts) < 2:
        return None
    return "LINESTRING(" + ",".join(parts) + ")"


# ── Tile helpers (zoom-14 grid for cell heatmap) ─────────────────────────────

def _latlon_to_cell_key(lat: float, lon: float, zoom: int = 14) -> str:
    """Approximate tile cell key from lat/lon at given zoom level."""
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n)
    return f"{zoom}/{x}/{y}"


def _geojson_to_cells(geojson_str: str | None, zoom: int = 14) -> list[str]:
    """Extract unique tile cell keys from a GeoJSON LineString."""
    if not geojson_str:
        return []
    try:
        geojson = json.loads(geojson_str)
        coords = geojson.get("coordinates", [])
    except Exception:
        return []

    cells = set()
    for point in coords:
        try:
            lon, lat = point[0], point[1]  # supports both 2D [lon,lat] and 3D [lon,lat,ele]
            cells.add(_latlon_to_cell_key(float(lat), float(lon), zoom))
        except Exception:
            continue
    return list(cells)


# ── Edge helpers (trail-level heatmap) ───────────────────────────────────────

def _snap(lat: float, lon: float) -> tuple[float, float]:
    """Snap lat/lon to 4 decimal places (~11 m grid) for edge matching.

    At 5dp (~1.1m), GPS jitter prevents traces on the same road from merging.
    At 4dp (~11m), traces on the same road reliably produce identical edge keys,
    giving meaningful user_count aggregation and ~10x fewer total edges.
    """
    return round(lat, 4), round(lon, 4)


# NOTE: `_snap_fine` (5dp ~1.1m) was removed 2026-05-31. It used to be
# applied to OSM-matched edges AFTER `_project_onto_segment`, which
# knocked the on-polyline projection back off the OSM line by up to
# ~0.55m. Combined with the WASM CH builder's 16.7m grid collapse
# (wasm-router/src/graph.rs::get_or_create_vertex) the heat_edges no
# longer shared vertices with osm_road_edges and the routing graph
# fragmented — routes fell through to OSRM.
#
# OSM-matched spatial-fallback paths now use the projection result
# AND a `_snap_along_segment` arc-length bucket (1m). Arc-length
# bucketing preserves cross-rider K-anonymity at the producer layer
# (same OSM segment + within 0.5m along the trail → same edge_key)
# WITHOUT pushing the point off the OSM line — the bucketed coord
# stays exactly on the segment by construction.

_DENSIFY_MAX_GAP_M = 15.0  # ~1.5x grid cell at 4dp — ensures connectivity between consecutive snapped points


# Gaps larger than this are not "normal contiguous recording" any more — they
# are either a downsampled GPX (sparse sampling along a real ride, e.g. a
# Strava-reduced export at ~1 point / 150-700 m) OR a genuine GPS dropout
# (tunnel, dead battery, a car transfer stitched into one file).
_DENSIFY_GPS_BREAK_M = 500.0
# We can't tell sampling from dropout at densify time (no DB), but we DON'T
# have to short-circuit to a hard break: gaps in the
# [_DENSIFY_GPS_BREAK_M, _DENSIFY_MAX_BRIDGE_M] band are densified across
# ("bridged") so the downstream OSM matcher gets a chance to snap the
# interpolated points. Where OSM has the connecting path (the common case for
# a downsampled ride), the run stays CONTINUOUS — the bridge becomes a chain
# of short ~15 m OSM-snapped heat_edges, never a single long straight chord.
# Where OSM is absent (genuine off-grid dropout), those bridge points simply
# fail to match and fall to the short grid-fallback / get dropped by the
# `_OSM_MIN_CONSECUTIVE` filter — again never a long phantom edge. Only gaps
# ABOVE the bridge cap stay a hard `None` break: a >2.5 km jump in a single
# track is overwhelmingly a transfer between two rides, not a downsample, and
# bridging it would paint fake heat along a road nobody rode.
#
# Trace integrity (Crouzet): this only shapes the densified coord list fed to
# the heat_edge matcher. The stored GPX `geometry_geojson` is untouched.
#
# Measured on the golden Hérault gravel ride (341 pts / 69.5 km, median spacing
# 143 m, p99 680 m, max jump 1549 m — a textbook downsampled export): bridging
# the [500 m, 2500 m] band lifted continuity@50m from 0.9956 to 0.9998 (16 gaps
# → 1) at unchanged 100 % OSM-match and unchanged 32.2 m max edge length. The
# single residual gap is an out-and-back overlap deduped by `seen_keys` (the
# edge IS present, just emitted earlier) — not a real break.
_DENSIFY_MAX_BRIDGE_M = 2500.0


def _densify_coords(coords: list, max_gap: float = _DENSIFY_MAX_GAP_M) -> list:
    """Densify a coordinate list so no gap exceeds max_gap meters.

    Ensures every 1.1m snap grid cell (5dp) the trace passes through gets a point.
    All traces on the same road produce identical snapped edge keys —
    no neighbor clustering needed for same-road merge.

    Preserves elevation (linear interpolation) when available.
    COORD ORDER: operates on raw GeoJSON [lon, lat, ?ele] coords.
    _haversine_m(lon1, lat1, lon2, lat2) matches this order.

    GPS-loss handling: when consecutive points are more than
    ``_DENSIFY_GPS_BREAK_M`` apart we treat that as a track break and
    insert ``None`` in the output. Callers iterating consecutive pairs
    must skip any pair where either endpoint is ``None`` — otherwise
    the rider gets a phantom straight-line edge spanning the gap (the
    "spaghetti" we used to see at K=1).
    """
    if len(coords) < 2:
        return coords
    result: list = [coords[0]]
    for i in range(len(coords) - 1):
        p1, p2 = coords[i], coords[i + 1]
        d = _haversine_m(p1[0], p1[1], p2[0], p2[1])
        if d < 0.1:
            continue
        if d > _DENSIFY_MAX_BRIDGE_M:
            # Too large to be a downsampled stride — almost certainly a
            # genuine GPS dropout or a transfer between two rides stitched
            # into one track. Insert a break sentinel instead of stitching
            # p1 and p2 together (avoids a phantom line across the gap).
            result.append(None)
            result.append(p2)
            continue
        # Gaps up to the bridge cap (incl. the [_DENSIFY_GPS_BREAK_M, cap]
        # downsampling band) are densified across so the OSM matcher can
        # snap the interpolated points and keep the run continuous. See the
        # `_DENSIFY_MAX_BRIDGE_M` rationale above.
        n = max(1, int(d / max_gap))
        for j in range(1, n + 1):
            frac = j / n
            lon = p1[0] + (p2[0] - p1[0]) * frac
            lat = p1[1] + (p2[1] - p1[1]) * frac
            ele = None
            if len(p1) > 2 and len(p2) > 2 and p1[2] is not None and p2[2] is not None:
                e1, e2 = float(p1[2]), float(p2[2])
                if not (math.isnan(e1) or math.isnan(e2)):
                    ele = e1 + (e2 - e1) * frac
            result.append([lon, lat] if ele is None else [lon, lat, ele])
    return result


# Backward-compatible alias
_resegment_coords = _densify_coords


# ── OSM map-matching helpers ─────────────────────────────────────────────────

def _normalize_surface(raw: str) -> str:
    """Map OSM surface tag to our 5 categories."""
    return _SURFACE_NORMALIZE.get(raw.lower(), "unknown") if raw else "unknown"


_HIGHWAY_KNOWN = frozenset({
    # The matched-era routing graph keyed these highway classes (the encoder
    # lived in the removed `services/graph_builder.py`). Drift here means
    # service-tagged edges get tagged highway='unknown', and the routing cost
    # lookup misses.
    "residential", "tertiary", "secondary", "primary",
    "unclassified", "track", "path", "cycleway", "steps",
    "service", "unknown",
})


def _normalize_highway(raw: str) -> str:
    """Map OSM highway tag to our index keys (strip _link suffixes)."""
    h = raw.lower() if raw else "unknown"
    if h.endswith("_link"):
        h = h[:-5]
    if h in ("footway", "bridleway", "pedestrian", "living_street"):
        h = "path"
    return h if h in _HIGHWAY_KNOWN else "unknown"


def _latlon_to_z14_tile(lat: float, lon: float) -> tuple[int, int, int]:
    """Convert lat/lon to z14 slippy tile (z, x, y)."""
    z = 14
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n)
    return z, x, y


def _tile_bbox(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Convert z/x/y tile to (min_lon, min_lat, max_lon, max_lat)."""
    n = 2 ** z
    min_lon = x / n * 360 - 180
    max_lon = (x + 1) / n * 360 - 180
    max_lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    min_lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return min_lon, min_lat, max_lon, max_lat


def _coords_to_z14_tiles(coords: list) -> set[int]:
    """Compute set of BIGINT z14 tile keys covering a GPS trace.

    Skips ``None`` entries (track-break sentinels from `_densify_coords`).
    """
    tiles: set[int] = set()
    for point in coords:
        if point is None:
            continue
        lon, lat = float(point[0]), float(point[1])
        _z, x, y = _latlon_to_z14_tile(lat, lon)
        tiles.add(_tile_key_from_xy(x, y))
    return tiles


def _load_known_osm_tiles(db) -> set[int]:
    """Load all tile keys from osm_road_edges into memory (one-time).

    Only caches non-empty results — if no PBF is imported yet, re-checks
    on next call (so a PBF import between calls is picked up automatically).
    """
    global _known_osm_tiles
    with _known_osm_tiles_lock:
        if _known_osm_tiles is not None:
            return _known_osm_tiles
    try:
        rows = db.execute(sa_text(
            "SELECT DISTINCT tile_key FROM osm_road_edges"
        )).fetchall()
    except Exception:
        # Defensive: the OSM substrate (osm_road_edges) is DROPPED in prod
        # under the raw-trace pivot (2026-07-29). Callers on the matched path
        # are already gated behind an information_schema existence check +
        # raw_display_enabled(), but if this is somehow reached the failed
        # statement aborts the transaction — roll it back so the caller's
        # subsequent writes don't cascade-fail, and behave as "no OSM data".
        db.rollback()
        logger.warning(
            "osm_road_edges unavailable — treating as no OSM coverage "
            "(raw-trace pivot / substrate dropped)", exc_info=True,
        )
        return set()
    tiles = {r[0] for r in rows}
    if tiles:  # only cache non-empty — allows PBF import to be picked up
        with _known_osm_tiles_lock:
            _known_osm_tiles = tiles
        logger.info("Loaded %d known OSM tile keys into memory", len(tiles))
    return tiles


def invalidate_osm_tile_cache() -> None:
    """Clear the in-memory tile cache (call after PBF re-import)."""
    global _known_osm_tiles
    with _known_osm_tiles_lock:
        _known_osm_tiles = None
    with _osm_segment_cache_lock:
        _osm_segment_cache.clear()
        _osm_segment_cache_order.clear()
    # Derived caches built FROM the segments must also be dropped, else after a
    # PBF re-import the matcher serves a stale grid / way-adjacency.
    with _osm_grid_cache_lock:
        _osm_grid_cache.clear()
        _osm_adjacency_cache.clear()


def _fetch_and_store_osm_tile(tile_key: int, db) -> int:
    """Fetch OSM road segments for a z14 tile from Overpass and store in DB.

    Returns segment count. On error/timeout returns 0 (caller uses grid-snap
    fallback). A tile that already has rows is never re-fetched — per-tile
    ``fetched_at`` staleness was dropped with migration 0056 (regions are
    refreshed wholesale via ``import_osm_roads``; the Overpass path is a
    dev-only gap-filler).
    """
    # Fast path: check in-memory cache first (avoids DB query)
    known = _load_known_osm_tiles(db)
    if tile_key in known:
        return -1  # already cached, skip

    # Fallback: DB existence check (handles tiles added outside this process)
    row = db.execute(sa_text(
        "SELECT 1 FROM osm_road_edges WHERE tile_key = :key LIMIT 1"
    ), {"key": tile_key}).fetchone()
    if row is not None:
        # Add to in-memory cache for next time
        with _known_osm_tiles_lock:
            if _known_osm_tiles is not None:
                _known_osm_tiles.add(tile_key)
        return -1  # already present, skip

    # Skip Overpass when disabled (PBF covers the region) or in test mode
    _overpass_enabled = os.environ.get("OVERPASS_ENABLED", "true").lower() == "true"
    if TEST_MODE or not _overpass_enabled:
        return 0

    # Parse tile key
    x, y = _tile_key_to_xy(tile_key)
    z = 14
    min_lon, min_lat, max_lon, max_lat = _tile_bbox(z, x, y)

    # Query Overpass
    highway_filter = "|".join(_ROUTABLE_HIGHWAYS)
    query = (
        f'[out:json][timeout:5];'
        f'way["highway"~"^({highway_filter})$"]({min_lat},{min_lon},{max_lat},{max_lon});'
        f'out geom;'
    )

    data = None
    with _osm_fetch_lock:
        for server_url in _OVERPASS_SERVERS:
            try:
                resp = httpx.post(server_url, data={"data": query}, timeout=_OSM_FETCH_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception:
                logger.debug("Overpass %s failed for tile %s, trying next", server_url, tile_key)
                continue
    # Rate-limit delay outside semaphore to avoid blocking other threads
    time.sleep(_OSM_FETCH_DELAY)

    if data is None:
        logger.warning("All Overpass servers failed for tile %s", tile_key)
        return 0

    # Parse ways into segments and store
    count = 0
    for element in data.get("elements", []):
        if element.get("type") != "way":
            continue
        tags = element.get("tags", {})
        hw_raw = tags.get("highway", "")
        if hw_raw not in _ROUTABLE_HIGHWAYS:
            continue

        # Full cascade classifier (mirrors the PBF import path) — returns
        # (class, confidence ∈ [0, 1]). The Overpass write path used to
        # call _normalize_surface and drop the confidence, which left every
        # Overpass-sourced osm_road_edges row with NULL surface_confidence
        # / ele_* / slope_grade after the 0041 + 0042 migrations. Dormant in
        # prod (OVERPASS_ENABLED=false) but hot in dev/CI.
        surface, surface_conf = _classify_surface({
            "surface": tags.get("surface", ""),
            "highway": hw_raw,
            "tracktype": tags.get("tracktype", ""),
            "smoothness": tags.get("smoothness", ""),
        })
        highway = _normalize_highway(hw_raw)
        osm_way_id = element.get("id", 0)
        geom = element.get("geometry", [])
        if len(geom) < 2:
            continue

        # Full way LineString → osm_ways side-table, ONCE per way (smooth
        # heatmap rendering). Rows written outside a regional PBF import
        # carry region='adhoc' (→ the DEFAULT osm_road_edges partition).
        way_wkt = "LINESTRING(" + ",".join(
            f'{g["lon"]} {g["lat"]}' for g in geom
        ) + ")"
        db.execute(sa_text("""
            INSERT INTO osm_ways (osm_way_id, way_geometry, region)
            VALUES (:way_id, ST_GeomFromText(:way_wkt, 4326), 'adhoc')
            ON CONFLICT (osm_way_id) DO UPDATE
            SET way_geometry = EXCLUDED.way_geometry
        """), {"way_id": osm_way_id, "way_wkt": way_wkt})

        for k in range(len(geom) - 1):
            lon1, lat1 = geom[k]["lon"], geom[k]["lat"]
            lon2, lat2 = geom[k + 1]["lon"], geom[k + 1]["lat"]

            # Skip degenerate segments
            seg_dist = _haversine_m(lon1, lat1, lon2, lat2)
            if seg_dist < 0.1:
                continue

            # Local DEM lookup — NULL when no HGT tile on disk for this
            # tile or the cell is a void. Same contract as the PBF import.
            e1, e2, slope = _dem_slope_grade(lat1, lon1, lat2, lon2)
            ele_delta = None if e1 is None else e2 - e1

            # Store the segment (region defaults to 'adhoc' → DEFAULT partition)
            db.execute(sa_text("""
                INSERT INTO osm_road_edges (
                    tile_key, osm_way_id, segment_idx, surface, highway,
                    geometry,
                    ele_start_m, ele_end_m, ele_delta_m, slope_grade,
                    surface_confidence
                )
                VALUES (:tile_key, :way_id, :seg_idx, :surface, :highway,
                        ST_SetSRID(ST_MakeLine(ST_MakePoint(:lon1, :lat1), ST_MakePoint(:lon2, :lat2)), 4326),
                        :e1, :e2, :ele_delta, :slope,
                        :surface_conf)
            """), {
                "tile_key": tile_key, "way_id": osm_way_id, "seg_idx": k,
                "surface": surface, "highway": highway,
                "lon1": lon1, "lat1": lat1, "lon2": lon2, "lat2": lat2,
                "e1": e1, "e2": e2, "ele_delta": ele_delta, "slope": slope,
                "surface_conf": surface_conf,
            })
            count += 1

    db.commit()
    # Add to in-memory cache
    with _known_osm_tiles_lock:
        if _known_osm_tiles is not None:
            _known_osm_tiles.add(tile_key)
    logger.info("OSM tile %s: %d segments stored", tile_key, count)
    return count


def _ensure_osm_tiles(coords: list, db) -> None:
    """Ensure all z14 tiles covering a trace are fetched and cached."""
    # Pre-load known tiles to avoid per-tile DB queries
    _load_known_osm_tiles(db)
    tiles = _coords_to_z14_tiles(coords)
    for tile_key in tiles:
        try:
            _fetch_and_store_osm_tile(tile_key, db)
        except Exception:
            logger.warning("Failed to fetch OSM tile %s, will use grid-snap fallback", tile_key, exc_info=True)


# ── Spatial grid + point-to-segment matching ─────────────────────────────────

def _point_to_segment_dist_m(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> float:
    """Distance (metres) from point P(lon,lat) to segment A-B(lon,lat).

    Projects P onto AB in lon/lat space, measures with haversine.
    NOTE: px/ax/bx = longitude, py/ay/by = latitude (lon-first like haversine_m).
    """
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return _haversine_m(px, py, ax, ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return _haversine_m(px, py, proj_x, proj_y)


# Pre-compute cos(45°) for mid-latitude France (~43-47°N) — good enough for
# the flat-earth approximation used in the hot matching loop.
_COS_LAT_FRANCE = math.cos(math.radians(45.0))
_DEG_TO_M = 111_320.0  # metres per degree of latitude


def _point_to_segment_dist_sq_fast(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> float:
    """Squared flat-earth distance from point P to segment A-B.

    ~100x faster than haversine — no trig, no sqrt.  Accurate to <1% for
    distances under a few km at mid-latitudes.  Returns metres² so callers
    must compare against radius² thresholds.
    """
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        dlat = (py - ay) * _DEG_TO_M
        dlon = (px - ax) * _DEG_TO_M * _COS_LAT_FRANCE
        return dlat * dlat + dlon * dlon
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    dlat = (py - (ay + t * dy)) * _DEG_TO_M
    dlon = (px - (ax + t * dx)) * _DEG_TO_M * _COS_LAT_FRANCE
    return dlat * dlat + dlon * dlon


def _project_onto_segment(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> tuple[float, float]:
    """Return the (lon, lat) of P's orthogonal projection onto segment A-B,
    clamped to the segment endpoints. Used to align noisy GPS samples to
    their matched OSM way so multiple riders' edges share identical
    geometry instead of fanning out as parallel offsets.
    """
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ax, ay
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return ax + t * dx, ay + t * dy


_ARC_LENGTH_BUCKET_M = 1.0  # cross-rider dedup tolerance along OSM segment


def _snap_along_segment(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
    bucket_m: float = _ARC_LENGTH_BUCKET_M,
) -> tuple[float, float]:
    """Snap an on-segment point (px, py) to the nearest bucket_m position
    along segment A-B, using arc-length (not lon/lat) so the snap
    respects the segment's orientation.

    Two riders' GPS samples projecting onto the same OSM segment within
    bucket_m/2 of each other along the trail RELIABLY collapse to the
    same (lon, lat) tuple → their heat_edges share an edge_key. Within
    bucket_m, ~50% collapse depending on whether the pair straddles a
    bucket boundary. At realistic GPS noise (a few metres of along-track
    jitter), the 1m bucket recovers most of the producer-side dedup
    property the deleted 5dp `_snap_fine` provided — without the
    orientation bug (see below).

    Latitude approximation: `seg_len_m` uses the file-wide
    `_DEG_TO_M * _COS_LAT_FRANCE` constants (cos(45°)). At France
    latitudes (43°-50°) this is within ~3% of true; at the equator
    it'd over-bucket by ~30%, at lat=60° under-bucket by ~30%. Since
    prod traces are France-only this is fine; non-France datasets
    would want per-segment cos(lat) instead.

    The deleted 5dp `_snap_fine` predecessor used lon/lat-axis rounding
    which didn't respect segment direction — two points 0.5m apart along
    a 45° segment could still snap to different 5dp cells AND get pushed
    off the OSM polyline by up to ~0.55m. Arc-length bucketing keeps the
    point ON the segment (0m perp distance preserved) while collapsing
    nearby projections to a shared key.

    The function assumes (px, py) was just produced by
    `_project_onto_segment(... ax, ay, bx, by)` so it already lies on
    A-B; t is re-derived from the same dot product. Cheap.
    """
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ax, ay
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    seg_len_lat_m = dy * _DEG_TO_M
    seg_len_lon_m = dx * _DEG_TO_M * _COS_LAT_FRANCE
    seg_len_m = math.sqrt(seg_len_lat_m * seg_len_lat_m + seg_len_lon_m * seg_len_lon_m)
    if seg_len_m < bucket_m:
        return ax + 0.5 * dx, ay + 0.5 * dy
    s = t * seg_len_m
    s_snapped = round(s / bucket_m) * bucket_m
    t_snapped = s_snapped / seg_len_m
    return ax + t_snapped * dx, ay + t_snapped * dy


class _OsmSegment:
    """Lightweight container for an OSM road segment loaded from DB."""
    __slots__ = ("lon1", "lat1", "lon2", "lat2", "surface", "highway", "osm_way_id", "seg_idx")

    def __init__(self, lon1, lat1, lon2, lat2, surface, highway, osm_way_id=0, seg_idx=0):
        self.lon1 = lon1
        self.lat1 = lat1
        self.lon2 = lon2
        self.lat2 = lat2
        self.surface = surface
        self.highway = highway
        self.osm_way_id = osm_way_id
        self.seg_idx = seg_idx


def _grid_cell(val: float) -> int:
    """Floor-divide for grid cell index (safe for negative coordinates)."""
    return math.floor(val / _OSM_GRID_SIZE)


def _build_segment_grid(segments: list[_OsmSegment]) -> dict[tuple[int, int], list[_OsmSegment]]:
    """Index segments into grid cells. Long segments added to ALL cells they cover."""
    grid: dict[tuple[int, int], list[_OsmSegment]] = defaultdict(list)
    for seg in segments:
        cx1 = _grid_cell(seg.lon1)
        cy1 = _grid_cell(seg.lat1)
        cx2 = _grid_cell(seg.lon2)
        cy2 = _grid_cell(seg.lat2)
        for cx in range(min(cx1, cx2), max(cx1, cx2) + 1):
            for cy in range(min(cy1, cy2), max(cy1, cy2) + 1):
                grid[(cx, cy)].append(seg)
    return grid


def _nearest_osm_segment(
    lon: float, lat: float,
    grid: dict[tuple[int, int], list[_OsmSegment]],
    max_dist_m: float = _OSM_MATCH_RADIUS_M,
) -> tuple[_OsmSegment | None, float | None]:
    """Find nearest OSM segment within max_dist_m using spatial grid.

    Uses fully-inlined flat-earth squared distance (no function calls, no trig)
    for the inner loop.  Only the final winner is converted to real metres.
    """
    cx = _grid_cell(lon)
    cy = _grid_cell(lat)
    best_dist_sq = float("inf")
    best_seg = None
    # Local-variable aliases — avoid global/attribute lookups in the hot loop
    _deg = _DEG_TO_M
    _cos = _COS_LAT_FRANCE
    _grid_get = grid.get
    _empty: list = []
    for ddx in (-1, 0, 1):
        for ddy in (-1, 0, 1):
            for seg in _grid_get((cx + ddx, cy + ddy), _empty):
                # Inline _point_to_segment_dist_sq_fast
                sdx = seg.lon2 - seg.lon1
                sdy = seg.lat2 - seg.lat1
                denom = sdx * sdx + sdy * sdy
                if denom == 0:
                    qlat = (lat - seg.lat1) * _deg
                    qlon = (lon - seg.lon1) * _deg * _cos
                else:
                    t = ((lon - seg.lon1) * sdx + (lat - seg.lat1) * sdy) / denom
                    if t < 0.0:
                        t = 0.0
                    elif t > 1.0:
                        t = 1.0
                    qlat = (lat - (seg.lat1 + t * sdy)) * _deg
                    qlon = (lon - (seg.lon1 + t * sdx)) * _deg * _cos
                d_sq = qlat * qlat + qlon * qlon
                if d_sq < best_dist_sq:
                    best_dist_sq = d_sq
                    best_seg = seg
    if best_seg is not None:
        best_dist_m = math.sqrt(best_dist_sq)
        if best_dist_m <= max_dist_m:
            return best_seg, best_dist_m
    return None, None


# ── HMM / Viterbi spatial map-matcher (Newson-Krumm 2009) ────────────────────
# The legacy spatial path snaps each GPS point to the NEAREST way within a hard
# radius and breaks the matched run when a point drifts past it. That both
# (a) FRAGMENTS continuity at curve apexes (one 20 m-off point → run break →
# grid-fallback), and (b) MIS-SNAPS to parallel features (the cycleway beside
# the road, the opposite carriageway) because "nearest" is decided per point in
# isolation. This matcher models the WHOLE trajectory:
#   emission  = Gaussian on perpendicular distance to a candidate way (small σ),
#   transition= plausibility of moving way A→B: on-network distance ≈ GPS step
#               AND the two ways are topologically connected.
# Viterbi picks the globally most-likely way sequence → it stays on the way it
# is already on (rejecting parallels) and tolerates brief off-radius excursions
# (fixing apex fragmentation). It is the refinement layer on top of the
# in-process spatial matcher — the sole map-matcher.
_HMM_SIGMA_M = 6.0                    # emission Gaussian σ on perp distance (m)
_HMM_BETA_M = 4.0                     # transition discrepancy scale (m)
_HMM_CONNECTED_PENALTY_M = 8.0        # extra route-distance for a junction hop
_HMM_DISCONNECTED_PENALTY_M = 500.0   # forbids teleporting between unconnected ways
_HMM_MAX_CANDIDATES = 5               # ways considered per point (Viterbi state space)
_HMM_SEARCH_MULT = 2.0                # candidate search radius = match_radius * this
_HMM_ENDPOINT_DP = 6                  # endpoint-coincidence precision (~0.11m = a shared OSM node)


def _hmm_enabled() -> bool:
    return os.environ.get("SPATIAL_HMM_ENABLED", "true").lower() == "true"


def _candidate_segments(
    lon: float, lat: float,
    grid: dict[tuple[int, int], list[_OsmSegment]],
    max_dist_m: float,
    k: int = _HMM_MAX_CANDIDATES,
) -> list[tuple[_OsmSegment, float]]:
    """The k closest OSM ways within ``max_dist_m`` — one (closest sub-segment,
    perp_dist_m) per ``osm_way_id`` so the Viterbi state space is "which way",
    not "which micro-segment". Mirrors `_nearest_osm_segment`'s inlined
    flat-earth distance but collects candidates instead of the single argmin."""
    cx = _grid_cell(lon)
    cy = _grid_cell(lat)
    _deg = _DEG_TO_M
    _cos = _COS_LAT_FRANCE
    _grid_get = grid.get
    _empty: list = []
    max_sq = max_dist_m * max_dist_m
    best_by_way: dict[int, tuple[float, _OsmSegment]] = {}
    for ddx in (-1, 0, 1):
        for ddy in (-1, 0, 1):
            for seg in _grid_get((cx + ddx, cy + ddy), _empty):
                sdx = seg.lon2 - seg.lon1
                sdy = seg.lat2 - seg.lat1
                denom = sdx * sdx + sdy * sdy
                if denom == 0:
                    qlat = (lat - seg.lat1) * _deg
                    qlon = (lon - seg.lon1) * _deg * _cos
                else:
                    t = ((lon - seg.lon1) * sdx + (lat - seg.lat1) * sdy) / denom
                    if t < 0.0:
                        t = 0.0
                    elif t > 1.0:
                        t = 1.0
                    qlat = (lat - (seg.lat1 + t * sdy)) * _deg
                    qlon = (lon - (seg.lon1 + t * sdx)) * _deg * _cos
                d_sq = qlat * qlat + qlon * qlon
                if d_sq > max_sq:
                    continue
                prev = best_by_way.get(seg.osm_way_id)
                if prev is None or d_sq < prev[0]:
                    best_by_way[seg.osm_way_id] = (d_sq, seg)
    cands = sorted(best_by_way.values(), key=lambda x: x[0])[:k]
    return [(seg, math.sqrt(d_sq)) for d_sq, seg in cands]


def _build_way_adjacency(segments: list[_OsmSegment]) -> dict[int, set[int]]:
    """way_id -> set of way_ids that share an OSM node (exact endpoint, 6dp).
    Real OSM junctions share the node coordinate exactly, so endpoint
    coincidence == topological connection without needing a router."""
    node_ways: dict[tuple[float, float], set[int]] = defaultdict(set)
    dp = _HMM_ENDPOINT_DP
    for seg in segments:
        node_ways[(round(seg.lat1, dp), round(seg.lon1, dp))].add(seg.osm_way_id)
        node_ways[(round(seg.lat2, dp), round(seg.lon2, dp))].add(seg.osm_way_id)
    adj: dict[int, set[int]] = defaultdict(set)
    for ways in node_ways.values():
        if len(ways) > 1:
            for w in ways:
                adj[w] |= ways
    for w in adj:
        adj[w].discard(w)
    return adj


def _segments_from_grid(grid: dict[tuple[int, int], list[_OsmSegment]]) -> list[_OsmSegment]:
    """Unique segments out of a (possibly cached) grid — long segments live in
    multiple cells, so dedup by identity."""
    seen: set[int] = set()
    out: list[_OsmSegment] = []
    for segs in grid.values():
        for seg in segs:
            if id(seg) not in seen:
                seen.add(id(seg))
                out.append(seg)
    return out


def _hmm_transition_cost(seg_p: _OsmSegment, seg_c: _OsmSegment, adj: dict[int, set[int]]) -> float:
    """Negative-log transition: |route_dist - gps_dist| / β, where the route
    distance is gps_dist + a tier penalty. Same way → ~0; connected junction →
    small; disconnected → effectively forbidden (rejects the parallel-feature
    hop, since road→cycleway→road is two costly transitions)."""
    if seg_p.osm_way_id == seg_c.osm_way_id:
        extra = 0.0
    elif seg_c.osm_way_id in adj.get(seg_p.osm_way_id, ()):
        extra = _HMM_CONNECTED_PENALTY_M
    else:
        extra = _HMM_DISCONNECTED_PENALTY_M
    return extra / _HMM_BETA_M


def _viterbi_point_matches(
    coords: list, sport: str,
    grid: dict[tuple[int, int], list[_OsmSegment]],
    adj: dict[int, set[int]],
) -> list[_OsmSegment | None]:
    """Most-likely OSM way per GPS point via Viterbi over the whole trajectory.
    Returns a `point_matches`-shaped list (chosen `_OsmSegment` or None) that the
    existing run-detection + edge-emission consume unchanged — so per-point
    `osm_way_id`, projection, edge keys and `_OSM_MIN_CONSECUTIVE` all still
    apply. A point with no candidate within the search radius → None (break →
    grid-fallback, exactly as before)."""
    search_radius = _osm_match_radius_for(sport) * _HMM_SEARCH_MULT
    inv2sigma2 = 1.0 / (2.0 * _HMM_SIGMA_M * _HMM_SIGMA_M)
    n = len(coords)
    cand_lists: list[list[tuple[_OsmSegment, float]] | None] = []
    for c in coords:
        if c is None:
            cand_lists.append(None)
        else:
            cand_lists.append(_candidate_segments(float(c[0]), float(c[1]), grid, search_radius))

    point_matches: list[_OsmSegment | None] = [None] * n
    i = 0
    while i < n:
        if not cand_lists[i]:          # None (break) or [] (no candidate)
            i += 1
            continue
        j = i
        while j < n and cand_lists[j]:  # maximal run where every point has a candidate
            j += 1
        _viterbi_run(coords, cand_lists, i, j, adj, inv2sigma2, point_matches)
        i = j
    return point_matches


def _viterbi_run(coords, cand_lists, start, end, adj, inv2sigma2, out) -> None:
    """Standard log-space Viterbi over cand_lists[start:end] (all non-empty);
    writes the chosen segment per point into ``out``."""
    cand_seq = [cand_lists[start]]
    # V[c] = min cost to reach candidate c at the current point.
    V = [dist * dist * inv2sigma2 for (_seg, dist) in cand_lists[start]]
    back: list[list[int | None]] = [[None] * len(V)]
    prev = cand_lists[start]
    for p in range(start + 1, end):
        cur = cand_lists[p]
        cand_seq.append(cur)
        newV = [float("inf")] * len(cur)
        newback: list[int | None] = [None] * len(cur)
        for ci, (seg_c, dist_c) in enumerate(cur):
            emit = dist_c * dist_c * inv2sigma2
            best = float("inf")
            best_pi = 0
            for pi, (seg_p, _dp) in enumerate(prev):
                cost = V[pi] + _hmm_transition_cost(seg_p, seg_c, adj)
                if cost < best:
                    best = cost
                    best_pi = pi
            newV[ci] = emit + best
            newback[ci] = best_pi
        V = newV
        back.append(newback)
        prev = cur
    # backtrack from the lowest-cost final state
    bi = min(range(len(V)), key=lambda x: V[x])
    for off in range(end - start - 1, -1, -1):
        out[start + off] = cand_seq[off][bi][0]
        if off > 0:
            bi = back[off][bi]


def _get_cached_tile_segments(tile_key: int, db) -> list:
    """Load OSM segments for a tile, with in-memory LRU cache.

    Single-tile loader retained as a stable seam — used by tests
    (`test_pending_bug_fixes.py::TestOsmMatchProjectsToOsmGeometry`
    monkey-patches this function to inject in-memory segments without a
    DB) and as a per-tile fallback for the lazy path.

    Bulk callers (`_match_to_osm`) should prefer
    ``_prewarm_osm_segments`` to load N tiles in one SQL query and avoid
    N round-trips on geographic cold tiles.
    """
    with _osm_segment_cache_lock:
        if tile_key in _osm_segment_cache:
            return _osm_segment_cache[tile_key]

    # Load from DB — dedup by (osm_way_id, segment_idx) instead of ST_AsText
    rows = db.execute(sa_text("""
        SELECT DISTINCT ON (osm_way_id, segment_idx)
               ST_X(ST_StartPoint(geometry)), ST_Y(ST_StartPoint(geometry)),
               ST_X(ST_EndPoint(geometry)), ST_Y(ST_EndPoint(geometry)),
               surface, highway, osm_way_id, segment_idx
        FROM osm_road_edges
        WHERE tile_key = :key
        ORDER BY osm_way_id, segment_idx
    """), {"key": tile_key}).fetchall()

    segments = [
        _OsmSegment(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7])
        for r in rows
    ]

    with _osm_segment_cache_lock:
        _osm_segment_cache[tile_key] = segments
        _osm_segment_cache_order.append(tile_key)
        # LRU eviction
        while len(_osm_segment_cache_order) > _osm_segment_cache_max():
            evict = _osm_segment_cache_order.pop(0)
            _osm_segment_cache.pop(evict, None)

    return segments


def _prewarm_osm_segments(coords: list, db) -> dict[int, list[_OsmSegment]]:
    """Pre-warm the OSM segment cache for an activity bbox in a single query.

    Computes the z14 tile set for ``coords`` (track-break sentinels are
    skipped via `_coords_to_z14_tiles`), looks up which of those tiles
    aren't already in `_osm_segment_cache`, and issues ONE SQL with
    ``WHERE tile_key = ANY(:keys)`` to fetch every missing tile's
    segments at once. Pre-May-2026 the per-tile lazy loader hit the DB
    once per cold tile — on a 50-activity audit that was 30 % of total
    ingest time (763 cold loads / 1404 ingests = 54 % miss rate). One
    query with `ANY` collapses N round-trips to 1.

    Concurrency: cache mutation is guarded by ``_osm_segment_cache_lock``.
    The DB query itself runs without the lock (we only hold it long
    enough to capture the missing-tile set, then again to publish results
    + LRU-evict). Multiple concurrent calls for overlapping tile sets
    are safe — each caller fetches what it sees as missing; duplicates
    just overwrite the cache entry with the same data.

    Defensive: if ``db is None`` (used by unit tests that monkey-patch
    `_get_cached_tile_segments`), skip the bulk SQL entirely. The
    caller's per-tile loop will then route through the patched
    `_get_cached_tile_segments`.

    Returns ``{tile_key: list[_OsmSegment]}`` for every tile in
    ``coords`` (cache hits + freshly-loaded misses, plus tiles whose
    bulk-query response was empty — those keys map to ``[]``).
    """
    tiles = _coords_to_z14_tiles(coords)
    if not tiles:
        return {}

    # Snapshot what's already cached to figure out what we still need.
    result: dict[int, list[_OsmSegment]] = {}
    missing: list[int] = []
    with _osm_segment_cache_lock:
        for tk in tiles:
            cached = _osm_segment_cache.get(tk)
            if cached is not None:
                result[tk] = cached
            else:
                missing.append(tk)

    if not missing or db is None:
        # Either every tile is hot, or we have no DB to consult (test path).
        # The caller will use `_get_cached_tile_segments` per tile, which
        # picks up the cached list or the test-installed monkey patch.
        return result

    # ONE bulk query for all missing tiles — replaces N per-tile round-trips.
    rows = db.execute(sa_text("""
        SELECT DISTINCT ON (tile_key, osm_way_id, segment_idx)
               tile_key,
               ST_X(ST_StartPoint(geometry)), ST_Y(ST_StartPoint(geometry)),
               ST_X(ST_EndPoint(geometry)), ST_Y(ST_EndPoint(geometry)),
               surface, highway, osm_way_id, segment_idx
        FROM osm_road_edges
        WHERE tile_key = ANY(:keys)
        ORDER BY tile_key, osm_way_id, segment_idx
    """), {"keys": missing}).fetchall()

    # Bucket rows by tile_key. Tiles with no rows still need an empty
    # entry so we cache the negative result and don't re-query them.
    bucketed: dict[int, list[_OsmSegment]] = {tk: [] for tk in missing}
    for r in rows:
        bucketed[r[0]].append(
            _OsmSegment(r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8])
        )

    # Publish to the shared cache + run LRU eviction.
    with _osm_segment_cache_lock:
        for tk, segs in bucketed.items():
            # Another thread may have populated this in the meantime —
            # prefer their copy to avoid making them pay another query
            # for the same tile_key.
            if tk in _osm_segment_cache:
                result[tk] = _osm_segment_cache[tk]
                continue
            _osm_segment_cache[tk] = segs
            _osm_segment_cache_order.append(tk)
            result[tk] = segs
        # LRU eviction (same policy as `_get_cached_tile_segments`).
        while len(_osm_segment_cache_order) > _osm_segment_cache_max():
            evict = _osm_segment_cache_order.pop(0)
            _osm_segment_cache.pop(evict, None)

    return result


def _match_to_osm(
    coords: list, sport: str, db,
) -> tuple[list[dict], list[list]]:
    """Match GPS coords to OSM road segments.

    Returns:
        (osm_matched_edges, fallback_coords) where:
        - osm_matched_edges: list of dicts with edge_key, sport, surface, highway, etc.
        - fallback_coords: list of GeoJSON [lon,lat,?ele] coords not matched to OSM

    The in-process per-point spatial matcher (with the Viterbi HMM
    refinement when ``SPATIAL_HMM_ENABLED`` is on, the default) is the
    sole map-matcher: GPS points are snapped to OSM segments, runs of
    consecutive matched points become ``match_source='spatial'`` edges,
    and unmatched points fall through as grid-fallback coords.
    """
    if not coords or len(coords) < 2:
        return [], coords

    # Load OSM segments per-tile with caching — avoids reloading for overlapping activities
    tile_keys = _coords_to_z14_tiles(coords)
    tile_key_set = frozenset(tile_keys)

    # Optimization #2: cache built spatial grids by tile set
    want_hmm = _hmm_enabled()
    with _osm_grid_cache_lock:
        cached_grid = _osm_grid_cache.get(tile_key_set)
        cached_adj = _osm_adjacency_cache.get(tile_key_set)
    adj: dict[int, set[int]] = {}
    if cached_grid is not None:
        grid = cached_grid
        if want_hmm:
            adj = cached_adj if cached_adj is not None else _build_way_adjacency(_segments_from_grid(grid))
    else:
        # Pre-warm cache: ONE bulk query for all tiles we don't have yet
        # (Improvement #B from May 2026 audit). Pre-warm only — the
        # per-tile lookup below still goes through `_get_cached_tile_segments`
        # so monkey-patches in tests (e.g.
        # `TestOsmMatchProjectsToOsmGeometry`) keep working as a seam.
        _prewarm_osm_segments(coords, db)

        all_segments: list[_OsmSegment] = []
        for tk in tile_keys:
            tile_segs = _get_cached_tile_segments(tk, db)
            all_segments.extend(tile_segs)

        if not all_segments:
            return [], coords

        if len(all_segments) > 5000:
            logger.warning("OSM match: %d segments loaded (%d tiles), matching may be slow", len(all_segments), len(tile_keys))

        grid = _build_segment_grid(all_segments)
        if want_hmm:
            adj = _build_way_adjacency(all_segments)

        # Cache the grid for reuse by other activities covering same tiles.
        # Skip caching for oversized grids — see comment on
        # _osm_grid_cache_max_segs_per_entry. Setting EITHER env var to 0
        # disables grid caching entirely (rebuild-each-call), which is
        # the right trade for memory-constrained workers like the Strava
        # import Cloud Run Job — segments stay in the tile cache so the
        # rebuild is CPU-only over an already-loaded list. The explicit
        # `> 0` check makes the "set to 0 to disable" contract load-bearing
        # on its own, not incidentally via the empty-segments early return
        # above (PR #345 review S2).
        grid_cache_max = _osm_grid_cache_max()
        segs_per_entry_max = _osm_grid_cache_max_segs_per_entry()
        cacheable = (
            grid_cache_max > 0
            and segs_per_entry_max > 0
            and len(all_segments) <= segs_per_entry_max
        )
        if cacheable:
            with _osm_grid_cache_lock:
                if len(_osm_grid_cache) < grid_cache_max:
                    _osm_grid_cache[tile_key_set] = grid
                    if want_hmm:
                        _osm_adjacency_cache[tile_key_set] = adj

    # Per-point matching. `coords` may contain `None` sentinels (track
    # breaks from `_densify_coords`) — leave them as None in
    # `point_matches` so the run-detection below treats a break as a
    # forced run boundary (you can't have a "consecutive match" across
    # a GPS dropout).
    if want_hmm:
        # Trajectory-aware: Viterbi over the whole sequence (emission =
        # Gaussian on perp distance, transition = topological plausibility).
        # Drop-in for the per-point nearest-snap below — same point_matches
        # shape, so all downstream run/edge logic is unchanged.
        point_matches = _viterbi_point_matches(coords, sport, grid, adj)
    else:
        match_radius = _osm_match_radius_for(sport)
        point_matches = []
        for c in coords:
            if c is None:
                point_matches.append(None)
                continue
            lon, lat = float(c[0]), float(c[1])
            seg, _ = _nearest_osm_segment(lon, lat, grid, max_dist_m=match_radius)
            point_matches.append(seg)

    # Apply 2-consecutive-point filter and group into matched/unmatched runs
    osm_edges: list[dict] = []
    fallback_coords: list[list] = []
    seen_keys: set[str] = set()

    i = 0
    while i < len(coords):
        # Track-break sentinel — propagate to fallback so the grid-snap
        # loop downstream also breaks at this point.
        if coords[i] is None:
            fallback_coords.append(None)
            i += 1
            continue
        seg = point_matches[i]
        if seg is None:
            # Unmatched — collect contiguous run of unmatched points
            fallback_coords.append(coords[i])
            i += 1
            continue

        # Check for consecutive match. A long real-world trail in OSM is
        # often split into many small `osm_way_id`s (way breaks at
        # intersections, name changes, ref boundaries). With ~5 m GPS
        # noise, two consecutive points can land on adjacent ways of the
        # *same physical trail* — strict same-way matching then fails
        # the `_OSM_MIN_CONSECUTIVE` filter and the whole run falls
        # through to grid-snap. Pre-fix data showed road / running
        # match rates of 1.6 % / 6.6 % largely because of this.
        #
        # The relaxed rule is "any OSM match counts as consecutive". The
        # per-point tagging below still uses `point_matches[k]` so each
        # edge ends up with the correct surface / highway / osm_way_id —
        # we only loosen the run-detection. Edges still get projected
        # onto their own matched segment via `_project_onto_segment` so
        # offset GPS still collapses to the trail.
        consecutive_count = 1
        j = i + 1
        while j < len(coords) and point_matches[j] is not None:
            consecutive_count += 1
            j += 1

        if consecutive_count < _OSM_MIN_CONSECUTIVE:
            # Not enough consecutive points — treat as unmatched
            for k in range(i, j):
                fallback_coords.append(coords[k])
            i = j
            continue

        # Build edges along the OSM way (not along the noisy GPS trace).
        #
        # Earlier versions used the GPS coords directly as edge endpoints —
        # the comment back then read "we use consecutive GPS points as
        # endpoints. This ensures edges follow the actual GPS trace and
        # connect seamlessly with grid-snap edges." That was wrong for
        # the heatmap rendering: with ~10 m GPS noise on a hiking trail,
        # each rider's edges land at slightly different 5dp grid cells
        # (perpendicular to the trail), so they never merge — the
        # heatmap renders as a "feather" of parallel offset segments
        # fanning around the actual OSM trail (Eminem-grade spaghetti).
        #
        # Fix: project each GPS point onto its matched OSM segment with
        # `_project_onto_segment`. Multiple riders' points along the same
        # trail collapse to identical positions on the OSM line, so the
        # heat_edges merge naturally instead of fanning out.
        # Per-point surface/highway still comes from `point_matches[k]`.
        fallback_surface = (seg.surface or "unknown") if seg else "unknown"
        fallback_highway = (seg.highway or "unknown") if seg else "unknown"

        for k in range(i, j - 1):
            c1, c2 = coords[k], coords[k + 1]
            seg1 = point_matches[k]
            seg2 = point_matches[k + 1]
            # Project onto the matched OSM segment, then bucket along the
            # segment's arc-length so two riders within 1m of each other
            # collapse to the same (lon, lat) — preserving cross-rider
            # K-anonymity at the producer layer. seg1/seg2 are non-None
            # here because the run filter above only picks matched runs.
            if seg1:
                p1_lon, p1_lat = _project_onto_segment(
                    float(c1[0]), float(c1[1]),
                    seg1.lon1, seg1.lat1, seg1.lon2, seg1.lat2,
                )
                p1_lon, p1_lat = _snap_along_segment(
                    p1_lon, p1_lat,
                    seg1.lon1, seg1.lat1, seg1.lon2, seg1.lat2,
                )
            else:
                p1_lon, p1_lat = float(c1[0]), float(c1[1])
            if seg2:
                p2_lon, p2_lat = _project_onto_segment(
                    float(c2[0]), float(c2[1]),
                    seg2.lon1, seg2.lat1, seg2.lon2, seg2.lat2,
                )
                p2_lon, p2_lat = _snap_along_segment(
                    p2_lon, p2_lat,
                    seg2.lon1, seg2.lat1, seg2.lon2, seg2.lat2,
                )
            else:
                p2_lon, p2_lat = float(c2[0]), float(c2[1])
            # Arc-length bucketing (vs lon/lat-axis 5dp snap) keeps the
            # point exactly on the OSM polyline (0m perp distance) while
            # collapsing nearby projections to a shared edge_key — see
            # `_snap_along_segment` docstring + PR #374 audit.
            sp1 = (p1_lat, p1_lon)
            sp2 = (p2_lat, p2_lon)
            if sp1 == sp2:
                continue
            key = _edge_key(sport, sp1, sp2)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            a_canonical, b_canonical = sorted([sp1, sp2])
            is_canonical = (sp1 == a_canonical)

            ele1 = float(c1[2]) if len(c1) > 2 and c1[2] is not None else None
            ele2 = float(c2[2]) if len(c2) > 2 and c2[2] is not None else None
            ele_delta_m = 0.0
            if ele1 is not None and ele2 is not None:
                raw_delta = ele2 - ele1
                ele_delta_m = raw_delta if is_canonical else -raw_delta

            dist_m = _haversine_m(float(c1[0]), float(c1[1]), float(c2[0]), float(c2[1]))
            slope_grade = abs(ele_delta_m) / dist_m * 100.0 if ele_delta_m != 0.0 and dist_m > 0 else 0.0

            # Use the surface/highway from the OSM segment matched at this point
            pt_seg = point_matches[k]
            pt_surface = (pt_seg.surface or fallback_surface) if pt_seg else fallback_surface
            pt_highway = (pt_seg.highway or fallback_highway) if pt_seg else fallback_highway

            osm_edges.append({
                "edge_key": key,
                "sport": sport,
                "is_canonical": is_canonical,
                "ele_delta_m": ele_delta_m,
                "slope_grade": slope_grade,
                "a_lat": a_canonical[0], "a_lon": a_canonical[1],
                "b_lat": b_canonical[0], "b_lon": b_canonical[1],
                "surface": pt_surface,
                "highway": pt_highway,
                # Per-point way id (NOT the run-level `seg`): a long ride
                # crosses many OSM ways, and the display aggregation groups by
                # osm_way_id to merge cross-rider heat onto one line per way.
                # Using the run's single `seg` collapsed a whole ride onto ~2
                # way ids → the aggregation mis-grouped unrelated edges and
                # pulled the wrong way_geometry. `pt_seg = point_matches[k]` is the
                # segment this edge's start point actually matched (same source
                # the surface/highway above use, as the run comment promises).
                "osm_way_id": pt_seg.osm_way_id if pt_seg else (seg.osm_way_id if seg else None),
                "match_source": "spatial",
            })

        i = j

    return osm_edges, fallback_coords


def _edge_key(sport: str, p1: tuple[float, float], p2: tuple[float, float]) -> str:
    """Canonical direction-agnostic edge key."""
    a, b = sorted([p1, p2])
    return f"{sport}/{a[0]},{a[1]}/{b[0]},{b[1]}"


_GRID_STEP = 0.0001  # ~11m (same as round(val, 4))


def _bearing(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Bearing in degrees (0-360) from p1 to p2. Tuples are (lat, lon).
    Applies cos(lat) correction for longitude at mid-latitude."""
    dlat = p2[0] - p1[0]
    cos_lat = math.cos(math.radians((p1[0] + p2[0]) / 2))
    dlon = (p2[1] - p1[1]) * cos_lat
    return math.degrees(math.atan2(dlon, dlat)) % 360


def _bearing_diff(a1: tuple[float, float], a2: tuple[float, float],
                  b1: tuple[float, float], b2: tuple[float, float]) -> float:
    """Minimum angle between two edges (0-180). Handles reversed direction."""
    ba = _bearing(a1, a2)
    bb = _bearing(b1, b2)
    diff = abs(ba - bb) % 360
    return min(diff, 360 - diff, abs(diff - 180))


# Module-level cardinal offsets for neighbor clustering.
# ── Spatial index for fast canonical merge ───────────────────────────────────
# Instead of testing 440 key combinations per edge (brute-force), we index
# existing edge endpoints into a grid dict for O(1) cell lookups.
# Merge radius: ±5 grid steps (~5.5m) per endpoint, same as before.

_MERGE_STEPS = 3  # grid steps in each direction (~3.3m merge radius)


def _build_endpoint_index(
    existing_endpoints: dict[str, tuple[tuple[float, float], tuple[float, float]]],
) -> dict[tuple[float, float], list[str]]:
    """Index existing edges by both endpoints (snapped to 5dp grid).

    Returns: dict mapping (lat, lon) → list of edge_keys that have an endpoint there.
    """
    idx: dict[tuple[float, float], list[str]] = {}
    for key, (ep1, ep2) in existing_endpoints.items():
        idx.setdefault(ep1, []).append(key)
        idx.setdefault(ep2, []).append(key)
    return idx


def _find_canonical_edge(
    sport: str,
    p1: tuple[float, float],
    p2: tuple[float, float],
    existing_keys: set[str],
    existing_endpoints: dict[str, tuple[tuple[float, float], tuple[float, float]]],
    existing_pass_counts: dict[str, int],
    endpoint_index: dict[tuple[float, float], list[str]] | None = None,
) -> str:
    """Check if a nearby edge already exists via spatial index lookup.

    Uses endpoint_index (grid-cell dict) for O(1) lookups instead of brute-force combos.
    Effective merge radius: ~3.3m per endpoint (3 grid steps at 5dp ≈ 1.1m/step).
    Only merges if bearing difference <= 30 degrees.
    """
    if p1 == p2:
        return _edge_key(sport, p1, p2)

    key = _edge_key(sport, p1, p2)
    if key in existing_keys:
        return key

    if not endpoint_index:
        return key  # no index → skip merge (same as SKIP_CANONICAL_MERGE)

    # Collect candidate edges: find edges with an endpoint near p1 OR p2
    s = _GRID_STEP
    candidates_set: set[str] = set()
    for pt in (p1, p2):
        lat, lon = pt
        for di in range(-_MERGE_STEPS, _MERGE_STEPS + 1):
            # Check lat offset (cardinal N/S)
            cell_lat = round(lat + di * s, 4)
            for edge_key in endpoint_index.get((cell_lat, lon), ()):
                candidates_set.add(edge_key)
            # Check lon offset (cardinal E/W)
            if di != 0:
                cell_lon = round(lon + di * s, 4)
                for edge_key in endpoint_index.get((lat, cell_lon), ()):
                    candidates_set.add(edge_key)

    # Filter: both endpoints must be within merge radius AND bearing matches
    candidates = []
    for ckey in candidates_set:
        if ckey == key:
            continue
        ep1, ep2 = existing_endpoints[ckey]
        # Check both endpoints are within merge radius (~5.5m)
        d1 = min(abs(p1[0] - ep1[0]) + abs(p1[1] - ep1[1]),
                 abs(p1[0] - ep2[0]) + abs(p1[1] - ep2[1]))
        d2 = min(abs(p2[0] - ep1[0]) + abs(p2[1] - ep1[1]),
                 abs(p2[0] - ep2[0]) + abs(p2[1] - ep2[1]))
        if d1 > _MERGE_STEPS * s * 2 or d2 > _MERGE_STEPS * s * 2:
            continue
        if _bearing_diff(p1, p2, ep1, ep2) <= 30:
            candidates.append(ckey)

    if candidates:
        best = max(candidates, key=lambda k: (existing_pass_counts.get(k, 0), k))
        return best

    return key


def _load_existing_keys(
    db, sport: str, bbox: tuple[float, float, float, float],
) -> tuple[set[str], dict[str, tuple[tuple[float, float], tuple[float, float]]], dict[str, int]]:
    """Load existing edge keys within bbox + buffer for neighbor lookups.

    Args:
        db: SQLAlchemy session
        sport: Sport filter
        bbox: (min_lat, min_lon, max_lat, max_lon)

    Returns:
        (existing_keys, existing_endpoints, existing_pass_counts)
    """
    buffer = 0.0001  # 10x grid step at 5dp = ~11m (covers ±5 step merge radius with margin)
    min_lat, min_lon, max_lat, max_lon = bbox
    rows = db.execute(sa_text("""
        SELECT edge_key,
               ST_Y(ST_StartPoint(geometry)) AS lat1,
               ST_X(ST_StartPoint(geometry)) AS lon1,
               ST_Y(ST_EndPoint(geometry)) AS lat2,
               ST_X(ST_EndPoint(geometry)) AS lon2,
               pass_count
        FROM heat_edges
        WHERE sport = :sport
          AND geometry && ST_MakeEnvelope(:xmin, :ymin, :xmax, :ymax, 4326)
    """), {
        "sport": sport,
        "xmin": min_lon - buffer, "ymin": min_lat - buffer,
        "xmax": max_lon + buffer, "ymax": max_lat + buffer,
    }).fetchall()

    existing_keys: set[str] = set()
    existing_endpoints: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {}
    existing_pass_counts: dict[str, int] = {}
    for row in rows:
        key = row[0]
        existing_keys.add(key)
        existing_endpoints[key] = ((row[1], row[2]), (row[3], row[4]))
        existing_pass_counts[key] = row[5]
    return existing_keys, existing_endpoints, existing_pass_counts


def _upsert_edge(db, edge_data: dict, new_keys: set[str], new_contributor_keys: set[str],
                  existing_keys: set[str], existing_endpoints: dict, existing_pass_counts: dict,
                  osm_tags: dict | None = None) -> None:
    """UPSERT a single edge into heat_edges + contributor tracking.

    Args:
        osm_tags: if provided, sets surface_type/highway_type on INSERT from OSM data.
    """
    _upsert_edges_batch(db, [edge_data], new_keys, new_contributor_keys,
                        existing_keys, existing_endpoints, existing_pass_counts,
                        osm_tags=[osm_tags])


_BATCH_UPSERT_SIZE = 500  # edges per multi-row INSERT


def _upsert_edges_batch(
    db, edges: list[dict], new_keys: set[str], new_contributor_keys: set[str],
    existing_keys: set[str], existing_endpoints: dict, existing_pass_counts: dict,
    osm_tags: list[dict | None] | None = None,
) -> None:
    """Batch UPSERT edges into heat_edges + contributor tracking.

    Uses UNNEST-based multi-row INSERT for O(1) round-trips instead of O(n).
    """
    if not edges:
        return

    if osm_tags is None:
        osm_tags = [None] * len(edges)

    # Prepare arrays for UNNEST
    keys, sports = [], []
    fwds, bwds = [], []
    ele_deltas, slopes = [], []
    a_lons, a_lats, b_lons, b_lats = [], [], [], []
    surfaces, highways = [], []
    uid_hashes, activity_dates, activity_ids = [], [], []

    osm_way_ids = []
    match_sources, match_confidences = [], []

    for i, ed in enumerate(edges):
        is_can = ed["is_canonical"]
        keys.append(ed["edge_key"])
        sports.append(ed["sport"])
        fwds.append(1 if is_can else 0)
        bwds.append(0 if is_can else 1)
        ele_deltas.append(ed["ele_delta_m"])
        slopes.append(ed["slope_grade"])
        a_lons.append(ed["a_lon"])
        a_lats.append(ed["a_lat"])
        b_lons.append(ed["b_lon"])
        b_lats.append(ed["b_lat"])
        tags = osm_tags[i]
        surfaces.append(tags.get("surface", "unknown") if tags else "unknown")
        highways.append(tags.get("highway", "unknown") if tags else "unknown")
        uid_hashes.append(ed["user_id_hash"])
        activity_dates.append(ed["activity_date"])
        activity_ids.append(ed["activity_id"])
        osm_way_ids.append(ed.get("osm_way_id"))
        match_sources.append(ed.get("match_source"))
        match_confidences.append(ed.get("match_confidence"))

    # Process in chunks — build multi-row VALUES for batch UPSERT
    for start in range(0, len(keys), _BATCH_UPSERT_SIZE):
        end = min(start + _BATCH_UPSERT_SIZE, len(keys))
        chunk_size = end - start

        # ── Step 1: UPSERT contributors FIRST.
        # ``xmax = 0`` distinguishes a brand-new INSERT (no prior tuple, so
        # the deleting-tx column is unset) from an ON CONFLICT update
        # (xmax is the inserting xid of the conflict candidate). This is
        # the canonical Postgres pattern for "tell me which rows were
        # actually new". We use that flag below to gate the
        # ``pass_count`` bump on heat_edges — the whole point of
        # migration 0052 is that re-ingesting the SAME activity must
        # NOT inflate pass_count.
        contrib_parts = []
        contrib_params: dict = {}
        for idx in range(chunk_size):
            j = start + idx
            p = f"_{idx}"
            contrib_parts.append(f"(:ck{p}, :cu{p}, :caid{p}, :cd{p})")
            contrib_params.update({
                f"ck{p}": keys[j], f"cu{p}": uid_hashes[j],
                f"caid{p}": activity_ids[j], f"cd{p}": activity_dates[j],
            })

        contrib_sql = ",\n".join(contrib_parts)
        contrib_rows = db.execute(sa_text(f"""
            INSERT INTO heat_edge_contributors (edge_key, user_id_hash, activity_id, activity_date)
            VALUES {contrib_sql}
            ON CONFLICT (edge_key, user_id_hash, activity_id) DO UPDATE
              SET activity_date = GREATEST(EXCLUDED.activity_date, heat_edge_contributors.activity_date)
            RETURNING edge_key, (xmax = 0) AS is_new_contributor
        """), contrib_params).fetchall()

        # Edge-keys whose (edge_key, user_id_hash, activity_id) tuple
        # was freshly inserted in this chunk. Drives pass_count bump.
        new_contrib_in_chunk: set[str] = set()
        for row in contrib_rows:
            new_contributor_keys.add(row[0])
            if row[1]:
                new_contrib_in_chunk.add(row[0])

        # ── Step 2: UPSERT heat_edges with pass_count gated on new contributors.
        # On conflict, only bump pass_count if THIS contributor row was
        # newly inserted in step 1. Same activity re-ingest → no new
        # contributor → no pass_count change. Different activity by same
        # user → new contributor row → pass_count += 1.
        values_parts = []
        params: dict = {"new_contrib_keys": list(new_contrib_in_chunk)}
        for idx in range(chunk_size):
            j = start + idx
            p = f"_{idx}"
            # `pass_count` in the VALUES list reflects whether this is a
            # fresh insert. For brand-new heat_edges rows we always want
            # pass_count=1 (the row didn't exist before; this is the
            # first contribution). For conflict-updates the ON CONFLICT
            # branch overrides this with the gated increment below.
            init_pc = 1 if keys[j] in new_contrib_in_chunk else 1
            values_parts.append(
                f"(:k{p}, :s{p}, :pc{p}, :f{p}, :b{p}, :e{p}, :sl{p}, 0, :sf{p}, :hw{p},"
                f" ST_MakeLine(ST_MakePoint(:alon{p}, :alat{p}), ST_MakePoint(:blon{p}, :blat{p})),"
                f" :owid{p}, :msrc{p}, :mconf{p})"
            )
            params.update({
                f"k{p}": keys[j], f"s{p}": sports[j],
                f"pc{p}": init_pc,
                f"f{p}": fwds[j], f"b{p}": bwds[j],
                f"e{p}": ele_deltas[j], f"sl{p}": slopes[j],
                f"alon{p}": a_lons[j], f"alat{p}": a_lats[j],
                f"blon{p}": b_lons[j], f"blat{p}": b_lats[j],
                f"sf{p}": surfaces[j], f"hw{p}": highways[j],
                f"owid{p}": osm_way_ids[j],
                f"msrc{p}": match_sources[j],
                f"mconf{p}": match_confidences[j],
            })

        values_sql = ",\n".join(values_parts)
        rows = db.execute(sa_text(f"""
            INSERT INTO heat_edges (edge_key, sport, pass_count, forward_count, backward_count,
                ele_delta_m, slope_grade, user_count, surface_type, highway_type, geometry,
                osm_way_id, match_source, match_confidence)
            VALUES {values_sql}
            ON CONFLICT (edge_key, sport) DO UPDATE SET
                pass_count = heat_edges.pass_count + CASE
                    WHEN heat_edges.edge_key = ANY(:new_contrib_keys) THEN 1
                    ELSE 0
                END,
                forward_count = heat_edges.forward_count + CASE
                    WHEN heat_edges.edge_key = ANY(:new_contrib_keys) THEN EXCLUDED.forward_count
                    ELSE 0
                END,
                backward_count = heat_edges.backward_count + CASE
                    WHEN heat_edges.edge_key = ANY(:new_contrib_keys) THEN EXCLUDED.backward_count
                    ELSE 0
                END,
                ele_delta_m = CASE WHEN heat_edges.ele_delta_m = 0 AND EXCLUDED.ele_delta_m != 0
                                   THEN EXCLUDED.ele_delta_m ELSE heat_edges.ele_delta_m END,
                slope_grade = CASE WHEN heat_edges.slope_grade = 0 AND EXCLUDED.slope_grade != 0
                                   THEN EXCLUDED.slope_grade ELSE heat_edges.slope_grade END,
                osm_way_id = COALESCE(heat_edges.osm_way_id, EXCLUDED.osm_way_id),
                -- Keep the existing match_source; only set it when the row
                -- had none. Values are 'spatial' / 'grid_fallback'.
                match_source = CASE
                    WHEN heat_edges.match_source IS NULL THEN EXCLUDED.match_source
                    ELSE heat_edges.match_source
                END,
                match_confidence = GREATEST(
                    COALESCE(heat_edges.match_confidence, 0),
                    COALESCE(EXCLUDED.match_confidence, 0)
                )
            RETURNING edge_key, pass_count
        """), params).fetchall()

        for row in rows:
            # pass_count=1 after INSERT-or-UPDATE only happens when the
            # row was just inserted (no prior pass_count to add 0 or 1
            # to). xmax check is not available here because heat_edges
            # is a partitioned parent — PG rejects system-column reads
            # against partition root RETURNING. Use the pass_count==1
            # heuristic, same as pre-migration-0052 code.
            if row[1] == 1:
                new_keys.add(row[0])

    # Update in-memory tracking for all edges
    for ed in edges:
        key = ed["edge_key"]
        existing_keys.add(key)
        a_can = (ed["a_lat"], ed["a_lon"])
        b_can = (ed["b_lat"], ed["b_lon"])
        existing_endpoints[key] = (a_can, b_can)
        existing_pass_counts[key] = existing_pass_counts.get(key, 0) + 1


def _normalize_heat_edge_sport(sport: str) -> str:
    """Map activity sport to a valid heat_edges partition key.

    heat_edges is LIST-partitioned by sport. Real partitions:
    `road`, `gravel`, `mtb`, `offroad` (added in migration 0035),
    `running`. Anything else lands in `heat_edges_default` (catch-all).

    Until May 2026 `offroad` was rewritten to `gravel` because it had
    no real partition, which erased the rider's intent in analytics.
    Now offroad keeps its own bucket; routing for offroad still
    queries gravel + mtb (+ offroad itself) via RELATED_SPORTS so the
    overall corridor is wider than just one partition.

    Test sports like `cache_test_mtb` are still stripped to keep the
    default partition empty.
    """
    if sport.startswith("cache_test_") or sport.startswith("test_"):
        return "gravel"
    if sport in ("road", "gravel", "mtb", "offroad", "running"):
        return sport
    # Unknown sport — default to gravel rather than poisoning the default
    # partition. Log a WARNING so a typo in upstream code (e.g. `"grravel"`)
    # or a stale sport enum surfaces in Sentry instead of silently
    # polluting the gravel heatmap. Audit 2026-05-27 S3.1.
    logger.warning(
        "Unknown sport %r coerced to 'gravel' in _normalize_heat_edge_sport — "
        "check upstream classifier for typos / stale enum values", sport,
    )
    return "gravel"


def _maintain_heat_agg(db, touched_way_ids: list[int]) -> None:
    """Incrementally refresh ``heat_edges_agg`` for the ways an activity
    touched, with a structured per-update log line (so Cloud Logging shows
    the aggregate staying fresh).

    Best-effort: a failure here must NOT fail the ingest (the heat_edges
    write already committed; the drift check + next backfill are the safety
    net). Skipped entirely during a bulk rebuild (SKIP_HEAT_AGG_MAINTENANCE).
    """
    if not touched_way_ids:
        return
    try:
        from app.jobs.rebuild_heat_agg import (
            agg_maintenance_enabled,
            recompute_heat_agg_for_ways,
        )
    except Exception:  # noqa: BLE001 — import guard, never fail ingest
        return
    if not agg_maintenance_enabled():
        return
    try:
        t0 = time.time()
        deleted, upserted = recompute_heat_agg_for_ways(db, touched_way_ids)
        db.commit()
        logger.info(
            "heat_agg incremental: ways=%d upserted=%d deleted=%d took=%.0fms",
            len(touched_way_ids), upserted, deleted, (time.time() - t0) * 1000,
        )
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning(
            "heat_agg incremental refresh failed for %d ways (heat_edges already "
            "committed; drift check will catch it)", len(touched_way_ids),
            exc_info=True,
        )
        try:
            import sentry_sdk
            sentry_sdk.capture_exception()
        except Exception:  # noqa: BLE001
            pass


def _update_heat_edges(
    user_id: str,
    sport: str,
    geojson_str: str | None,
    activity_date: datetime | None = None,
    activity_id: str | None = None,
    collect_touched_ways: set[int] | None = None,
) -> tuple[int, set[str]]:
    """Compute GPS edges and update community heat_edges in DB.

    Two-phase pipeline:
    1. OSM map-matching: snap GPS points to OSM road segments (if osm_road_edges table exists)
    2. Grid-snap fallback: remaining unmatched points use the 5dp grid-snap pipeline

    Returns (edge_count, new_keys) where new_keys are freshly created edges.

    ``activity_id`` is the contributor-identity key in
    ``heat_edge_contributors``. When the same activity is re-ingested
    (rebuild_heatmap, fixture bootstrap, retry) the same activity_id is
    passed → contributors UPSERT collapses to a no-op → pass_count
    stays put. When None (legacy callers, tests with no activity
    context) a fresh uuid4() is generated each call — preserves the
    pre-migration behaviour of "each call counts as a distinct
    contribution".

    ``collect_touched_ways`` (archive-drain batching, 2026-07-20 incident):
    when a set is passed, the per-activity incremental ``heat_edges_agg``
    recompute is DEFERRED — the touched osm_way_ids are added to the set
    instead, and the caller runs ONE deduplicated recompute for the whole
    batch. A single 2,813-activity archive recomputing the same ways dozens
    of times (5-46 s of SQL each) pegged db-f1-micro for ~7 h and DoS'd the
    site. When None (default — /imports/files, webhook, single upload), the
    per-activity incremental recompute runs as before (near-live map).
    """
    import uuid as _uuid_mod
    if activity_id is None:
        activity_id = str(_uuid_mod.uuid4())
    if not geojson_str:
        return 0, set()
    # Normalize: keep activity.sport as-is for labeling, but store heat_edges
    # under a real partition key (road/gravel/mtb/running). Avoids ballooning
    # heat_edges_default with offroad / test_* labels.
    sport = _normalize_heat_edge_sport(sport)
    try:
        coords = json.loads(geojson_str).get("coordinates", [])
    except (json.JSONDecodeError, AttributeError):
        return 0, set()

    # Densify before matching (20m max gap)
    coords = _resegment_coords(coords)

    from app.db.session import SessionLocal
    db = SessionLocal()

    user_id_hash = heat_user_id_hash(user_id)
    new_keys: set[str] = set()
    new_contributor_keys: set[str] = set()
    osm_edge_count = 0
    grid_edge_count = 0

    # ── Phase 1: OSM map-matching ─────────────────────────────────────────────
    fallback_coords = coords  # default: all coords go to grid-snap
    osm_edges: list[dict] = []
    try:
        # Check if osm_road_edges table exists. Postgres metadata lookup is
        # microseconds — not worth caching. The previous module-level cache
        # silently degraded quality for the lifetime of an instance whenever
        # OSM data was imported AFTER backend boot (the cached "no" never
        # refreshed → 100 % grid-fallback even with osm_road_edges populated).
        osm_table_exists = db.execute(sa_text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'osm_road_edges'
            )
        """)).scalar()

        if osm_table_exists:
            # Only fetch new OSM tiles if not in bulk import mode.
            # Overpass API is too slow/rate-limited for bulk ingestion.
            if os.environ.get("SKIP_OSM_FETCH", "").lower() != "true":
                _ensure_osm_tiles(coords, db)
            osm_edges, fallback_coords = _match_to_osm(coords, sport, db)
    except Exception:
        logger.warning("OSM matching failed, falling back to grid-snap", exc_info=True)
        fallback_coords = coords
        osm_edges = []

    # Compute bbox from all coords for existing key loading.
    # Track-break sentinels (None) carry no coordinate — skip them.
    all_lats: list[float] = []
    all_lons: list[float] = []
    for c in coords:
        if c is None:
            continue
        all_lats.append(float(c[1]))
        all_lons.append(float(c[0]))
    if not all_lats:
        db.close()
        return 0, set()

    # SKIP_CANONICAL_MERGE: skip the expensive bbox query + 441-check neighbor merge.
    # ON CONFLICT handles dedup anyway. Set during rebuild for ~10x speedup.
    skip_all_merge = os.environ.get("SKIP_CANONICAL_MERGE", "").lower() == "true"
    if skip_all_merge:
        existing_keys: set[str] = set()
        existing_endpoints: dict = {}
        existing_pass_counts: dict = {}
    else:
        activity_bbox = (min(all_lats), min(all_lons), max(all_lats), max(all_lons))
        existing_keys, existing_endpoints, existing_pass_counts = _load_existing_keys(
            db, sport, activity_bbox,
        )

    try:
        # ── Phase 2a: UPSERT OSM-matched edges (batched) ──────────────────────
        seen_osm: set[str] = set()
        deduped_osm_edges: list[dict] = []
        osm_edge_tags: list[dict | None] = []
        for edge_data in osm_edges:
            edge_data["user_id_hash"] = user_id_hash
            edge_data["activity_date"] = activity_date
            edge_data["activity_id"] = activity_id
            key = edge_data["edge_key"]
            if key in seen_osm:
                continue
            seen_osm.add(key)
            deduped_osm_edges.append(edge_data)
            osm_edge_tags.append({"surface": edge_data.get("surface", "unknown"),
                                  "highway": edge_data.get("highway", "unknown")})
        _upsert_edges_batch(
            db, deduped_osm_edges, new_keys, new_contributor_keys,
            existing_keys, existing_endpoints, existing_pass_counts,
            osm_tags=osm_edge_tags,
        )
        osm_edge_count = len(seen_osm)

        # ── Phase 2b: Grid-snap fallback for unmatched coords ────────────────
        # Snap fallback coords to 5dp grid, drop consecutive duplicates.
        # `None` entries in fallback_coords are track breaks propagated
        # by `_densify_coords` (GPS gap > 500m); we pass them through as
        # None so the consecutive-pair loop below knows not to bridge
        # across the break.
        snapped: list[tuple[float, float] | None] = []
        elevations: list[float | None] = []
        prev: tuple[float, float] | None = None
        for point in fallback_coords:
            if point is None:
                snapped.append(None)
                elevations.append(None)
                prev = None
                continue
            lon, lat = point[0], point[1]
            ele = float(point[2]) if len(point) > 2 and point[2] is not None else None
            pt = _snap(float(lat), float(lon))
            if pt != prev:
                snapped.append(pt)
                elevations.append(ele)
                prev = pt

        seen_grid: set[str] = set()
        merged_count = 0
        grid_edges_to_upsert: list[dict] = []

        # Fast path: skip canonical merge when no existing edges in bbox
        # (common during bulk rebuild on clean DB)
        skip_merge = not existing_keys
        # Build spatial index once for O(1) lookups (replaces 440 brute-force combos per edge)
        ep_index = _build_endpoint_index(existing_endpoints) if not skip_merge else None

        # Track-break sentinels (None) split the snapped list into
        # disconnected sub-traces; the densifier inserts them on >500 m
        # GPS gaps. We additionally drop grid-fallback edges longer than
        # 60 m (4× the 15 m densifier target) as a safety net for cases
        # the densifier didn't catch (e.g. existing data ingested before
        # the break sentinel was introduced).
        _MAX_GRID_EDGE_M = 60.0
        for i in range(len(snapped) - 1):
            p1, p2 = snapped[i], snapped[i + 1]
            if p1 is None or p2 is None:
                continue  # track break — no edge across the gap
            edge_dist_m = _haversine_m(p1[1], p1[0], p2[1], p2[0])
            if edge_dist_m > _MAX_GRID_EDGE_M:
                continue
            if skip_merge:
                key = _edge_key(sport, p1, p2)
            else:
                key = _find_canonical_edge(
                    sport, p1, p2, existing_keys, existing_endpoints, existing_pass_counts, ep_index,
                )
            if key in seen_grid or key in seen_osm:
                continue
            seen_grid.add(key)

            original_key = _edge_key(sport, p1, p2)
            is_merge = (key != original_key)
            if is_merge:
                merged_count += 1

            if is_merge and key in existing_endpoints:
                ep1, ep2 = existing_endpoints[key]
                a_canonical, b_canonical = sorted([ep1, ep2])
            else:
                a_canonical, b_canonical = sorted([p1, p2])

            if is_merge:
                d_to_a = abs(p1[0] - a_canonical[0]) + abs(p1[1] - a_canonical[1])
                d_to_b = abs(p1[0] - b_canonical[0]) + abs(p1[1] - b_canonical[1])
                is_canonical = (d_to_a <= d_to_b)
            else:
                is_canonical = (p1 == a_canonical)

            ele1, ele2 = elevations[i], elevations[i + 1]
            ele_delta_m = 0.0
            if ele1 is not None and ele2 is not None:
                raw_delta = float(ele2) - float(ele1)
                ele_delta_m = raw_delta if is_canonical else -raw_delta

            slope_grade = abs(ele_delta_m) / edge_dist_m * 100.0 if ele_delta_m != 0.0 and edge_dist_m > 0 else 0.0

            grid_edges_to_upsert.append({
                "edge_key": key,
                "sport": sport,
                "is_canonical": is_canonical,
                "ele_delta_m": ele_delta_m,
                "slope_grade": slope_grade,
                "a_lat": a_canonical[0], "a_lon": a_canonical[1],
                "b_lat": b_canonical[0], "b_lon": b_canonical[1],
                "user_id_hash": user_id_hash,
                "activity_date": activity_date,
                "activity_id": activity_id,
                "match_source": "grid_fallback",
            })

        _upsert_edges_batch(
            db, grid_edges_to_upsert, new_keys, new_contributor_keys,
            existing_keys, existing_endpoints, existing_pass_counts,
        )
        grid_edge_count = len(seen_grid)

        # Batch update user_count for edges that got new contributors.
        # COUNT(DISTINCT user_id_hash) — since migration 0052 the
        # contributors PK is (edge_key, user_id_hash, activity_id), so a
        # user with N activities on the same edge has N contributor
        # rows. user_count drives K-anonymity which is per-user, not
        # per-contribution → must DISTINCT on user_id_hash.
        if new_contributor_keys:
            db.execute(sa_text("""
                UPDATE heat_edges SET user_count = sub.cnt
                FROM (
                    SELECT edge_key, COUNT(DISTINCT user_id_hash) AS cnt
                    FROM heat_edge_contributors
                    WHERE edge_key = ANY(:keys)
                    GROUP BY edge_key
                ) sub
                WHERE heat_edges.edge_key = sub.edge_key
                  AND heat_edges.sport = :sport
            """), {"keys": list(new_contributor_keys), "sport": sport})

        _bump_edge_version(db)  # joins the heat_edges transaction
        db.commit()

        # Incremental maintenance of the display aggregate. Recompute-from-
        # source (idempotent, self-healing) ONLY the OSM ways this activity
        # touched — never the full 5 M-row aggregation. Runs AFTER the
        # heat_edges commit so it reads the just-committed state; commits in
        # its own short txn. Skipped during a bulk rebuild (which backfills
        # once at the end) via SKIP_HEAT_AGG_MAINTENANCE. See migration 0057.
        touched_way_ids = sorted({
            int(ed["osm_way_id"]) for ed in deduped_osm_edges
            if ed.get("osm_way_id") is not None
        })
        if collect_touched_ways is not None:
            # Drain-batching path: the caller recomputes ONE deduplicated
            # union at end-of-archive (see docstring). Never per-activity.
            collect_touched_ways.update(touched_way_ids)
        else:
            _maintain_heat_agg(db, touched_way_ids)

        _schedule_pmtiles_rebuild()

        if osm_edge_count > 0 or grid_edge_count > 0:
            logger.info("Match: %d OSM, %d grid-fallback", osm_edge_count, grid_edge_count)
        if merged_count > 0:
            logger.info("Grid edges: %d merged (neighbor clustering)", merged_count)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return osm_edge_count + grid_edge_count, new_keys


def get_heat_edges_public(
    sport: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    k: int = HEATMAP_K_ANONYMITY,
    days: int | None = None,
) -> list[dict]:
    """Return K-anonymous trail edges from DB. No user IDs are exposed."""
    # RAW-trace cutover: ``heat_edges`` is a LEGACY read model being DROPPED in
    # prod under the raw pivot (the community layer renders from raw traces /
    # static PMTiles). Return an empty set in raw mode instead of querying a
    # table that no longer exists (would 500 every caller — /heatmap/trails is
    # already 410-gated upstream, but this keeps the SSOT reader itself safe).
    if raw_display_enabled():
        return []
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        # NOTE: two query paths — if you add columns to SELECT, update both.
        if days is not None and days > 0:
            # Time-filtered path: JOIN contributors, enforce K-anonymity on filtered set
            conditions = []
            params: dict[str, Any] = {"k": k, "days": days}

            conditions.append("hec.activity_date >= NOW() - MAKE_INTERVAL(days => :days)")
            # Filter out spaghetti: grid-fallback edges (no OSM match)
            # longer than 60m. These come from GPS dropouts the densifier
            # didn't break — straight lines crossing fields. Matches the
            # PMTiles export filter and the ingest-time 60m cap.
            conditions.append(
                "NOT (he.osm_way_id IS NULL AND ST_Length(he.geometry::geography) > 60)"
            )

            if sport:
                conditions.append("he.sport = :sport")
                params["sport"] = sport

            if bbox:
                min_lon, min_lat, max_lon, max_lat = bbox
                conditions.append(
                    "ST_Intersects(he.geometry, ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326))"
                )
                params.update({"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat})

            where_clause = " AND ".join(conditions)
            rows = db.execute(sa_text(f"""
                SELECT he.edge_key, he.sport, COUNT(DISTINCT hec.user_id_hash) AS filtered_user_count,
                       he.pass_count, he.forward_count, he.backward_count,
                       he.ele_delta_m, he.slope_grade, he.surface_type, he.highway_type,
                       he.tracktype, he.smoothness, he.trail_network, he.trail_type,
                       ST_AsGeoJSON(he.geometry)::text AS geom_json
                FROM heat_edges he
                JOIN heat_edge_contributors hec ON hec.edge_key = he.edge_key
                WHERE {where_clause}
                GROUP BY he.edge_key, he.id, he.sport, he.pass_count, he.forward_count, he.backward_count,
                         he.ele_delta_m, he.slope_grade, he.surface_type, he.highway_type,
                         he.tracktype, he.smoothness, he.trail_network, he.trail_type, he.geometry
                HAVING COUNT(DISTINCT hec.user_id_hash) >= :k
            """), params).fetchall()
        else:
            # All-time path: fast query using pre-computed user_count (no JOIN)
            conditions = [
                "user_count >= :k",
                # Filter out grid-snap artifacts: 2-point straight lines > 150m
                "(ST_NPoints(geometry) > 2 OR ST_Length(geometry::geography) <= 150)",
            ]
            params = {"k": k}

            if sport:
                conditions.append("sport = :sport")
                params["sport"] = sport

            if bbox:
                min_lon, min_lat, max_lon, max_lat = bbox
                conditions.append(
                    "ST_Intersects(geometry, ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326))"
                )
                params.update({"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat})

            where_clause = " AND ".join(conditions)
            rows = db.execute(sa_text(f"""
                SELECT edge_key, sport, user_count, pass_count, forward_count, backward_count,
                       ele_delta_m, slope_grade, surface_type, highway_type, tracktype, smoothness,
                       trail_network, trail_type,
                       ST_AsGeoJSON(geometry)::text AS geom_json
                FROM heat_edges
                WHERE {where_clause}
            """), params).fetchall()

        results = []
        for row in rows:
            uc = row[2]
            tt = row[13]
            geom = json.loads(row[14])
            results.append({
                "edge_key": row[0],
                "sport": row[1],
                "user_count": uc,
                "pass_count": row[3],
                "forward_count": row[4] or 0,
                "backward_count": row[5] or 0,
                "ele_delta_m": row[6] or 0.0,
                "heat_score": round(compute_heat_score(uc), 4),
                "slope_grade": row[7] or 0.0,
                "surface_type": row[8] or "unknown",
                "highway_type": row[9],
                "tracktype": row[10],
                "smoothness": row[11],
                "trail_network": row[12] or False,
                "trail_type": tt,
                "trail_score": round(compute_trail_score(tt), 2),
                "geometry": geom,
            })
        return results
    finally:
        db.close()


def get_heatmap_summary() -> dict:
    """Return aggregate heatmap statistics per sport (no user IDs exposed).

    The homepage banner cares about three numbers, which need to be the
    REAL ones — not approximations:

    - `total_contributors` — distinct hashed users
    - `total_activities` — actual rows in `activities` (not pass-count). The
      previous homepage labelled the cumulative `pass_count` SUM as "traces",
      which is a 1000× overstatement for a beta with 1 user × 1404 rides.
    - `total_km` — true sum of `ST_Length(geometry::geography)` rounded to km.
      Previous code estimated `total_edges × 0.3 km`, but heat_edges are
      mostly 11 m grid-snapped, so the estimate was ~30× too high.
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        rows = db.execute(sa_text("""
            SELECT sport,
                   COUNT(*) AS edges,
                   SUM(pass_count) AS total_passes,
                   COALESCE(SUM(ST_Length(geometry::geography)), 0) AS total_length_m
            FROM heat_edges
            GROUP BY sport
            ORDER BY COUNT(*) DESC
        """)).fetchall()

        # Count total distinct contributors
        total_row = db.execute(sa_text(
            "SELECT COUNT(DISTINCT user_id_hash) FROM heat_edge_contributors"
        )).fetchone()
        total_contributors = total_row[0] if total_row else 0

        # Actual activity rows (not pass-count) for the "traces" label
        total_activities_row = db.execute(sa_text(
            "SELECT COUNT(*) FROM activities"
        )).fetchone()
        total_activities = total_activities_row[0] if total_activities_row else 0

        # Per-sport contributor counts
        sport_contrib_rows = db.execute(sa_text("""
            SELECT he.sport, COUNT(DISTINCT hec.user_id_hash)
            FROM heat_edge_contributors hec
            JOIN heat_edges he ON he.edge_key = hec.edge_key
            GROUP BY he.sport
        """)).fetchall()
        sport_contribs = {r[0]: r[1] for r in sport_contrib_rows}

        total_edges = 0
        total_length_m = 0.0
        sports_out = {}
        for row in rows:
            sport_name, edge_count, total_passes, length_m = row[0], row[1], row[2], float(row[3] or 0)
            total_edges += edge_count
            total_length_m += length_m
            sports_out[sport_name] = {
                "edges": edge_count,
                "contributors": sport_contribs.get(sport_name, 0),
                "total_passes": total_passes or 0,
                "total_km": round(length_m / 1000),
            }

        return {
            "total_edges": total_edges,
            "total_contributors": total_contributors,
            "total_activities": total_activities,
            "total_km": round(total_length_m / 1000),
            "sports": sports_out,
        }
    finally:
        db.close()


# ── DFCI edges (fire-prevention tracks) ────────────────────────────────────

def store_dfci_edges(edges: list[dict]) -> int:
    """Store DFCI edges in DB (replaces all). Returns the number stored."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM dfci_edges"))
        count = 0
        for edge in edges:
            coords = edge.get("geometry", {}).get("coordinates", [])
            linestring = _coords_to_linestring(coords)
            if not linestring:
                continue
            db.execute(sa_text("""
                INSERT INTO dfci_edges (ref, surface, highway, trail_type, slope_grade, geometry)
                VALUES (:ref, :surface, :highway, :trail_type, :slope_grade,
                        ST_GeomFromText(:geom, 4326))
            """), {
                "ref": edge.get("ref", ""),
                "surface": edge.get("surface", "unknown"),
                "highway": edge.get("highway", "track"),
                "trail_type": edge.get("trail_type", "DFCI"),
                "slope_grade": edge.get("slope_grade", 0),
                "geom": linestring,
            })
            count += 1
        _bump_edge_version(db)
        db.commit()
        _schedule_pmtiles_rebuild()
        return count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def append_dfci_edges(edges: list[dict]) -> int:
    """Append DFCI edges without clearing existing ones. Returns count added."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        count = 0
        for edge in edges:
            coords = edge.get("geometry", {}).get("coordinates", [])
            linestring = _coords_to_linestring(coords)
            if not linestring:
                continue
            db.execute(sa_text("""
                INSERT INTO dfci_edges (ref, surface, highway, trail_type, slope_grade, geometry)
                VALUES (:ref, :surface, :highway, :trail_type, :slope_grade,
                        ST_GeomFromText(:geom, 4326))
            """), {
                "ref": edge.get("ref", ""),
                "surface": edge.get("surface", "unknown"),
                "highway": edge.get("highway", "track"),
                "trail_type": edge.get("trail_type", "DFCI"),
                "slope_grade": edge.get("slope_grade", 0),
                "geom": linestring,
            })
            count += 1
        _bump_edge_version(db)
        db.commit()
        _schedule_pmtiles_rebuild()
        return count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_dfci_edges_in_bbox(bbox: tuple[float, float, float, float]) -> list[dict]:
    """Return DFCI edges intersecting the bbox using PostGIS spatial index."""
    from app.db.session import SessionLocal

    min_lon, min_lat, max_lon, max_lat = bbox
    db = SessionLocal()
    try:
        rows = db.execute(sa_text("""
            SELECT ref, surface, highway, trail_type,
                   ST_AsGeoJSON(geometry)::text AS geom_json
            FROM dfci_edges
            WHERE ST_Intersects(geometry, ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326))
        """), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}).fetchall()

        results = []
        for row in rows:
            results.append({
                "geometry": json.loads(row[4]),
                "ref": row[0] or "",
                "surface": row[1] or "unknown",
                "highway": row[2] or "track",
                "trail_type": row[3] or "DFCI",
                "trail_network": True,
                "user_count": 0,
                "pass_count": 0,
            })
        return results
    finally:
        db.close()


def get_dfci_edge_count(fast: bool = True) -> int:
    """Return the number of DFCI edges.

    Args:
        fast: Use a bounded scan (LIMIT 1001, ~1ms) instead of exact
              COUNT(*) (~6s on 3.7M rows). Returns min(actual, 1001).
              Sufficient for startup "skip if > 1000" checks. Set False
              only when an exact count is needed.
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        if fast:
            row = db.execute(sa_text(
                "SELECT COUNT(*) FROM (SELECT 1 FROM dfci_edges LIMIT 1001) sub"
            )).fetchone()
        else:
            row = db.execute(sa_text("SELECT COUNT(*) FROM dfci_edges")).fetchone()
        return row[0] if row else 0
    finally:
        db.close()


def get_dfci_geojson(limit: int = 0) -> dict:
    """Return DFCI edges as a GeoJSON FeatureCollection.

    Args:
        limit: Max features to return. 0 = no limit. Use 100_000 from
               the API endpoint to prevent OOM on large datasets.
    """
    from app.db.session import SessionLocal

    limit_clause = f"LIMIT {int(limit)}" if limit > 0 else ""
    db = SessionLocal()
    try:
        # Payload discipline (fixes the non-gzip 500): the full-precision
        # FeatureCollection for the ~1000 DFCI tracks is ~34 MB uncompressed,
        # OVER Cloud Run's 32 MiB response cap → any client that does NOT send
        # ``Accept-Encoding: gzip`` gets a hard 500 (browsers gzip → fine, but
        # curl/tools/monitors don't). Shrink it at source: simplify to ~11 m
        # (``ST_SimplifyPreserveTopology``, 0.0001°) and emit 5-decimal coords
        # (~1 m) instead of PostGIS' 9-decimal default — a home/overlay display
        # needs neither. Cuts the raw payload ~3× (well under the cap) and the
        # gzipped one the home loads too. Geometry-only; stats are untouched.
        rows = db.execute(sa_text(f"""
            SELECT ref, surface, highway, trail_type,
                   ST_AsGeoJSON(ST_SimplifyPreserveTopology(geometry, 0.0001), 5)::text
                       AS geom_json
            FROM dfci_edges
            {limit_clause}
        """)).fetchall()

        features = []
        for row in rows:
            features.append({
                "type": "Feature",
                "geometry": json.loads(row[4]),
                "properties": {
                    "ref": row[0] or "",
                    "surface": row[1] or "unknown",
                    "highway": row[2] or "track",
                    "trail_type": "DFCI",
                    "trail_network": True,
                },
            })
        return {
            "type": "FeatureCollection",
            "features": features,
        }
    finally:
        db.close()


# ── Marked trail edges (GR, GT, PR, EV, GRP) ──────────────────────────────


def store_trail_edges(edges: list[dict]) -> int:
    """Store marked trail edges in DB (replaces all). Returns the number stored."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(sa_text("DELETE FROM trail_edges"))
        count = 0
        for edge in edges:
            coords = edge.get("geometry", {}).get("coordinates", [])
            linestring = _coords_to_linestring(coords)
            if not linestring:
                continue
            db.execute(sa_text("""
                INSERT INTO trail_edges (ref, surface, highway, trail_type, slope_grade, geometry)
                VALUES (:ref, :surface, :highway, :trail_type, :slope_grade,
                        ST_GeomFromText(:geom, 4326))
            """), {
                "ref": edge.get("ref", ""),
                "surface": edge.get("surface", "unknown"),
                "highway": edge.get("highway", "path"),
                "trail_type": edge.get("trail_type", "PR"),
                "slope_grade": edge.get("slope_grade", 0),
                "geom": linestring,
            })
            count += 1
        _bump_edge_version(db)
        db.commit()
        _schedule_pmtiles_rebuild()
        return count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def append_trail_edges(edges: list[dict]) -> int:
    """Append trail edges without clearing existing ones. Returns count added."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        count = 0
        for edge in edges:
            coords = edge.get("geometry", {}).get("coordinates", [])
            linestring = _coords_to_linestring(coords)
            if not linestring:
                continue
            db.execute(sa_text("""
                INSERT INTO trail_edges (ref, surface, highway, trail_type, slope_grade, geometry)
                VALUES (:ref, :surface, :highway, :trail_type, :slope_grade,
                        ST_GeomFromText(:geom, 4326))
            """), {
                "ref": edge.get("ref", ""),
                "surface": edge.get("surface", "unknown"),
                "highway": edge.get("highway", "path"),
                "trail_type": edge.get("trail_type", "PR"),
                "slope_grade": edge.get("slope_grade", 0),
                "geom": linestring,
            })
            count += 1
        _bump_edge_version(db)
        db.commit()
        _schedule_pmtiles_rebuild()
        return count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_trail_edges_in_bbox(bbox: tuple[float, float, float, float]) -> list[dict]:
    """Return trail edges intersecting the bbox using PostGIS spatial index."""
    from app.db.session import SessionLocal

    min_lon, min_lat, max_lon, max_lat = bbox
    db = SessionLocal()
    try:
        rows = db.execute(sa_text("""
            SELECT ref, surface, highway, trail_type,
                   ST_AsGeoJSON(geometry)::text AS geom_json
            FROM trail_edges
            WHERE ST_Intersects(geometry, ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326))
        """), {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat}).fetchall()

        results = []
        for row in rows:
            results.append({
                "geometry": json.loads(row[4]),
                "ref": row[0] or "",
                "surface": row[1] or "unknown",
                "highway": row[2] or "path",
                "trail_type": row[3] or "PR",
                "trail_network": True,
                "user_count": 0,
                "pass_count": 0,
            })
        return results
    finally:
        db.close()


def get_trail_edge_count(fast: bool = True) -> int:
    """Return the number of marked trail edges.

    Args:
        fast: Use a bounded scan (LIMIT 1001, ~1ms). See get_dfci_edge_count.
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        if fast:
            row = db.execute(sa_text(
                "SELECT COUNT(*) FROM (SELECT 1 FROM trail_edges LIMIT 1001) sub"
            )).fetchone()
        else:
            row = db.execute(sa_text("SELECT COUNT(*) FROM trail_edges")).fetchone()
        return row[0] if row else 0
    finally:
        db.close()


async def enrich_dfci_trail_slopes(source: str = "all") -> int:
    """Enrich DFCI + trail edges that have slope_grade=0 with DEM-derived slopes.

    Called once at import time (baked into import_dfci_ign / import_dfci_herault /
    import_trails — these only do real work the first time data is loaded).

    Args:
        source: "dfci" (only DFCI edges), "trail" (only trail edges),
                or "all" (both — default).

    Returns the number of edges enriched.
    Skips entirely when DEM_PROVIDER=none (tests).
    """
    import os

    from app.db.session import SessionLocal
    from app.services.dem import compute_slopes_for_edges

    if os.environ.get("DEM_PROVIDER", "open-meteo").lower() == "none":
        return 0

    if source not in ("dfci", "trail", "all"):
        raise ValueError(f"source must be 'dfci', 'trail', or 'all'; got {source!r}")

    queries = []
    if source in ("dfci", "all"):
        queries.append("""
            SELECT 'dfci' AS src, id,
                   ST_X(ST_StartPoint(geometry)), ST_Y(ST_StartPoint(geometry)),
                   ST_X(ST_EndPoint(geometry)), ST_Y(ST_EndPoint(geometry))
            FROM dfci_edges WHERE slope_grade = 0 OR slope_grade IS NULL
        """)
    if source in ("trail", "all"):
        queries.append("""
            SELECT 'trail' AS src, id,
                   ST_X(ST_StartPoint(geometry)), ST_Y(ST_StartPoint(geometry)),
                   ST_X(ST_EndPoint(geometry)), ST_Y(ST_EndPoint(geometry))
            FROM trail_edges WHERE slope_grade = 0 OR slope_grade IS NULL
        """)
    sql = " UNION ALL ".join(queries)

    db = SessionLocal()
    try:
        rows = db.execute(sa_text(sql)).fetchall()
    finally:
        db.close()

    if not rows:
        return 0

    # Build compact edge list for DEM batch lookup
    compact_edges = [[r[2], r[3], r[4], r[5]] for r in rows]
    slopes = await compute_slopes_for_edges(compact_edges)

    # Batch update slopes back to DB
    dfci_updates = []
    trail_updates = []
    for i, row in enumerate(rows):
        slope = slopes[i]
        if slope == 0.0:
            continue
        if row[0] == "dfci":
            dfci_updates.append((row[1], slope))
        else:
            trail_updates.append((row[1], slope))

    db = SessionLocal()
    try:
        for edge_id, slope in dfci_updates:
            db.execute(sa_text(
                "UPDATE dfci_edges SET slope_grade = :slope WHERE id = :id"
            ), {"slope": slope, "id": edge_id})
        for edge_id, slope in trail_updates:
            db.execute(sa_text(
                "UPDATE trail_edges SET slope_grade = :slope WHERE id = :id"
            ), {"slope": slope, "id": edge_id})
        db.commit()
    finally:
        db.close()

    total = len(dfci_updates) + len(trail_updates)
    logger.info("DFCI/trail slope enrichment: %d/%d edges updated", total, len(rows))
    return total


def get_personal_edges(
    user_id: str,
    sport: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> list[dict]:
    """Return GPS edge segments from a user's personal activities (no K-anonymity).

    Used for personal-trace routing when no community heatmap exists for the area.
    """
    from app.db.models import Activity
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        q = db.query(Activity).filter(
            Activity.user_id == user_id,
            Activity.geometry_geojson.isnot(None),
        )
        if sport:
            q = q.filter(Activity.sport == sport)
        results = []
        for act in q.yield_per(100):
            try:
                geom = json.loads(act.geometry_geojson)
            except Exception:
                continue
            if geom.get("type") != "LineString":
                continue
            coords = geom.get("coordinates", [])
            coords = _resegment_coords(coords)
            for i in range(len(coords) - 1):
                # Skip pairs that span a track break (densifier inserts
                # `None` on >500m GPS gaps so the personal-trace overlay
                # doesn't draw straight lines across signal losses).
                if coords[i] is None or coords[i + 1] is None:
                    continue
                lon_a, lat_a = float(coords[i][0]), float(coords[i][1])
                lon_b, lat_b = float(coords[i + 1][0]), float(coords[i + 1][1])
                if bbox:
                    min_lon, min_lat, max_lon, max_lat = bbox
                    in_bbox = (
                        (min_lon <= lon_a <= max_lon and min_lat <= lat_a <= max_lat)
                        or (min_lon <= lon_b <= max_lon and min_lat <= lat_b <= max_lat)
                    )
                    if not in_bbox:
                        continue
                # Compute signed elevation delta and slope from 3D coords if available
                ele_delta_m = 0.0
                slope_grade = 0.0
                if len(coords[i]) > 2 and len(coords[i + 1]) > 2:
                    ele_a = coords[i][2]
                    ele_b = coords[i + 1][2]
                    if ele_a is not None and ele_b is not None:
                        dist_m = _haversine_m(lon_a, lat_a, lon_b, lat_b)
                        if dist_m > 0:
                            ele_delta_m = float(ele_b) - float(ele_a)
                            slope_grade = abs(ele_delta_m) / dist_m * 100.0
                results.append({
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[lon_a, lat_a], [lon_b, lat_b]],
                    },
                    "sport": act.sport or "road",
                    "user_count": 1,
                    "pass_count": 1,
                    "forward_count": 1,
                    "backward_count": 0,
                    "ele_delta_m": ele_delta_m,
                    "heat_score": round(compute_heat_score(1), 4),
                    "slope_grade": slope_grade,
                    "surface_type": "unknown",
                    "trail_network": False,
                    "trail_type": None,
                    "trail_score": 0.0,
                })
        return results
    finally:
        db.close()


# ── Activity ingestion ────────────────────────────────────────────────────────

def _promote_activity_to_community(
    db, activity_id: str, activity_data: dict, contribute_heatmap: bool
) -> None:
    """Promote an existing (non-community) activity row to ``manual_upload``.

    Called when a community-eligible upload matches an already-stored twin
    that is NOT community-eligible (③ cross-source idempotence). Instead of
    creating a second row (double personal display) or reusing the Strava-API
    polyline (compliance breach), we:

    * flip ``source`` → ``manual_upload`` (community-eligible),
    * REPLACE the stored geometry with the user's authoritative uploaded GPX
      (higher fidelity than the Strava summary polyline, and — crucially —
      NOT Strava-API data, so a future ``rebuild_heatmap`` reads compliant
      geometry). Private ``activity_cells`` are refreshed to match, and
    * adopt the INCOMING upload's ``contribute_heatmap`` intent. This last
      part is load-bearing on the ASYNC path: the enqueued
      ``process_heat_compute`` re-reads the row's ``contribute_heatmap`` from
      the DB, so if the pre-existing Strava twin was opted OUT
      (``contribute_heatmap=False``) while the manual upload intends IN, the
      promoted ride would silently never reach the community without this.

    This is not a Crouzet violation (invariant #1): the row's prior geometry
    was a decoded Strava polyline placeholder, not a user GPX trace — we are
    upgrading TO the verbatim user trace at the user's explicit upload. The
    Strava ``provider`` / ``provider_activity_id`` linkage is left intact so
    later webhook events for the same activity still dedupe.

    The caller commits (this only stages the mutations on ``db``).
    """
    from app.db.models import Activity, ActivityCell

    act = db.query(Activity).filter(Activity.id == activity_id).first()
    if act is None:
        return
    act.source = COMMUNITY_SOURCE
    act.contribute_heatmap = contribute_heatmap
    geo = activity_data.get("geometry_geojson")
    if geo:
        act.geometry_geojson = geo
        act.geometry = _geom_from_geojson_sql(geo)
        act.geometry_source = activity_data.get("geometry_source", "stream")
        db.query(ActivityCell).filter(ActivityCell.activity_id == activity_id).delete()
        for cell_key in _geojson_to_cells(geo):
            db.add(ActivityCell(
                activity_id=activity_id,
                user_id=act.user_id,
                cell_key=cell_key,
                zoom=14,
            ))
    if activity_data.get("distance_m") is not None:
        act.distance_m = activity_data["distance_m"]
    if activity_data.get("elevation_gain_m") is not None:
        act.elevation_gain_m = activity_data["elevation_gain_m"]
    if activity_data.get("file_hash"):
        act.file_hash = activity_data["file_hash"]


def ingest_activity(
    user_id: str,
    activity_data: dict,
    contribute_heatmap: bool = True,
    rebuild_cache: bool = True,
    skip_heat_computation: bool = False,
    collect_touched_ways: set[int] | None = None,
) -> dict:
    """Store an activity in DB and optionally contribute to community heatmap.

    activity_data keys:
    - provider: str
    - provider_activity_id: str | None
    - source: str | None  (provenance, migration 0059; e.g. "manual_upload")
    - sport: str
    - name: str | None
    - geometry_geojson: str | None
    - distance_m: float | None
    - elevation_gain_m: float | None
    - file_hash: str | None
    """
    from app.db.models import Activity, ActivityCell
    from app.db.session import SessionLocal

    provider = activity_data.get("provider", "file")
    provider_activity_id = activity_data.get("provider_activity_id")
    file_hash = activity_data.get("file_hash")

    db = SessionLocal()
    try:
        # Idempotency check: skip if already imported by provider ID
        if provider_activity_id:
            existing = db.query(Activity).filter(
                Activity.user_id == user_id,
                Activity.provider == provider,
                Activity.provider_activity_id == provider_activity_id,
            ).first()
            if existing:
                # Backfill activity_date / total_photo_count if missing
                updated = False
                new_date = activity_data.get("activity_date")
                if new_date and not existing.activity_date:
                    existing.activity_date = new_date
                    updated = True
                new_photo_count = activity_data.get("total_photo_count")
                if new_photo_count and not existing.total_photo_count:
                    existing.total_photo_count = new_photo_count
                    updated = True
                if updated:
                    db.commit()
                return {"activity_id": existing.id, "status": "already_exists"}

        # Idempotency by file hash
        if file_hash:
            existing_hash = db.query(Activity).filter(
                Activity.user_id == user_id,
                Activity.file_hash == file_hash,
            ).first()
            if existing_hash:
                return {"activity_id": existing_hash.id, "status": "already_exists"}

        # Cross-provider dedup: same user + same date (±5min) + same distance (±5%)
        # Catches: GPX import then Strava resync for the same activity.
        # Tightened from ±10% to ±5% (audit 2026-05-27 S2.6) — a 80 km
        # gravel ride started within 5 min of an 88 km road ride was
        # 1:1 dedupable under the old window. Real-world impact near
        # zero (users don't start two activities within 5 min) but
        # the wider window made cross-sport false positives possible.
        # Strava ISO 8601 strings ("2024-08-15T10:30:45Z") need to be
        # parsed to datetime before the timedelta subtraction below.
        # GPX path passes a real datetime already. First exposed when
        # Cloud Run Job common-trails-resync-strava-prod ran 2026-05-31
        # — the dedup window was added 2026-05-27 but never exercised
        # against Strava until then. Mutating the dict so the
        # Activity() INSERT below also gets the parsed datetime (the
        # Postgres column is `timestamp with time zone`; a malformed
        # string would otherwise crash the INSERT).
        raw_date = activity_data.get("activity_date")
        if isinstance(raw_date, str):
            try:
                activity_data["activity_date"] = datetime.fromisoformat(
                    raw_date.replace("Z", "+00:00")
                )
            except ValueError:
                activity_data["activity_date"] = None
        activity_date = activity_data.get("activity_date")
        distance_m = activity_data.get("distance_m")
        if activity_date and distance_m and distance_m > 0:
            dist_tolerance = distance_m * DEDUP_DISTANCE_TOL  # ±5%
            existing_dup = db.execute(sa_text("""
                SELECT id, source FROM activities
                WHERE user_id = :uid
                  AND activity_date BETWEEN :dt_min AND :dt_max
                  AND distance_m BETWEEN :dmin AND :dmax
                ORDER BY CASE WHEN source = :community THEN 0 ELSE 1 END, created_at
                LIMIT 1
            """), {
                "uid": user_id,
                "dt_min": activity_date - timedelta(minutes=DEDUP_START_WINDOW_MIN),
                "dt_max": activity_date + timedelta(minutes=DEDUP_START_WINDOW_MIN),
                "dmin": distance_m - dist_tolerance,
                "dmax": distance_m + dist_tolerance,
                "community": COMMUNITY_SOURCE,
            }).fetchone()
            if existing_dup:
                existing_id = str(existing_dup[0])
                existing_source = existing_dup[1]
                incoming_source = activity_data.get("source")
                # ③ Cross-source idempotence: the same ride can arrive via the
                # Strava API (personal, strava_api) AND via a manual archive/GPX
                # upload (community, manual_upload) with no shared
                # provider_activity_id. When the community-eligible copy lands
                # over a non-community twin, PROMOTE the existing row (source +
                # authoritative uploaded geometry) instead of dropping the
                # upload — otherwise the ride never reaches the community layer.
                # The heat contribution then fires once, on the uploaded
                # geometry (NOT the Strava-API polyline — compliance).
                if should_promote_to_community(incoming_source, existing_source):
                    _promote_activity_to_community(
                        db, existing_id, activity_data, contribute_heatmap
                    )
                    db.commit()
                    edges_indexed = 0
                    promoted_geom = activity_data.get("geometry_geojson")
                    # RAW-TRACE DISPLAY (pivot 2026-07-29): the community map
                    # renders precise GPS traces straight from `activities`, so
                    # OSM-matching + the heat_edges write are pointless — and
                    # their substrate (osm_road_edges/osm_ways) is DROPPED in
                    # prod. Skip the matched heat path entirely; the activity is
                    # already promoted + committed above.
                    if (
                        contribute_heatmap
                        and not skip_heat_computation
                        and promoted_geom
                        and not raw_display_enabled()
                    ):
                        edges_indexed, _ = _update_heat_edges(
                            user_id,
                            activity_data.get("sport", "road"),
                            promoted_geom,
                            activity_date=activity_date or datetime.now(UTC),
                            activity_id=existing_id,
                            collect_touched_ways=collect_touched_ways,
                        )
                    return {
                        "activity_id": existing_id,
                        "status": "promoted",
                        "edges_indexed": edges_indexed,
                        "cells_indexed": 0,
                    }
                # Cast to str: column is UUID and the response models on the
                # upload endpoints declare `activity_id: str`. Without this,
                # the dedup hit raises pydantic ValidationError → 500 to the
                # client (the exact branch this code is supposed to handle).
                return {"activity_id": existing_id, "status": "already_exists"}

        activity_id = str(uuid.uuid4())
        sport = activity_data.get("sport", "road")
        geometry_geojson = activity_data.get("geometry_geojson")

        activity = Activity(
            id=activity_id,
            user_id=user_id,
            provider=provider,
            provider_activity_id=provider_activity_id,
            source=activity_data.get("source"),
            sport=sport,
            name=activity_data.get("name"),
            geometry_geojson=geometry_geojson,
            # Dual-write: populate the binary PostGIS column (added in
            # migration 0036) alongside the legacy TEXT column. Once all
            # readers move to the binary column, the TEXT column will be
            # dropped in a follow-up migration.
            geometry=_geom_from_geojson_sql(geometry_geojson),
            distance_m=activity_data.get("distance_m"),
            elevation_gain_m=activity_data.get("elevation_gain_m"),
            file_hash=file_hash,
            contribute_heatmap=contribute_heatmap,
            geometry_source=activity_data.get("geometry_source", "polyline"),
            moving_time=activity_data.get("moving_time"),
            total_photo_count=activity_data.get("total_photo_count", 0),
            activity_date=activity_data.get("activity_date"),
        )
        db.add(activity)

        # Extract tile cells from geometry
        cells = _geojson_to_cells(geometry_geojson)

        # Store private activity_cells in DB
        for cell_key in cells:
            db.add(ActivityCell(
                activity_id=activity_id,
                user_id=user_id,
                cell_key=cell_key,
                zoom=14,
            ))

        db.commit()
    finally:
        db.close()

    # Contribute to community heatmap (skip during bulk import — run a heatmap rebuild after).
    # heat_cells write path removed in PR #214 — cells are now aggregated from
    # heat_edges at query time via get_heat_cells_aggregated. The legacy tables
    # are dropped by migration 0037 (deferred placeholder until prod soak passes).
    edges_indexed = 0
    # PROVENANCE GATE (②): only community-eligible activities (source ==
    # "manual_upload") ever write to heat_edges. Strava-API-sourced rides
    # (strava_api / legacy NULL) are personal-only — they are shown from the
    # `activities` table directly and MUST NOT feed the community layer
    # (Strava 2026 API Policy §5.4/§5.10). heat_edges == community layer by
    # construction; see app/services/provenance.py.
    #
    # RAW-TRACE DISPLAY (pivot 2026-07-29): under HEATMAP_DISPLAY_SOURCE=raw the
    # community map is rendered from `activities.geometry_geojson` directly, so
    # OSM map-matching + the heat_edges/heat_edge_contributors write are pointless
    # AND their substrate (osm_road_edges/osm_ways) has been DROPPED in prod.
    # Skip the whole matched path — the activity + geometry are already saved and
    # returned normally with edges_indexed=0.
    if (
        contribute_heatmap
        and not skip_heat_computation
        and geometry_geojson
        and is_community_source(activity_data.get("source"))
        and not raw_display_enabled()
    ):
        edges_indexed, _ = _update_heat_edges(
            user_id, sport, geometry_geojson,
            activity_date=activity_data.get("activity_date") or datetime.now(UTC),
            activity_id=activity_id,
            collect_touched_ways=collect_touched_ways,
        )

    return {
        "activity_id": activity_id,
        "status": "created",
        "cells_indexed": len(cells),
        "edges_indexed": edges_indexed,
    }



def _update_heat_cells(user_id: str, sport: str, cells: list[str]) -> None:
    """Update community heat_cells in DB (batched UNNEST)."""
    from app.db.session import SessionLocal

    if not cells:
        return

    user_id_hash = heat_user_id_hash(user_id)
    db = SessionLocal()
    try:
        # Batch upsert heat_cells (1 query instead of N)
        cell_values = []
        cell_params: dict = {}
        for idx, ck in enumerate(cells):
            p = f"_{idx}"
            cell_values.append(f"(:k{p}, :s{p}, 14, 0, 1)")
            cell_params[f"k{p}"] = ck
            cell_params[f"s{p}"] = sport
        db.execute(sa_text(f"""
            INSERT INTO heat_cells (cell_key, sport, zoom, user_count, pass_count)
            VALUES {','.join(cell_values)}
            ON CONFLICT (cell_key, sport) DO UPDATE SET
                pass_count = heat_cells.pass_count + 1
        """), cell_params)

        # Batch upsert contributors (1 query instead of N)
        contrib_values = []
        contrib_params: dict = {}
        for idx, ck in enumerate(cells):
            p = f"_{idx}"
            contrib_values.append(f"(:ck{p}, :cs{p}, :cu{p})")
            contrib_params[f"ck{p}"] = ck
            contrib_params[f"cs{p}"] = sport
            contrib_params[f"cu{p}"] = user_id_hash
        new_contrib_rows = db.execute(sa_text(f"""
            INSERT INTO heat_cell_contributors (cell_key, sport, user_id_hash)
            VALUES {','.join(contrib_values)}
            ON CONFLICT DO NOTHING
            RETURNING cell_key
        """), contrib_params).fetchall()

        # Batch update user_count only for cells that got new contributors
        if new_contrib_rows:
            new_cell_keys = [r[0] for r in new_contrib_rows]
            db.execute(sa_text("""
                UPDATE heat_cells SET user_count = sub.cnt
                FROM (
                    SELECT cell_key, sport, COUNT(*) AS cnt
                    FROM heat_cell_contributors
                    WHERE cell_key = ANY(:keys) AND sport = :sport
                    GROUP BY cell_key, sport
                ) sub
                WHERE heat_cells.cell_key = sub.cell_key
                  AND heat_cells.sport = sub.sport
            """), {"keys": new_cell_keys, "sport": sport})

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def ingest_activities_bulk(
    user_id: str,
    activities_data: list[dict],
    contribute_heatmap: bool = True,
    on_progress: Callable[[int, int, int], None] | None = None,
    source: str | None = None,
) -> dict:
    """Bulk-ingest activities, releasing the DB connection between batches.

    ``source`` stamps every inserted row's provenance (migration 0059) —
    the Strava-API callers pass ``"strava_api"`` so those rides are
    personal-only and never feed the community heatmap. A per-row
    ``act_data["source"]`` overrides it. See app/services/provenance.py.

    Thin wrapper that takes a per-user advisory lock then delegates to
    ``_ingest_activities_bulk_impl``. Separating the wrapper keeps the
    implementation flat (no 350-line indent into a try/finally block).

    See `[[project_strava_concurrency_2026_05_26]]` for why the lock
    matters: parallel `ingest_activities_bulk` calls for the same user
    (webhook firing during a bulk re-import) would each take their own
    pre-load snapshot of `existing_date_distance` → neither sees the
    other's in-flight rows → cross-provider duplicates leak through.
    `pg_try_advisory_lock` (session-scoped) serializes the body
    per-user across all instances; if contention exceeds 15 s we
    proceed without it and rely on the in-memory + DB-level dedup
    safety net.
    """
    import time as _time

    from app.db.session import SessionLocal

    _lock_db = SessionLocal()
    _lock_key = f"strava_ingest:{user_id}"
    _lock_acquired = False
    try:
        try:
            _deadline = _time.time() + 15
            while _time.time() < _deadline:
                _got = _lock_db.execute(
                    sa_text("SELECT pg_try_advisory_lock(hashtext(:k))"),
                    {"k": _lock_key},
                ).scalar()
                if _got:
                    _lock_acquired = True
                    break
                _time.sleep(1.0)
            if not _lock_acquired:
                logger.warning(
                    "ingest_activities_bulk: could not acquire user lock for %s "
                    "in 15s — proceeding without",
                    user_id,
                )
        except Exception as _lock_exc:
            logger.warning(
                "ingest_activities_bulk: lock acquire raised for %s: %s — proceeding without",
                user_id, _lock_exc,
            )

        return _ingest_activities_bulk_impl(
            user_id=user_id,
            activities_data=activities_data,
            contribute_heatmap=contribute_heatmap,
            on_progress=on_progress,
            source=source,
        )
    finally:
        # Explicit unlock — `pg_advisory_lock` is session-scoped, but
        # in SQLAlchemy "session-scoped" means "connection-scoped" and
        # closing the SQLAlchemy session returns the connection to the
        # pool with the lock STILL HELD. Must unlock explicitly.
        if _lock_acquired:
            try:
                _lock_db.execute(
                    sa_text("SELECT pg_advisory_unlock(hashtext(:k))"),
                    {"k": _lock_key},
                )
                _lock_db.commit()
            except Exception:
                pass
        _lock_db.close()


def _ingest_activities_bulk_impl(
    user_id: str,
    activities_data: list[dict],
    contribute_heatmap: bool = True,
    on_progress: Callable[[int, int, int], None] | None = None,
    source: str | None = None,
) -> dict:
    """Implementation body of ``ingest_activities_bulk`` — see wrapper above.

    Per-batch session pattern (PR #310 — db-pool-pressure bundle): each
    batch opens a fresh ``SessionLocal()``, INSERTs, commits, and closes
    in ``try/finally``. The previous implementation held one session open
    for the entire job, which on db-f1-micro (5+3 pool) starved every
    other request for tens of seconds at a time.

    Failure granularity preserved via SAVEPOINT (``db.begin_nested()``)
    per activity inside the flush helper — one bad row only rolls back
    that row, not the whole batch.

    Args:
        on_progress: optional callback(created, skipped, failed) called
            after each batch flush.

    Returns {"created": int, "skipped": int, "failed": int}.
    """
    import time as _time  # noqa: F401  — used by nested helpers

    from app.db.models import Activity, ActivityCell
    from app.db.session import SessionLocal

    BATCH_SIZE = 50  # 50 acts × ~20 cells = ~1000 rows/batch on db-f1-micro
    created = 0
    skipped = 0
    failed = 0

    # Per-user advisory lock lives in the public wrapper (above) so
    # the implementation body stays flat — see the wrapper docstring
    # for the design rationale.

    # ── Pre-load existing provider_activity_ids + cross-provider tuples ──
    #
    # Two sets are loaded in a single short-lived session:
    #
    #   1. `existing_ids` — Strava `provider_activity_id` set. Catches
    #      same-provider duplicates (webhook fires while bulk is also
    #      running, or Strava resync re-paginates the same activity).
    #
    #   2. `existing_date_distance` — list of `(activity_date_epoch,
    #      distance_m)` tuples ACROSS ALL providers for this user.
    #      Catches the GPX → Strava trap: user uploads `morning_ride.gpx`
    #      manually (provider=file), then connects Strava → the same
    #      activity comes back via the API (provider=strava). The
    #      `(user_id, provider, provider_activity_id)` unique constraint
    #      doesn't catch this because providers differ. The single-row
    #      `ingest_activity` path has had this check for a while
    #      (ingest.py:2660-2685); the bulk path was missing it until
    #      this PR. Match criteria: same date ±5 min AND distance ±5%
    #      (tightened from ±10% in PR #348 — see _is_cross_provider_dupe).
    #
    # Cost: one extra SELECT at the top of the import job. Already
    # backed by `activities_user_date_idx` (alembic 0047). For a 5000-
    # activity user the result is ~5000 small tuples in memory.
    existing_ids: set[str] = set()
    existing_date_distance: list[tuple[float, float]] = []  # (epoch_seconds, distance_m)
    preload_db = SessionLocal()
    try:
        rows = preload_db.query(Activity.provider_activity_id).filter(
            Activity.user_id == user_id,
            Activity.provider == "strava",
            Activity.provider_activity_id.isnot(None),
        ).all()
        existing_ids = {r[0] for r in rows}

        # Cross-provider tuples — load anything with both date AND
        # distance, regardless of provider.
        #
        # 5-year cap: cross-provider duplicates always happen close in
        # time (user uploads a GPX + connects Strava within days), never
        # across a decade. Capping bounds memory + Cloud SQL CPU for
        # power users with 50 k+ activity histories.
        #
        # `EXTRACT(epoch FROM ...)` returns DOUBLE PRECISION — we keep
        # it as float (no `::bigint` truncation) so the 5-min window
        # comparisons preserve sub-second drift. The DB column is
        # TIMESTAMPTZ so the epoch is always true UTC seconds; the
        # Python side normalizes naive datetimes to UTC below before
        # calling `.timestamp()`.
        dd_rows = preload_db.execute(sa_text("""
            SELECT
                EXTRACT(epoch FROM activity_date) AS dt_epoch,
                distance_m
            FROM activities
            WHERE user_id = :uid
              AND activity_date IS NOT NULL
              AND activity_date > NOW() - INTERVAL '5 years'
              AND distance_m IS NOT NULL
              AND distance_m > 0
        """), {"uid": user_id}).fetchall()
        existing_date_distance = [(float(r[0]), float(r[1])) for r in dd_rows]
    finally:
        preload_db.close()

    # Bucket the pre-loaded tuples by 5-min epoch slot so the per-row
    # check is O(constant) (look at 3 adjacent buckets only) instead of
    # O(N) (scan every prior activity). For a 50 k-activity user with
    # 500-act batches the difference is ~25 M Python compares per batch
    # vs ~3 × ~5 compares per row.
    from collections import defaultdict
    _BUCKET_S = 300  # = window radius; lookup hits at most 3 adjacent buckets
    _existing_bucket: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for ep, d in existing_date_distance:
        _existing_bucket[int(ep) // _BUCKET_S].append((ep, d))

    def _epoch_from_activity_date(ad: object) -> float | None:
        """Convert a Python datetime or ISO string to UTC epoch seconds.

        Naive datetimes (no tzinfo) are interpreted as UTC, NOT the
        host's local TZ. `datetime.timestamp()` on a naive object uses
        the local TZ — that means a GPX upload setting a naive
        `activity_date` would compute a different epoch on a Paris dev
        machine vs a Cloud Run UTC container, breaking the 5-min
        window. We fix tzinfo to UTC if missing.
        """
        from datetime import datetime as _dt

        if isinstance(ad, _dt):
            if ad.tzinfo is None:
                ad = ad.replace(tzinfo=UTC)
            return ad.timestamp()
        # ISO string path
        try:
            parsed = _dt.fromisoformat(str(ad).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()

    def _is_cross_provider_dupe(act_data: dict) -> bool:
        """True if (date ±5min, distance ±5%) matches an existing row.

        Bucketed lookup — O(constant) regardless of user history size.
        Tolerance tightened from ±10% to ±5% in PR #348 (audit S2.6) —
        mirrors the single-activity path at `ingest_activity()`.
        """
        ad = act_data.get("activity_date")
        dm = act_data.get("distance_m")
        if not ad or not dm or dm <= 0:
            return False
        target_epoch = _epoch_from_activity_date(ad)
        if target_epoch is None:
            return False
        dist_tol = dm * 0.05
        bucket_idx = int(target_epoch) // _BUCKET_S
        # Adjacent buckets cover the ±5-min window. If `_BUCKET_S` == 300
        # the window is exactly one bucket wide → look at slot-1, slot,
        # slot+1 to cover all boundary cases.
        for off in (-1, 0, 1):
            for existing_epoch, existing_dist in _existing_bucket.get(bucket_idx + off, ()):
                if (
                    abs(existing_epoch - target_epoch) <= 300
                    and abs(existing_dist - dm) <= dist_tol
                ):
                    return True
        return False

    def _flush_batch(batch: list[dict], batch_new_ids: list[str]) -> tuple[int, int]:
        """Insert a batch in a fresh session. Returns (created, failed) deltas.

        SAVEPOINT-per-activity: a single bad row rolls back inside its
        nested transaction without aborting the outer batch — preserves
        the legacy "one bad row doesn't kill the batch" semantics now
        that we commit per-batch instead of once at the end.

        `batch_new_ids` carries the `provider_activity_id`s that the
        outer loop eagerly added to `existing_ids` before this flush.
        If the commit fails we MUST pop them back out (symmetric with
        the `batch_new_dd` rollback below) — otherwise a re-run sees
        them as "already exists" and silently drops the rows. Audit
        2026-05-27 S2.3.
        """
        batch_created = 0
        batch_failed = 0
        batch_dedup_skipped = 0
        # Buffer this batch's newly-inserted (date, distance) tuples
        # in a LOCAL list. Only merged into `existing_date_distance` +
        # `_existing_bucket` *after* `flush_db.commit()` succeeds. If
        # commit raises, the rows didn't persist and the tuples must
        # not survive — otherwise subsequent batches would dedupe
        # against ghost rows, causing silent data loss the next time
        # the user re-imports.
        batch_new_dd: list[tuple[float, float]] = []
        flush_db = SessionLocal()
        try:
            for act_data in batch:
                # Cross-provider dedup (catches GPX → Strava trap).
                # Runs BEFORE the begin_nested + INSERT so we don't burn
                # a savepoint on rows we already know to skip. The
                # check sees both the pre-loaded list AND any prior
                # successful rows in THIS batch (via batch_new_dd —
                # see the temp-extend below).
                #
                # Subtle: temporarily extend `_existing_bucket` for
                # intra-batch dedup so a malformed Strava response with
                # two copies of the same activity in one batch dedupes
                # the second. We undo this if commit fails.
                if _is_cross_provider_dupe(act_data):
                    batch_dedup_skipped += 1
                    continue

                try:
                    with flush_db.begin_nested():
                        activity_id = str(uuid.uuid4())
                        sport = act_data.get("sport", "road")
                        geometry_geojson = act_data.get("geometry_geojson")

                        activity = Activity(
                            id=activity_id,
                            user_id=user_id,
                            provider=act_data.get("provider", "strava"),
                            provider_activity_id=act_data.get("provider_activity_id"),
                            # Provenance (migration 0059): a per-row value wins,
                            # else the bulk-call default (strava_api for the
                            # Strava paths). Gates community eligibility.
                            source=act_data.get("source", source),
                            sport=sport,
                            name=act_data.get("name"),
                            geometry_geojson=geometry_geojson,
                            geometry=_geom_from_geojson_sql(geometry_geojson),
                            distance_m=act_data.get("distance_m"),
                            elevation_gain_m=act_data.get("elevation_gain_m"),
                            file_hash=act_data.get("file_hash"),
                            contribute_heatmap=contribute_heatmap,
                            geometry_source=act_data.get("geometry_source", "polyline"),
                            moving_time=act_data.get("moving_time"),
                            total_photo_count=act_data.get("total_photo_count", 0),
                            activity_date=act_data.get("activity_date"),
                        )
                        flush_db.add(activity)

                        # Activity cells
                        cells = _geojson_to_cells(geometry_geojson)
                        for cell_key in cells:
                            flush_db.add(ActivityCell(
                                activity_id=activity_id,
                                user_id=user_id,
                                cell_key=cell_key,
                                zoom=14,
                            ))
                    batch_created += 1

                    # Tentatively register this row for intra-batch
                    # dedup. We push it into the bucket structure right
                    # away so later iterations in this same batch see
                    # it — but ALSO record it in `batch_new_dd` so we
                    # can roll back if `flush_db.commit()` fails.
                    new_dm = act_data.get("distance_m")
                    new_ad = act_data.get("activity_date")
                    if new_ad and new_dm and new_dm > 0:
                        new_epoch = _epoch_from_activity_date(new_ad)
                        if new_epoch is not None:
                            tup = (float(new_epoch), float(new_dm))
                            batch_new_dd.append(tup)
                            _existing_bucket[int(new_epoch) // _BUCKET_S].append(tup)
                except Exception as row_exc:
                    # Classify the failure. A `(user_id, provider,
                    # provider_activity_id)` unique-constraint violation
                    # is NOT a bug — it's the natural race between
                    # webhook + bulk + resync all targeting the same
                    # activity. Treat as a silent dedup-skip, not a
                    # failure (failed_count surfaces to the UI; we
                    # don't want false alarms).
                    msg = str(row_exc).lower()
                    is_dedup = (
                        "duplicate key" in msg
                        or "unique constraint" in msg
                        or "uq_activities_provider" in msg
                    )
                    if is_dedup:
                        batch_dedup_skipped += 1
                    else:
                        # Real failure — log + Sentry. Audit 2026-05-25
                        # found this except block was the largest
                        # silent-failure source in the ingest path.
                        batch_failed += 1
                        logger.warning(
                            "Activity insert failed user=%s strava_id=%s: %s",
                            user_id,
                            act_data.get("provider_activity_id"),
                            row_exc,
                            exc_info=True,
                        )
                        try:
                            import sentry_sdk
                            sentry_sdk.set_tag("ingest.phase", "flush_batch_row")
                            sentry_sdk.set_tag(
                                "ingest.provider",
                                act_data.get("provider", "?"),
                            )
                            sentry_sdk.capture_exception(row_exc)
                        except Exception:
                            # Sentry capture itself shouldn't take down ingest.
                            pass
            flush_db.commit()
            # Commit succeeded — promote the batch's tentative tuples
            # to the persistent canonical list. (Already in the bucket;
            # this keeps `existing_date_distance` in sync for code that
            # iterates it.)
            existing_date_distance.extend(batch_new_dd)
            if batch_dedup_skipped:
                logger.info(
                    "Batch dedup: %d row(s) already present (likely "
                    "webhook/bulk race) — not counted as failed",
                    batch_dedup_skipped,
                )
        except Exception:
            flush_db.rollback()
            # Commit failed — undo the bucket inserts so subsequent
            # batches don't dedupe against ghost rows that never
            # persisted. F2 from the 2nd-pass review.
            import contextlib
            for ghost in batch_new_dd:
                bkt = int(ghost[0]) // _BUCKET_S
                with contextlib.suppress(ValueError):
                    _existing_bucket[bkt].remove(ghost)
            batch_new_dd.clear()
            # Symmetric to the `batch_new_dd` rollback: pop the
            # provider_activity_ids the outer loop eagerly added to
            # `existing_ids` for this batch. Without this, a future
            # re-import would see them as "already exists" and silently
            # drop the rows. Audit 2026-05-27 S2.3.
            for ghost_id in batch_new_ids:
                existing_ids.discard(ghost_id)
            # On unexpected outer failure (not a per-row issue), mark the
            # whole batch as failed so we don't double-count successes.
            batch_failed = len(batch)
            batch_created = 0
            raise
        finally:
            flush_db.close()
        return batch_created, batch_failed

    def _backfill_existing(act_data: dict) -> None:
        """Backfill activity_date / total_photo_count for an existing row.

        Short-lived session so the connection is released between
        backfills. The common case is a no-op (both fields already set).
        """
        new_date = act_data.get("activity_date")
        new_photo_count = act_data.get("total_photo_count")
        if not (new_date or new_photo_count):
            return
        bf_db = SessionLocal()
        try:
            existing = bf_db.query(Activity).filter(
                Activity.user_id == user_id,
                Activity.provider == "strava",
                Activity.provider_activity_id == act_data.get("provider_activity_id"),
            ).first()
            if existing:
                dirty = False
                if new_date and not existing.activity_date:
                    existing.activity_date = new_date
                    dirty = True
                if new_photo_count and not existing.total_photo_count:
                    existing.total_photo_count = new_photo_count
                    dirty = True
                if dirty:
                    bf_db.commit()
        finally:
            bf_db.close()

    pending: list[dict] = []
    # `pending_new_ids` mirrors the IDs we eagerly added to `existing_ids`
    # so _flush_batch can roll them back if commit fails (S2.3).
    pending_new_ids: list[str] = []
    for act_data in activities_data:
        provider_activity_id = act_data.get("provider_activity_id")

        # Fast in-memory idempotency check (no DB query per activity)
        if provider_activity_id and provider_activity_id in existing_ids:
            _backfill_existing(act_data)
            skipped += 1
            continue

        pending.append(act_data)
        if provider_activity_id:
            existing_ids.add(provider_activity_id)
            pending_new_ids.append(provider_activity_id)

        if len(pending) >= BATCH_SIZE:
            c, f = _flush_batch(pending, pending_new_ids)
            created += c
            failed += f
            pending = []
            pending_new_ids = []
            if on_progress:
                on_progress(created, skipped, failed)

    # Flush the tail
    if pending:
        c, f = _flush_batch(pending, pending_new_ids)
        created += c
        failed += f
        if on_progress:
            on_progress(created, skipped, failed)

    # Heat edge computation is deferred during bulk import.
    # Activities are stored with contribute_heatmap=True so a later heatmap
    # rebuild picks them up. This keeps import fast (seconds, not hours).

    return {"created": created, "skipped": skipped, "failed": failed}


def get_heat_cells_public(
    sport: str | None = None,
    zoom: int = 14,
    bbox: tuple[float, float, float, float] | None = None,
    k: int = HEATMAP_K_ANONYMITY,
) -> list[dict]:
    """Return community heatmap cells respecting K-anonymity from DB.

    DEPRECATED: kept for compatibility while ``heat_cells`` exists. New
    callers should use ``get_heat_cells_aggregated`` which derives cells
    from ``heat_edges`` at query time and removes the ingest-time write
    hot path. Once the May 2026 audit step has soaked in production for
    one rebuild cycle the ``heat_cells`` table will be dropped via the
    pending migration ``0036_drop_heat_cells_tables.py`` and this
    function will be removed.
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        conditions = ["user_count >= :k", "zoom = :zoom"]
        params: dict[str, Any] = {"k": k, "zoom": zoom}

        if sport:
            conditions.append("sport = :sport")
            params["sport"] = sport

        if bbox:
            min_lon, min_lat, max_lon, max_lat = bbox
            # Filter by zoom prefix (tile-based partitioning)
            conditions.append(
                "cell_key LIKE :zoom_prefix || '/%'"
            )
            params["zoom_prefix"] = str(zoom)

        where_clause = " AND ".join(conditions)
        rows = db.execute(sa_text(f"""
            SELECT cell_key, sport, zoom, user_count, pass_count
            FROM heat_cells
            WHERE {where_clause}
        """), params).fetchall()

        results = []
        for row in rows:
            cell_data = {
                "cell_key": row[0],
                "sport": row[1],
                "zoom": row[2] or zoom,
                "user_count": row[3],
                "pass_count": row[4],
            }

            # Apply fine-grained bbox filter if needed
            if bbox:
                try:
                    parts = row[0].split("/")
                    cx, cy = int(parts[1]), int(parts[2])
                    nn = 2 ** zoom
                    lon = cx / nn * 360.0 - 180.0
                    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * cy / nn)))
                    lat = math.degrees(lat_rad)
                    if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
                        continue
                except Exception:
                    pass

            results.append(cell_data)
        return results
    finally:
        db.close()


def get_heat_cells_aggregated(
    sport: str | None = None,
    zoom: int = 14,
    bbox: tuple[float, float, float, float] | None = None,
    k: int = HEATMAP_K_ANONYMITY,
    statement_timeout_ms: int | None = None,
) -> list[dict]:
    """Aggregate community heat cells from ``heat_edges`` at query time.

    Replaces the dedicated ``heat_cells`` table — see May 2026 ingest
    audit, improvement #4. Deriving cells on read drops ~30% from the
    per-activity ingest hot path and removes a whole write/dedup table
    pair (``heat_cells`` + ``heat_cell_contributors``).

    Semantics vs the old ``get_heat_cells_public``:

    - ``cell_key``: still ``"{zoom}/{x}/{y}"`` (slippy tile, default
      z14). Derived from each edge's start point — same convention as
      the old ingest-time ``_geojson_to_cells``/``_latlon_to_cell_key``
      pair, keeping cell_keys directly comparable for clients that
      saved a cell_key list.
    - ``user_count``: ``COUNT(DISTINCT heat_edge_contributors.user_id_hash)``
      across all edges starting in the tile. Same definition as the old
      table (distinct contributors per cell), now computed live.
    - ``pass_count``: ``SUM(heat_edges.pass_count)`` across edges in
      the tile. **This differs from the old semantic** which was
      "number of activities touching the cell". The new value is "total
      edge traversals inside the tile" — a strictly more granular
      density signal that grows with both passes and edge density. It
      is recorded in the migration notes; the consumer (frontend stats
      page + ODbL export) only uses it as a heat-weight, never as an
      absolute count.

    K-anonymity is enforced after aggregation: only tiles where the
    distinct-user count is >= ``k`` are returned. The ``user_count >= 1``
    pre-filter on ``heat_edges`` uses the partial index from migration
    0027 so the planner skips empty edges before the join.
    """
    # RAW-trace cutover: derived from the LEGACY ``heat_edges`` table, DROPPED
    # in prod under the raw pivot. There is no on-read cell layer in raw mode
    # (the map is the static raw PMTiles), so return an empty result rather
    # than querying a table that no longer exists → this keeps every caller
    # (/heatmap/stats, /heatmap/export, /me/unexplored) from 500-ing.
    if raw_display_enabled():
        return []
    from app.db.session import SessionLocal

    if zoom != 14:
        # Cell heatmap was always served at z14 in production. Derived
        # cells use the same Web Mercator math for a single zoom; supporting
        # arbitrary zoom would require a parameterized SQL CTE, which adds
        # cost without a known consumer. Fall back to the legacy table for
        # the rare non-z14 callers.
        return get_heat_cells_public(sport=sport, zoom=zoom, bbox=bbox, k=k)

    params: dict[str, Any] = {"k": k}
    sport_clause = ""
    if sport:
        sport_clause = "AND he.sport = :sport"
        params["sport"] = sport

    bbox_clause = ""
    if bbox:
        min_lon, min_lat, max_lon, max_lat = bbox
        bbox_clause = (
            "AND he.geometry && "
            "ST_MakeEnvelope(:lon_min, :lat_min, :lon_max, :lat_max, 4326)"
        )
        params["lon_min"] = min_lon
        params["lat_min"] = min_lat
        params["lon_max"] = max_lon
        params["lat_max"] = max_lat

    # z14 slippy-tile math, in SQL, computed from each edge's start
    # point (same convention as the old `_latlon_to_z14_tile`). Web
    # Mercator y is clamped via least/greatest on the latitude to keep
    # the LN/TAN domain valid for poles (won't occur in our data, but
    # defensive — PostgreSQL raises ERROR on out-of-range tan/log).
    #
    # We aggregate in two stages:
    #   1. Per-edge tile assignment + pass_count carry.
    #   2. JOIN to contributors and GROUP BY (tile, sport).
    # The JOIN is cheap because heat_edge_contributors PK is
    # (edge_key, user_id_hash) so the index supports edge_key lookups.
    db = SessionLocal()
    try:
        # Bounded work (cost control): /heatmap/stats + /heatmap/export are
        # PUBLIC and attacker-parameterised (bbox/sport/zoom). A pathological
        # request must not pin a worker on an unbounded aggregation. SET LOCAL
        # scopes the timeout to THIS transaction only (never leaks onto the
        # pooled connection). No-op when the caller passes None.
        if statement_timeout_ms and statement_timeout_ms > 0:
            db.execute(sa_text(f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}"))
        rows = db.execute(sa_text(f"""
            WITH edge_tiles AS (
                SELECT
                    he.edge_key,
                    he.sport,
                    he.pass_count,
                    FLOOR(
                        (ST_X(ST_StartPoint(he.geometry)) + 180.0) / 360.0
                        * 16384
                    )::int AS tx,
                    FLOOR(
                        (
                            1.0 - LN(
                                TAN(
                                    RADIANS(
                                        GREATEST(
                                            -85.05112878,
                                            LEAST(85.05112878, ST_Y(ST_StartPoint(he.geometry)))
                                        )
                                    )
                                )
                                + 1.0 / COS(
                                    RADIANS(
                                        GREATEST(
                                            -85.05112878,
                                            LEAST(85.05112878, ST_Y(ST_StartPoint(he.geometry)))
                                        )
                                    )
                                )
                            ) / PI()
                        ) / 2.0 * 16384
                    )::int AS ty
                FROM heat_edges he
                WHERE he.user_count >= 1
                  {sport_clause}
                  {bbox_clause}
            )
            SELECT
                '14/' || et.tx || '/' || et.ty AS cell_key,
                et.sport,
                COUNT(DISTINCT hec.user_id_hash) AS user_count,
                SUM(et.pass_count) AS pass_count
            FROM edge_tiles et
            JOIN heat_edge_contributors hec
              ON hec.edge_key = et.edge_key
            GROUP BY cell_key, et.sport
            HAVING COUNT(DISTINCT hec.user_id_hash) >= :k
        """), params).fetchall()
    finally:
        db.close()

    return [
        {
            "cell_key": row[0],
            "sport": row[1],
            "zoom": zoom,
            "user_count": int(row[2]),
            "pass_count": int(row[3]) if row[3] is not None else 0,
        }
        for row in rows
    ]


# ── Heatmap cache (now just thin wrappers around DB queries) ──────────────────

def build_heatmap_cache(k: int = HEATMAP_K_ANONYMITY) -> None:
    """No-op: data is now in PostGIS. Kept for API compatibility."""
    pass


def get_cached_edges(
    sport: str | None,
    bbox: tuple[float, float, float, float] | None,
    k: int = HEATMAP_K_ANONYMITY,
) -> list[dict]:
    """Return K-filtered edges (now delegates to PostGIS query)."""
    return get_heat_edges_public(sport=sport, bbox=bbox, k=k)


def get_cached_cells(
    sport: str | None,
    zoom: int,
    bbox: tuple[float, float, float, float] | None,
    k: int = HEATMAP_K_ANONYMITY,
) -> list[dict]:
    """Return K-filtered cells (delegates to read-time aggregation)."""
    return get_heat_cells_aggregated(sport=sport, zoom=zoom, bbox=bbox, k=k)


# ── Activity loading from DB ─────────────────────────────────────────────────

def _bundled_fixture_path() -> str:
    """Path to the bundled anonymized activities fixture (gzipped)."""
    return os.path.join(os.path.dirname(__file__), "..", "..", "fixtures", "activities.jsonl.gz")


def _load_fixture_to_db() -> int:
    """Load bundled fixture activities into DB. Returns count loaded."""
    import gzip as _gzip

    from app.db.models import Activity, ActivityCell
    from app.db.session import SessionLocal

    fixture = os.path.normpath(_bundled_fixture_path())
    if not os.path.exists(fixture):
        return 0

    opener = _gzip.open if fixture.endswith(".gz") else open
    db = SessionLocal()
    count = 0
    try:
        with opener(fixture, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    act = json.loads(line)
                except Exception:
                    continue
                act_id = act.get("id")
                if not act_id:
                    continue

                activity = Activity(
                    id=act_id,
                    user_id=act.get("user_id", "anonymous"),
                    provider=act.get("provider", "file"),
                    provider_activity_id=act.get("provider_activity_id"),
                    sport=act.get("sport", "road"),
                    name=act.get("name"),
                    geometry_geojson=act.get("geometry_geojson"),
                    geometry=_geom_from_geojson_sql(act.get("geometry_geojson")),
                    distance_m=act.get("distance_m"),
                    elevation_gain_m=act.get("elevation_gain_m"),
                    file_hash=act.get("file_hash"),
                    contribute_heatmap=act.get("contribute_heatmap", True),
                )
                db.add(activity)

                # Store activity cells
                cells = _geojson_to_cells(act.get("geometry_geojson"))
                for cell_key in cells:
                    db.add(ActivityCell(
                        activity_id=act_id,
                        user_id=act.get("user_id", "anonymous"),
                        cell_key=cell_key,
                        zoom=14,
                    ))
                count += 1

        db.commit()
    except Exception:
        db.rollback()
        import logging
        logging.getLogger(__name__).warning("Failed to load fixture to DB", exc_info=True)
    finally:
        db.close()
    return count


def load_persisted_activities() -> int:
    """Load activities from DB and populate heat_edges/heat_cells in PostGIS.

    If DB has no activities, loads from bundled fixture into DB first.
    If heat_edges table already has data, skips rebuild (data is persisted).
    Called once at startup.
    """
    import logging as _logging

    from app.db.models import Activity
    from app.db.session import SessionLocal

    logger = _logging.getLogger(__name__)

    db = SessionLocal()
    try:
        act_count = db.query(Activity).count()

        # If DB is empty, bootstrap from bundled fixture
        if act_count == 0:
            act_count = _load_fixture_to_db()
            if act_count:
                logger.info("Loaded %d activities from bundled fixture into DB", act_count)

        # Under raw display mode the community heatmap renders from `activities`
        # directly (raw_trace_display) — `heat_edges`/`heat_edges_agg` are legacy
        # and, in prod, DROPPED. So skip the entire matched-era heat_edges
        # rebuild here: the COUNT below would raise on the missing relation, and
        # the rebuild does dead work. (Fixture bootstrap above already ran.)
        from app.services.raw_trace_display import raw_display_enabled
        if raw_display_enabled():
            logger.info(
                "Raw display mode — skipping heat_edges rebuild at startup (%d activities).",
                act_count,
            )
            return act_count

        # Check if heat_edges already has data (persisted from previous run)
        heat_count = db.execute(sa_text("SELECT COUNT(*) FROM heat_edges")).fetchone()
        if heat_count and heat_count[0] > 0:
            logger.info("Heat edges already in DB (%d), skipping rebuild", heat_count[0])
            return act_count

        # One-time migration: populate heat_edges from activities (parallel)
        logger.info("Rebuilding heat data from %d activities...", act_count)
        activities_data = [
            (act.user_id, act.sport or "road", act.geometry_geojson,
             act.activity_date or act.created_at, act.contribute_heatmap,
             act.id)
            for act in db.query(Activity).yield_per(500)
        ]
        loaded = rebuild_heatmap_parallel(activities_data)

        # rebuild_heatmap_parallel disables the per-activity heat_edges_agg
        # refresh internally (the 4 workers would contend on the agg table),
        # so on this fresh/bootstrap-DB rebuild path the display aggregate is
        # left empty → the all-time heatmap renders BLANK until a manual
        # backfill. Populate it once now. This ONLY runs in the rebuild branch
        # (the persisted-heat_edges path above returned early), so a normal
        # prod startup does no heavy work here.
        try:
            from app.jobs.rebuild_heat_agg import backfill_heat_agg
            agg_rows = backfill_heat_agg(db)
            logger.info("Populated heat_edges_agg: %d rows", agg_rows)
        except Exception:
            logger.warning(
                "heat_edges_agg backfill failed after startup rebuild "
                "(heat_edges are fine; run `python -m app.jobs.rebuild_heat_agg`)",
                exc_info=True,
            )
        return loaded
    finally:
        db.close()


_PARALLEL_WORKERS = int(os.environ.get("HEATMAP_WORKERS", "4"))


_DEADLOCK_MAX_RETRIES = 3
_DEADLOCK_BASE_DELAY = 0.2  # seconds


def retry_on_deadlock(fn, *args, **kwargs):
    """Run ``fn(*args, **kwargs)`` with exponential-backoff retry on
    PostgreSQL deadlocks.

    Concurrent UPSERTs on overlapping edge keys cause `psycopg2.errors
    .DeadlockDetected`. We let the lock-loser retry rather than fail
    the whole ingest. Used by the parallel bulk-import scripts
    (`import_strava_export.py`, `audit_ingest_perf.py`) and by
    `_process_activity` in `rebuild_heatmap`.
    """
    import random
    for attempt in range(_DEADLOCK_MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if "deadlock" in str(e).lower() and attempt < _DEADLOCK_MAX_RETRIES:
                delay = _DEADLOCK_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.1)
                time.sleep(delay)
                continue
            raise


def _process_activity(args: tuple) -> tuple[bool, str]:
    """Process a single activity for heatmap — runs in a thread pool worker.

    Retries on deadlock (concurrent UPSERT on overlapping edge keys).

    Accepts the 6-tuple shape since migration 0052:
      (user_id, sport, geojson, activity_date, contribute, activity_id)
    Older 5-tuple callers (no activity_id) are tolerated for
    compatibility — `_update_heat_edges` will synthesize a uuid4().
    """
    if len(args) == 6:
        user_id, sport, geometry_geojson, activity_date, contribute, activity_id = args
    else:
        # Legacy 5-tuple — no activity_id available. Each call gets a
        # fresh synthetic uuid4 inside _update_heat_edges → behaves
        # like pre-migration (each call counts as one contribution).
        user_id, sport, geometry_geojson, activity_date, contribute = args
        activity_id = None
    if not (contribute if contribute is not None else True):
        return False, "skipped"
    try:
        def _do():
            # heat_cells write removed — see ingest_activity comment.
            # The rebuild path is the second hot path that benefits the
            # most from this; per-activity ingest gain is ~30%, but the
            # full-fleet rebuild now skips ~1M needless heat_cell writes.
            if geometry_geojson:
                _update_heat_edges(
                    user_id, sport, geometry_geojson,
                    activity_date=activity_date, activity_id=activity_id,
                )
        retry_on_deadlock(_do)
        return True, "ok"
    except Exception as e:
        logger.warning("Activity failed for user %s: %s", user_id, e)
        return False, str(e)


def _prewarm_osm_cache(activities_data: list[tuple]) -> int:
    """Pre-load OSM tile segments for all activities into the shared cache.

    Called once before parallel workers start. Eliminates redundant per-worker
    DB queries for overlapping tiles. Returns number of tiles loaded.
    """
    from app.db.session import SessionLocal

    # Collect all z14 tiles across all activities
    all_tiles: set[str] = set()
    for tup in activities_data:
        # Tuple shape: (user_id, sport, geojson, date, contribute[, activity_id])
        # — accept both 5-tuple legacy and 6-tuple post-migration-0052.
        geojson = tup[2]
        if not geojson:
            continue
        try:
            coords = json.loads(geojson).get("coordinates", [])
        except (json.JSONDecodeError, AttributeError):
            continue
        all_tiles.update(_coords_to_z14_tiles(coords))

    # Check which tiles have OSM data
    db = SessionLocal()
    try:
        known = _load_known_osm_tiles(db)
        tiles_to_load = all_tiles & known if known else set()
        if not tiles_to_load:
            return 0

        logger.info("Pre-warming OSM cache: %d tiles to load", len(tiles_to_load))
        loaded = 0
        for tk in tiles_to_load:
            with _osm_segment_cache_lock:
                if tk in _osm_segment_cache:
                    continue
            _get_cached_tile_segments(tk, db)
            loaded += 1
        logger.info("OSM cache pre-warmed: %d tiles loaded", loaded)
        return loaded
    finally:
        db.close()


def rebuild_heatmap_parallel(
    activities_data: list[tuple],
    max_workers: int | None = None,
) -> int:
    """Rebuild heatmap edges from a list of activities using parallel workers.

    Optimizations:
    1. Pre-warms OSM tile cache (single DB session, shared across workers)
    2. Per-activity progress logging (every 25 activities)
    3. Deadlock retry with exponential backoff

    Args:
        activities_data: list of (user_id, sport, geometry_geojson, activity_date, contribute_heatmap)
        max_workers: thread pool size (default: HEATMAP_WORKERS env or 4)

    Returns:
        Number of activities successfully processed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    workers = max_workers or _PARALLEL_WORKERS
    total = len(activities_data)
    logger.info("Rebuilding heatmap: %d activities, %d workers", total, workers)

    # Disable the per-activity heat_edges_agg refresh for the whole parallel
    # rebuild: a bulk rebuild touches the same ways thousands of times and the
    # 4 workers would contend DELETE+INSERT-ing overlapping ways in
    # heat_edges_agg. The correct maintenance for a full rebuild is ONE
    # backfill afterwards (rebuild_heatmap.main does it; direct callers should
    # backfill_heat_agg() themselves). Restored in the finally.
    from app.jobs.rebuild_heat_agg import SKIP_AGG_MAINTENANCE_ENV
    _prev_skip_agg = os.environ.get(SKIP_AGG_MAINTENANCE_ENV)
    os.environ[SKIP_AGG_MAINTENANCE_ENV] = "true"

    # Optimization #1: Pre-warm OSM segment cache (shared across all workers)
    _prewarm_osm_cache(activities_data)

    # Optimization #2: Increase OSM cache size for rebuild (no eviction during
    # rebuild). The cap is read live from OSM_TILE_CACHE_MAX (see
    # _osm_segment_cache_max), so bump the env for the duration and restore it.
    _prev_tile_cache_max = os.environ.get("OSM_TILE_CACHE_MAX")
    os.environ["OSM_TILE_CACHE_MAX"] = str(max(_osm_segment_cache_max(), 2000))

    loaded = 0
    failed = 0
    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_process_activity, args): i for i, args in enumerate(activities_data)}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    success, msg = future.result()
                    if success:
                        loaded += 1
                    else:
                        failed += 1
                except Exception as e:
                    failed += 1
                    logger.warning("Activity %d raised: %s", idx, e)

                # Optimization #5: Log every 25 activities for better visibility
                done = loaded + failed
                if done % 25 == 0:
                    elapsed = time.time() - t0
                    rate = done / elapsed if elapsed > 0 else 0
                    eta = (total - done) / rate if rate > 0 else 0
                    logger.info(
                        "Progress: %d/%d (%.0f%%) — %d ok, %d failed — %.1f act/s — ETA %.0fs",
                        done, total, done / total * 100, loaded, failed, rate, eta,
                    )
    finally:
        # Restore original cache size
        if _prev_tile_cache_max is None:
            os.environ.pop("OSM_TILE_CACHE_MAX", None)
        else:
            os.environ["OSM_TILE_CACHE_MAX"] = _prev_tile_cache_max
        if _prev_skip_agg is None:
            os.environ.pop(SKIP_AGG_MAINTENANCE_ENV, None)
        else:
            os.environ[SKIP_AGG_MAINTENANCE_ENV] = _prev_skip_agg

    elapsed = time.time() - t0
    logger.info("Rebuild complete: %d/%d ok, %d failed in %.0fs (%.1f act/s)",
                loaded, total, failed, elapsed, total / elapsed if elapsed > 0 else 0)
    return loaded


def load_seed_gpx(user_id: str) -> int:
    """Load GPX files from {DATA_DIR}/seed_gpx/ as heatmap activities.

    Drop your Strava/personal GPX exports into data/seed_gpx/ and they'll be
    ingested at startup for community heatmap + routing data in dev.

    Files are parsed once per startup (in-memory only, not persisted to JSONL).
    Sport is inferred from filename: *mtb*, *vtt* → mtb, *gravel* → gravel,
    *run*, *trail* → running, *offroad* → offroad, default → road.

    Returns the number of activities loaded.
    """
    seed_dir = os.path.join(DATA_DIR, "seed_gpx") if DATA_DIR else ""
    if not seed_dir or not os.path.isdir(seed_dir):
        return 0

    import glob
    import logging

    logger = logging.getLogger(__name__)
    gpx_files = sorted(glob.glob(os.path.join(seed_dir, "*.gpx")))
    if not gpx_files:
        return 0

    from app.services.gpx import parse_gpx

    count = 0
    for gpx_path in gpx_files:
        fname = os.path.basename(gpx_path).lower()

        # Infer sport from filename
        if any(k in fname for k in ("mtb", "vtt", "mountain")):
            sport = "mtb"
        elif "gravel" in fname:
            sport = "gravel"
        elif any(k in fname for k in ("run", "trail", "hike", "rando")):
            sport = "running"
        elif "offroad" in fname:
            sport = "offroad"
        else:
            sport = "road"

        try:
            with open(gpx_path, encoding="utf-8") as f:
                content = f.read()
            parsed = parse_gpx(content)
        except Exception:
            logger.warning("Seed GPX: failed to parse %s", fname, exc_info=True)
            continue

        activity_data = {
            "provider": "seed",
            "provider_activity_id": f"seed-{fname}",
            # Seed/dev fixtures feed the local community heatmap.
            "source": COMMUNITY_SOURCE,
            "sport": sport,
            "name": parsed.get("name") or fname.replace(".gpx", ""),
            "distance_m": parsed.get("distance_m", 0),
            "elevation_gain_m": parsed.get("elevation_gain_m", 0),
            "geometry_geojson": parsed.get("geometry_geojson"),
            "contribute_heatmap": True,
        }
        result = ingest_activity(
            user_id, activity_data,
            contribute_heatmap=True, rebuild_cache=False,
        )
        if result.get("status") != "already_exists":
            count += 1

    if count:
        logger.info("Seed GPX: loaded %d activities from %s", count, seed_dir)
    return count


def get_user_activities(user_id: str) -> list[dict]:
    """Return all activities for a user (private). Used by me_activities endpoint."""
    from app.db.models import Activity
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        activities = db.query(Activity).filter(Activity.user_id == user_id).all()
        return [
            {
                "id": a.id,
                "user_id": a.user_id,
                "provider": a.provider,
                "provider_activity_id": a.provider_activity_id,
                "sport": a.sport,
                "name": a.name,
                "geometry_geojson": a.geometry_geojson,
                "distance_m": a.distance_m,
                "elevation_gain_m": a.elevation_gain_m,
                "file_hash": a.file_hash,
                "contribute_heatmap": a.contribute_heatmap,
                "moving_time": a.moving_time,
                "activity_date": (a.activity_date or a.created_at).isoformat() if (a.activity_date or a.created_at) else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "total_photo_count": a.total_photo_count or 0,
            }
            for a in activities
        ]
    finally:
        db.close()


def get_user_activities_meta(user_id: str) -> list[dict]:
    """Return activity metadata only — NO geometry_geojson.

    Companion to ``get_user_activities`` for callers that don't need the
    LineString (personal-bests, per-sport stats). Projects only the
    columns required for aggregation; cuts payload from MB-per-row to
    a few hundred bytes and lets SQLAlchemy stream rows instead of
    materialising the whole LineString in Python memory.
    """
    from sqlalchemy import select

    from app.db.models import Activity
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        stmt = select(
            Activity.id,
            Activity.user_id,
            Activity.provider,
            Activity.provider_activity_id,
            Activity.sport,
            Activity.name,
            Activity.distance_m,
            Activity.elevation_gain_m,
            Activity.file_hash,
            Activity.contribute_heatmap,
            Activity.moving_time,
            Activity.activity_date,
            Activity.created_at,
            Activity.total_photo_count,
        ).where(Activity.user_id == user_id)

        rows = db.execute(stmt).all()
        return [
            {
                "id": r.id,
                "user_id": r.user_id,
                "provider": r.provider,
                "provider_activity_id": r.provider_activity_id,
                "sport": r.sport,
                "name": r.name,
                "distance_m": r.distance_m,
                "elevation_gain_m": r.elevation_gain_m,
                "file_hash": r.file_hash,
                "contribute_heatmap": r.contribute_heatmap,
                "moving_time": r.moving_time,
                "activity_date": (r.activity_date or r.created_at).isoformat() if (r.activity_date or r.created_at) else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "total_photo_count": r.total_photo_count or 0,
            }
            for r in rows
        ]
    finally:
        db.close()


def get_user_activities_stats_by_sport(user_id: str) -> dict[str, dict]:
    """Aggregate per-sport stats in one query via GROUP BY.

    Replaces the N round-trips pattern (``/me/stats?sport=X`` × 5 sports
    + 1 totals) the frontend used to issue at stats-page load. Returns:

        {
            "total": {"activity_count", "total_distance_m", "total_elevation_gain_m", "unique_cells"},
            "road":  {...},
            "gravel": {...},
            ...
        }

    Sports with zero activities are still present with zeros so the
    frontend never has to special-case missing keys.
    """
    from sqlalchemy import func, select

    from app.db.models import Activity, ActivityCell
    from app.db.session import SessionLocal

    sports = ["road", "gravel", "mtb", "offroad", "running"]
    zero: dict = {
        "activity_count": 0,
        "total_distance_m": 0.0,
        "total_elevation_gain_m": 0.0,
        "unique_cells": 0,
    }
    out: dict[str, dict] = {s: dict(zero) for s in sports}
    out["total"] = dict(zero)

    db = SessionLocal()
    try:
        # Per-sport totals (activities only, no cells join).
        # Filter to the 5 supported sports at SQL level so unknown / legacy
        # sport values (NULL, "other", historical "cycling", etc.) don't get
        # silently folded into a bucket. They count toward `total` via the
        # second query below (unfiltered) — total stays honest, per-sport
        # only reflects sports the UI actually renders.
        sport_rows = db.execute(
            select(
                Activity.sport,
                func.count(Activity.id),
                func.coalesce(func.sum(Activity.distance_m), 0),
                func.coalesce(func.sum(Activity.elevation_gain_m), 0),
            )
            .where(Activity.user_id == user_id, Activity.sport.in_(sports))
            .group_by(Activity.sport)
        ).all()

        for sport, count, dist, elev in sport_rows:
            out[sport]["activity_count"] = int(count or 0)
            out[sport]["total_distance_m"] = float(dist or 0)
            out[sport]["total_elevation_gain_m"] = float(elev or 0)

        # Totals: unfiltered scan so unknown-sport activities count too.
        total_row = db.execute(
            select(
                func.count(Activity.id),
                func.coalesce(func.sum(Activity.distance_m), 0),
                func.coalesce(func.sum(Activity.elevation_gain_m), 0),
            )
            .where(Activity.user_id == user_id)
        ).one()
        out["total"]["activity_count"] = int(total_row[0] or 0)
        out["total"]["total_distance_m"] = float(total_row[1] or 0)
        out["total"]["total_elevation_gain_m"] = float(total_row[2] or 0)

        # Unique cells per sport (cells join, distinct cell_key).
        # Same SQL-level sport filter so unknown sports don't leak in.
        cell_rows = db.execute(
            select(
                Activity.sport,
                func.count(func.distinct(ActivityCell.cell_key)),
            )
            .select_from(ActivityCell)
            .join(Activity, ActivityCell.activity_id == Activity.id)
            .where(ActivityCell.user_id == user_id, Activity.sport.in_(sports))
            .group_by(Activity.sport)
        ).all()

        for sport, cells in cell_rows:
            out[sport]["unique_cells"] = int(cells or 0)

        # Total unique cells (distinct cell_key across all sports)
        total_cells = db.execute(
            select(func.count(func.distinct(ActivityCell.cell_key)))
            .where(ActivityCell.user_id == user_id)
        ).scalar() or 0
        out["total"]["unique_cells"] = int(total_cells)

        return out
    finally:
        db.close()


def get_activity_by_id(user_id: str, activity_id: str) -> dict | None:
    """Return a single activity by id, scoped to the requesting user.

    Returns ``None`` when the row doesn't exist or belongs to another user.
    Replaces the ``GET /me/activities?limit=5000`` + client-side filter
    pattern in the frontend which loaded the whole activity list to find
    one row — that broke at the 5000-cap for users with big Strava
    histories.
    """
    from app.db.models import Activity
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        a = db.query(Activity).filter(
            Activity.user_id == user_id,
            Activity.id == activity_id,
        ).first()
        if not a:
            return None
        return {
            "id": a.id,
            "user_id": a.user_id,
            "provider": a.provider,
            "provider_activity_id": a.provider_activity_id,
            "sport": a.sport,
            "name": a.name,
            "geometry_geojson": a.geometry_geojson,
            "distance_m": a.distance_m,
            "elevation_gain_m": a.elevation_gain_m,
            "file_hash": a.file_hash,
            "contribute_heatmap": a.contribute_heatmap,
            "moving_time": a.moving_time,
            "activity_date": (a.activity_date or a.created_at).isoformat() if (a.activity_date or a.created_at) else None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "total_photo_count": a.total_photo_count or 0,
        }
    finally:
        db.close()


def get_user_cell_keys(user_id: str, sport: str | None = None) -> set[str]:
    """Return set of cell keys visited by a user (private). Used by /me/unexplored and /me/cells."""
    from app.db.models import Activity, ActivityCell
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        if sport:
            rows = db.query(ActivityCell.cell_key).join(
                Activity, ActivityCell.activity_id == Activity.id
            ).filter(
                ActivityCell.user_id == user_id,
                Activity.sport == sport,
            ).distinct().all()
        else:
            rows = db.query(ActivityCell.cell_key).filter(
                ActivityCell.user_id == user_id
            ).distinct().all()
        return {r[0] for r in rows}
    finally:
        db.close()


def get_user_stats(user_id: str, sport: str | None = None) -> dict:
    """Return private stats for a specific user.

    SQL-side aggregate: returns 3 scalars in one round-trip. Previously
    materialised every Activity row (with full `geometry_geojson`, 5-50
    KB each) into Python just to sum two scalars — for a 5000-activity
    Strava user that's 25-250 MB pulled into the API worker on every
    sidebar render of /me/stats. See PR-E observability+perf bundle.
    """
    from sqlalchemy import func

    from app.db.models import Activity, ActivityCell
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        agg_q = db.query(
            func.count(Activity.id),
            func.coalesce(func.sum(Activity.distance_m), 0),
            func.coalesce(func.sum(Activity.elevation_gain_m), 0),
        ).filter(Activity.user_id == user_id)
        if sport:
            agg_q = agg_q.filter(Activity.sport == sport)
        activity_count, total_distance, total_elevation = agg_q.one()

        # Count unique cells
        if sport:
            cell_q = db.query(func.count(func.distinct(ActivityCell.cell_key))).join(
                Activity, ActivityCell.activity_id == Activity.id
            ).filter(
                ActivityCell.user_id == user_id,
                Activity.sport == sport,
            )
        else:
            cell_q = db.query(func.count(func.distinct(ActivityCell.cell_key))).filter(
                ActivityCell.user_id == user_id
            )
        unique_cells = cell_q.scalar() or 0

        return {
            "activity_count": int(activity_count or 0),
            "total_distance_m": float(total_distance or 0),
            "total_elevation_gain_m": float(total_elevation or 0),
            "unique_cells": unique_cells,
        }
    finally:
        db.close()


def get_pending_gps_upgrade_count(user_id: str) -> int:
    """Count Strava activities still on low-res polyline (not yet GPS-upgraded)."""
    from app.db.models import Activity
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        return db.query(Activity).filter(
            Activity.user_id == user_id,
            Activity.provider == "strava",
            Activity.geometry_source == "polyline",
            Activity.geometry_geojson.isnot(None),
        ).count()
    finally:
        db.close()
