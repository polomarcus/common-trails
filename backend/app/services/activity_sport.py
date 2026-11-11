"""Correct the sport of a user's OWN activities after import.

A wrong sport at import (e.g. MTB rides classified as road) is not cosmetic:
the community heatmap is split by sport, so the ride pollutes the wrong layer.
This service lets the owner fix it, for one activity or a batch.

What a change touches:

1. ``activities.sport`` — the source of truth. The PMTiles build reads
   ``activities.sport`` live, so this is all the community layer needs once
   rebuilt.
2. Fires the debounced ``build-pmtiles`` rebuild (the same ping
   ``/imports/files`` uses) when at least one changed row is community-eligible
   — personal-only rows never reach the static display. Debounced because a
   user looping PATCH must not fire N expensive rebuild jobs.

Derived caches deliberately NOT touched: ``display_coords`` (fingerprint =
max-span / bbox / version) and ``display_geojson`` are pure functions of the
geometry, never of the sport. ``/me/stats`` is computed live in SQL (no cache).

``sport_is_default`` (migration 0071): TRUE when the sport is only the generic
default of an import ("Ride" → road with no finer signal). ANY owner action on
the sport — a change, or a re-PATCH of the same sport ("yes, it is road") —
clears it: the rider has now said what the activity is.
"""

import logging

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.db.models import Activity
from app.services.provenance import is_community_eligible

logger = logging.getLogger(__name__)


def _community_eligible(act: Activity) -> bool:
    """Python mirror of ``provenance.community_eligible_conditions``."""
    return is_community_eligible(
        source=act.source,
        contribute_heatmap=act.contribute_heatmap,
        has_geometry=bool(act.geometry_geojson),
        activity_date=act.activity_date,
    )


def change_activities_sport(
    db: Session, user_id: str, activity_ids: list[str], sport: str
) -> tuple[list[str], list[str]]:
    """Set ``sport`` on the activities of ``activity_ids`` OWNED by ``user_id``.

    Returns ``(found_ids, changed_ids)``: ``found_ids`` are the owned rows that
    exist (an id belonging to someone else is indistinguishable from a missing
    one — no existence leak); ``changed_ids`` is the subset whose sport actually
    differed (an idempotent re-PATCH changes nothing and triggers nothing).
    ``sport`` must already be validated against ``EDITABLE_SPORTS``.
    """
    if not activity_ids:
        return [], []
    rows = db.query(Activity).filter(Activity.user_id == user_id, Activity.id.in_(activity_ids)).all()
    found_ids = [a.id for a in rows]
    changed = [a for a in rows if a.sport != sport]
    # The owner has now stated the sport of every row found — changed or
    # confirmed: none of them is a "default" any more.
    confirmed = [a for a in rows if a.sport_is_default and a.sport == sport]
    for act in rows:
        if act.sport_is_default:
            act.sport_is_default = False
    if confirmed:
        _refresh_my_heatmap_file(db, user_id)
    if not changed:
        if confirmed:
            db.commit()
        return found_ids, []

    any_community = False
    for act in changed:
        old = act.sport
        act.sport = sport
        any_community = any_community or _community_eligible(act)
        logger.info("activity sport changed: id=%s user=%s %s -> %s", act.id, user_id, old, sport)
    changed_ids = [a.id for a in changed]
    db.commit()

    if any_community:
        _request_community_rebuild()

    return found_ids, changed_ids


def _refresh_my_heatmap_file(db: Session, user_id: str) -> None:
    """A flag-only change (sport confirmed, not changed) is invisible to the
    "Ma heatmap" staleness trigger — ``sport_is_default`` is deliberately NOT
    a watched column, so a mass backfill never rebuilds every file. But the
    file's summary carries the flag: bump THIS user's file by hand (same gated
    statement as the trigger) and schedule its rebuild. Staged on ``db``; the
    caller commits."""
    from app.services import my_heatmap_files

    db.execute(
        sa_text(
            """
            INSERT INTO my_heatmap_files (user_id, data_rev, dirty_at)
            SELECT :u, 1, now()
             WHERE COALESCE((SELECT enabled FROM my_heatmap_config WHERE id = 1), false)
            ON CONFLICT (user_id) DO UPDATE
               SET data_rev = my_heatmap_files.data_rev + 1, dirty_at = now()
            """
        ),
        {"u": user_id},
    )
    my_heatmap_files.mark_dirty(db, user_id)


