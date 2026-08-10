"""Build PMTiles heatmap from heat_edges (DISPLAY ONLY).

Generates ONE file for MapLibre rendering:
  - heatmap-display.pmtiles  (~10MB with min_uc=2, OSM-matched only;
    grid-fallback "desire lines" are KEPT by default since 2026-07-20 —
    env HEATMAP_KEEP_GRID_FALLBACK / HEATMAP_GRID_FALLBACK_MIN_UC, SSOT
    heat_aggregation.resolve_grid_fallback_display)
    props: user_count, sport, heat_score

NOTE: Routing data uses a separate CTGB tile system (not PMTiles).
PMTiles is for display only — tippecanoe simplifies geometry and drops
"densest" features at low zooms, which is wrong for routing accuracy.

Run:
    python -m app.jobs.build_pmtiles
    python -m app.jobs.build_pmtiles --output-dir /tmp --min-uc 2

Time: ~1 min (with min_uc=2 filter)
Prerequisites: tippecanoe installed (brew install tippecanoe)
"""
import argparse
import contextlib
import gzip
import json
import logging
import os
import shutil
import subprocess
import time

from sqlalchemy import text as sa_text

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def export_geojson(db, path: str, props: list[str], min_uc: int,
                   max_grid_fallback_m: float = 60.0,
                   grid_fallback_min_uc: int | None = None,
                   drop_grid_fallback: bool | None = None) -> int:
    """Stream heat_edges as newline-delimited GeoJSON. Returns feature count.

    Grid-fallback edges (``osm_way_id IS NULL``) are the "desire lines" —
    off-OSM traces like MTB singletracks / DFCI paths that don't exist in
    OSM. Since 2026-07-20 (Paul: "j'ai pas envie de perdre les lignes de
    désir") they are KEPT by default; the policy is env-driven and SSOT'd
    in ``heat_aggregation.resolve_grid_fallback_display`` so this export
    and the live MVT endpoint cannot drift. ``drop_grid_fallback`` /
    ``grid_fallback_min_uc`` default to ``None`` = resolve from env
    (``HEATMAP_KEEP_GRID_FALLBACK`` default true;
    ``HEATMAP_GRID_FALLBACK_MIN_UC`` default follows ``min_uc``, i.e. the
    K-anonymity floor — 1 in the K=1 beta so solo singletracks show).
    Explicit values override (ops / tests).

    Two residual filters on grid-fallback edges when kept:

    1. **Length cap** — drop anything longer than ``max_grid_fallback_m``.
       These are GPS-jump artifacts the densifier failed to fill in
       (tunnels, dropouts) and render as straight-line spaghetti
       crossing fields/buildings. Accepted tradeoff: GPS-noise fragments
       ≤60 m may render (the Strava-like imperfection Paul accepts).

    2. **Community confirmation** — keep only when ``user_count >=
       grid_fallback_min_uc`` (the resolved floor above).

    OSM-matched edges (``osm_way_id IS NOT NULL``) are kept regardless
    of length and regardless of user_count >= ``min_uc``.
    """
    from app.services.heat_aggregation import resolve_grid_fallback_display

    env_drop, env_grid_min_uc = resolve_grid_fallback_display(min_uc)
    if drop_grid_fallback is None:
        drop_grid_fallback = env_drop
    if grid_fallback_min_uc is None:
        grid_fallback_min_uc = env_grid_min_uc
    prop_json = ", ".join(f"'{p}', {p}" for p in props)
    if 'heat_score' in props:
        # heat_score now factors in highway_type so primary roads burn
        # brighter than residential lanes at equal user_count — Komoot's
        # bright pink/orange on D-series. Boost: +0.15 for primary /
        # secondary / trunk, +0.05 for tertiary / unclassified, none for
        # residential / service / paths.
        prop_json = prop_json.replace(
            "'heat_score', heat_score",
            "'heat_score', ROUND(LEAST(1.0, "
            "LN(1 + user_count) / (8.0 * LN(2)) "
            "+ CASE WHEN highway_type IN ('primary', 'secondary', 'trunk') THEN 0.15 "
            "WHEN highway_type IN ('tertiary', 'unclassified') THEN 0.05 "
            "ELSE 0 END"
            ")::numeric, 3)"
        )
    # Aggregate heat_edges by OSM way (one feature per OSM way). Without
    # this, an OSM way that 12 GPS traces snapped to produces 12 overlapping
    # copies of the way's 2-point segment — visible as micro-zigzag at z16+
    # from 5-dp snap noise on each segment, never forming a single continuous
    # road line.
    #
    # READ from the pre-materialised ``heat_edges_agg`` table (SSOT builder
    # ``build_agg_read_sql``) instead of re-running the ~5 M-row GROUP BY over
    # ``heat_edges`` at build time — that live aggregation OOMed the
    # whole-world build on db-f1-micro. ``build_agg_read_sql`` serves the
    # EXACT same ``combined`` shape (agg holds the OSM-matched half; grid
    # fallback is read live only when kept). With grid fallback KEPT (the
    # prod default since 2026-07-20 — desire lines), the live half is a
    # single index-driven scan of the NULL-osm_way_id heat_edges only; with
    # ``drop_grid_fallback=True`` it is a pure indexed SELECT of the agg
    # table — ZERO heat_edges scan.
    # The agg table is maintained by the SAME aggregation
    # definition (``build_heat_aggregation_sql``) via the incremental hook +
    # ``rebuild_heat_agg`` backfill, so the two display paths can't drift.
    # Whole-world here (no bbox predicate).
    from app.services.heat_aggregation import build_agg_read_sql

    aggregation_cte = build_agg_read_sql(
        min_uc=min_uc,
        bbox_predicate=None,
        grid_fallback_min_uc=grid_fallback_min_uc,
        max_grid_fallback_m=max_grid_fallback_m,
        drop_grid_fallback=drop_grid_fallback,
    )
    query = f"""
        {aggregation_cte}
        SELECT json_build_object(
            'type', 'Feature',
            'geometry', ST_AsGeoJSON(geometry)::json,
            'properties', json_build_object({prop_json})
        )::text
        FROM combined
    """

    written = 0
    with open(path, "w") as f:
        for row in db.execute(sa_text(query)):
            f.write(row[0])
            f.write("\n")
            written += 1
            if written % 500000 == 0:
                log.info("  Exported %d", written)
    return written


