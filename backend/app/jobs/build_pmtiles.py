"""Build the community heatmap PMTiles from masked raw traces (DISPLAY ONLY).

Generates ``heatmap-display.pmtiles`` for MapLibre rendering, straight from
``activities`` (``app.services.raw_trace_display``):
  - ``trails`` LineString layer (user_count / heat_score / sport)
  - ``heat_points`` Point layer (density weights)

Privacy = endpoint masking (``trace_privacy``) + the ``HEATMAP_MIN_USERS``
floor — see docs/raw-trace-heatmap-prototype.md.

Run:
    python -m app.jobs.build_pmtiles
    python -m app.jobs.build_pmtiles --output-dir /tmp

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


def _isolated_export_enabled() -> bool:
    """Run the raw export in a CHILD process (the 2026-09-02 OOM fix)?

    Default ON — except under pytest (``PYTEST_CURRENT_TEST`` is set by the
    runner for the duration of each test), where the export runs in-process so
    the suite's monkeypatched ``export_raw_geojson`` seams keep working (a
    spawned child re-imports the real module and would silently ignore them).
    ``HEATMAP_EXPORT_ISOLATED=true|false`` overrides explicitly in BOTH
    directions (ops/debugging, and the isolation golden test which forces the
    real child under pytest).
    """
    v = os.environ.get("HEATMAP_EXPORT_ISOLATED", "").strip().lower()
    if v in ("true", "false"):
        return v == "true"
    return "PYTEST_CURRENT_TEST" not in os.environ


def _raw_export_child(geojson_path: str, points_path: str, stats_path: str) -> None:
    """Child-process entrypoint for the raw export (spawned by
    ``_run_raw_export`` — see there for WHY). Opens its OWN DB session (a
    spawn-child inherits no parent connection), writes the two geojsonl files,
    and hands the scalar results back through a tiny JSON file."""
    from app.db.session import SessionLocal
    from app.services.raw_trace_display import export_raw_geojson
    db = SessionLocal()
    try:
        stats: dict = {}
        written = export_raw_geojson(db, geojson_path, points_path=points_path,
                                     stats_out=stats)
        with open(stats_path, "w") as f:
            json.dump({"written": written,
                       "network_m": stats.get("network_m"),
                       "occupied_cells": stats.get("occupied_cells")}, f)
    finally:
        db.close()


def _run_raw_export(db, geojson_path: str, points_path: str,
                    stats_out: dict) -> int:
    """Run the raw export, ISOLATED in a child process by default.

    WHY (the 2026-09-02 4Gi OOM, execution 22mkq, signal 9): the export's peak
    RAM — the per-cell lattice plus the allocator high-water of streaming/parsing
    thousands of activity geometries — stays RESIDENT in this process afterwards
    (pymalloc never returns its arenas), and tippecanoe then runs as a
    subprocess ON TOP of it, inside the same Cloud Run memory cgroup that ALSO
    counts every /tmp file (tmpfs = RAM). Growing the corpus (a heavy
    contributor's +1089 rides) pushed that stack past 4Gi. Running the export in
    a short-lived spawn-child returns ALL of its memory to the OS at exit, so
    tippecanoe starts with a clean budget. Bonus: if the export itself is
    OOM-killed, the cgroup killer takes the (biggest) CHILD and this parent
    survives to log a clear error instead of the whole container dying silently.

    In-process fallback (TEST_MODE / ``HEATMAP_EXPORT_ISOLATED=false``) is
    byte-identical — same function, same files.
    """
    from app.services.raw_trace_display import export_raw_geojson
    if not _isolated_export_enabled():
        return export_raw_geojson(db, geojson_path, points_path=points_path,
                                  stats_out=stats_out)
    import multiprocessing
    stats_path = geojson_path + ".stats.json"
    ctx = multiprocessing.get_context("spawn")
    p = ctx.Process(target=_raw_export_child,
                    args=(geojson_path, points_path, stats_path))
    p.start()
    p.join()
    if p.exitcode != 0:
        raise RuntimeError(
            f"raw export child exited {p.exitcode} "
            "(negative = killed by signal; -9 usually means the export itself "
            "outgrew the job memory — raise the job's --memory)")
    with open(stats_path) as f:
        payload = json.load(f)
    os.unlink(stats_path)
    stats_out["network_m"] = payload.get("network_m")
    stats_out["occupied_cells"] = payload.get("occupied_cells")
    return int(payload["written"])


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
        # -P / --read-parallel: input is line-delimited GeoJSON (one feature
        # per line — both the trails and heat_points files), so tippecanoe can
        # split the parse across cores. Free wall-clock on the cpu2 job; no
        # effect on the produced tiles.
        "-P",
    ]
    if points_path is not None:
        cmd += ["-L", f"trails:{geojson_path}", "-L", f"heat_points:{points_path}"]
        # -r1 / --drop-rate=1: NO rate-based dot dropping below the max zoom.
        # tippecanoe's default (-r 2.5) kept 1/2.5 of the heat_points per zoom,
        # chosen along its spatial index → a lone trace's 20 m points became
        # ~150 m mean / 750 m max gaps at z13 (~100 px between ~8 px kernels)
        # = the "beads" bug (fix/heatmap-beads). The per-zoom thinning is now
        # decided by the export (each point carries a tippecanoe ``minzoom``,
        # raw_trace_display.heat_point_levels) so the spacing along every trace
        # tracks the heatmap radius. Rate dropping only ever applies to POINTS
        # (lines are untouched without --drop-lines); --drop-densest-as-needed
        # below still caps tile bytes.
        cmd += ["-r1"]
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


def main(output_dir: str, min_zoom: int, max_zoom: int, min_uc: int) -> None:
    """Build + publish the heatmap. ``min_uc`` no longer filters anything (the
    render floor is ``HEATMAP_MIN_USERS``); it is only stamped into
    ``stats.json`` (``min_uc`` field), as before."""
    if not shutil.which("tippecanoe"):
        log.error("tippecanoe not found. Install: brew install tippecanoe")
        return

    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        t0 = time.time()
        # Snapshot start, recorded BEFORE the export query: the pointer publish
        # is a compare-and-set on this timestamp, so a build that started
        # earlier (older DB snapshot) and finishes LATER can never overwrite a
        # newer pointer (overlapping builds — see _publish_pointer_cas).
        snapshot_started_at = _utc_now_iso()
        geojson_path = "/tmp/heat_edges_display.geojsonl"
        # Precise masked raw traces. Emits BOTH the `trails` LineString layer
        # (user_count/heat_score/sport — crisp lines + arrows) AND a companion
        # `heat_points` Point layer (w/sport) that the frontend renders as a
        # maplibre `heatmap` density field. Privacy = endpoint masking
        # (trace_privacy), NOT K-anonymity — see
        # docs/raw-trace-heatmap-prototype.md.
        points_path = "/tmp/heat_points_display.geojsonl"
        log.info("Building heatmap-display.pmtiles from RAW masked traces "
                 "— trails + heat_points layers...")
        # stats_out captures the occupied-lattice network estimate for the
        # home "km de chemins" banner.
        raw_stats: dict = {}
        written = _run_raw_export(db, geojson_path, points_path, raw_stats)
        raw_network_m: float | None = raw_stats.get("network_m")
        pts_mb = (os.path.getsize(points_path) / 1024 / 1024
                  if os.path.exists(points_path) else 0.0)
        log.info("Raw export: %.0fs (%d line features, %.1f MB trails + "
                 "%.1f MB heat_points)", time.time() - t0, written,
                 os.path.getsize(geojson_path) / 1024 / 1024, pts_mb)

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
            # Free the heat_points intermediate NOW, not at the end: /tmp on
            # Cloud Run is tmpfs (= RAM, counted against the job's memory
            # limit), the file runs to hundreds of MB at scale, and tippecanoe
            # was its ONLY reader. Deleting it before the upload/gzip/raster
            # phases returns that RAM to the budget (2026-09-02 OOM fix).
            if points_path and os.path.exists(points_path):
                os.unlink(points_path)
            # PUBLISH THE PRIMARY ARTIFACT FIRST. The static heatmap-display.pmtiles
            # is what /map + the hero load; it MUST reach GCS before the best-effort
            # secondary artifacts (geojsonl, raster pyramid). The raster pyramid
            # re-materialises the whole corpus in RAM for ~8-10 min and could be
            # OOM-SIGKILL'd — which no try/except can catch — so if it ran first a
            # kill would strand /map on the PREVIOUS build with no error surfaced.
            _upload_to_export_bucket(output, snapshot_started_at=snapshot_started_at)
            # Also publish the un-tiled geojsonl (gzipped) so the
            # community-EXPORT endpoints (export._query_aggregated_heat_edges)
            # can STREAM + bbox-clip this pre-built artifact instead of
            # re-running the full raw aggregation per request. Best-effort.
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
        # Runs AFTER the PMTiles deliverable + its upload. Queries are cheap
        # (indexed activities aggregates).
        publish_stats_json(db, output_dir, min_uc, network_m_override=raw_network_m)

        # Evolution time-series — append ONE snapshot of the community-heatmap
        # size at the END of the rebuild (the admin dashboard's "is it growing?"
        # chart). Reuses the stats just computed (SSOT) plus two cheap size
        # probes; fully fail-soft so it can never break the deliverable above.
        capture_heatmap_metrics_snapshot(db, source="rebuild", min_uc=min_uc,
                                         network_m_override=raw_network_m)
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


# Per-sport calques (raster-<sport>/): one XYZ pyramid per sport so a gpx.studio
# overlay can show just gravel / mtb / road / … The set + expansion MIRRORS the
# frontend chips (app/page.tsx SPORT_PILLS / communityHeatSportFilter) via the
# backend SSOT config.expand_sport — offroad → mtb+offroad+gravel. The combined
# all-sports pyramid stays at the bare ``raster/`` prefix (unchanged URL).
_CALQUE_SPORTS = ("road", "gravel", "mtb", "running", "offroad")

# Concurrent GCS PNG uploads per pyramid. The render loop produces thousands of
# small tiles; serially each one is a full HTTPS round-trip (~50-100 ms) that
# dominated the raster phase's wall-clock. 16 parallel PUTs of ~1-20 KB PNGs is
# well under any GCS/egress limit on a cpu2 job.
_DEFAULT_UPLOAD_WORKERS = 16


def _raster_upload_workers() -> int:
    """Thread-pool size for raster tile uploads (env, defensive parse)."""
    try:
        workers = int(os.environ.get(
            "HEATMAP_RASTER_UPLOAD_WORKERS", str(_DEFAULT_UPLOAD_WORKERS)))
    except ValueError:
        workers = _DEFAULT_UPLOAD_WORKERS
    return max(1, workers)


def _render_and_upload_pyramid(
    parsed_features, *, upload_key_png, prefix: str, min_zoom: int, max_zoom: int,
    sport_filter, workers: int,
) -> tuple[dict, set[str]]:
    """Render one XYZ pyramid from PRE-PARSED features, uploading each tile PNG
    through a bounded thread pool.

    ``upload_key_png(key, png_bytes)`` performs one blocking upload (the GCS
    round-trip). The render loop only SUBMITS uploads and keeps rasterising the
    next tile, so upload latency overlaps CPU work instead of serialising after
    it. Returns ``(stats, written_keys)`` where ``written_keys`` holds ONLY the
    keys whose upload COMPLETED — collected from the futures' results, so the
    set is race-free and the caller's stale-tile purge (which deletes every
    blob NOT in it) can never be poisoned by a torn concurrent update.

    Every upload is joined before returning; the FIRST upload exception (in
    submission order) is re-raised so a failed calque is surfaced to the
    per-sport best-effort handler instead of silently publishing a partial
    pyramid + purging its siblings.
    """
    from concurrent.futures import Future, ThreadPoolExecutor

    from app.services.heatmap_raster_pyramid import render_pyramid_from_parsed

    futures: list[Future] = []

    def _do_upload(key: str, png: bytes) -> str:
        upload_key_png(key, png)
        return key

    with ThreadPoolExecutor(max_workers=workers) as pool:
        def _submit_png(z: int, x: int, y: int, png: bytes) -> None:
            key = f"{prefix}/{z}/{x}/{y}.png"
            futures.append(pool.submit(_do_upload, key, png))

        stats = render_pyramid_from_parsed(
            parsed_features, upload_png=_submit_png,
            min_zoom=min_zoom, max_zoom=max_zoom, sport_filter=sport_filter,
            source=f"<parsed:{prefix}>",
        )
        written_keys: set[str] = set()
        first_exc: Exception | None = None
        for fut in futures:
            try:
                written_keys.add(fut.result())
            except Exception as exc:  # noqa: BLE001 — surfaced below
                if first_exc is None:
                    first_exc = exc
        if first_exc is not None:
            raise first_exc
    return stats, written_keys


def _build_and_upload_raster_pyramid(geojson_path: str) -> None:
    """Best-effort: render raster XYZ tile pyramids from the raw-display geojsonl
    and publish them to the public heatmap bucket so gpx.studio / VisuGPX (any
    Leaflet/OL client) can add the community heatmap as a custom overlay
    ("calque") via one URL:

    - ``raster/{z}/{x}/{y}.png`` + ``raster/tiles.json`` — ALL sports (unchanged).
    - ``raster-<sport>/{z}/{x}/{y}.png`` + ``raster-<sport>/tiles.json`` — one per
      sport in ``_CALQUE_SPORTS`` (env ``HEATMAP_RASTER_PER_SPORT``, default on),
      so the overlay can be filtered by sport (there's no way to filter a raster
      client-side — the filtering must be baked into separate tilesets).

    Env-gated on ``HEATMAP_RASTER_PYRAMID=true`` (default off → nothing changes)
    + ``HEATMAP_GCS_BUCKET``. Never raises — a raster hiccup must not break the
    PMTiles build. Zoom range via ``HEATMAP_RASTER_MIN/MAX_ZOOM`` (default 6-14).
    Each prefix is overwrite-in-place, then STALE tiles are PURGED per prefix:
    every ``.png`` under it NOT written this run is deleted, so a tile left behind
    by a now-deleted / below-K trace can't linger publicly (GDPR + K-flip). Purge
    is gated on a real build (features>0) so a transient empty corpus never wipes
    a calque.
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
        from collections.abc import Collection

        from app.config import expand_sport
        from app.services.heatmap_raster_pyramid import (
            DEFAULT_MAX_ZOOM,
            DEFAULT_MIN_ZOOM,
            build_tilejson,
            parse_heat_edges,
        )

        min_zoom = int(os.environ.get("HEATMAP_RASTER_MIN_ZOOM", str(DEFAULT_MIN_ZOOM)))
        max_zoom = int(os.environ.get("HEATMAP_RASTER_MAX_ZOOM", str(DEFAULT_MAX_ZOOM)))
        combined_on = os.environ.get(
            "HEATMAP_RASTER_COMBINED", "false").strip().lower() == "true"
        per_sport_on = os.environ.get(
            "HEATMAP_RASTER_PER_SPORT", "true").strip().lower() == "true"
        if not combined_on and not per_sport_on:
            return
        upload_workers = _raster_upload_workers()
        client = storage.Client()
        bucket = client.bucket(bucket_name)

        # Parse + RDP-simplify the whole geojsonl ONCE and render every calque
        # from the shared in-memory feature list. The legacy path re-read +
        # re-json-parsed + re-simplified the ENTIRE national corpus per pyramid
        # (5 per-sport + optional combined = up to 6 full parses) — pure waste,
        # since only the sport FILTER differs between them.
        t_parse = time.time()
        parsed = parse_heat_edges(geojson_path)
        log.info("raster pyramid: parsed %d features once in %.0fs "
                 "(shared across calques)", len(parsed), time.time() - t_parse)

        def _render_prefix(
            prefix: str, sport_filter: Collection[str] | None, max_z: int = max_zoom,
        ) -> None:
            """Render one pyramid to ``<prefix>/`` (+ tiles.json + stale-purge).

            ``max_z`` caps this prefix's top zoom (per-sport calques render to a
            LOWER zoom than the combined one — see the caller)."""

            def _upload_key_png(key: str, png: bytes) -> None:
                blob = bucket.blob(key)
                # Stable-named MUTABLE artifact (same URL, new bytes each rebuild)
                # — keep the cache short so a rebuild reaches consumers promptly.
                blob.cache_control = "public, max-age=3600"
                blob.upload_from_string(png, content_type="image/png")

            t = time.time()
            stats, written_keys = _render_and_upload_pyramid(
                parsed, upload_key_png=_upload_key_png, prefix=prefix,
                min_zoom=min_zoom, max_zoom=max_z, sport_filter=sport_filter,
                workers=upload_workers,
            )
            purged = 0
            if stats.get("bounds"):
                tiles_url = f"{_public_base_url(bucket_name)}/{prefix}/{{z}}/{{x}}/{{y}}.png"
                tj = build_tilejson(
                    tiles_url=tiles_url, min_zoom=min_zoom, max_zoom=max_z,
                    bounds=tuple(stats["bounds"]),
                    attribution=(
                        '© <a href="https://chemins-communs.fr">CHEMINS COMMUNS</a> '
                        "contributors — ODbL 1.0"
                    ),
                )
                tj_blob = bucket.blob(f"{prefix}/tiles.json")
                tj_blob.cache_control = "no-store"
                tj_blob.upload_from_string(json.dumps(tj), content_type="application/json")

                # PURGE stale tiles for THIS prefix (GDPR + K-flip): a tile
                # occupied ONLY by a now-deleted / below-K trace is not re-rendered
                # this build, so its old PNG would linger publicly forever. Delete
                # every .png under the prefix NOT written this run (tiles.json kept).
                # ``prefix + "/"`` so ``raster/`` never lists ``raster-road/``.
                for blob in bucket.list_blobs(prefix=f"{prefix}/"):
                    if blob.name.endswith(".png") and blob.name not in written_keys:
                        try:
                            blob.delete()
                            purged += 1
                        except Exception:  # pragma: no cover - best-effort per-tile
                            pass
            log.info(
                "raster pyramid: %d tiles (z%d-%d, %d features), %d stale purged, in %.0fs → gs://%s/%s/",
                stats.get("tiles", 0), min_zoom, max_z, stats.get("features", 0),
                purged, time.time() - t, bucket_name, prefix,
            )

        # Combined all-sports calque — OFF by default (Paul 2026-08-15: "drop
        # Tous, except the landing page"). The landing-page all-sports view is the
        # VECTOR pmtiles hero (community-trails, app/page.tsx) at a wide z10, NOT
        # this raster — so nothing needs the combined raster once the "Tous" calque
        # export is dropped. It was also the single biggest build cost (z6-14 =
        # ~21k tiles / ~24 min of a 46-min build). Re-enable via
        # HEATMAP_RASTER_COMBINED=true if an all-sports XYZ overlay is ever wanted.
        if combined_on:
            _render_prefix("raster", None)
        # Per-sport calques — the community heatmap EXPORT (one XYZ overlay per
        # sport for gpx.studio). Capped at HEATMAP_RASTER_PER_SPORT_MAX_ZOOM
        # (default 12): the client overzooms above that; a per-sport calque is
        # planning CONTEXT, and z13/z14 would be ~95% of the tiles (each an
        # individual GCS upload). Each sport isolated so one failure can't skip
        # the rest.
        if per_sport_on:
            try:
                per_sport_max = int(os.environ.get("HEATMAP_RASTER_PER_SPORT_MAX_ZOOM", "12"))
            except ValueError:
                per_sport_max = 12
            per_sport_max = min(per_sport_max, max_zoom)
            for sport in _CALQUE_SPORTS:
                try:
                    _render_prefix(f"raster-{sport}", set(expand_sport(sport)), max_z=per_sport_max)
                except Exception:  # pragma: no cover - best-effort per sport
                    log.warning("raster-%s calque failed (best-effort)", sport, exc_info=True)
    except Exception:
        log.warning("raster pyramid build/upload failed (best-effort)", exc_info=True)


