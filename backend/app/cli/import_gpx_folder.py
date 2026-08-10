"""Bulk-import GPX files from a local folder into the database.

Walks a folder (recursive) for .gpx files, parses each, and ingests
them as activities for a specified user. Uses file_hash for dedup —
running twice is idempotent (same file → skipped on 2nd run).

Usage:
    # From host machine (after starting docker compose):
    docker compose exec -T backend python -m app.cli.import_gpx_folder \\
        --folder /data/my-gpx-folder \\
        --user-email admin@admin \\
        --sport gravel

    # Mount your local GPX folder into the backend container:
    docker compose run --rm \\
        -v /Users/paul/gpx-export:/data/gpx-import \\
        backend python -m app.cli.import_gpx_folder \\
        --folder /data/gpx-import \\
        --user-email admin@admin \\
        --sport offroad

Exit codes: 0 success, 1 fatal error, 2 no files found.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("import_gpx_folder")


def _find_user_id(email_or_uuid: str) -> str | None:
    """Resolve a user_id.

    If the input looks like a UUID, look up the users table by id (return
    as-is if found, None otherwise). Otherwise look up by email. The
    UUID-shape probe is a length+hyphen pattern, not a strict parse, so
    we accept e.g. ``19ec59b1-247d-413b-ab71-a5e6e8f7ee02``.
    """
    from sqlalchemy import text as sa_text

    from app.db.session import SessionLocal

    looks_like_uuid = (
        len(email_or_uuid) == 36
        and email_or_uuid.count("-") == 4
        and "@" not in email_or_uuid
    )

    db = SessionLocal()
    try:
        if looks_like_uuid:
            row = db.execute(
                sa_text("SELECT id FROM users WHERE id = :uid LIMIT 1"),
                {"uid": email_or_uuid},
            ).fetchone()
        else:
            row = db.execute(
                sa_text("SELECT id FROM users WHERE email = :email LIMIT 1"),
                {"email": email_or_uuid},
            ).fetchone()
        return str(row[0]) if row else None
    finally:
        db.close()


def _walk_gpx(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    if folder.is_file() and folder.suffix.lower() == ".gpx":
        return [folder]
    return sorted(folder.rglob("*.gpx"))


def _ingest_one(user_id: str, path: Path, sport: str, contribute: bool) -> tuple[str, str | None]:
    """Returns (status, message). status in {'created', 'duplicate', 'error', 'skipped'}."""
    from app.config import VALID_SPORTS
    from app.services import gpx as gpx_service
    from app.services import ingest as ingest_service
    from app.services.gpx import MAX_GPX_COORDS, MAX_GPX_SIZE

    # Byte-level cap MUST come BEFORE parse_gpx (audit 2026-05-27 S2.4
    # + PR #347 review #12). Parsing a 5M-coord file allocates ~300-500
    # MB BEFORE the coord-count check could fire; that's how a single
    # file OOMs the worker even with the coord cap in place. Same
    # guard as `gpx_upload.py:67`.
    try:
        size = path.stat().st_size
    except OSError as e:
        return ("error", f"stat failed: {e}")
    if size > MAX_GPX_SIZE:
        return ("error", f"file too large ({size} bytes > {MAX_GPX_SIZE})")

    try:
        content = path.read_bytes()
        parsed = gpx_service.parse_gpx(content)
    except Exception as e:
        return ("error", f"parse failed: {e}")

    # Skip-out-of-scope short-circuit: bulk-folder operators run this
    # with a single `--sport` flag, so a yoga.gpx slipped into the
    # folder would land on the operator-picked heatmap. Same rule as
    # `/gpx/upload` + `/imports/files` — see [[feedback_skip_beats_pollute_heatmap]].
    if parsed.get("skip_reason"):
        return ("skipped", parsed["skip_reason"])

    # Coord-cap defence. `parse_gpx` already counted; reuse that
    # instead of re-parsing the multi-MB GeoJSON string here (PR #347
    # review #13). Same guard as `gpx_upload.py:83`.
    coord_count = parsed.get("coord_count", 0)
    if coord_count > MAX_GPX_COORDS:
        return (
            "error",
            f"{coord_count} coords > {MAX_GPX_COORDS}; split or simplify the trace",
        )

    # Sport cascade (audit 2026-05-27 S2.2). Prefer the GPX `<trk><type>`
    # when granular enough to map (Garmin Connect emits `mountain_biking`,
    # `gravel_cycling`, etc. → `classify_sport_from_gpx_type`). The
    # operator's `--sport` flag is the fallback for files whose type is
    # absent or collapsed to generic `cycling` (Strava export). Same
    # cascade shape as `imports.py:167-173`, scoped down because the CLI
    # has no FIT or CSV inputs.
    sport_from_gpx = parsed.get("sport_from_gpx")
    effective_sport = sport_from_gpx if sport_from_gpx in VALID_SPORTS else sport

    from app.services.provenance import COMMUNITY_SOURCE
    activity_data = {
        "provider": "file",
        "provider_activity_id": None,
        # Operator uploading the user's own GPX folder → community-eligible.
        "source": COMMUNITY_SOURCE,
        "sport": effective_sport,
        "name": parsed.get("name") or path.stem,
        "geometry_geojson": parsed.get("geometry_geojson"),
        "distance_m": parsed.get("distance_m"),
        "elevation_gain_m": parsed.get("elevation_gain_m"),
        "file_hash": parsed.get("file_hash"),
        "activity_date": parsed.get("activity_date"),
        "geometry_source": "stream",
    }

    try:
        result = ingest_service.ingest_activity(
            user_id=user_id,
            activity_data=activity_data,
            contribute_heatmap=contribute,
            skip_heat_computation=False,
        )
    except Exception as e:
        return ("error", f"ingest failed: {e}")

    return (result.get("status", "unknown"), None)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.import_gpx_folder",
        description="Bulk-import a folder of GPX files.",
    )
    parser.add_argument("--folder", required=True, help="Path to folder (or single .gpx file)")
    parser.add_argument("--user-email", required=True, help="Owner email (must exist in users table)")
    parser.add_argument(
        "--sport", default="gravel",
        choices=["road", "gravel", "mtb", "offroad", "running"],
        help="Sport to tag the imported activities with (default: gravel)",
    )
    parser.add_argument(
        "--no-contribute-heatmap", action="store_true",
        help="Skip heatmap contribution (private import only)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List files that would be imported without actually ingesting",
    )
    args = parser.parse_args()

    folder = Path(args.folder)
    files = _walk_gpx(folder)
    if not files:
        logger.error("No .gpx files found at %s", folder)
        return 2

    logger.info("Found %d .gpx files in %s", len(files), folder)
    if args.dry_run:
        for p in files:
            logger.info("  would import: %s", p)
        return 0

    user_id = _find_user_id(args.user_email)
    if not user_id:
        logger.error("User not found: %s", args.user_email)
        return 1
    logger.info("Importing as user: %s (%s)", args.user_email, user_id)

    contribute = not args.no_contribute_heatmap
    t0 = time.monotonic()
    stats = {"created": 0, "duplicate": 0, "error": 0, "skipped": 0, "other": 0}
    for i, path in enumerate(files, 1):
        status, msg = _ingest_one(user_id, path, args.sport, contribute)
        stats[status if status in stats else "other"] = stats.get(status if status in stats else "other", 0) + 1
        prefix = {"created": "✓", "duplicate": "·", "error": "✗", "skipped": "⊘"}.get(status, "?")
        logger.info("  %s [%d/%d] %s%s", prefix, i, len(files), path.name, f" — {msg}" if msg else "")

    elapsed = time.monotonic() - t0
    logger.info(
        "Done in %.1fs — created=%d duplicate=%d error=%d skipped=%d other=%d",
        elapsed, stats["created"], stats["duplicate"], stats["error"], stats["skipped"], stats["other"],
    )
    return 0 if stats["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