def run_tippecanoe(geojson_path: str, output: str, min_zoom: int, max_zoom: int,
                   points_path: str | None = None) -> None:
    # Tile-size budget: 500KB/tile is the default tippecanoe ceiling and
    # a sensible target for fast panning. With --no-tile-size-limit the
    # builder used to emit multi-MB low-zoom tiles which made the first
    # render of a panned area visibly slow. We keep --no-feature-limit
    # so detail at z14 isn't truncated, and let --drop-densest-as-needed
    # / --coalesce-densest-as-needed shrink low zooms.
    #
    # When ``points_path`` is given we build a MULTI-LAYER tileset: the existing
    # ``trails`` LINE layer PLUS a ``heat_points`` POINT layer (density weights)
    # that the frontend renders as a maplibre ``heatmap`` (raster-style density
    # field — the Strava look that fixes the diffuse vector-line "pâté"). Each
    # input file gets its own named layer via ``-L name:file`` (which cannot be
    # combined with the single-layer ``-l``). --drop-densest-as-needed thins the
    # dense point layer at low zoom so the raster never blobs.
    cmd = [
        "tippecanoe",
        "-o", output,
        f"-Z{min_zoom}", f"-z{max_zoom}",
    ]
    if points_path is not None:
        cmd += ["-L", f"trails:{geojson_path}", "-L", f"heat_points:{points_path}"]
    else:
        cmd += ["-l", "trails", geojson_path]
    cmd += [
        "--no-feature-limit",
        "--maximum-tile-bytes=500000",
        "--drop-densest-as-needed",
        "--coalesce-densest-as-needed",
        "--accumulate-attribute=user_count:sum",
        # Stronger simplification at low zooms, raw geometry at z14.
        "--simplification=10",
        "--simplify-only-low-zooms",
        "--force",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("tippecanoe failed: %s", result.stderr[-500:])
        raise RuntimeError("tippecanoe failed")


def main(output_dir: str, min_zoom: int, max_zoom: int, min_uc: int,
         drop_grid_fallback: bool | None = None,
         grid_fallback_min_uc: int | None = None) -> None:
    if not shutil.which("tippecanoe"):
        log.error("tippecanoe not found. Install: brew install tippecanoe")
        return

    from app.db.session import SessionLocal
    from app.services.raw_trace_display import export_raw_geojson, raw_display_enabled
    db = SessionLocal()
    try:
        raw_mode = raw_display_enabled()

        t0 = time.time()
        geojson_path = "/tmp/heat_edges_display.geojsonl"
        # RAW mode also emits a companion heat_points GeoJSONL → a second
        # tippecanoe layer rendered as a maplibre `heatmap` density field
        # (raster-style Strava look, fixes the vector-line "pâté"). None in
        # matched mode (single `trails` layer, unchanged).
        points_path: str | None = None
        raw_network_m: float | None = None
        if raw_mode:
            # HEATMAP_DISPLAY_SOURCE=raw — precise masked raw traces instead
            # of the matched heat_edges_agg. Emits BOTH the `trails` LineString
            # layer (user_count/heat_score/sport — crisp lines + arrows) AND a
            # `heat_points` Point layer (w/sport — the density raster). Privacy =
            # endpoint masking (trace_privacy), NOT K-anonymity — see
            # docs/raw-trace-heatmap-prototype.md.
            points_path = "/tmp/heat_points_display.geojsonl"
            log.info("Building heatmap-display.pmtiles from RAW masked traces "
                     "(HEATMAP_DISPLAY_SOURCE=raw) — trails + heat_points layers...")
            # stats_out captures the occupied-lattice network estimate for the
            # home "km de chemins" banner (heat_edges_agg is dropped under raw).
            raw_stats: dict = {}
            written = export_raw_geojson(db, geojson_path, points_path=points_path,
                                         stats_out=raw_stats)
            raw_network_m = raw_stats.get("network_m")
            pts_mb = (os.path.getsize(points_path) / 1024 / 1024
                      if os.path.exists(points_path) else 0.0)
            log.info("Raw export: %.0fs (%d line features, %.1f MB trails + "
                     "%.1f MB heat_points)", time.time() - t0, written,
                     os.path.getsize(geojson_path) / 1024 / 1024, pts_mb)
        else:
            # Count the pre-materialised aggregate (one row per (osm_way_id,
            # sport)), NOT raw ``heat_edges``. This is a log line only — but the
            # old ``SELECT COUNT(*) FROM heat_edges`` was a full scan of the ~5 M
            # row table that severed the db-f1-micro connection ("server closed
            # the connection unexpectedly") BEFORE the light agg build could run,
            # defeating the whole point of #441. ``heat_edges_agg`` is ~45 k rows
            # with an indexed ``user_count``; the meaningful "size of the build"
            # number is aggregated ways anyway.
            count = db.execute(sa_text(
                f"SELECT COUNT(*) FROM heat_edges_agg WHERE user_count >= {min_uc}"
            )).scalar()
            log.info("Building heatmap-display.pmtiles for %d aggregated ways (min_uc=%d)...", count, min_uc)

            # ``None`` values defer to export_geojson, which resolves the
            # desire-lines policy from env via the SSOT helper
            # (heat_aggregation.resolve_grid_fallback_display): keep grid
            # fallback by default, confirmation floor follows min_uc/K.
            # Explicit CLI flags (--keep-grid-fallback / --drop-grid-fallback /
            # --grid-fallback-min-uc) override for ops.
            written = export_geojson(db, geojson_path,
                           ['user_count', 'pass_count', 'sport',
                            'heat_score', 'highway_type'],
                           min_uc,
                           grid_fallback_min_uc=grid_fallback_min_uc,
                           drop_grid_fallback=drop_grid_fallback)
            log.info("Export: %.0fs (%.1f MB)", time.time() - t0, os.path.getsize(geojson_path) / 1024 / 1024)

        output = os.path.join(output_dir, "heatmap-display.pmtiles")
        if written == 0:
            # A 0-feature corpus is a VALID state under the raw pivot: until
            # users upload their own manual_upload archives the community layer
            # is legitimately empty. tippecanoe ABORTS on empty input ("Did not
            # read any valid geometries" → non-zero), so DON'T call it — skip the
            # binary + its upload and leave the published tileset UNCHANGED. Ops
            # sees this warning; stats/metrics below still run (honest zeros).
            log.warning("No community traces (0 features) — skipping tippecanoe + "
                        "PMTiles upload; published tileset left UNCHANGED. Valid "
                        "under the raw pivot until users upload archives.")
            os.unlink(geojson_path)
            if points_path and os.path.exists(points_path):
                os.unlink(points_path)
        else:
            t1 = time.time()
            run_tippecanoe(geojson_path, output, min_zoom, max_zoom,
                           points_path=points_path)
            log.info("tippecanoe: %.0fs (%.1f MB)", time.time() - t1, os.path.getsize(output) / 1024 / 1024)
            # PUBLISH THE PRIMARY ARTIFACT FIRST. The static heatmap-display.pmtiles
            # is what /map + the hero load; it MUST reach GCS before the best-effort
            # secondary artifacts (geojsonl, raster pyramid). The raster pyramid
            # re-materialises the whole corpus in RAM for ~8-10 min and could be
            # OOM-SIGKILL'd — which no try/except can catch — so if it ran first a
            # kill would strand /map on the PREVIOUS build with no error surfaced.
            _upload_to_export_bucket(output)
            # RAW mode only: also publish the un-tiled geojsonl (gzipped) so the
            # community-EXPORT endpoints (export._query_aggregated_heat_edges)
            # can STREAM + bbox-clip this pre-built artifact instead of
            # re-running the full raw aggregation per request. Best-effort.
            if raw_mode:
                _upload_geojsonl_to_export_bucket(geojson_path)
                # Export speedup: also publish one gzipped geojsonl per sport
                # (``heatmap-display-<sport>.geojsonl.gz``) so a sport-filtered
                # export streams a SMALL per-sport file instead of the whole
                # national all-sports one. Best-effort, never raises — the
                # combined artifact above already backs every export.
                _upload_per_sport_geojsonl_to_export_bucket(geojson_path)
                # Raster XYZ pyramid: render {z}/{x}/{y}.png + a TileJSON to the
                # public bucket so gpx.studio / VisuGPX can add the heatmap as a
                # custom overlay ("calque") via one URL. Reads the still-on-disk
                # geojsonl. Env-gated (HEATMAP_RASTER_PYRAMID); never raises.
                _build_and_upload_raster_pyramid(geojson_path)
            os.unlink(geojson_path)
            # The heat_points geojsonl is a build-only intermediate (baked into
            # the multi-layer PMTiles above) — not published for export, so just
            # delete it.
            if points_path and os.path.exists(points_path):
                os.unlink(points_path)
            log.info("=== Done in %.0fs ===", time.time() - t0)
            log.info("Output: %s", output)

        # (The PMTiles publish to GCS now happens RIGHT AFTER tippecanoe, before
        # the geojsonl + raster-pyramid secondaries — see the reordering above —
        # so a slow/OOM-killed pyramid can never strand the primary /map artifact
        # on the previous build. Env-gated on HEATMAP_GCS_BUCKET; never raises.
        # Skipped on an empty corpus — no fresh binary; the previously-published
        # tileset is intentionally left in place.)

        # Homepage banner stats (contributeurs / traces / km de chemins).
        # Computed here — during the rebuild that already holds a DB
        # session — and published as a tiny static ``stats.json`` next to
        # the PMTiles (local ``output_dir`` + the public GCS bucket). The
        # homepage fetches that static file, NEVER the DB at request time,
        # so a cold db-f1-micro (min-instances=0) can't blank the hero.
        # Runs AFTER the PMTiles deliverable + its upload (same ordering
        # discipline as the quality audit) but BEFORE the heavy raw-edge
        # quality scan, so a scan that severs the connection can't stop
        # stats from being published. Queries are cheap (indexed
        # activities aggregates + the ~45-87k-row heat_edges_agg).
        publish_stats_json(db, output_dir, min_uc, network_m_override=raw_network_m)

        # Evolution time-series — append ONE snapshot of the community-heatmap
        # size at the END of the rebuild (the admin dashboard's "is it growing?"
        # chart). Reuses the stats just computed (SSOT) plus two cheap size
        # probes; fully fail-soft so it can never break the deliverable above.
        capture_heatmap_metrics_snapshot(db, source="rebuild", min_uc=min_uc,
                                         network_m_override=raw_network_m)

        # Post-build heat-quality audit — log structured metrics per
        # monitored region so Cloud Logging / Sentry can alert on
        # spaghetti drift over time. The thresholds are advisory only;
        # we never fail the build on quality (a quality issue should
        # surface in the heatmap, not in the rebuild pipeline).
        #
        # ORDERING INVARIANT (db-f1-micro): this is the ONLY raw-``heat_edges``
        # access left on the default build path (grid_fallback / dangling-
        # endpoint degree are inherently raw-edge concepts, uncomputable from
        # the by-way ``heat_edges_agg``). It MUST run strictly AFTER the
        # deliverables are done — the local PMTiles binary + its GCS upload
        # above — so that a heavy raw scan severing the connection can never
        # abort the build. The queries are bbox-scoped (GiST-indexed, not a
        # full-table scan), per-region try/except'd, statement-timeout-boxed,
        # and the whole function never raises. Do NOT move this call before
        # ``_upload_to_export_bucket``.
        #
        # RAW-trace cutover: grid_fallback / dangling-endpoint metrics are
        # matched-pipeline (``heat_edges``) concepts — meaningless under raw and
        # a pointless full-scan on f1-micro (``heat_edges`` is empty/dropped
        # post-pivot). Skip the raw-``heat_edges`` audit entirely in raw mode.
        if not raw_mode:
            _emit_heat_quality_metrics(db)
    finally:
        db.close()


def _public_base_url(bucket_name: str) -> str:
    """Public base URL under which the heatmap artefacts are served.

    SSOT for every public heatmap URL this job emits (the raster
    ``tiles.json`` tile template, the mutable pmtiles url, the immutable
    pinned/``latest_url`` snapshot, and the stats.json log line).

    Defaults to the direct GCS object URL
    (``https://storage.googleapis.com/<bucket>``) so nothing changes unless
    the env is set. In prod ``HEATMAP_PUBLIC_BASE_URL`` points at the
    first-party CDN domain (``https://tiles.chemins-communs.fr``) that fronts
    the same bucket — this makes the freshness pointer's ``latest_url`` (the
    URL the client resolves the pmtiles THROUGH) become the tiles.* URL, and
    fixes ad-blocker/Firefox blocking of ``storage.googleapis.com``.

    No trailing slash — callers append ``/<path>``.
    """
    return os.environ.get(
        "HEATMAP_PUBLIC_BASE_URL", f"https://storage.googleapis.com/{bucket_name}"
    ).rstrip("/")


def _pmtiles_version_id(pmtiles_bytes: bytes | None = None) -> str:
    """Per-build unique id for the pinned-snapshot URL.

    Format: ``v{epoch_hour}-{sha8}`` — e.g. ``v494544-a1b2c3d4``.
    Two components, both required so the immutable contract holds:

    - ``epoch_hour`` (hours since 1970-01-01 UTC) — monotonic, sorts
      lexicographically; a 6-digit integer for ~85 more years.
    - ``sha8`` (first 8 hex chars of sha256(pmtiles_bytes)) —
      content-addressed so two builds in the SAME hour with the SAME
      bytes collapse onto one snapshot (good — no orphan blob), and
      two builds with DIFFERENT bytes never collide (good — preserves
      the ``Cache-Control: public, max-age=31536000, immutable`` promise).

    Previously ``v{epoch_day}`` — caused intra-day rebuilds to silently
    overwrite a blob marked ``immutable``, breaking downstream CDN /
    consumer caches that had already pinned the day's bytes. See PR #400
    review S1 #2.

    Passing ``pmtiles_bytes=None`` (e.g. from tests that just want a
    representative id shape) falls back to a deterministic placeholder
    hash so the function stays callable without a real artefact.
    """
    import hashlib
    from datetime import UTC, datetime

    epoch_hour = int(datetime.now(UTC).timestamp() // 3600)
    content_hash = (
        "00000000"
        if pmtiles_bytes is None
        else hashlib.sha256(pmtiles_bytes).hexdigest()[:8]
    )
    return f"v{epoch_hour}-{content_hash}"


def _build_and_upload_raster_pyramid(geojson_path: str) -> None:
    """Best-effort: render a raster XYZ tile pyramid from the raw-display
    geojsonl and publish ``raster/{z}/{x}/{y}.png`` + ``raster/tiles.json`` to
    the public heatmap bucket, so gpx.studio / VisuGPX (and any Leaflet/OL
    client) can add the community heatmap as a custom overlay ("calque") via
    one URL.

    Env-gated on ``HEATMAP_RASTER_PYRAMID=true`` (default off → nothing changes)
    + ``HEATMAP_GCS_BUCKET``. Never raises — a raster hiccup must not break the
    PMTiles build. Zoom range via ``HEATMAP_RASTER_MIN/MAX_ZOOM`` (default 6-14).
    Written to the ``raster/`` prefix (overwrite in place), then STALE tiles are
    PURGED: after the build, every ``.png`` under ``raster/`` that was NOT written
    this run is deleted, so a tile left behind by a now-deleted / below-K trace
    can't linger publicly (the GDPR + K-flip correctness the mutable-overwrite
    approach previously lacked). Purge is gated on a real build (features>0) so a
    transient empty corpus never wipes the whole calque.
    """
    if os.environ.get("HEATMAP_RASTER_PYRAMID", "false").strip().lower() != "true":
        return
    bucket_name = os.environ.get("HEATMAP_GCS_BUCKET", "").strip()
    if not bucket_name:
        log.info("HEATMAP_GCS_BUCKET unset → skipping raster pyramid (local dev OK)")
        return
    try:
        from google.cloud import storage  # type: ignore[attr-defined]
    except ImportError:
        log.warning("google-cloud-storage not installed; cannot publish raster pyramid")
        return
    try:
        from app.services.heatmap_raster_pyramid import (
            DEFAULT_MAX_ZOOM,
            DEFAULT_MIN_ZOOM,
            build_raster_pyramid,
            build_tilejson,
        )

        min_zoom = int(os.environ.get("HEATMAP_RASTER_MIN_ZOOM", str(DEFAULT_MIN_ZOOM)))
        max_zoom = int(os.environ.get("HEATMAP_RASTER_MAX_ZOOM", str(DEFAULT_MAX_ZOOM)))
        client = storage.Client()
        bucket = client.bucket(bucket_name)

        written_keys: set[str] = set()

        def _upload_png(z: int, x: int, y: int, png: bytes) -> None:
            key = f"raster/{z}/{x}/{y}.png"
            blob = bucket.blob(key)
            # Stable-named MUTABLE artifact (same URL, new bytes each rebuild) —
            # keep the cache short so a rebuild reaches consumers promptly.
            blob.cache_control = "public, max-age=3600"
            blob.upload_from_string(png, content_type="image/png")
            written_keys.add(key)

        t = time.time()
        stats = build_raster_pyramid(
            geojson_path, upload_png=_upload_png, min_zoom=min_zoom, max_zoom=max_zoom,
        )
        purged = 0
        if stats.get("bounds"):
            tiles_url = (
                f"{_public_base_url(bucket_name)}/raster/{{z}}/{{x}}/{{y}}.png"
            )
            tj = build_tilejson(
                tiles_url=tiles_url, min_zoom=min_zoom, max_zoom=max_zoom,
                bounds=tuple(stats["bounds"]),
                attribution=(
                    '© <a href="https://chemins-communs.fr">CHEMINS COMMUNS</a> '
                    "contributors — ODbL 1.0"
                ),
            )
            tj_blob = bucket.blob("raster/tiles.json")
            # Stable-named MUTABLE artifact — short cache so rebuilds are seen.
            tj_blob.cache_control = "no-store"
            tj_blob.upload_from_string(json.dumps(tj), content_type="application/json")

            # PURGE stale tiles (GDPR + K-flip): a tile occupied ONLY by a now-
            # deleted / below-K trace is NOT re-rendered this build, so its old
            # PNG would linger PUBLICLY on the calque forever (a solo desire line's
            # tiles are all unique → the whole deleted trace stays visible). List
            # the raster/ prefix and delete every .png NOT written this build
            # (tiles.json is kept). Gated on a real build (bounds set ⇒ features>0)
            # so a transient empty corpus can NEVER wipe the whole calque.
            for blob in bucket.list_blobs(prefix="raster/"):
                if blob.name.endswith(".png") and blob.name not in written_keys:
                    try:
                        blob.delete()
                        purged += 1
                    except Exception:  # pragma: no cover - best-effort per-tile
                        pass
        log.info(
            "raster pyramid: %d tiles (z%d-%d, %d features), %d stale purged, in %.0fs → gs://%s/raster/",
            stats.get("tiles", 0), min_zoom, max_zoom, stats.get("features", 0),
            purged, time.time() - t, bucket_name,
        )
    except Exception:
        log.warning("raster pyramid build/upload failed (best-effort)", exc_info=True)


def _upload_to_export_bucket(pmtiles_path: str) -> None:
    """Best-effort upload of the freshly-built PMTiles to the public
    heatmap-export GCS bucket.

    Phase 1 (PRD #391): published the mutable ``heatmap-display.pmtiles``
    object only. Phase 3 (this commit) ADDS:

    1. ``heatmap-display-{v{N}}.pmtiles`` — immutable per-day snapshot
       so consumers can pin to an exact version (Garmin-Connect-style
       "click here to reproduce the exact map I downloaded last week").
    2. ``heatmap-display.json`` — pointer file listing the latest
       version, size, captured_at, k_anonymity, edge_count. Clients
       hit this JSON first, then ``heatmap-display-v{N}.pmtiles`` to
       follow the pointer (Garmin-style 2-hop), OR hit the mutable
       ``heatmap-display.pmtiles`` for the "always latest" semantic.

    The mutable file stays the primary path (Phase 1 backwards compat
    + the in-app heatmap reads it). The versioned snapshots are
    additive — no consumer is required to switch.

    Env-gated on ``HEATMAP_GCS_BUCKET``. Never raises — a transient
    GCS hiccup must not break the build: the local file still exists
    and the next ingest burst will trigger another rebuild via the
    existing internal_artefacts handler.
    """
    bucket_name = os.environ.get("HEATMAP_GCS_BUCKET", "").strip()
    if not bucket_name:
        log.info("HEATMAP_GCS_BUCKET unset → skipping public-export upload (local dev OK)")
        return

    try:
        from google.cloud import storage  # type: ignore[attr-defined]
    except ImportError:
        log.warning("google-cloud-storage not installed; cannot upload PMTiles to %s", bucket_name)
        return

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        size = os.path.getsize(pmtiles_path)
        # Read the bytes once for content-hashing the version id (S1 #2).
        # PMTiles binaries are ~10 MB; the read cost is dwarfed by the
        # GCS upload immediately after. Reading lets us pass the bytes
        # to _pmtiles_version_id so the pinned-snapshot URL is unique
        # per-build (epoch_hour + sha8) and the immutable cache
        # contract holds across same-day rebuilds.
        with open(pmtiles_path, "rb") as fh:
            pmtiles_bytes = fh.read()
        version_id = _pmtiles_version_id(pmtiles_bytes)

        # Step 1: Atomic upload of the MUTABLE name (Phase 1 — keeps
        # all existing consumers working). Upload to a `.tmp` name,
        # then `rewrite()` into the canonical name. Browsers mid-poll
        # either see the OLD object (rewrite hasn't completed) or the
        # NEW object — never a half-written body.
        tmp_blob = bucket.blob("heatmap-display.pmtiles.tmp")
        canonical_blob = bucket.blob("heatmap-display.pmtiles")
        # 24 h browser/CDN cache. CDN purge happens on every upload
        # because GCS object versions update the Last-Modified / ETag.
        tmp_blob.cache_control = "public, max-age=86400"
        tmp_blob.upload_from_filename(pmtiles_path, content_type="application/vnd.pmtiles")
        canonical_blob.rewrite(tmp_blob)
        try:
            tmp_blob.delete()
        except Exception:  # noqa: BLE001
            log.debug("could not delete tmp blob (already gone?)")
        mutable_url = f"{_public_base_url(bucket_name)}/heatmap-display.pmtiles"
        log.info("Uploaded PMTiles atomically (%d bytes) → %s", size, mutable_url)

        # Step 2: IMMUTABLE per-day snapshot (Phase 3). Upload to
        # `heatmap-display-{v{N}}.pmtiles` with a 1 y cache header
        # (immutable filename → safe to cache forever). Skipping the
        # atomic-rename trick because a torn read on a versioned name
        # is harmless — the file is identified by its NAME, so a
        # consumer that read a half-written `v20606` would still
        # re-fetch the right complete bytes on retry (same name, just
        # done uploading). And we don't want to double the upload cost
        # for the no-readers case (most snapshots are never pinned to).
        pinned_name = f"heatmap-display-{version_id}.pmtiles"
        pinned_blob = bucket.blob(pinned_name)
        pinned_blob.cache_control = "public, max-age=31536000, immutable"
        pinned_blob.upload_from_filename(pmtiles_path, content_type="application/vnd.pmtiles")
        pinned_url = f"{_public_base_url(bucket_name)}/{pinned_name}"
        log.info("Published immutable snapshot %s (%d bytes) → %s",
                 version_id, size, pinned_url)

        # Step 3: pointer JSON. Written LAST so a partial upload
        # (steps 1–2 failed but step 3 succeeded) is impossible — the
        # pointer always references a snapshot we already pushed.
        # Cache: 5 min so consumers see new versions within a short
        # window without thundering-herding the JSON endpoint.
        pointer = {
            "latest": version_id,
            "latest_url": pinned_url,
            "mutable_url": mutable_url,
            "size_bytes": size,
            "captured_at": _utc_now_iso(),
            "license": "ODbL-1.0",
            "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
            "attribution": "© CHEMINS COMMUNS contributors — ODbL 1.0",
        }
        # Best-effort edge count + k_anonymity from env / DB — optional
        # fields so this branch doesn't fail the whole upload on a
        # transient DB hiccup. Count the light ``heat_edges_agg`` table (one
        # row per (osm_way_id, sport), ~45 k rows), NOT raw ``heat_edges`` —
        # a full 5 M-row COUNT would sever the db-f1-micro connection, and
        # this runs AFTER the PMTiles upload (steps 1-2 above) where the
        # aggregated-way count is the meaningful number anyway.
        try:
            from sqlalchemy import text as _sa_text

            from app.db.session import SessionLocal
            _db = SessionLocal()
            try:
                edges = _db.execute(_sa_text(
                    "SELECT COUNT(*) FROM heat_edges_agg WHERE user_count >= 1"
                )).scalar()
                pointer["edge_count"] = int(edges) if edges is not None else 0
            finally:
                _db.close()
        except Exception:  # noqa: BLE001 — pointer best-effort
            pass
        pointer["k_anonymity"] = int(os.environ.get("HEATMAP_K_ANONYMITY", "2"))

        # Atomic write: .tmp → rewrite() into the canonical pointer name
        # so a mid-flight discovery request never reads a torn JSON
        # body (same posture as the mutable PMTiles step above). The
        # JSON is small (~300 B) so the tear window was narrow, but
        # the inconsistency with the rest of this pipeline was
        # intentional sloppiness — PR #400 review S2 #1.
        import json as _json
        pointer_tmp = bucket.blob("heatmap-display.json.tmp")
        pointer_canonical = bucket.blob("heatmap-display.json")
        pointer_tmp.cache_control = "no-store"
        pointer_tmp.upload_from_string(
            _json.dumps(pointer, indent=2),
            content_type="application/json",
        )
        pointer_canonical.rewrite(pointer_tmp)
        try:
            pointer_tmp.delete()
        except Exception:  # noqa: BLE001
            log.debug("could not delete pointer tmp blob (already gone?)")
        log.info("Updated pointer JSON (latest=%s)", version_id)

        # Prune OLD immutable snapshots — keep ONLY the latest (Paul 2026-08-07:
        # "on doit l'écraser par la dernière version à chaque fois"). They used
        # to accumulate forever, so a GDPR-deleted trace stayed retrievable in a
        # frozen old snapshot indefinitely (right-to-erasure gap). The pointer
        # above now references the new snapshot, so deleting the rest is safe.
        # Matches heatmap-display-<version>.pmtiles ONLY (the mutable
        # "heatmap-display.pmtiles" has no trailing '-' and the per-sport
        # geojsonl end in .geojsonl.gz — neither is touched). Best-effort.
        pruned = 0
        for old in bucket.list_blobs(prefix="heatmap-display-"):
            if old.name.endswith(".pmtiles") and old.name != pinned_name:
                try:
                    old.delete()
                    pruned += 1
                except Exception:  # noqa: BLE001 — best-effort per-object
                    pass
        if pruned:
            log.info("Pruned %d old pmtiles snapshot(s), kept %s", pruned, pinned_name)
    except Exception as exc:  # noqa: BLE001 — best-effort upload
        log.warning("PMTiles upload to gs://%s failed (build keeps local file): %s",
                    bucket_name, exc)


def _upload_geojsonl_to_export_bucket(geojsonl_path: str) -> None:
    """Best-effort upload of the raw-display geojsonl (gzipped) to the public
    heatmap-export GCS bucket as ``heatmap-display.geojsonl.gz``.

    WHY: the community-EXPORT endpoints (``/export/heatmap.{gpx,geojson,kml}``)
    under raw mode used to re-run the FULL raw aggregation
    (``aggregate_raw_for_export`` — two streaming passes over every bbox-scoped
    activity) PER REQUEST on the weak 512 Mi web instance → ~54 s → Cloud Run
    503. That is the SAME expensive work THIS job already did once to build the
    PMTiles. Publishing the intermediate geojsonl lets the export endpoints
    STREAM + bbox-clip a pre-built artifact instead.

    Stored WITHOUT ``content_encoding=gzip`` (opaque gzipped object,
    ``application/gzip``) so the reader decompresses it itself as a stream —
    setting content_encoding would make GCS transcode-decompress on download
    and defeat the bounded-memory streaming decode on the reader side.

    Same bucket / creds / atomic-rename (``.tmp`` → ``rewrite``) posture as
    ``_upload_to_export_bucket``. Env-gated on ``HEATMAP_GCS_BUCKET`` and NEVER
    raises — the PMTiles binary is the primary deliverable; a failed geojsonl
    upload just means exports fall back to the (correct, slower) live aggregate
    until the next rebuild.
    """
    bucket_name = os.environ.get("HEATMAP_GCS_BUCKET", "").strip()
    if not bucket_name:
        log.info("HEATMAP_GCS_BUCKET unset → skipping geojsonl export upload (local dev OK)")
        return

    try:
        from google.cloud import storage  # type: ignore[attr-defined]
    except ImportError:
        log.warning("google-cloud-storage not installed; cannot upload geojsonl to %s", bucket_name)
        return

    gz_path = geojsonl_path + ".gz"
    try:
        raw_size = os.path.getsize(geojsonl_path)
        with open(geojsonl_path, "rb") as src, \
                gzip.open(gz_path, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        gz_size = os.path.getsize(gz_path)

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        tmp_blob = bucket.blob("heatmap-display.geojsonl.gz.tmp")
        canonical_blob = bucket.blob("heatmap-display.geojsonl.gz")
        # Stable-named MUTABLE export — short cache so a rebuild reaches consumers.
        tmp_blob.cache_control = "public, max-age=3600"
        tmp_blob.upload_from_filename(gz_path, content_type="application/gzip")
        canonical_blob.rewrite(tmp_blob)
        try:
            tmp_blob.delete()
        except Exception:  # noqa: BLE001
            log.debug("could not delete geojsonl tmp blob (already gone?)")
        log.info(
            "Uploaded raw display geojsonl (%.1f MB raw → %.1f MB gz) → "
            "gs://%s/heatmap-display.geojsonl.gz",
            raw_size / 1024 / 1024, gz_size / 1024 / 1024, bucket_name,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort upload
        log.warning("geojsonl upload to gs://%s failed (build keeps PMTiles): %s",
                    bucket_name, exc)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(gz_path)


def _upload_per_sport_geojsonl_to_export_bucket(geojsonl_path: str) -> None:
    """Split the combined raw-display geojsonl into ONE gzipped file per sport
    and best-effort upload each to the public heatmap-export GCS bucket as
    ``heatmap-display-<sport>.geojsonl.gz``.

    WHY: a sport-filtered export (the common case — mtb / gravel / …) otherwise
    has to stream the WHOLE national all-sports artifact just to keep a small
    fraction of its lines. Publishing per-sport files lets the export path
    stream a SMALL file: the sport filter becomes a no-op on it (still correct),
    while the bbox + K-anon filters still apply.

    Memory-safe: the combined geojsonl is streamed ONE LINE at a time, routed to
    the right per-sport gzip writer by its ``properties.sport`` — no feature
    list is ever held in RAM. At most one gzip writer per distinct sport
    (running / road / mtb / gravel / offroad) is open at once.

    Same bucket / creds / atomic-rename (``.tmp`` → ``rewrite``) posture as
    ``_upload_geojsonl_to_export_bucket``. Env-gated on ``HEATMAP_GCS_BUCKET``
    and NEVER raises — a failed per-sport upload just means that sport's export
    falls back to streaming the combined artifact (correct, only slower) until
    the next rebuild.
    """
    import json as _json

    bucket_name = os.environ.get("HEATMAP_GCS_BUCKET", "").strip()
    if not bucket_name:
        log.info("HEATMAP_GCS_BUCKET unset → skipping per-sport geojsonl upload (local dev OK)")
        return

    try:
        from google.cloud import storage  # type: ignore[attr-defined]
    except ImportError:
        log.warning("google-cloud-storage not installed; cannot upload per-sport geojsonl to %s", bucket_name)
        return

    # Split (streaming) into per-sport gz temp files next to the source.
    writers: dict[str, object] = {}
    paths: dict[str, str] = {}
    counts: dict[str, int] = {}
    try:
        with open(geojsonl_path, encoding="utf-8") as src:
            for line in src:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    sport = (_json.loads(stripped).get("properties") or {}).get("sport")
                except (ValueError, AttributeError):
                    continue
                if not sport:
                    continue
                w = writers.get(sport)
                if w is None:
                    gz_path = f"{geojsonl_path}-{sport}.gz"
                    # One writer per sport stays open for the whole streaming
                    # pass (closed explicitly below) — a context manager can't
                    # express "route each line to one of N open writers".
                    w = gzip.open(gz_path, "wt", encoding="utf-8", compresslevel=6)  # noqa: SIM115
                    writers[sport] = w
                    paths[sport] = gz_path
                    counts[sport] = 0
                # Preserve the ORIGINAL line verbatim (no re-serialization) so
                # per-sport features are byte-identical to the combined ones.
                w.write(stripped + "\n")
                counts[sport] += 1
    except Exception as exc:  # noqa: BLE001 — split is best-effort; broadened
        # from OSError so a non-OSError during the split (e.g. a gzip/encode
        # error) can't leak open writers or abort the PMTiles publish, matching
        # _upload_geojsonl_to_export_bucket's never-raises posture.
        log.warning("per-sport geojsonl split failed (build keeps combined): %s", exc)
        for w in writers.values():
            with contextlib.suppress(Exception):
                w.close()
        for p in paths.values():
            with contextlib.suppress(OSError):
                os.unlink(p)
        return

    for w in writers.values():
        with contextlib.suppress(Exception):
            w.close()

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        for sport, gz_path in paths.items():
            try:
                blob_name = f"heatmap-display-{sport}.geojsonl.gz"
                tmp_blob = bucket.blob(blob_name + ".tmp")
                canonical_blob = bucket.blob(blob_name)
                # Stable-named MUTABLE export — short cache so rebuilds propagate.
                tmp_blob.cache_control = "public, max-age=3600"
                tmp_blob.upload_from_filename(gz_path, content_type="application/gzip")
                canonical_blob.rewrite(tmp_blob)
                with contextlib.suppress(Exception):
                    tmp_blob.delete()
                log.info(
                    "Uploaded per-sport geojsonl %s (%d features, %.1f MB gz)",
                    blob_name, counts.get(sport, 0),
                    os.path.getsize(gz_path) / 1024 / 1024,
                )
            except Exception as exc:  # noqa: BLE001 — per-sport best-effort
                log.warning("per-sport geojsonl upload failed for %s: %s", sport, exc)
    except Exception as exc:  # noqa: BLE001 — storage client init failure
        log.warning("per-sport geojsonl upload to gs://%s failed: %s", bucket_name, exc)
    finally:
        for gz_path in paths.values():
            with contextlib.suppress(OSError):
                os.unlink(gz_path)


def _utc_now_iso() -> str:
    """``datetime.now(UTC).isoformat()`` factored for unit-testing."""
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


ODBL_LICENSE = "ODbL-1.0"
ODBL_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
STATS_ATTRIBUTION = "© CHEMINS COMMUNS contributors — ODbL 1.0"


def compute_community_stats(db, min_uc: int, network_m_override: float | None = None) -> dict:
    """Compute the three homepage-banner numbers with CHEAP, build-time queries.

    ALL THREE numbers are scoped to the COMMUNITY-ELIGIBLE activity set —
    the same provenance gate the raw display + exports use (``manual_upload``
    + ``geometry_geojson IS NOT NULL`` + ``contribute_heatmap``, SSOT
    ``provenance.community_eligible_conditions``). Counting over ALL
    activities over-counted: ``strava_api`` / legacy-NULL / non-consented
    rides are personal-only and never appear on the public map, so they must
    not inflate the banner.

    Returned dict (published verbatim as ``stats.json``):

    - ``contributors`` — distinct users with at least one COMMUNITY-ELIGIBLE
      activity (``COUNT(DISTINCT user_id)`` on the indexed
      ``activities.user_id``, gated to the eligible set).
    - ``traces`` — count of COMMUNITY-ELIGIBLE activity rows (``COUNT(*)``):
      the honest number of contributed rides, NOT the ~4.6 M cumulative
      ``pass_count`` the old banner mislabelled.
    - ``km`` — **unique km of the community NETWORK**: ``SUM(ST_Length)`` over
      the DISTINCT OSM ways in ``heat_edges_agg`` (one row per
      ``(osm_way_id, sport)``, ~45-87 k indexed rows), deduped by
      ``osm_way_id`` so a way mapped under two sports (gravel+mtb) counts
      once, and filtered to ``user_count >= min_uc`` so it matches what the
      heatmap actually displays. Chosen over ``SUM(activities.distance_m)``
      (total km *ridden*) because the label is "km de chemins" (km of
      *paths* / the network the community has reclaimed), not km ridden.
      It is LIGHT: build-time only (never per-visit), reads the small
      pre-aggregated table, never the ~5 M-row raw ``heat_edges``.
      **Fallback:** when ``heat_edges_agg`` is empty (local dev without an
      OSM import, or a fresh DB before the backfill) the network sum is 0 —
      we then fall back to ``SUM(activities.distance_m)`` so the number is
      never a misleading 0 in any environment.

    Never raises: each query is defensively wrapped so a transient hiccup
    yields 0 for that field rather than aborting the whole build.
    """
    from app.services.raw_trace_display import raw_display_enabled

    def _scalar(sql: str, params: dict | None = None, default=0):
        try:
            val = db.execute(sa_text(sql), params or {}).scalar()
            return val if val is not None else default
        except Exception as exc:  # noqa: BLE001 — stats are best-effort
            db.rollback()
            log.warning("community-stats query failed (%s): %s", sql.split()[1], exc)
            return default

    # Count contributors / traces over the COMMUNITY-ELIGIBLE set only — the
    # same provenance gate the raw display + exports use (manual_upload +
    # geometry + contribute_heatmap). Counting ALL activities over-counts:
    # strava_api / legacy-NULL / non-consented rides are personal-only and
    # never appear on the public map, so they must not inflate the banner.
    from app.services.provenance import community_eligible_conditions
    _elig_conds, _elig_params = community_eligible_conditions()
    _elig_where = " AND ".join(_elig_conds)

    contributors = int(_scalar(
        f"SELECT COUNT(DISTINCT user_id) FROM activities WHERE {_elig_where}",
        _elig_params,
    ))
    traces = int(_scalar(
        f"SELECT COUNT(*) FROM activities WHERE {_elig_where}",
        _elig_params,
    ))

    # Unique network metres — "km de chemins" (the network the community has
    # reclaimed), NOT total km ridden.
    #
    # RAW mode (prod since the pivot): ``heat_edges_agg`` is DROPPED, so the
    # old SUM(ST_Length) query below would fail → warning spam → silent fallback
    # to total km RIDDEN (over-counts every repeat ride). Instead the build path
    # passes ``network_m_override`` = the occupied-lattice estimate from
    # ``export_raw_geojson`` (deduped by geography — see its comment). The live
    # ``/heatmap/stats`` fallback endpoint has no lattice → override is None and
    # we drop to the ridden-distance fallback below (rare; the home always reads
    # the accurate static ``stats.json`` this build wrote).
    #
    # MATCHED mode (legacy): the DISTINCT-ON(osm_way_id) aggregate sum, collapsing
    # the gravel/mtb duplicate rows for a shared physical way before summing.
    if network_m_override is not None:
        network_m = float(network_m_override)
    elif raw_display_enabled():
        network_m = 0.0  # no agg table in raw mode; drop to the ridden fallback
    else:
        network_m = float(_scalar(
            """
            SELECT COALESCE(SUM(ST_Length(geometry::geography)), 0)
            FROM (
                SELECT DISTINCT ON (osm_way_id) geometry
                FROM heat_edges_agg
                WHERE user_count >= :min_uc
                ORDER BY osm_way_id
            ) w
            """,
            {"min_uc": min_uc},
        ))
    if network_m <= 0:
        # Dev / pre-backfill fallback: total ridden distance is cheap (indexed
        # single-column SUM). Gated to the SAME community-eligible set so the
        # fallback km can't be inflated by personal (strava_api / non-consented)
        # rides that the primary heat_edges_agg path already excludes.
        network_m = float(_scalar(
            f"SELECT COALESCE(SUM(distance_m), 0) FROM activities WHERE {_elig_where}",
            _elig_params,
        ))

    return {
        "contributors": contributors,
        "traces": traces,
        "km": round(network_m / 1000),
        "generated_at": _utc_now_iso(),
        "min_uc": min_uc,
        "license": ODBL_LICENSE,
        "license_url": ODBL_URL,
        "attribution": STATS_ATTRIBUTION,
    }


def capture_heatmap_metrics_snapshot(
    db, source: str, min_uc: int = 1, *, grid_fallback_pct: float | None = None,
    network_m_override: float | None = None,
) -> dict | None:
    """Append ONE row to ``heatmap_metrics`` (the admin evolution time-series).

    Reuses ``compute_community_stats`` (SSOT) for contributors / activities /
    network_km so the chart never drifts from the home-banner headline, and
    adds two CHEAP heatmap-size numbers:

    * ``agg_ways`` — ``COUNT(*)`` of the small ``heat_edges_agg`` table.
    * ``heat_edges`` — the ``pg_class`` reltuples ESTIMATE (same cheap source
      as ``/readyz``); NEVER a live ``COUNT(*)`` scan of the ~5 M-row table
      (that severed the f1-micro connection in #442).

    ``grid_fallback_pct`` is left NULL unless a caller passes it (computing it
    needs a raw ``heat_edges`` scan — off-limits on the hot path).

    FAIL-SOFT: wrapped end-to-end in try/except so a snapshot hiccup can NEVER
    break the heatmap rebuild or the daily cron that calls it. Returns the
    inserted row as a dict (for logging/tests) or ``None`` on failure.
    """
    try:
        stats = compute_community_stats(db, min_uc, network_m_override=network_m_override)

        def _scalar(sql: str, default: int = 0) -> int:
            try:
                val = db.execute(sa_text(sql)).scalar()
                return int(val) if val is not None else default
            except Exception:  # noqa: BLE001 — best-effort size probe
                db.rollback()
                return default

        agg_ways = _scalar("SELECT COUNT(*) FROM heat_edges_agg")
        # reltuples estimate across the heat_edges partitions — cheap, no scan.
        heat_edges = _scalar(
            "SELECT COALESCE(SUM(GREATEST(reltuples, 0)), 0)::bigint FROM pg_class "
            "WHERE relkind = 'r' AND relname LIKE 'heat_edges_%' "
            "AND relname <> 'heat_edges_agg' "
            "AND relname NOT LIKE '%idx%' AND relname NOT LIKE '%key%'"
        )

        row = {
            "heat_edges": heat_edges,
            "agg_ways": agg_ways,
            "activities": int(stats["traces"]),
            "contributors": int(stats["contributors"]),
            "network_km": float(stats["km"]),
            "grid_fallback_pct": grid_fallback_pct,
            "source": source,
        }
        db.execute(
            sa_text(
                "INSERT INTO heatmap_metrics "
                "(heat_edges, agg_ways, activities, contributors, network_km, "
                " grid_fallback_pct, source) "
                "VALUES (:heat_edges, :agg_ways, :activities, :contributors, "
                " :network_km, :grid_fallback_pct, :source)"
            ),
            row,
        )
        db.commit()
        log.info("heatmap_metrics snapshot (%s): %s", source, row)
        return row
    except Exception as exc:  # noqa: BLE001 — a snapshot must never break the rebuild
        log.warning("heatmap_metrics snapshot (%s) failed — skipped: %s", source, exc)
        import contextlib
        with contextlib.suppress(Exception):
            db.rollback()
        return None


def publish_stats_json(db, output_dir: str, min_uc: int,
                       network_m_override: float | None = None) -> dict:
    """Compute + write ``stats.json`` locally and (if configured) to GCS.

    Local write (``{output_dir}/stats.json``) always happens so
    ``make pmtiles`` can copy it into ``frontend/public/`` for dev serving.
    The GCS upload targets the SAME public bucket the PMTiles goes to
    (``HEATMAP_GCS_BUCKET`` → ``gs://common-trails-heatmap-prod``) with an
    atomic ``.tmp`` → ``rewrite()`` swap and a 1 h cache header, so the
    homepage fetch is a static, DB-free object refreshed on every rebuild.

    Never raises — returns the computed stats dict for logging/tests. A GCS
    hiccup logs a warning; the local file (and the whole build) survive.
    """
    import json as _json

    stats = compute_community_stats(db, min_uc, network_m_override=network_m_override)
    local_path = os.path.join(output_dir, "stats.json")
    try:
        with open(local_path, "w") as fh:
            _json.dump(stats, fh, indent=2)
        log.info("Wrote community stats → %s (%s)", local_path, stats)
    except OSError as exc:
        log.warning("could not write local stats.json: %s", exc)

    bucket_name = os.environ.get("HEATMAP_GCS_BUCKET", "").strip()
    if not bucket_name:
        log.info("HEATMAP_GCS_BUCKET unset → skipping stats.json upload (local dev OK)")
        return stats

    try:
        from google.cloud import storage  # type: ignore[attr-defined]
    except ImportError:
        log.warning("google-cloud-storage not installed; cannot upload stats.json")
        return stats

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        tmp_blob = bucket.blob("stats.json.tmp")
        canonical_blob = bucket.blob("stats.json")
        # no-store: the homepage stats are stable-named + mutable, so the CDN
        # must never serve a stale copy past its TTL — always revalidate at
        # origin so a rebuild shows promptly. The object is tiny (negligible
        # re-fetch cost).
        tmp_blob.cache_control = "no-store"
        tmp_blob.upload_from_string(
            _json.dumps(stats, indent=2), content_type="application/json"
        )
        canonical_blob.rewrite(tmp_blob)
        try:
            tmp_blob.delete()
        except Exception:  # noqa: BLE001
            log.debug("could not delete stats tmp blob (already gone?)")
        log.info("Uploaded stats.json → %s/stats.json",
                 _public_base_url(bucket_name))
    except Exception as exc:  # noqa: BLE001 — best-effort upload
        log.warning("stats.json upload to gs://%s failed (build keeps local file): %s",
                    bucket_name, exc)
    return stats


def _emit_heat_quality_metrics(db) -> None:
    """Compute disconnection metrics for the monitored regions and
    emit them as structured logs + a Sentry measurement. Best-effort —
    never raises (a quality-check failure should not break the build).

    Guarded by a per-statement timeout: the 2026-07 O(n²) regression
    (correlated subqueries in the metric SQL) made ONE region take
    >29 min, silently eating build time on every rebuild. The metric
    query is O(n) now (seconds per region at 2.3M edges); if it ever
    regresses past the timeout we log a warning instead of stalling
    the build."""
    import time

    from sqlalchemy import text as sa_text

    try:
        from app.services.heat_quality import (
            ALERT_GRID_FALLBACK_RATIO,
            ALERT_ISOLATED_RATIO,
            MONITORED_REGIONS,
            compute_disconnection_metrics,
        )
        try:
            import sentry_sdk
        except ImportError:
            sentry_sdk = None  # type: ignore[assignment]

        db.execute(sa_text("SET statement_timeout = '120s'"))
        for label, bbox in MONITORED_REGIONS:
            t0 = time.monotonic()
            try:
                m = compute_disconnection_metrics(db, bbox)
            except Exception as exc:
                db.rollback()
                # SET inside a transaction is undone by the rollback —
                # re-apply so the NEXT region is still time-boxed.
                db.execute(sa_text("SET statement_timeout = '120s'"))
                log.warning("heat_quality region=%s failed after %.1fs: %s",
                            label, time.monotonic() - t0, exc)
                continue
            payload = {"event": "heat_quality", "region": label, **m.as_log_dict()}
            log.info("heat_quality region=%s edges=%d grid=%.1f%% iso=%.1f%% took=%.1fs data=%s",
                     label, m.edges_total,
                     100 * m.grid_fallback_ratio, 100 * m.isolated_ratio,
                     time.monotonic() - t0, payload)
            if m.edges_total > 0 and sentry_sdk is not None:
                # Sentry measurement: one per region per build. Use
                # set_tag for the region so dashboards can group.
                sentry_sdk.set_tag(f"heat_quality.{label}.grid_fallback_ratio",
                                   round(m.grid_fallback_ratio, 4))
                sentry_sdk.set_tag(f"heat_quality.{label}.isolated_ratio",
                                   round(m.isolated_ratio, 4))
                if (m.grid_fallback_ratio > ALERT_GRID_FALLBACK_RATIO or
                        m.isolated_ratio > ALERT_ISOLATED_RATIO):
                    sentry_sdk.capture_message(
                        f"heat_quality alert: region={label} "
                        f"grid={m.grid_fallback_ratio:.1%} iso={m.isolated_ratio:.1%}",
                        level="warning",
                    )
        db.execute(sa_text("SET statement_timeout = 0"))
    except Exception as exc:
        # Best effort — never fail the build
        log.warning("heat_quality emit failed: %s", exc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build PMTiles (display + routing)")
    parser.add_argument("--output-dir", default="/tmp")
    parser.add_argument("--min-zoom", type=int, default=6)
    # max-zoom 15 (was 14): at z17+ the user pans street-level and the
    # over-zoomed z14 tiles produce visible kinks. z15 doubles tile count
    # but gives sharper curves at the zoom level that matters for "is
    # this heatmap continuous?" assessment.
    parser.add_argument("--max-zoom", type=int, default=15)
    parser.add_argument("--min-uc", type=int, default=1,
                        help="Minimum user_count (default 1 for dev/beta — see all traces. Use 2+ for production K-anonymity)")
    grid_group = parser.add_mutually_exclusive_group()
    grid_group.add_argument("--keep-grid-fallback", action="store_true",
                            help="Force-include heat_edges with NULL osm_way_id "
                                 "(grid-snapped GPS 'desire lines'). Default "
                                 "behaviour already follows env "
                                 "HEATMAP_KEEP_GRID_FALLBACK (true unless set "
                                 "false); this flag overrides env for ops.")
    grid_group.add_argument("--drop-grid-fallback", action="store_true",
                            help="Force-exclude grid-fallback edges (the old "
                                 "Komoot-quality default), overriding env.")
    parser.add_argument("--grid-fallback-min-uc", type=int, default=None,
                        help="Override the grid_fallback_min_uc confirmation "
                             "floor (default: env HEATMAP_GRID_FALLBACK_MIN_UC, "
                             "else follows --min-uc).")
    args = parser.parse_args()

    _drop: bool | None = None
    if args.drop_grid_fallback:
        _drop = True
    elif args.keep_grid_fallback:
        _drop = False

    main(args.output_dir, args.min_zoom, args.max_zoom, args.min_uc,
         drop_grid_fallback=_drop,
         grid_fallback_min_uc=args.grid_fallback_min_uc)
