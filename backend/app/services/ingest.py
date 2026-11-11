"""Activity ingestion service.

Stores activities (``activities`` + the private ``activity_cells`` index).
The community heatmap is NOT written here: it is rendered straight from
``activities`` by ``app.jobs.build_pmtiles`` (masked raw traces, see
``app.services.raw_trace_display``), which applies the consent, provenance
and distinct-user gates at build time.

Also hosts the DFCI overlay store (``dfci_edges``) and the per-user read
helpers behind ``/me/*``.
"""
import json
import logging
import math
import os
import threading
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy import text as sa_text

from app.services.display_geometry import compact_display_geojson
from app.services.geo import haversine_m as _haversine_m
from app.services.provenance import (
    COMMUNITY_SOURCE,
    should_promote_to_community,
)
from app.services.raw_trace_display import (
    display_coords_fingerprint,
    encode_display_coords,
)

logger = logging.getLogger(__name__)


def _display_coords_pair(geometry_geojson, source, contribute_heatmap):
    """``(blob, fingerprint)`` for the precomputed display-coords cache
    (migration 0065) — community rows only (they're the only rows the raw
    heatmap build ever streams), ``(None, None)`` otherwise. Best-effort: a
    failure here must never fail an ingest — the build's live-pipeline fallback
    handles NULL rows identically, just slower."""
    if not contribute_heatmap or source != COMMUNITY_SOURCE:
        return None, None
    try:
        blob = encode_display_coords(geometry_geojson)
    except Exception:  # noqa: BLE001 — derived cache is strictly optional
        return None, None
    if blob is None:
        return None, None
    return blob, display_coords_fingerprint()


def _display_geojson(geometry_geojson):
    """Compact display geometry for the personal map (``activities.display_geojson``,
    migration 0067) — ALL rows (every activity is drawn on "Ma heatmap").
    Derived cache, never the source of truth (Crouzet). Best-effort: a failure
    here must never fail an ingest — /me/activities recomputes on the fly when
    the cache is NULL."""
    try:
        return compact_display_geojson(geometry_geojson)
    except Exception:  # noqa: BLE001 — derived cache is strictly optional
        return None

HEATMAP_K_ANONYMITY = int(os.environ.get("HEATMAP_K_ANONYMITY", "2"))

# Cross-source dedup window: "the same physical ride" iff same user + start
# time within ±DEDUP_START_WINDOW_MIN AND distance within ±DEDUP_DISTANCE_TOL.
# SSOT — the ingest cross-provider dedup (below) AND account-merge dedup
# (app/services/account_merge.py) both key off these so they can never drift.
# Tightened to ±5 min / ±5 % in audit 2026-05-27 S2.6 (see the dedup call site).
DEDUP_START_WINDOW_MIN = 5
DEDUP_DISTANCE_TOL = 0.05


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
    schedule entirely (use ``make pmtiles`` after the loop).

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


_DENSIFY_MAX_GAP_M = 15.0  # max spacing between consecutive densified points


# Gaps larger than this are not "normal contiguous recording" any more — they
# are either a downsampled GPX (sparse sampling along a real ride, e.g. a
# reduced export at ~1 point / 150-700 m) OR a genuine GPS dropout (tunnel,
# dead battery, a car transfer stitched into one file).
_DENSIFY_GPS_BREAK_M = 500.0
# We can't tell sampling from dropout at densify time, so gaps up to
# _DENSIFY_MAX_BRIDGE_M are densified across ("bridged"): a downsampled ride
# stays one continuous run. Only gaps ABOVE the bridge cap become a hard
# ``None`` break: a >2.5 km jump in a single track is overwhelmingly a
# transfer between two rides, and bridging it would paint fake heat along a
# line nobody rode.
#
# Trace integrity (Crouzet): this only shapes the densified coord list used
# for display / privacy masking. The stored ``geometry_geojson`` is untouched.
_DENSIFY_MAX_BRIDGE_M = 2500.0