_POINTER_NAME = "heatmap-display.json"
_MUTABLE_NAME = "heatmap-display.pmtiles"


def _parse_iso(value):
    """``datetime`` from an ISO string, or None (missing / unparseable)."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _read_pointer(bucket) -> tuple[int, dict | None]:
    """Return ``(generation, pointer_dict)`` of the live pointer — ``(0, None)``
    when absent. Downloads with ``if_generation_match`` so the body matches the
    generation we will compare-and-set against."""
    import json as _json

    blob = bucket.get_blob(_POINTER_NAME)
    if blob is None:
        return 0, None
    generation = int(blob.generation or 0)
    try:
        body = _json.loads(blob.download_as_bytes(if_generation_match=generation))
    except Exception:  # noqa: BLE001 — unreadable pointer ≡ no timestamp (older)
        body = None
    return generation, body if isinstance(body, dict) else None


def _pointer_is_ours(bucket, snapshot_started_at: str) -> bool:
    """True while the live pointer still carries OUR snapshot timestamp (no
    newer build has published since)."""
    try:
        _gen, current = _read_pointer(bucket)
    except Exception:  # noqa: BLE001
        return False
    return bool(current) and current.get("snapshot_started_at") == snapshot_started_at


def _carries(current: dict | None, pointer: dict) -> bool:
    """The live pointer already IS ours (same snapshot timestamp + version)."""
    return bool(current) and (
        current.get("snapshot_started_at") == pointer["snapshot_started_at"]
        and current.get("latest") == pointer["latest"]
    )


def _publish_pointer_cas(bucket, pointer: dict) -> bool:
    """Compare-and-set publish of ``heatmap-display.json``.

    build-pmtiles executions CAN overlap (the archive drain pings per archive,
    ``/imports/files`` pings too, and the job has no concurrency guard). The
    pointer used to be last-writer-wins, and every run shared ONE
    ``heatmap-display.json.tmp`` — so an older build (older DB snapshot)
    finishing last re-published stale data and the newer rides vanished.

    Now: publish ONLY if our ``snapshot_started_at`` is strictly newer than the
    live pointer's (a missing/unparseable field counts as older), via a per-run
    unique tmp blob + ``rewrite(if_generation_match=<generation read>)`` (``0``
    = "must not exist yet"). A precondition failure (another build published
    between our read and our write) is retried ONCE with a fresh read.
    Returns True when the pointer now references our snapshot.

    ⚠️ ``Blob.rewrite`` with ``if_generation_match`` is AUTO-RETRIED by the
    client (DEFAULT_RETRY_IF_GENERATION_SPECIFIED): if our first write landed
    but its response was lost, the retry gets a 412 although the pointer IS
    ours. So every read first checks "already carries our snapshot" → success.
    """
    import json as _json
    import uuid

    from google.api_core.exceptions import PreconditionFailed

    ours = _parse_iso(pointer["snapshot_started_at"])
    for attempt in (1, 2):
        generation, current = _read_pointer(bucket)
        if _carries(current, pointer):
            return True  # our earlier write landed (lost response + 412 retry)
        theirs = _parse_iso((current or {}).get("snapshot_started_at"))
        if theirs is not None and ours is not None and theirs >= ours:
            log.info(
                "pointer NOT updated: live snapshot %s is not older than ours %s "
                "(an overlapping build published first)",
                (current or {}).get("snapshot_started_at"),
                pointer["snapshot_started_at"],
            )
            return False
        tmp = bucket.blob(f"{_POINTER_NAME}.tmp-{uuid.uuid4().hex}")
        tmp.cache_control = "no-store"
        tmp.upload_from_string(_json.dumps(pointer, indent=2), content_type="application/json")
        try:
            bucket.blob(_POINTER_NAME).rewrite(tmp, if_generation_match=generation)
            return True
        except PreconditionFailed:
            if attempt == 2:
                if _carries(_read_pointer(bucket)[1], pointer):
                    return True
                log.warning("pointer CAS lost twice in a row — leaving the live pointer as is")
                return False
            log.info("pointer CAS precondition failed (concurrent publish) — retrying once")
        finally:
            try:
                tmp.delete()
            except Exception:  # noqa: BLE001
                log.debug("could not delete pointer tmp blob (already gone?)")
    return False


def _upload_to_export_bucket(pmtiles_path: str, snapshot_started_at: str | None = None) -> None:
    """Best-effort upload of the freshly-built PMTiles to the public
    heatmap-export GCS bucket.

    Phase 1 (PRD #391): published the mutable ``heatmap-display.pmtiles``
    object only. Phase 3 ADDS:

    1. ``heatmap-display-{v{N}}.pmtiles`` — immutable per-build snapshot
       so consumers can pin to an exact version.
    2. ``heatmap-display.json`` — pointer file listing the latest
       version, size, captured_at, snapshot_started_at, k_anonymity,
       edge_count. /map resolves the PMTiles URL THROUGH this pointer
       (``frontend/lib/cdn-cache.ts``); the mutable
       ``heatmap-display.pmtiles`` is the "always latest" fallback.

    Overlapping builds (2026-11): the order is snapshot → pointer CAS →
    mutable copy → prune. ``snapshot_started_at`` (recorded by ``main`` BEFORE
    the export query) drives ``_publish_pointer_cas``: a build OLDER than the
    live pointer skips the pointer, the mutable copy and the prune. It NEVER
    deletes its pinned snapshot: two builds with the same bytes in the same
    hour share the same ``pinned_name`` (``v{epoch_hour}-{sha8}``), so "our
    orphan" can be the winner's LIVE snapshot — the next build's prune cleans
    real orphans. The mutable copy is only rewritten while
    the pointer still carries our timestamp (re-checked right before the
    rewrite) — a residual sub-second race there self-heals on the next build,
    and /map follows the pointer anyway. The raster pyramid / geojsonl
    secondaries are NOT guarded (best-effort, last-writer-wins: a late older
    build can leave them one build behind until the next one).

    Env-gated on ``HEATMAP_GCS_BUCKET``. Never raises — a transient
    GCS hiccup must not break the build.
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

    if not snapshot_started_at:
        snapshot_started_at = _utc_now_iso()

    try:
        import uuid

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        size = os.path.getsize(pmtiles_path)
        # Read the bytes once for content-hashing the version id (S1 #2): the
        # pinned-snapshot URL is unique per build (epoch_hour + sha8).
        with open(pmtiles_path, "rb") as fh:
            pmtiles_bytes = fh.read()
        version_id = _pmtiles_version_id(pmtiles_bytes)
        mutable_url = f"{_public_base_url(bucket_name)}/{_MUTABLE_NAME}"

        # Step 1: IMMUTABLE per-build snapshot, 1 y cache (immutable name). No
        # tmp+rewrite: nothing references this name until the pointer does.
        pinned_name = f"heatmap-display-{version_id}.pmtiles"
        pinned_blob = bucket.blob(pinned_name)
        pinned_blob.cache_control = "public, max-age=31536000, immutable"
        pinned_blob.upload_from_filename(pmtiles_path, content_type="application/vnd.pmtiles")
        pinned_url = f"{_public_base_url(bucket_name)}/{pinned_name}"
        log.info("Published immutable snapshot %s (%d bytes) → %s",
                 version_id, size, pinned_url)

        # Step 2: pointer JSON, compare-and-set. Written AFTER the snapshot so
        # the pointer always references bytes already pushed. 5 min cache
        # (cdn-cache.ts) so consumers see new versions quickly.
        pointer = {
            "latest": version_id,
            "latest_url": pinned_url,
            "mutable_url": mutable_url,
            "size_bytes": size,
            "captured_at": _utc_now_iso(),
            "snapshot_started_at": snapshot_started_at,
            "license": "ODbL-1.0",
            "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
            "attribution": "© CHEMINS COMMUNS contributors — ODbL 1.0",
        }
        # The gate the render used: min_users() (HEATMAP_MIN_USERS).
        from app.services.raw_trace_display import min_users
        pointer["k_anonymity"] = min_users()

        if not _publish_pointer_cas(bucket, pointer):
            # An overlapping NEWER build already published: touch nothing else.
            # Do NOT delete our pinned snapshot — with identical bytes in the
            # same hour it IS the live one (same version_id); the next build's
            # prune removes it if it is really an orphan.
            return
        log.info("Updated pointer JSON (latest=%s)", version_id)

        # Step 3: MUTABLE "always latest" copy (Phase 1 consumers), atomic via a
        # per-run unique tmp + rewrite(). The pointer is re-checked right before
        # the rewrite so a slower OLDER build never lands last.
        tmp_blob = bucket.blob(f"{_MUTABLE_NAME}.tmp-{uuid.uuid4().hex}")
        # 24 h browser/CDN cache — /map reads the pinned URL via the pointer.
        tmp_blob.cache_control = "public, max-age=86400"
        tmp_blob.upload_from_filename(pmtiles_path, content_type="application/vnd.pmtiles")
        try:
            if _pointer_is_ours(bucket, snapshot_started_at):
                bucket.blob(_MUTABLE_NAME).rewrite(tmp_blob)
                log.info("Uploaded PMTiles atomically (%d bytes) → %s", size, mutable_url)
            else:
                log.info("mutable copy NOT updated: a newer build published meanwhile")
        finally:
            try:
                tmp_blob.delete()
            except Exception:  # noqa: BLE001
                log.debug("could not delete tmp blob (already gone?)")

        # Prune OLD immutable snapshots — keep ONLY the latest (Paul 2026-08-07:
        # "on doit l'écraser par la dernière version à chaque fois"). They used
        # to accumulate forever, so a GDPR-deleted trace stayed retrievable in a
        # frozen old snapshot indefinitely (right-to-erasure gap). Only while the
        # pointer is still ours, and NEVER: the snapshot the live pointer
        # references, nor one created after OUR start (an overlapping newer
        # build may have uploaded it and not published its pointer yet — or
        # time_created unknown). Leftovers go at the next build's prune.
        # Matches heatmap-display-<version>.pmtiles ONLY (the mutable name has
        # no trailing '-'; geojsonl end in .geojsonl.gz).
        _gen, live = _read_pointer(bucket)
        if not live or live.get("snapshot_started_at") != snapshot_started_at:
            return
        keep = {pinned_name, str(live.get("latest_url") or "").rsplit("/", 1)[-1]}
        started = _parse_iso(snapshot_started_at)
        pruned = 0
        for old in bucket.list_blobs(prefix="heatmap-display-"):
            created = getattr(old, "time_created", None)
            if (
                old.name.endswith(".pmtiles")
                and old.name not in keep
                and created is not None
                and started is not None
                and created < started
            ):
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
    - ``km`` — **unique km of the community NETWORK** ("km de chemins", the
      network the community has reclaimed — not km ridden): the build passes
      ``network_m_override`` = the occupied-lattice estimate from
      ``export_raw_geojson`` (deduped by geography). **Fallback:** without an
      override (the live ``/heatmap/summary`` endpoint has no lattice) or when
      it is 0, ``SUM(activities.distance_m)`` over the eligible set.

    Never raises: each query is defensively wrapped so a transient hiccup
    yields 0 for that field rather than aborting the whole build.
    """
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

    # Unique network metres — see the docstring.
    network_m = float(network_m_override) if network_m_override is not None else 0.0
    if network_m <= 0:
        # Fallback: total ridden distance is cheap (indexed single-column SUM).
        # Gated to the SAME community-eligible set so the fallback km can't be
        # inflated by personal (strava_api / non-consented) rides.
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
    network_km so the chart never drifts from the home-banner headline. The
    legacy ``heat_edges`` / ``agg_ways`` columns (matched pipeline, removed)
    are written as 0 until the schema drops them; ``grid_fallback_pct`` stays
    NULL unless a caller passes it.

    FAIL-SOFT: wrapped end-to-end in try/except so a snapshot hiccup can NEVER
    break the heatmap rebuild that calls it. Returns the
    inserted row as a dict (for logging/tests) or ``None`` on failure.
    """
    try:
        stats = compute_community_stats(db, min_uc, network_m_override=network_m_override)

        row = {
            "heat_edges": 0,
            "agg_ways": 0,
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the community heatmap PMTiles")
    parser.add_argument("--output-dir", default="/tmp")
    parser.add_argument("--min-zoom", type=int, default=6)
    # max-zoom 15 (was 14): at z17+ the user pans street-level and the
    # over-zoomed z14 tiles produce visible kinks. z15 doubles tile count
    # but gives sharper curves at the zoom level that matters for "is
    # this heatmap continuous?" assessment.
    parser.add_argument("--max-zoom", type=int, default=15)
    parser.add_argument("--min-uc", type=int, default=1,
                        help="Stamped into stats.json (min_uc). Does not filter the "
                             "render — the floor is HEATMAP_MIN_USERS.")
    args = parser.parse_args()

    main(args.output_dir, args.min_zoom, args.max_zoom, args.min_uc)
