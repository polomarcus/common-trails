"""Find (and optionally delete) activities that were ingested from the
``routes/`` folder of a Strava bulk export.

Before the fix in ``archive_intake._plan_zip``, every ``.gpx`` of a Strava
export was ingested — including ``routes/*.gpx`` (PLANNED routes: no
``<time>``, no activities.csv row). Those that carried a usable sport landed in
the community heatmap as if they had been ridden. This CLI lists them so prod
can be cleaned. DRY-RUN by default; ``--apply`` deletes exactly the listed
activities through the app's normal per-activity deletion path
(``activity_deletion.delete_user_activity``: heat contributor rows, cascaded
cells/photos, agg refresh, rebuild ping).

What is persisted about an archive member's origin? An ``activities`` row does
NOT keep the member path or the archive id (``provider='file'``,
``source='manual_upload'``, ``provider_activity_id`` NULL). Identification
therefore uses, in decreasing reliability:

1. ``member-row`` — the legacy per-member queue (``pending_archive_files``)
   keeps ``original_filename`` AND the created ``activity_id``: a member whose
   path is ``…/routes/<file>`` is an exact match.
2. ``archive-hash`` — whole-archive uploads (``pending_archives``) whose .zip
   is still in storage (retained ~30 days): the archive is re-opened, every
   member the importer now EXCLUDES (routes/, files activities.csv doesn't
   list) is hashed exactly like the importer hashes it, and matched against
   ``activities.file_hash`` of that archive's owner. Exact content match.
3. ``heuristic`` — ONLY with ``--include-heuristic``: ``provider='file'``,
   ``source='manual_upload'``, ``activity_date IS NULL`` (routes carry no
   timestamp). NOT reliable — a legit timestamp-less GPX matches too — so it
   is opt-in and should be reviewed by hand before ``--apply``.

Run (local dev):  docker compose exec backend python -m app.cli.purge_strava_route_activities
Options: --user-id U (one user), --include-heuristic, --apply.
"""

import argparse
import logging
import re

from sqlalchemy import text as sa_text

from app.db.session import SessionLocal

log = logging.getLogger(__name__)

# ``routes/<file>`` at the archive root or under a re-zipped top folder.
_ROUTE_MEMBER_RE = r"(^|/)routes/[^/]+$"


def _member_row_matches(db, user_id: str | None) -> list[dict]:
    params: dict = {"re": _ROUTE_MEMBER_RE}
    where = ""
    if user_id:
        where = " AND paf.user_id = :uid"
        params["uid"] = user_id
    rows = (
        db.execute(
            sa_text(f"""
        SELECT a.id AS activity_id, a.user_id, a.name, a.sport, a.activity_date
        FROM pending_archive_files paf
        JOIN activities a ON a.id::text = paf.activity_id::text
                         AND a.user_id = paf.user_id
        WHERE paf.activity_id IS NOT NULL
          AND lower(paf.original_filename) ~ :re{where}
    """),
            params,
        )
        .mappings()
        .all()
    )
    return [dict(r, how="member-row") for r in rows]


def _excluded_member_hashes(zf) -> set[str]:
    """sha256 (as the importer computes ``file_hash``) of every member the
    importer now drops as "not an activity of this Strava export"."""
    from app.jobs.ingest_pending_archives import _parse_member
    from app.services import archive_intake

    hashes: set[str] = set()
    plan = archive_intake._plan_zip(zf)
    for info, lookup in plan.selected:
        if lookup is not None:
            continue
        try:
            parsed = _parse_member(zf.read(info.filename), info.filename)
        except Exception:  # noqa: BLE001 — unparseable → was never ingested
            continue
        if parsed.get("file_hash"):
            hashes.add(parsed["file_hash"])
    return hashes


