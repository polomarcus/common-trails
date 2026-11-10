"""Operate the private per-user "Ma heatmap" files (docs/ma-heatmap-fichier-prive.md).

Subcommands:

  backfill       Build the file of every user that has map activities and no
                 fresh file (idempotent + resumable: fresh files are skipped).
  delete         GDPR / support: delete ALL objects of one user + the row.
  sweep-orphans  Delete files whose user no longer exists (missed GDPR step).

Run (local dev):        docker compose exec backend python -m app.cli.my_heatmap_files backfill
Run (prod, via proxy):  DATABASE_URL=... MY_HEATMAP_BUCKET=common-trails-my-heatmap-prod \\
                        MY_HEATMAP_FILES_ENABLED=true python -m app.cli.my_heatmap_files backfill --pace 1

Backfill options: --user-id U (one user), --limit N (cap users this run),
--pace S (sleep between users — DB courtesy on db-f1-micro), --force (rebuild
even fresh files, e.g. after a tile-format change). Users are processed
lightest first so the bulk of accounts is served early.

Same shape as app.cli.backfill_display_geojson: one user at a time, each
build streams its rows (O(window) memory), failures are recorded per user
(``my_heatmap_files.last_error``) and never abort the run. The flag
(MY_HEATMAP_FILES_ENABLED) must be on for the build to write anything — the
allow-list (MY_HEATMAP_FILES_USERS) is honoured too.
"""

import argparse
import logging
import time

from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services import my_heatmap_files as mhf

log = logging.getLogger(__name__)


def _candidates(user_id: str | None, force: bool, limit: int | None) -> list[str]:
    where = "a.geometry_geojson IS NOT NULL AND a.geometry_geojson <> ''"
    params: dict = {}
    if user_id:
        where += " AND a.user_id = :uid"
        params["uid"] = user_id
    fresh = "" if force else (
        " HAVING NOT bool_or(f.version IS NOT NULL AND f.built_rev = f.data_rev) "
        "OR bool_or(f.user_id IS NULL)"
    )
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    db = SessionLocal()
    try:
        rows = db.execute(
            sa_text(
                f"""
                SELECT a.user_id FROM activities a
                LEFT JOIN my_heatmap_files f ON f.user_id = a.user_id
                WHERE {where}
                GROUP BY a.user_id{fresh}
                ORDER BY count(*) ASC, a.user_id {limit_sql}
                """
            ),
            params,
        )
        return [str(r[0]) for r in rows]
    finally:
        db.close()


def backfill(
    user_id: str | None = None,
    limit: int | None = None,
    pace_seconds: float = 0.0,
    force: bool = False,
) -> dict:
    if not mhf.flag_enabled() or mhf.get_store() is None:
        raise SystemExit(
            "MY_HEATMAP_FILES_ENABLED=true and MY_HEATMAP_BUCKET (or MY_HEATMAP_LOCAL_DIR) are required"
        )
    users = _candidates(user_id, force, limit)
    counts: dict[str, int] = {}
    t0 = time.time()
    for i, uid in enumerate(users, 1):
        if not mhf.is_enabled_for(uid):
            counts["not_allowed"] = counts.get("not_allowed", 0) + 1
            continue
        res = mhf.build_for_user(uid, force=force)
        counts[res["status"]] = counts.get(res["status"], 0) + 1
        log.info("[%d/%d] user=%s → %s", i, len(users), uid, res)
        if pace_seconds > 0:
            time.sleep(pace_seconds)
    summary = {"users": len(users), **counts, "seconds": round(time.time() - t0)}
    log.info("my-heatmap backfill done: %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="Build missing/stale files.")
    b.add_argument("--user-id", default=None)
    b.add_argument("--limit", type=int, default=None, help="Max users this run.")
    b.add_argument("--pace", type=float, default=0.0, help="Seconds to sleep between users.")
    b.add_argument("--force", action="store_true", help="Rebuild fresh files too.")
    d = sub.add_parser("delete", help="Delete one user's objects + row (GDPR).")
    d.add_argument("--user-id", required=True)
    s = sub.add_parser("sweep-orphans", help="Delete files of users that no longer exist.")
    s.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.cmd == "backfill":
        backfill(user_id=args.user_id, limit=args.limit, pace_seconds=args.pace, force=args.force)
    elif args.cmd == "delete":
        n = mhf.delete_for_user(args.user_id)
        log.info("deleted %d object(s) + row for user %s", n, args.user_id)
    else:
        orphans = mhf.sweep_orphans(dry_run=args.dry_run)
        log.info("%s %d orphan user file set(s): %s", "would delete" if args.dry_run else "deleted",
                 len(orphans), orphans)