# ── Default-sport activities ("Ride" → road with no finer signal) ───────────

def count_default_sport(db: Session, user_id: str) -> int:
    return int(
        db.execute(
            sa_text("SELECT COUNT(*) FROM activities WHERE user_id = :u AND sport_is_default"),
            {"u": user_id},
        ).scalar()
        or 0
    )


def list_default_sport(db: Session, user_id: str, limit: int) -> list[dict]:
    """The user's default-sport activities, newest first — no geometry read."""
    rows = db.execute(
        sa_text(
            """
            SELECT id, name, sport, distance_m, activity_date, created_at
              FROM activities
             WHERE user_id = :u AND sport_is_default
             ORDER BY COALESCE(activity_date, created_at) DESC NULLS LAST, id DESC
             LIMIT :lim
            """
        ),
        {"u": user_id, "lim": limit},
    ).fetchall()
    return [
        {
            "id": str(r[0]),
            "name": r[1] or "",
            "sport": r[2],
            "distance_m": r[3],
            "activity_date": (r[4] or r[5]).isoformat() if (r[4] or r[5]) else None,
        }
        for r in rows
    ]


def resolve_default_sport(db: Session, user_id: str, sport: str) -> tuple[int, list[str]]:
    """Apply ``sport`` to EVERY default-sport activity of ``user_id`` and clear
    their flag. Returns ``(matched, changed_ids)``.

    Nothing but ``activities.sport`` feeds the community layer, so the whole
    set — thousands of rows — is ONE statement in ONE transaction: no row is
    loaded, no geometry read. The "Ma heatmap" staleness is bumped once (by the
    statement-level trigger when a sport changed, by hand for a pure
    confirmation), its rebuild scheduled once, and ONE debounced community
    rebuild is requested if a changed row is community-eligible.
    """
    from app.services import my_heatmap_files
    from app.services.provenance import community_eligible_conditions

    conds, params = community_eligible_conditions()
    rows = db.execute(
        sa_text(
            f"""
            WITH target AS (
                SELECT id, sport AS old_sport, ({" AND ".join(conds)}) AS eligible
                  FROM activities
                 WHERE user_id = :u AND sport_is_default
                   FOR UPDATE
            )
            UPDATE activities a
               SET sport = :s, sport_is_default = false
              FROM target t
             WHERE a.id = t.id
            RETURNING a.id, t.old_sport, t.eligible
            """
        ),
        {**params, "u": user_id, "s": sport},
    ).fetchall()
    changed = [str(r[0]) for r in rows if r[1] != sport]
    any_community = any(r[2] for r in rows if r[1] != sport)
    if rows:
        if changed:
            # The trigger saw ``sport`` change and marked the file stale —
            # once for the statement; only the prompt rebuild is left to ask.
            my_heatmap_files.mark_dirty(db, user_id)
        else:
            _refresh_my_heatmap_file(db, user_id)
    db.commit()
    if changed:
        logger.info(
            "default-sport activities re-classified: user=%s -> %s changed=%d matched=%d",
            user_id, sport, len(changed), len(rows),
        )
    if any_community:
        _request_community_rebuild()
    return len(rows), changed


def _request_community_rebuild() -> None:
    try:
        from app.services.run_jobs import build_pmtiles_debounce_ok, trigger_build_pmtiles_job

        if build_pmtiles_debounce_ok():
            trigger_build_pmtiles_job()
        else:
            logger.info("build-pmtiles trigger debounced after sport change")
    except Exception:  # pragma: no cover - defense in depth
        logger.warning("build-pmtiles trigger failed after sport change", exc_info=True)