def _densify_coords(coords: list, max_gap: float = _DENSIFY_MAX_GAP_M) -> list:
    """Densify a coordinate list so no gap exceeds max_gap meters.

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
        # downsampling band) are densified across so the run stays
        # continuous. See the `_DENSIFY_MAX_BRIDGE_M` rationale above.
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


def _normalize_heat_edge_sport(sport: str) -> str:
    """Map an activity sport to one of the heatmap's sport buckets
    (``road``, ``gravel``, ``mtb``, ``offroad``, ``running``).

    Used by the raw-trace build to bucket traces per sport. Test sports
    (``cache_test_*`` / ``test_*``) and unknown values fall back to
    ``gravel``; an unknown value is logged so a typo upstream surfaces.
    (The name dates from the removed ``heat_edges`` table.)
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
        db.commit()
        _schedule_pmtiles_rebuild()
        return count
    except Exception:
        db.rollback()
        raise
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
      NOT Strava-API data, so the community build reads compliant
      geometry). Private ``activity_cells`` are refreshed to match, and
    * adopt the INCOMING upload's ``contribute_heatmap`` intent: if the
      pre-existing Strava twin was opted OUT while the manual upload intends
      IN, the promoted ride would otherwise never reach the community.

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
        # Refresh the display-coords cache (0065): the row just became
        # community-eligible AND its geometry changed — the derived blob must
        # follow the NEW authoritative geometry or the map would draw the old.
        act.display_coords, act.display_coords_params = _display_coords_pair(
            geo, COMMUNITY_SOURCE, contribute_heatmap)
        act.display_geojson = _display_geojson(geo)
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
) -> dict:
    """Store an activity (``activities`` + private ``activity_cells``).

    Nothing else is written: the community heatmap is built from
    ``activities`` by ``app.jobs.build_pmtiles``, which only reads rows that
    are opted in (``contribute_heatmap``), community-sourced and dated — see
    ``app.services.provenance``. ``edges_indexed`` in the result is always 0
    (kept for the upload response models).

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
                # Backfill activity_date if missing
                new_date = activity_data.get("activity_date")
                if new_date and not existing.activity_date:
                    existing.activity_date = new_date
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
                # The community build then reads the uploaded geometry (NOT
                # the Strava-API polyline — compliance).
                if should_promote_to_community(incoming_source, existing_source):
                    _promote_activity_to_community(
                        db, existing_id, activity_data, contribute_heatmap
                    )
                    db.commit()
                    return {
                        "activity_id": existing_id,
                        "status": "promoted",
                        "edges_indexed": 0,
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
        # Precomputed display-coords cache (0065) — community rows only, so the
        # raw heatmap build streams a compact blob instead of the full JSON.
        dc_blob, dc_params = _display_coords_pair(
            geometry_geojson, activity_data.get("source"), contribute_heatmap)

        activity = Activity(
            id=activity_id,
            user_id=user_id,
            provider=provider,
            provider_activity_id=provider_activity_id,
            source=activity_data.get("source"),
            sport=sport,
            # Road only by default (generic "Ride" with no finer signal): the
            # rider is asked to classify it. See strava_utils.DefaultSport.
            sport_is_default=bool(activity_data.get("sport_is_default", False)),
            name=activity_data.get("name"),
            geometry_geojson=geometry_geojson,
            display_coords=dc_blob,
            display_coords_params=dc_params,
            display_geojson=_display_geojson(geometry_geojson),
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

    return {
        "activity_id": activity_id,
        "status": "created",
        "cells_indexed": len(cells),
        "edges_indexed": 0,
    }


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
                    display_geojson=_display_geojson(act.get("geometry_geojson")),
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
    """Bootstrap an EMPTY database from the bundled fixture (dev / tests).

    Returns the number of activities in the DB. No-op when the DB already has
    activities. Called once at startup.
    """
    from app.db.models import Activity
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        act_count = db.query(Activity).count()
    finally:
        db.close()
    if act_count == 0:
        act_count = _load_fixture_to_db()
        if act_count:
            logger.info("Loaded %d activities from bundled fixture into DB", act_count)
    return act_count


def _map_activity_filters(user_id: str, sport: str | None):
    """WHERE clauses shared by ``count_user_map_activities`` and
    ``iter_user_map_activities`` — the /me/activities semantics: the user's
    own rows, with a non-empty geometry, optionally one sport."""
    from app.db.models import Activity

    conds = [
        Activity.user_id == user_id,
        Activity.geometry_geojson.isnot(None),
        Activity.geometry_geojson != "",
    ]
    if sport is not None:
        conds.append(Activity.sport == sport)
    return conds


def count_user_map_activities(user_id: str, sport: str | None = None) -> int:
    """COUNT of the user's map-displayable activities (geometry + sport filter).

    Companion to ``iter_user_map_activities`` — the /me/activities endpoint
    streams its body, so ``total`` (emitted at the head of the JSON) must be
    known before the rows are iterated. An indexed COUNT on ``user_id`` is
    far cheaper than materialising the rows to len() them (the pre-fix code
    loaded EVERY column of EVERY activity — 14k rows ≈ 0.4–1.4 GB for a real
    beta user — into web RAM just to slice ``[-limit:]``).
    """
    from sqlalchemy import func, select

    from app.db.models import Activity
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        stmt = (
            select(func.count())
            .select_from(Activity)
            .where(*_map_activity_filters(user_id, sport))
        )
        return int(db.execute(stmt).scalar() or 0)
    finally:
        db.close()


def user_map_activities_validator(user_id: str, sport: str | None = None) -> tuple[int, str]:
    """``(count, digest)`` of the user's map-displayable activities, in ONE
    indexed query — the HTTP validator (ETag) for ``GET /me/activities``.

    ``count`` is the same COUNT as ``count_user_map_activities`` (same WHERE).
    ``digest`` is the md5 of every matching row's ``id:xmin``, ordered by id.

    Why ``xmin`` and not a list of columns: Postgres gives every row VERSION a
    new ``xmin`` (the inserting transaction id) — ANY ``UPDATE`` of the row
    (sport edit, rename, date change, geometry upgrade/promotion, display cache
    backfill, contribute/consent flags…) writes a new tuple with a
    new ``xmin``, an INSERT adds an id, a DELETE removes one. So the digest
    changes whenever anything the feed serialises can have changed, without
    enumerating columns (a column added to the feed later is covered for free)
    and WITHOUT detoasting a single geometry: only the heap tuple header and the
    ``id`` are read (``geometry_geojson <> ''`` in the shared WHERE short-circuits
    on the toast header length). Freezing keeps the raw ``xmin`` (PG ≥ 9.4), so
    VACUUM does not churn it; a dump/restore does — a harmless cache miss.

    The digest covers ALL matching rows, not only the ``limit`` most recent:
    a superset of what the response depends on (a change outside the window is
    a spurious, harmless revalidation miss).
    """
    from sqlalchemy import func, literal, literal_column, select
    from sqlalchemy.dialects.postgresql import aggregate_order_by

    from app.db.models import Activity
    from app.db.session import SessionLocal

    row_key = func.concat(Activity.id, ":", literal_column("activities.xmin::text"))
    digest = func.md5(
        func.coalesce(func.string_agg(row_key, aggregate_order_by(literal(","), Activity.id)), "")
    )
    stmt = (
        select(func.count(), digest)
        .select_from(Activity)
        .where(*_map_activity_filters(user_id, sport))
    )
    db = SessionLocal()
    try:
        count, md5 = db.execute(stmt).one()
        return int(count or 0), str(md5)
    finally:
        db.close()


def iter_user_map_activities(
    user_id: str, sport: str | None = None, limit: int | None = None
) -> Iterator[dict]:
    """Stream the user's ``limit`` most recent map-displayable activities,
    oldest-first — filtering/ordering/limiting pushed into SQL.

    Replaces ``get_user_activities`` (which SELECTed every column of the
    user's WHOLE corpus — geometry_geojson included — then filtered and
    sliced ``[-limit:]`` in Python: an OOM on a 512Mi instance for a 14k-
    activity user, and the biggest recurring db-f1-micro read).

    Semantics preserved from the ``[-limit:]`` era:
    - "most recent" = ``COALESCE(activity_date, created_at)`` — the same
      effective date the response serialises as ``activity_date``;
    - rows come back ASCENDING by that date (the old code returned the tail
      of an insertion-ordered list, so the newest activity streamed LAST —
      the frontend and tests rely on ``features[-1]`` being the newest);
    - only the columns the /me/activities Feature serialises are projected:
      the compact ``display_geojson`` cache (migration 0067), and the
      full-resolution ``geometry_geojson`` ONLY for rows whose cache is NULL
      (``None`` otherwise) — only for the ``limit`` selected rows;
    - ``yield_per`` streams rows through a server-side cursor so at most a
      small window of geometries is in memory at once. The session stays
      open until the generator is exhausted or closed (StreamingResponse
      closes it on client disconnect via the ``finally``).
    """
    from sqlalchemy import case, func, select

    from app.db.models import Activity
    from app.db.session import SessionLocal

    effective_date = func.coalesce(Activity.activity_date, Activity.created_at)
    inner = (
        select(
            Activity.id,
            Activity.provider,
            Activity.provider_activity_id,
            Activity.sport,
            Activity.sport_is_default,
            Activity.name,
            # The full-resolution TEXT is only shipped off the db-f1-micro when
            # the compact cache is missing (pre-backfill rows) — the handler
            # then derives the display geometry on the fly.
            case(
                (Activity.display_geojson.is_(None), Activity.geometry_geojson),
                else_=None,
            ).label("geometry_geojson"),
            Activity.display_geojson,
            Activity.distance_m,
            Activity.elevation_gain_m,
            Activity.moving_time,
            Activity.activity_date,
            Activity.created_at,
        )
        .where(*_map_activity_filters(user_id, sport))
        .order_by(effective_date.desc().nullslast(), Activity.id.desc())
    )
    if limit is not None:
        inner = inner.limit(limit)
    sub = inner.subquery()
    outer_date = func.coalesce(sub.c.activity_date, sub.c.created_at)
    stmt = (
        select(sub)
        .order_by(outer_date.asc().nullsfirst(), sub.c.id.asc())
        .execution_options(yield_per=200)
    )

    db = SessionLocal()
    try:
        for r in db.execute(stmt):
            yield {
                "id": r.id,
                "provider": r.provider,
                "provider_activity_id": r.provider_activity_id,
                "sport": r.sport,
                "sport_is_default": bool(r.sport_is_default),
                "name": r.name,
                "geometry_geojson": r.geometry_geojson,
                "display_geojson": r.display_geojson,
                "distance_m": r.distance_m,
                "elevation_gain_m": r.elevation_gain_m,
                "moving_time": r.moving_time,
                "activity_date": (r.activity_date or r.created_at).isoformat() if (r.activity_date or r.created_at) else None,
                # activity_date above falls back to created_at for display/sort;
                # ``undated`` tells the truth (undated = never public, provenance SSOT).
                "undated": r.activity_date is None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
    finally:
        db.close()


def get_user_activities_meta(user_id: str) -> list[dict]:
    """Return activity metadata only — NO geometry_geojson.

    Companion to ``iter_user_map_activities`` for callers that don't need the
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
                # activity_date above falls back to created_at for display/sort;
                # ``undated`` tells the truth (undated = never public, provenance SSOT).
                "undated": r.activity_date is None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
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
            "sport_is_default": bool(a.sport_is_default),
            "name": a.name,
            "geometry_geojson": a.geometry_geojson,
            "distance_m": a.distance_m,
            "elevation_gain_m": a.elevation_gain_m,
            "file_hash": a.file_hash,
            "contribute_heatmap": a.contribute_heatmap,
            "moving_time": a.moving_time,
            "activity_date": (a.activity_date or a.created_at).isoformat() if (a.activity_date or a.created_at) else None,
            # activity_date above falls back to created_at for display/sort;
            # ``undated`` tells the truth (undated = never public, provenance SSOT).
            "undated": a.activity_date is None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
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
