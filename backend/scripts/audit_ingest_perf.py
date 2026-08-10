"""Benchmark the ingest pipeline stage-by-stage.

Usage:
    python -m scripts.audit_ingest_perf /strava-export [--sample N]

Outputs per-stage timings:
  - Read + decompress
  - Parse (gpx / fit)
  - ingest_activity (DB insert + dedup checks)
  - _update_heat_edges (densify + OSM match + grid snap + UPSERT)

Reports p50 / p95 / total / files-per-second so we can spot regressions.
This is read-only against the user table — uses the first user in DB.
"""
import argparse
import csv
import gzip
import logging
import os
import statistics
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# Classification routed through the SINGLE SOURCE OF TRUTH so this
# perf-audit harness sees the same sport mix the real intakes produce.
from app.services.strava_utils import classify_sport


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("export_dir")
    parser.add_argument("--sample", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--full", action="store_true",
                        help="Skip sample, ingest all files")
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--with-heat", action="store_true",
                        help="Compute heat edges per activity (slower but realistic)")
    parser.add_argument(
        "--keep-data", action="store_true",
        help="Skip the cleanup at the end (so you can inspect the result on the map)",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Parallel ingest workers (default 1 = sequential, useful for stage-timing). "
             "Use 4+ for production-realistic throughput.",
    )
    parser.add_argument(
        "--fragmentation-bbox", default=None,
        help=(
            "If set (lon_min,lat_min,lon_max,lat_max), report fragmentation "
            "metrics for heat_edges this audit produced inside that bbox. "
            "Use with --with-heat. Lets you measure 'spaghetti vs continuum' "
            "as a number without looking at the map."
        ),
    )
    args = parser.parse_args()

    activities_dir = os.path.join(args.export_dir, "activities")
    csv_path = os.path.join(args.export_dir, "activities.csv")
    sport_map: dict[str, str] = {}
    if os.path.exists(csv_path):
        with open(csv_path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                aid = row.get("Activity ID", "").strip()
                atype = row.get("Activity Type", "").strip()
                if aid and atype:
                    sport = classify_sport(atype, row.get("Activity Name"))
                    # Perf harness: keep a usable sport for unclassified /
                    # out-of-scope rows so timing covers every file.
                    sport_map[aid] = sport or "road"

    files = sorted(
        f for f in os.listdir(activities_dir)
        if f.endswith((".gpx", ".gpx.gz", ".fit", ".fit.gz"))
    )
    log.info("Found %d activity files", len(files))

    if not args.full:
        import random
        random.seed(args.seed)
        files = random.sample(files, min(args.sample, len(files)))
        log.info("Sampling %d files", len(files))

    # Skip the per-upload PMTiles rebuild during the loop. (The
    # heat_edges_display matview was dropped June 2026 — the live MVT
    # endpoint aggregates heat_edges directly now, so there is no matview
    # refresh to debounce.) Set BEFORE importing ingest.
    os.environ.setdefault("SKIP_PMTILES_REBUILD", "true")
    # Skip the canonical-merge bbox query during the loop. Per-activity
    # it scans heat_edges in the activity bbox to power the spatial
    # endpoint index — for dense areas this is tens of thousands of
    # rows. ON CONFLICT in the UPSERT handles dedup so the merge is
    # primarily about coalescing GPS noise within ±5 grid cells (~5 m).
    # The May 2026 projection fix already makes co-trail edges collapse
    # at the 5dp grid, so the merge is mostly redundant on bulk imports.
    os.environ.setdefault("SKIP_CANONICAL_MERGE", "true")

    from app.db.session import SessionLocal
    from app.services.gpx import parse_gpx
    from app.services.ingest import (
        ingest_activity,
        retry_on_deadlock,
    )

    user_id = args.user_id
    if not user_id:
        # Use a fresh "audit" user so the cross-provider dedup
        # (user + date±5min + dist±10%) doesn't skip everything when the
        # real user already has these activities. Uses a fixed UUID per
        # run-second so repeated invocations don't collide either.
        import uuid

        from sqlalchemy import text as sa_text
        user_id = str(uuid.uuid4())
        db = SessionLocal()
        try:
            db.execute(sa_text("""
                INSERT INTO users (id, email, username, hashed_password, created_at)
                VALUES (:uid, :email, :uname, 'audit-no-login', NOW())
                ON CONFLICT (id) DO NOTHING
            """), {
                "uid": user_id,
                "email": f"audit-{user_id[:8]}@local",
                "uname": f"audit_{user_id[:8]}",
            })
            db.commit()
        finally:
            db.close()
        log.info("created temp audit user %s", user_id)
    log.info("user_id=%s with_heat=%s", user_id, args.with_heat)

    # Per-stage timings
    t_read: list[float] = []
    t_parse: list[float] = []
    t_ingest: list[float] = []
    coords_count: list[int] = []

    # Capture ingest log lines for OSM-match diagnostics
    ingest_log_buf: list[str] = []
    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            msg = record.getMessage()
            if "Match: " in msg and " OSM," in msg:
                ingest_log_buf.append(msg)
    ingest_logger = logging.getLogger("app.services.ingest")
    cap = _CaptureHandler()
    ingest_logger.addHandler(cap)

    counters = {"imported": 0, "skipped": 0, "failed": 0}
    counters_lock = __import__("threading").Lock()
    timings_lock = __import__("threading").Lock()
    t_total0 = time.monotonic()

    def _process_one(filename: str) -> None:
        filepath = os.path.join(activities_dir, filename)
        activity_id = filename.split(".")[0]
        sport = sport_map.get(activity_id, "road")

        t0 = time.monotonic()
        try:
            if filename.endswith(".gz"):
                with gzip.open(filepath, "rb") as f:
                    content = f.read()
            else:
                with open(filepath, "rb") as f:
                    content = f.read()
        except Exception as e:
            with counters_lock:
                counters["failed"] += 1
            log.warning("read failed %s: %s", filename, e)
            return
        t_r = time.monotonic() - t0

        t0 = time.monotonic()
        try:
            if ".gpx" in filename:
                parsed = parse_gpx(content)
            elif ".fit" in filename:
                from app.services.fit_parser import parse_fit
                parsed = parse_fit(content)
                _sport = parsed.get("sport")
                if _sport and _sport != "road":
                    nonlocal_sport = _sport
                    sport_for_ingest = nonlocal_sport
                else:
                    sport_for_ingest = sport
            else:
                return
        except Exception as e:
            with counters_lock:
                counters["failed"] += 1
            log.warning("parse failed %s: %s", filename, e)
            return
        # Use parsed sport if FIT provided one
        sport_for_ingest = parsed.get("sport") if parsed.get("sport") else sport
        t_p = time.monotonic() - t0
        n_coords = parsed.get("coord_count", 0) or 0

        activity_data = {
            "provider": "strava_export_audit",
            "provider_activity_id": f"{activity_id}_audit_{int(t_total0)}",
            "sport": sport_for_ingest,
            "name": parsed.get("name"),
            "geometry_geojson": parsed.get("geometry_geojson"),
            "distance_m": parsed.get("distance_m"),
            "elevation_gain_m": parsed.get("elevation_gain_m"),
            "file_hash": f"audit-{activity_id}-{int(t_total0)}",
            "activity_date": parsed.get("activity_date"),
        }
        t0 = time.monotonic()
        try:
            # retry_on_deadlock: parallel workers can hit
            # `psycopg2.errors.DeadlockDetected` on heat_edges UPSERT
            # when their bbox overlap. Cheaper to retry the lock-loser
            # than fail the whole activity.
            result = retry_on_deadlock(
                ingest_activity,
                user_id=user_id,
                activity_data=activity_data,
                contribute_heatmap=args.with_heat,
                skip_heat_computation=not args.with_heat,
            )
        except Exception as e:
            with counters_lock:
                counters["failed"] += 1
            log.warning("ingest failed %s: %s", filename, e)
            return
        t_i = time.monotonic() - t0
        with timings_lock:
            t_read.append(t_r)
            t_parse.append(t_p)
            t_ingest.append(t_i)
            coords_count.append(n_coords)
        with counters_lock:
            if result["status"] == "created":
                counters["imported"] += 1
            else:
                counters["skipped"] += 1
        # Progress
        done = counters["imported"] + counters["skipped"] + counters["failed"]
        if done % 50 == 0:
            elapsed = time.monotonic() - t_total0
            rate = done / elapsed if elapsed > 0 else 0
            eta = (len(files) - done) / rate if rate > 0 else 0
            log.info(
                "Progress: %d/%d (%.0f%%) — %d imp / %d skp / %d fld — %.2f files/s — ETA %.0fs",
                done, len(files), 100.0 * done / len(files),
                counters["imported"], counters["skipped"], counters["failed"],
                rate, eta,
            )

    if args.workers > 1:
        from concurrent.futures import ThreadPoolExecutor
        log.info("Running with %d worker threads", args.workers)
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(_process_one, files))
    else:
        for f in files:
            _process_one(f)
    imported = counters["imported"]
    skipped = counters["skipped"]
    failed = counters["failed"]

    t_total = time.monotonic() - t_total0

    # The heat_edges_display matview was dropped (June 2026); the live MVT
    # endpoint + search/popup queries read heat_edges directly, so no
    # post-loop refresh is needed. Rebuild the display PMTiles separately
    # (`make pmtiles`) if you want the browser map to reflect the new data.

    # Parse OSM match counts from captured log
    osm_total = grid_total = 0
    for msg in ingest_log_buf:
        # "Match: 12 OSM, 34 grid-fallback"
        try:
            parts = msg.split("Match:")[1].strip().split()
            osm_total += int(parts[0])
            grid_total += int(parts[2])
        except Exception:
            pass

    # Report
    def stats(label, samples):
        if not samples:
            log.info("%-12s no samples", label)
            return
        s = sorted(samples)
        n = len(s)
        p50 = s[n // 2]
        p95 = s[min(n - 1, int(n * 0.95))]
        log.info(
            "%-12s n=%d  total=%6.2fs  mean=%6.3fs  p50=%6.3fs  p95=%6.3fs  max=%6.3fs",
            label, n, sum(s), sum(s)/n, p50, p95, max(s),
        )

    log.info("================ AUDIT RESULTS ================")
    log.info("files: %d total — imported=%d skipped=%d failed=%d in %.1fs (%.2f files/s)",
             len(files), imported, skipped, failed, t_total,
             len(files) / t_total if t_total > 0 else 0)
    if coords_count:
        log.info("coords/activity: median=%d, p95=%d, max=%d",
                 statistics.median(coords_count),
                 sorted(coords_count)[min(len(coords_count) - 1, int(len(coords_count) * 0.95))],
                 max(coords_count))
    stats("read+gunzip", t_read)
    stats("parse",       t_parse)
    stats("ingest",      t_ingest)
    if osm_total + grid_total > 0:
        log.info("OSM match: %d edges OSM-matched / %d grid-fallback (%.1f%% matched)",
                 osm_total, grid_total,
                 100.0 * osm_total / max(1, osm_total + grid_total))

    # Fragmentation metrics — quantitative check that "the spaghetti is
    # gone". If two riders walk the same trail with offset GPS noise,
    # the projection fix collapses them to identical edge_keys, so:
    #   - fewer total edges per OSM way
    #   - higher avg user_count per edge
    #   - fewer "feathery" perpendicular outliers (we approximate this
    #     as edges within 30 m of an OSM way that DON'T share its
    #     osm_way_id).
    if args.with_heat and args.fragmentation_bbox and not args.user_id:
        import hashlib

        from sqlalchemy import text as sa_text
        uid_hash = int(hashlib.sha256(user_id.encode()).hexdigest()[:8], 16)
        try:
            lon_min, lat_min, lon_max, lat_max = (
                float(x) for x in args.fragmentation_bbox.split(",")
            )
        except ValueError:
            log.warning("--fragmentation-bbox must be lon_min,lat_min,lon_max,lat_max")
        else:
            db = SessionLocal()
            try:
                row = db.execute(sa_text("""
                    WITH this_audit AS (
                      SELECT he.edge_key, he.osm_way_id, he.user_count,
                             ST_Length(he.geometry::geography) AS len_m,
                             he.geometry
                      FROM heat_edges he
                      JOIN heat_edge_contributors hec ON hec.edge_key = he.edge_key
                      WHERE hec.user_id_hash = :h
                        AND ST_Intersects(he.geometry,
                              ST_MakeEnvelope(:lon_min, :lat_min, :lon_max, :lat_max, 4326))
                    )
                    SELECT
                      COUNT(*) AS total_edges,
                      COUNT(*) FILTER (WHERE osm_way_id IS NOT NULL) AS osm_matched,
                      ROUND(AVG(user_count)::numeric, 2) AS avg_user_count,
                      COUNT(DISTINCT osm_way_id) AS distinct_osm_ways,
                      ROUND(AVG(len_m)::numeric, 1) AS avg_edge_len_m,
                      MAX(user_count) AS max_user_count
                    FROM this_audit
                """), {
                    "h": uid_hash,
                    "lon_min": lon_min, "lat_min": lat_min,
                    "lon_max": lon_max, "lat_max": lat_max,
                }).fetchone()
                log.info("---- fragmentation in bbox %s ----", args.fragmentation_bbox)
                if row and row[0]:
                    edges_per_way = row[0] / row[3] if row[3] else float("inf")
                    log.info(
                        "  total=%d  osm_matched=%d  distinct_osm_ways=%d  "
                        "edges/way=%.1f  avg_user_count=%s  max_user_count=%d  "
                        "avg_edge_len=%sm",
                        row[0], row[1], row[3], edges_per_way,
                        row[2], row[5], row[4],
                    )
                    log.info(
                        "  → after the projection fix, edges/way should be small "
                        "(~1-3) and avg_user_count > 1 if multiple activities "
                        "covered this bbox. Pre-fix runs had edges/way > 10 and "
                        "avg_user_count = 1.0 even with multi-rider coverage."
                    )
                else:
                    log.info("  no edges in bbox — try a different one")
            finally:
                db.close()
    log.info("===============================================")

    # Cleanup the temp audit user + everything they wrote so the DB is
    # clean for the next run. heat_edges from --with-heat are tagged via
    # heat_edge_contributors(user_id_hash) — also cleaned.
    # Skip cleanup if --keep-data so the rendered tiles can be inspected.
    if args.keep_data:
        log.info("--keep-data: leaving audit user %s + their data in DB", user_id)
        return
    if not args.user_id:
        import hashlib

        from sqlalchemy import text as sa_text
        uid_hash = int(hashlib.sha256(user_id.encode()).hexdigest()[:8], 16)
        db = SessionLocal()
        try:
            db.execute(sa_text(
                "DELETE FROM heat_edge_contributors WHERE user_id_hash = :h"
            ), {"h": uid_hash})
            db.execute(sa_text(
                "DELETE FROM activities WHERE user_id = :uid"
            ), {"uid": user_id})
            db.execute(sa_text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
            db.commit()
            log.info("cleanup: removed audit user %s + their data", user_id)
        finally:
            db.close()


if __name__ == "__main__":
    main()