def _archive_hash_matches(db, user_id: str | None) -> tuple[list[dict], int]:
    """Returns (matches, archives_unavailable)."""
    from app.services import archive_intake

    params: dict = {}
    where = ""
    if user_id:
        where = " AND user_id = :uid"
        params["uid"] = user_id
    archives = (
        db.execute(
            sa_text(f"""
        SELECT id, user_id, storage_backend, bucket_key
        FROM pending_archives
        WHERE status IN ('done', 'failed', 'processing'){where}
        ORDER BY created_at
    """),
            params,
        )
        .mappings()
        .all()
    )
    matches: list[dict] = []
    unavailable = 0
    for arch in archives:
        try:
            if not archive_intake.archive_exists(arch["storage_backend"], arch["bucket_key"]):
                unavailable += 1
                continue
            with archive_intake.open_archive_zip(arch["storage_backend"], arch["bucket_key"]) as zf:
                hashes = _excluded_member_hashes(zf)
        except Exception:  # noqa: BLE001 — one unreadable archive ≠ abort
            log.warning("archive %s unreadable — skipped", arch["id"], exc_info=True)
            unavailable += 1
            continue
        if not hashes:
            continue
        rows = (
            db.execute(
                sa_text("""
            SELECT id AS activity_id, user_id, name, sport, activity_date
            FROM activities
            WHERE user_id = :uid AND provider = 'file' AND source = 'manual_upload'
              AND file_hash = ANY(:hashes)
        """),
                {"uid": arch["user_id"], "hashes": sorted(hashes)},
            )
            .mappings()
            .all()
        )
        matches.extend(dict(r, how="archive-hash") for r in rows)
    return matches, unavailable


def _heuristic_matches(db, user_id: str | None) -> list[dict]:
    params: dict = {}
    where = ""
    if user_id:
        where = " AND user_id = :uid"
        params["uid"] = user_id
    rows = (
        db.execute(
            sa_text(f"""
        SELECT id AS activity_id, user_id, name, sport, activity_date
        FROM activities
        WHERE provider = 'file' AND source = 'manual_upload'
          AND activity_date IS NULL{where}
        ORDER BY created_at
    """),
            params,
        )
        .mappings()
        .all()
    )
    return [dict(r, how="heuristic") for r in rows]


def find_candidates(*, user_id: str | None = None, include_heuristic: bool = False) -> dict:
    """Collect candidate activities (deduplicated by id, most reliable
    method wins). Read-only."""
    db = SessionLocal()
    try:
        found: dict[str, dict] = {}
        for row in _member_row_matches(db, user_id):
            found.setdefault(str(row["activity_id"]), row)
        archive_rows, unavailable = _archive_hash_matches(db, user_id)
        for row in archive_rows:
            found.setdefault(str(row["activity_id"]), row)
        if include_heuristic:
            for row in _heuristic_matches(db, user_id):
                found.setdefault(str(row["activity_id"]), row)
    finally:
        db.close()
    return {"candidates": list(found.values()), "archives_unavailable": unavailable}


def run(*, user_id: str | None = None, include_heuristic: bool = False, apply: bool = False) -> dict:
    result = find_candidates(user_id=user_id, include_heuristic=include_heuristic)
    candidates = result["candidates"]
    for c in candidates:
        log.info(
            "%s activity=%s user=%s sport=%s date=%s name=%r",
            c["how"],
            c["activity_id"],
            c["user_id"],
            c["sport"],
            c["activity_date"],
            (c["name"] or "")[:60],
        )
    by_how: dict[str, int] = {}
    for c in candidates:
        by_how[c["how"]] = by_how.get(c["how"], 0) + 1
    summary = {
        "candidates": len(candidates),
        "by_method": by_how,
        "archives_unavailable": result["archives_unavailable"],
        "deleted": 0,
        "applied": apply,
    }
    if apply:
        from app.services.activity_deletion import delete_user_activity

        db = SessionLocal()
        try:
            for c in candidates:
                if delete_user_activity(db, str(c["user_id"]), str(c["activity_id"])):
                    summary["deleted"] += 1
        finally:
            db.close()
    else:
        log.info("[dry-run] nothing deleted — re-run with --apply to delete the %d listed", len(candidates))
    log.info("strava-route purge: %s", summary)
    return summary


def _valid_uuid(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", value):
        raise argparse.ArgumentTypeError("expected a UUID")
    return value


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user-id", type=_valid_uuid, default=None, help="Only this user (default: everyone).")
    parser.add_argument(
        "--include-heuristic",
        action="store_true",
        help="Also list manual_upload activities with no date (UNRELIABLE — review before --apply).",
    )
    parser.add_argument("--apply", action="store_true", help="Delete the listed activities (default: dry-run).")
    args = parser.parse_args()
    run(user_id=args.user_id, include_heuristic=args.include_heuristic, apply=args.apply)
