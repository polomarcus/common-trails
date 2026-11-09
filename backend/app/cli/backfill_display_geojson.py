"""One-shot backfill of activities.display_geojson (migration 0067).

Populates the compact personal-map geometry (gap-split → Douglas-Peucker 2 m →
5-decimal coords, app.services.display_geometry) for every activity whose cache
is missing. New ingests write it inline (ingest._display_geojson); this catches
the pre-0067 corpus. Until a row is backfilled, GET /me/activities serves the
pre-cache full-precision (gap-split) geometry — correct, just heavier.

Run (local dev):        docker compose exec backend python -m app.cli.backfill_display_geojson
Run (prod):             see docs/runbooks/display-geometry-cache.md (Cloud Run job --args override)
Options: --limit N (cap this run), --pace S (sleep between commit batches),
         --user-id U (one user only),
         --recompute (also rewrite rows already cached — after an algorithm change).

Design (same shape as app.cli.backfill_display_coords):
- READ on a streaming session (server-side cursor, small ``yield_per`` — a
  single raw ride can be >1 MB of JSON, the corpus never sits in RAM), WRITE +
  batched commits on a SECOND session: committing on the reader's connection
  would kill its named cursor mid-stream.
- Idempotent + resumable: rows already cached are excluded by the WHERE, so a
  rerun only touches what's left.
- Best-effort per row: an unparseable geometry just stays NULL (the endpoint's
  on-the-fly fallback treats it identically).
- TRACE INTEGRITY (Crouzet): only ``display_geojson`` is written;
  ``geometry_geojson`` is read, never modified.
"""

import argparse
import logging
import time

from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.display_geometry import compact_display_geojson

log = logging.getLogger(__name__)

_BATCH = 100
_YIELD_PER = 20


def backfill(
    limit: int | None = None,
    pace_seconds: float = 0.0,
    recompute: bool = False,
    user_id: str | None = None,
) -> dict:
    where = "geometry_geojson IS NOT NULL AND geometry_geojson <> ''"
    params: dict = {}
    if not recompute:
        where += " AND display_geojson IS NULL"
    if user_id:
        where += " AND user_id = :uid"
        params["uid"] = user_id
    limit_sql = f"LIMIT {int(limit)}" if limit else ""

    reader = SessionLocal()
    writer = SessionLocal()
    done = skipped = pending = 0
    t0 = time.time()
    try:
        rows = reader.execute(
            sa_text(
                f"SELECT id, geometry_geojson, md5(geometry_geojson) FROM activities WHERE {where} ORDER BY id {limit_sql}"
            ).execution_options(stream_results=True, yield_per=_YIELD_PER),
            params,
        )
        for aid, geojson_str, src_md5 in rows:
            compact = compact_display_geojson(geojson_str)
            if compact is None:
                skipped += 1
                continue
            # Guards against a concurrent writer (Strava stream upgrade, GPX
            # re-upload) that replaced the geometry + cache after our snapshot:
            # never overwrite a fresher cache with one derived from the old
            # geometry (later runs would skip the row → stale forever).
            writer.execute(
                sa_text(
                    "UPDATE activities SET display_geojson = :g WHERE id = :id AND md5(geometry_geojson) = :h"
                    + ("" if recompute else " AND display_geojson IS NULL")
                ),
                {"g": compact, "id": aid, "h": src_md5},
            )
            done += 1
            pending += 1
            if pending >= _BATCH:
                writer.commit()
                pending = 0
                log.info("backfilled %d (skipped %d) in %.0fs", done, skipped, time.time() - t0)
                if pace_seconds > 0:
                    time.sleep(pace_seconds)
        writer.commit()
    finally:
        reader.close()
        writer.close()
    summary = {"backfilled": done, "skipped": skipped, "seconds": round(time.time() - t0)}
    log.info("display-geojson backfill done: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Max rows this run (default: all uncached rows).")
    parser.add_argument(
        "--pace",
        type=float,
        default=0.0,
        help="Seconds to sleep between commit batches (DB courtesy on a shared instance).",
    )
    parser.add_argument("--recompute", action="store_true", help="Also rewrite rows that already have a cache.")
    parser.add_argument("--user-id", default=None, help="Only this user's activities (default: everyone).")
    args = parser.parse_args()
    backfill(limit=args.limit, pace_seconds=args.pace, recompute=args.recompute, user_id=args.user_id)
