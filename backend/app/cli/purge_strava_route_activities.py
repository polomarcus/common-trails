"""Find (and optionally delete) activities that were ingested from the
``routes/`` folder of a Strava bulk export.

Before the fix in ``archive_intake._plan_zip``, every ``.gpx`` of a Strava
export was ingested — including ``routes/*.gpx`` (PLANNED routes: no
``<time>``, no activities.csv row). Those that carried a usable sport landed in
the community heatmap as if they had been ridden.

This touches user data, so the bar is ZERO false positives. Every candidate
must satisfy ALL of these hard guards:

* ``provider='file'``, ``source='manual_upload'`` (archive provenance), owned
  by the archive's uploader;
* ``activity_date IS NULL`` — a route has no ``<time>``; a real ride does;
* positively identified by ONE of:

  1. ``member-row`` — the legacy per-member queue (``pending_archive_files``)
     keeps ``original_filename`` + the created ``activity_id``. The member path
     is ``<P>routes/<file>`` AND the same upload has a sibling under
     ``<P>activities/`` whose queued sport came from activities.csv (so ``<P>``
     really is the root of a Strava export, not a hand-made "routes" folder).
  2. ``archive-hash`` — whole-archive uploads (``pending_archives``) whose .zip
     is still in storage: the archive is re-opened; it must be a Strava export
     (activities.csv) without nested zips; ONLY its ``routes/`` members are
     hashed exactly like the importer hashes them (``file_hash``), MINUS the
     hashes of every other ingestible member (a duplicated export folder can't
     turn a real ride into a "route").

There is no heuristic mode: an activity without one of these proofs is never
listed. An activity row keeps neither the member path nor the archive id, so
an ingested route whose archive is gone (storage retention) and that did not
go through the per-member queue cannot be identified — it is reported in
``archives_unavailable``, not guessed.

Workflow (review before deleting):

    python -m app.cli.purge_strava_route_activities --out ids.json      # dry-run
    python -m app.cli.purge_strava_route_activities --apply --ids-file ids.json

``--apply`` deletes ONLY ids from the reviewed file that are STILL identified
by a fresh run of the guards above (anything else is skipped + reported),
through ``activity_deletion.delete_user_activity`` (heat contributor rows,
cascaded cells, agg refresh) with ONE build-pmtiles ping at the end.
"""

import argparse
import hashlib
import json
import logging
import re

from sqlalchemy import text as sa_text

from app.db.session import SessionLocal

log = logging.getLogger(__name__)

# ``<prefix>routes/<file>`` (prefix = "" or a re-zipped top folder).
_ROUTE_MEMBER_RE = r"^(.*/)?routes/[^/]+$"

# Hard guards shared by every identification method (alias ``a`` = activities).
_GUARDS_SQL = "a.provider = 'file' AND a.source = 'manual_upload' AND a.activity_date IS NULL"


def _member_row_matches(db, user_ids: list[str] | None) -> list[dict]:
    params: dict = {"re": _ROUTE_MEMBER_RE}
    where = ""
    if user_ids is not None:
        where = " AND paf.user_id = ANY(:uids)"
        params["uids"] = user_ids
    rows = (
        db.execute(
            sa_text(f"""
        SELECT a.id AS activity_id, a.user_id, a.name, a.sport, a.activity_date
        FROM pending_archive_files paf
        JOIN activities a ON a.id::text = paf.activity_id::text
                         AND a.user_id = paf.user_id
        WHERE paf.activity_id IS NOT NULL
          AND lower(paf.original_filename) ~ :re
          AND {_GUARDS_SQL}
          AND EXISTS (
              -- a sibling of the same upload under <P>activities/ whose sport
              -- came from activities.csv → <P> is a Strava export root.
              SELECT 1 FROM pending_archive_files s
              WHERE s.user_id = paf.user_id
                AND s.consent_id IS NOT DISTINCT FROM paf.consent_id
                AND s.id <> paf.id
                AND left(lower(s.original_filename),
                         length(regexp_replace(lower(paf.original_filename), 'routes/[^/]+$', '')) + 11)
                    = regexp_replace(lower(paf.original_filename), 'routes/[^/]+$', '') || 'activities/'
                AND s.sport IS NOT NULL
                AND s.sport <> 'unknown'
                AND s.sport NOT LIKE 'pick:%'
          ){where}
    """),
            params,
        )
        .mappings()
        .all()
    )
    return [dict(r, how="member-row") for r in rows]


def _member_hash(zf, name: str) -> str | None:
    """``file_hash`` the importer would compute for this member."""
    from app.jobs.ingest_pending_archives import decompress_member

    try:
        data, _low = decompress_member(zf.read(name), name)
    except Exception:  # noqa: BLE001 — unreadable → never ingested
        return None
    return hashlib.sha256(data).hexdigest()


def route_only_hashes(zf) -> set[str]:
    """Hashes of the ``routes/`` members of a Strava export, minus the hash of
    ANY other ingestible member of the archive. Empty for a non-Strava archive
    or one with nested zips (can't vouch for their content)."""
    from app.services import archive_intake

    plan = archive_intake._plan_zip(zf)
    if not plan.is_strava_export or plan.nested_zips:
        return set()
    routes: set[str] = set()
    others: set[str] = set()
    for info, _lookup, kind in plan.selected:
        h = _member_hash(zf, info.filename)
        if h is None:
            continue
        (routes if kind == archive_intake.MEMBER_ROUTE else others).add(h)
    return routes - others


