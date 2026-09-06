"""One-shot backfill of activities.display_coords (migration 0065).

Populates the precomputed display-coords cache for every community-eligible
activity whose cache is missing OR stamped with a stale parameter fingerprint —
new ingests write it inline (ingest._display_coords_pair); this catches the
pre-0065 corpus and any param/algorithm change.

Run (local dev):        docker compose exec backend python -m app.cli.backfill_display_coords
Run (prod, via proxy):  DATABASE_URL=... python -m app.cli.backfill_display_coords

Design:
- READ on a streaming session (server-side cursor — never materialises the
  corpus), WRITE + batched commits on a SECOND session: committing on the
  reader's connection would kill its named cursor mid-stream.
- Idempotent + resumable: rows already stamped with the current fingerprint are
  excluded by the WHERE, so a rerun only touches what's left.
- Best-effort per row: an unparseable geometry just stays NULL (the build's
  live-pipeline fallback treats it identically).
"""
import argparse
import logging
import time

from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.provenance import community_eligible_conditions
from app.services.raw_trace_display import (
    display_coords_fingerprint,
    encode_display_coords,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_BATCH = 200


def backfill(limit: int | None = None, pace_seconds: float = 0.0) -> dict:
    fp = display_coords_fingerprint()
    conditions, params = community_eligible_conditions()
    conditions.append(
        "(display_coords IS NULL OR display_coords_params IS DISTINCT FROM :fp)")
    params["fp"] = fp
    where = " AND ".join(conditions)
    limit_sql = f"LIMIT {int(limit)}" if limit else ""

    reader = SessionLocal()
    writer = SessionLocal()
    done = skipped = 0
    t0 = time.time()
    try:
        rows = reader.execute(sa_text(
            f"SELECT id, geometry_geojson FROM activities WHERE {where} "
            f"ORDER BY id {limit_sql}"
        ), params).yield_per(50)
        for aid, geojson_str in rows:
            blob = encode_display_coords(geojson_str)
            if blob is None:
                skipped += 1
                continue
            writer.execute(sa_text(
                "UPDATE activities SET display_coords = :b, "
                "display_coords_params = :fp WHERE id = :id"
            ), {"b": blob, "fp": fp, "id": aid})
            done += 1
            if done % _BATCH == 0:
                writer.commit()
                log.info("backfilled %d (skipped %d) in %.0fs",
                         done, skipped, time.time() - t0)
                if pace_seconds > 0:
                    time.sleep(pace_seconds)
        writer.commit()
    finally:
        reader.close()
        writer.close()
    summary = {"backfilled": done, "skipped": skipped,
               "seconds": round(time.time() - t0), "fingerprint": fp}
    log.info("display-coords backfill done: %s", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="Max rows this run (default: all stale rows).")
    parser.add_argument("--pace", type=float, default=0.0,
                        help="Seconds to sleep between commit batches "
                             "(DB courtesy on a shared instance).")
    args = parser.parse_args()
    backfill(limit=args.limit, pace_seconds=args.pace)
