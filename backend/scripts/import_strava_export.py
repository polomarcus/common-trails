"""Import a Strava bulk export (from strava.com/athlete/delete_your_account).

Usage:
    python -m scripts.import_strava_export /path/to/export_folder [--batch 50] [--sport mtb]

Reads activities.csv for sport mapping, imports GPX and FIT files.
"""
import csv
import gzip
import logging
import os
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# Sentinel for "the activities.csv row exists but the Activity Type is
# out-of-scope (Yoga/Virtual/Ski/...) → SKIP this file". Distinguished
# from "not in the CSV at all" (filename absent from sport_map) so the
# CLI doesn't import ski/virtual rides as road. See
# [[feedback_skip_beats_pollute_heatmap]].
_SKIP = "__skip__"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Import Strava bulk export")
    parser.add_argument("export_dir", help="Path to extracted Strava export folder")
    parser.add_argument("--batch", type=int, default=50, help="Files per batch (default: 50)")
    parser.add_argument("--sport", default=None, help="Override sport for all activities")
    parser.add_argument("--dry-run", action="store_true", help="Count files without importing")
    parser.add_argument("--user-id", default=None, help="User ID (default: first user in DB)")
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Parallel workers for ingest (default: 4). Single-threaded "
             "ingest is ~10s/activity on dense areas; 4 workers brings the "
             "1406-activity Strava bulk export from ~5h to ~1.5h.",
    )
    args = parser.parse_args()

    export_dir = args.export_dir
    activities_dir = os.path.join(export_dir, "activities")

    if not os.path.isdir(activities_dir):
        log.error("No activities/ folder in %s", export_dir)
        sys.exit(1)

    # Read activities.csv for sport mapping. Classification is delegated
    # to the SINGLE SOURCE OF TRUTH `strava_utils.classify_sport` (type +
    # Activity Name) so this CLI agrees byte-for-byte with the UI-upload
    # CSV path and the four Strava live-sync paths. The old local
    # STRAVA_SPORT_MAP + inline name-refinement were deleted.
    from app.services.strava_utils import classify_sport
    csv_path = os.path.join(export_dir, "activities.csv")
    # Value is our sport enum, or `_SKIP` for out-of-scope rows. Filename
    # absent from the dict means "no CSV info" (fall back to args.sport).
    sport_map: dict[str, str] = {}
    if os.path.exists(csv_path):
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Key by FILENAME STEM, not "Activity ID": for many files
                # (esp. .fit) the CSV Activity ID differs from the filename,
                # and _process_file looks up sport_map by filename.split(".")[0].
                # Keying by Activity ID silently misses those → defaults to road.
                fn = (row.get("Filename") or "").strip()
                activity_type = row.get("Activity Type", "").strip()
                if fn and activity_type:
                    activity_id = os.path.basename(fn).split(".")[0]
                    # Activity Name is the real gravel/mtb signal — Strava
                    # collapses cycling to "Ride" (→ road), the name keyword
                    # (~165 "gravel" + ~60 "vtt/mtb" in Paul's export) is
                    # what upgrades it. The classifier returns None for
                    # out-of-scope types → record _SKIP so we don't import
                    # ski/virtual as road.
                    activity_name = row.get("Activity Name") or ""
                    sport = classify_sport(activity_type, activity_name)
                    sport_map[activity_id] = sport if sport is not None else _SKIP
        log.info("Loaded %d sport mappings from activities.csv", len(sport_map))

    # List all activity files
    files = sorted(os.listdir(activities_dir))
    gpx_files = [f for f in files if f.endswith(".gpx") or f.endswith(".gpx.gz")]
    fit_files = [f for f in files if f.endswith(".fit") or f.endswith(".fit.gz")]
    log.info("Found %d GPX + %d FIT = %d total activity files",
             len(gpx_files), len(fit_files), len(gpx_files) + len(fit_files))

    if args.dry_run:
        sports = {}
        for f in gpx_files + fit_files:
            aid = f.split(".")[0]
            if args.sport:
                s = args.sport
            else:
                csv_sport = sport_map.get(aid)
                if csv_sport == _SKIP:
                    s = "(skipped: out-of-scope)"
                elif csv_sport is not None:
                    s = csv_sport
                else:
                    s = "road"  # FIT sport may refine at import; CLI default
            sports[s] = sports.get(s, 0) + 1
        log.info("Sports breakdown: %s", sports)
        return

    # Skip the per-upload PMTiles rebuild during the loop (saves repeated
    # heavy display-artefact builds on bulk imports). We rebuild once at the
    # end. Set BEFORE importing ingest. (The heat_edges_display matview was
    # dropped June 2026 — the live MVT endpoint now aggregates heat_edges
    # directly, so there is no matview to refresh anymore.)
    os.environ.setdefault("SKIP_PMTILES_REBUILD", "true")
    # Skip per-activity canonical-merge bbox query — saves ~30% per
    # activity for dense areas. ON CONFLICT in the UPSERT still
    # deduplicates by edge_key, and the projection fix already
    # collapses co-trail edges at the 5dp grid.
    os.environ.setdefault("SKIP_CANONICAL_MERGE", "true")

    # Import
    from app.db.session import SessionLocal
    from app.services.gpx import parse_gpx
    from app.services.ingest import (
        ingest_activity,
        retry_on_deadlock,
    )
    from app.services.provenance import COMMUNITY_SOURCE

    # Get user ID
    user_id = args.user_id
    if not user_id:
        db = SessionLocal()
        row = db.execute(__import__("sqlalchemy").text("SELECT id FROM users LIMIT 1")).fetchone()
        db.close()
        if not row:
            log.error("No users in DB. Create a user first.")
            sys.exit(1)
        user_id = str(row[0])
        log.info("Using user: %s", user_id)

    all_files = gpx_files + fit_files
    total = len(all_files)
    t0 = time.time()
    counters = {"imported": 0, "skipped": 0, "failed": 0}
    counters_lock = __import__("threading").Lock()

    def _process_file(idx_filename: tuple[int, str]) -> None:
        i, filename = idx_filename
        filepath = os.path.join(activities_dir, filename)
        activity_id = filename.split(".")[0]
        # Sport cascade: explicit --sport override wins; else the CSV
        # (type + name via the SSOT classifier); else fall through to the
        # FIT device sport at parse time; else default road. A CSV _SKIP
        # row (out-of-scope: Yoga/Virtual/Ski/...) drops the file unless
        # --sport forces a sport.
        csv_sport = sport_map.get(activity_id)
        if not args.sport and csv_sport == _SKIP:
            with counters_lock:
                counters["skipped"] += 1
            return
        if args.sport:
            sport: str | None = args.sport
        elif csv_sport not in (None, _SKIP):
            sport = csv_sport
        else:
            sport = None  # let FIT sport win below, else default road
        try:
            # Read + decompress
            if filename.endswith(".gz"):
                with gzip.open(filepath, "rb") as f:
                    content = f.read()
            else:
                with open(filepath, "rb") as f:
                    content = f.read()
            # Parse
            if ".gpx" in filename:
                parsed = parse_gpx(content)
            elif ".fit" in filename:
                from app.services.fit_parser import parse_fit
                parsed = parse_fit(content)
                # FIT device sport refines only when the CSV/name didn't
                # already resolve a sport. `_infer_sport` now returns None
                # when the device session has no sport field.
                if sport is None and parsed.get("sport"):
                    sport = parsed["sport"]
            else:
                return
            if sport is None:
                sport = "road"  # last-resort default for the CLI path
            activity_data = {
                "provider": "strava_export",
                "provider_activity_id": activity_id,
                # The user's OWN downloaded Strava archive, uploaded by them
                # → community-eligible (same basis as the archive-intake path).
                "source": COMMUNITY_SOURCE,
                "sport": sport,
                "name": parsed.get("name"),
                "geometry_geojson": parsed.get("geometry_geojson"),
                "distance_m": parsed.get("distance_m"),
                "elevation_gain_m": parsed.get("elevation_gain_m"),
                "file_hash": parsed.get("file_hash"),
                "activity_date": parsed.get("activity_date"),
            }
            # retry_on_deadlock: parallel UPSERTs on overlapping
            # heat_edges bboxes can deadlock; the lock-loser retries.
            result = retry_on_deadlock(
                ingest_activity,
                user_id=user_id,
                activity_data=activity_data,
                contribute_heatmap=True,
                skip_heat_computation=True,  # Compute heatmap in batch after
            )
            with counters_lock:
                if result["status"] == "created":
                    counters["imported"] += 1
                else:
                    counters["skipped"] += 1
        except Exception as e:
            with counters_lock:
                counters["failed"] += 1
                if counters["failed"] <= 10:
                    log.warning("Failed %s: %s", filename, e)
        if (i + 1) % args.batch == 0:
            with counters_lock:
                imp = counters["imported"]
                skp = counters["skipped"]
                fld = counters["failed"]
            elapsed = time.time() - t0
            rate = (imp + skp + fld) / elapsed if elapsed > 0 else 0
            eta = (total - imp - skp - fld) / rate if rate > 0 else 0
            log.info(
                "Progress: %d done (%d imp, %d skp, %d fld) — %.1f/s — ETA %.0fs",
                imp + skp + fld, imp, skp, fld, rate, eta,
            )

    if args.workers > 1:
        from concurrent.futures import ThreadPoolExecutor
        log.info("Using %d worker threads", args.workers)
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(_process_file, enumerate(all_files)))
    else:
        for idx_filename in enumerate(all_files):
            _process_file(idx_filename)
    imported = counters["imported"]
    skipped = counters["skipped"]
    failed = counters["failed"]
    elapsed = time.time() - t0
    log.info("Done: %d imported, %d skipped, %d failed in %.0fs (%.1f files/s)",
             imported, skipped, failed, elapsed, total / elapsed if elapsed > 0 else 0)
    log.info("Heatmap computation was skipped — run rebuild_heatmap job to generate edges.")
    log.info(
        "Display artefacts were skipped during the loop. Rebuild the heatmap "
        "PMTiles once with `make pmtiles` (or `python -m app.jobs.build_pmtiles`) "
        "after the heat_edges are generated."
    )


if __name__ == "__main__":
    main()