def _archive_hash_matches(db, user_ids: list[str] | None) -> tuple[list[dict], int]:
    """Returns (matches, archives_unavailable)."""
    from app.services import archive_intake

    params: dict = {}
    where = ""
    if user_ids is not None:
        where = " AND user_id = ANY(:uids)"
        params["uids"] = user_ids
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
                hashes = route_only_hashes(zf)
        except Exception:  # noqa: BLE001 — one unreadable archive ≠ abort
            log.warning("archive %s unreadable — skipped", arch["id"], exc_info=True)
            unavailable += 1
            continue
        if not hashes:
            continue
        rows = (
            db.execute(
                sa_text(f"""
            SELECT a.id AS activity_id, a.user_id, a.name, a.sport, a.activity_date
            FROM activities a
            WHERE a.user_id = :uid AND {_GUARDS_SQL}
              AND a.file_hash = ANY(:hashes)
        """),
                {"uid": arch["user_id"], "hashes": sorted(hashes)},
            )
            .mappings()
            .all()
        )
        matches.extend(dict(r, how="archive-hash") for r in rows)
    return matches, unavailable


def find_candidates(*, user_ids: list[str] | None = None) -> dict:
    """Collect positively identified route activities (deduplicated by id).
    Read-only."""
    db = SessionLocal()
    try:
        found: dict[str, dict] = {}
        for row in _member_row_matches(db, user_ids):
            found.setdefault(str(row["activity_id"]), row)
        archive_rows, unavailable = _archive_hash_matches(db, user_ids)
        for row in archive_rows:
            found.setdefault(str(row["activity_id"]), row)
    finally:
        db.close()
    return {"candidates": list(found.values()), "archives_unavailable": unavailable}


def _log_candidates(candidates: list[dict]) -> None:
    for c in candidates:
        log.info(
            "%s activity=%s user=%s sport=%s name=%r",
            c["how"],
            c["activity_id"],
            c["user_id"],
            c["sport"],
            (c["name"] or "")[:60],
        )


def dry_run(*, user_id: str | None = None, out: str | None = None) -> dict:
    result = find_candidates(user_ids=[user_id] if user_id else None)
    candidates = result["candidates"]
    _log_candidates(candidates)
    by_how: dict[str, int] = {}
    for c in candidates:
        by_how[c["how"]] = by_how.get(c["how"], 0) + 1
    if out:
        with open(out, "w") as fh:
            json.dump(
                [
                    {"activity_id": str(c["activity_id"]), "user_id": str(c["user_id"]), "how": c["how"]}
                    for c in candidates
                ],
                fh,
                indent=1,
            )
    summary = {
        "candidates": len(candidates),
        "by_method": by_how,
        "archives_unavailable": result["archives_unavailable"],
        "out": out,
    }
    log.info("[dry-run] nothing deleted: %s", summary)
    return summary


def apply(*, ids_file: str) -> dict:
    """Delete the ids listed in ``ids_file`` (a dry-run ``--out``) that are
    STILL positively identified now; skip + report the others."""
    from app.services.activity_deletion import delete_user_activity

    with open(ids_file) as fh:
        listed = json.load(fh)
    wanted = {(str(e["activity_id"]), str(e["user_id"])) for e in listed}
    user_ids = sorted({uid for _aid, uid in wanted})
    fresh = find_candidates(user_ids=user_ids)["candidates"] if user_ids else []
    still = {(str(c["activity_id"]), str(c["user_id"])) for c in fresh}

    summary = {"listed": len(wanted), "deleted": 0, "skipped": [], "rebuild_triggered": False}
    db = SessionLocal()
    try:
        for aid, uid in sorted(wanted):
            if (aid, uid) not in still:
                summary["skipped"].append(aid)
                log.warning("skip %s: no longer satisfies the route guards", aid)
                continue
            if delete_user_activity(db, uid, aid, trigger_rebuild=False):
                summary["deleted"] += 1
            else:
                summary["skipped"].append(aid)
    finally:
        db.close()
    if summary["deleted"]:
        # ONE rebuild for the whole batch (not one Cloud Run build per row).
        try:
            from app.services.run_jobs import trigger_build_pmtiles_job

            summary["rebuild_triggered"] = bool(trigger_build_pmtiles_job())
        except Exception:  # noqa: BLE001
            log.warning("build-pmtiles trigger failed after purge", exc_info=True)
    log.info("strava-route purge applied: %s", summary)
    return summary


def _valid_uuid(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", value):
        raise argparse.ArgumentTypeError("expected a UUID")
    return value


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user-id", type=_valid_uuid, default=None, help="Dry-run: only this user.")
    parser.add_argument("--out", default=None, help="Dry-run: write the candidate list (JSON) here.")
    parser.add_argument("--apply", action="store_true", help="Delete the ids of --ids-file (re-checked).")
    parser.add_argument("--ids-file", default=None, help="With --apply: a reviewed dry-run --out file.")
    args = parser.parse_args()
    if args.apply:
        if not args.ids_file:
            parser.error("--apply requires --ids-file (a reviewed dry-run --out file)")
        apply(ids_file=args.ids_file)
    else:
        dry_run(user_id=args.user_id, out=args.out)
