"""Account unification — self-merge a credential-less synthetic Strava account
into a real (survivor) account.

WHY (#477 / #520 Part B). One physical person can end up split across TWO
``users`` rows because account creation has two independent find-or-create
paths keyed on DIFFERENT identifiers (see ``app/cli/list_dup_accounts.py``):

  * email magic-link → a user keyed on the real email, ``hashed_password=NULL``;
  * "login with Strava" (retired; the OAuth login and its
    ``integration_accounts`` table are gone) → a SYNTHETIC user
    ``strava_<athlete>@strava.local``, ``hashed_password=NULL``.

They never reconciled. The result — one person owns activities under both
``someone@example.com`` AND ``strava_<athlete>@strava.local``. Legacy
synthetic rows can still exist, which is why this merge is kept.

SAFE DESIGN (the one Paul approved). Merge is gated to CREDENTIAL-LESS
SYNTHETIC ``@strava.local`` accounts only. A real, credentialed account is
NEVER auto-absorbed — that keeps the anti-theft guarantee intact (a logged-in
user must not be able to swallow someone else's real account just by
authorizing its Strava athlete). The merge:

  1. Deduplicates rides (the same ride can exist under both accounts — a
     Strava-API copy AND a manual upload). Reuses the ingest cross-source
     match (same user, start-time ±5 min, distance ±5 %) + provenance
     preference (keep the community-eligible ``manual_upload`` copy).
  2. Reassigns every user-owned row (activities, consents,
     routes, trips, …) from the synthetic account to the survivor.
  3. Deletes the now-empty synthetic ``users`` row.

Transactional (one commit unless ``commit=False``), idempotent (re-running
after the synthetic row is gone is a no-op), and it leaves the per-activity
GDPR deletion cascade intact (reassigned activities delete exactly as before).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.db.models import Activity, User
from app.services.ingest import (
    DEDUP_DISTANCE_TOL,
    DEDUP_START_WINDOW_MIN,
)
from app.services.provenance import is_community_source

logger = logging.getLogger(__name__)

SYNTHETIC_EMAIL_SUFFIX = "@strava.local"

# Every table that carries an OWNER reference to ``users.id`` as (table, column).
# Reassigned wholesale from the absorbed account to the survivor. ``activities``
# is handled SEPARATELY (UNIQUE constraint + dedup) and is intentionally
# excluded here.
_OWNED_TABLES: tuple[tuple[str, str], ...] = (
    ("activity_cells", "user_id"),
    ("pending_archive_files", "user_id"),
    ("pending_archives", "user_id"),
    ("contribution_consents", "user_id"),
    ("magic_link_tokens", "user_id"),
    ("notifications", "user_id"),
    ("export_requests", "user_id"),
    ("routes", "owner_id"),
    ("route_versions", "author_id"),
    ("route_collections", "owner_id"),
    ("trips", "owner_id"),
    ("suggestions", "author_id"),
    ("route_annotations", "author_id"),
)


class AccountMergeRefused(Exception):
    """Raised when a merge is asked for but the target is NOT a credential-less
    synthetic account — the anti-theft guarantee forbids absorbing it."""


@dataclass
class MergeResult:
    """Outcome of a merge (or of a no-op / dry-run)."""

    merged: bool = False
    survivor_id: str = ""
    absorbed_id: str = ""
    reassigned_activities: int = 0
    dropped_duplicate_activities: int = 0
    reassigned_rows: dict[str, int] = field(default_factory=dict)
    reason: str = ""


def _relation_exists(db: Session, name: str) -> bool:
    """True when a base table ``name`` exists in the connected database."""
    return bool(
        db.execute(
            sa_text(
                "SELECT EXISTS (SELECT FROM information_schema.tables "
                "WHERE table_name = :n)"
            ),
            {"n": name},
        ).scalar()
    )


def is_synthetic_account(user: User | None) -> bool:
    """True iff ``user`` is a credential-less synthetic ``@strava.local`` row —
    the ONLY kind an account merge may absorb. A real password (or any
    non-synthetic email) makes it off-limits."""
    if user is None:
        return False
    email = (user.email or "").lower()
    return email.endswith(SYNTHETIC_EMAIL_SUFFIX) and user.hashed_password is None


def _resolve_activity_dedup(
    db: Session, survivor_id: str, absorbed_id: str
) -> int:
    """Drop rides duplicated across the two accounts BEFORE reassignment.

    Two activities are "the same ride" iff (reusing the ingest heuristic):
      * they share a non-null ``(provider, provider_activity_id)`` — the exact
        same-provider key; OR
      * same start time within ±``DEDUP_START_WINDOW_MIN`` min AND distance
        within ±``DEDUP_DISTANCE_TOL``.

    For each matched pair we keep the community-eligible (``manual_upload``,
    authoritative-geometry) copy and delete the other. Ties (both/neither
    community-eligible) keep the survivor's existing row.

    Returns the number of dropped activities. Does NOT commit.
    """
    pairs = db.execute(
        sa_text(
            """
            SELECT s.id  AS abs_id,  s.source AS abs_source,
                   r.id  AS surv_id, r.source AS surv_source
            FROM activities s
            JOIN activities r
              ON r.user_id = :survivor
             AND (
                   (s.provider_activity_id IS NOT NULL
                     AND s.provider = r.provider
                     AND s.provider_activity_id = r.provider_activity_id)
                OR (s.activity_date IS NOT NULL AND r.activity_date IS NOT NULL
                     AND s.distance_m IS NOT NULL AND r.distance_m IS NOT NULL
                     AND s.distance_m > 0
                     AND r.activity_date BETWEEN
                         s.activity_date - MAKE_INTERVAL(mins => :win)
                       AND s.activity_date + MAKE_INTERVAL(mins => :win)
                     AND r.distance_m BETWEEN
                         s.distance_m * (1 - :tol) AND s.distance_m * (1 + :tol))
                 )
            WHERE s.user_id = :absorbed
            """
        ),
        {
            "survivor": survivor_id,
            "absorbed": absorbed_id,
            "win": DEDUP_START_WINDOW_MIN,
            "tol": DEDUP_DISTANCE_TOL,
        },
    ).fetchall()

    dropped: set[str] = set()          # activity ids already deleted
    consumed_survivors: set[str] = set()  # survivor ids already paired off

    for row in pairs:
        abs_id = str(row.abs_id)
        surv_id = str(row.surv_id)
        if abs_id in dropped or surv_id in dropped or surv_id in consumed_survivors:
            continue
        abs_community = is_community_source(row.abs_source)
        surv_community = is_community_source(row.surv_source)
        # Keep the community-eligible copy; default to the survivor's existing
        # row. Only when the absorbed copy is community-eligible and the
        # survivor's is NOT do we drop the survivor's (the absorbed one is then
        # reassigned to the survivor below).
        loser = surv_id if (abs_community and not surv_community) else abs_id
        db.execute(
            sa_text("DELETE FROM activities WHERE id = :id"), {"id": loser}
        )
        dropped.add(loser)
        consumed_survivors.add(surv_id)

    return len(dropped)


def merge_synthetic_account(
    db: Session,
    survivor_user_id: str,
    absorbed_user_id: str,
    *,
    commit: bool = True,
) -> MergeResult:
    """Absorb the credential-less synthetic account ``absorbed_user_id`` into
    ``survivor_user_id``.

    SAFETY:
      * SYNTHETIC-ONLY — refuses (``AccountMergeRefused``) if the absorbed row
        is a real credentialed account (anti-theft preserved).
      * TRANSACTIONAL — one commit (or none when ``commit=False``); any error
        rolls the whole merge back.
      * IDEMPOTENT — if the absorbed row is gone / equals the survivor, returns
        a no-op result.
      * DEDUP — cross-account duplicate rides collapse to the community copy.
      * GDPR-SAFE — reassigned activities keep the exact per-activity deletion
        cascade (``activity_deletion.delete_user_activity``).
    """
    result = MergeResult(survivor_id=survivor_user_id, absorbed_id=absorbed_user_id)

    if not absorbed_user_id or absorbed_user_id == survivor_user_id:
        result.reason = "noop_same_or_missing"
        return result

    survivor = db.query(User).filter(User.id == survivor_user_id).first()
    absorbed = db.query(User).filter(User.id == absorbed_user_id).first()
    if survivor is None:
        result.reason = "noop_survivor_missing"
        return result
    if absorbed is None:
        result.reason = "noop_absorbed_missing"
        return result
    if not is_synthetic_account(absorbed):
        # HARD STOP — never auto-absorb a real credentialed account.
        raise AccountMergeRefused(
            f"refusing to merge non-synthetic account {absorbed_user_id} "
            f"(email={absorbed.email!r}, has_password={absorbed.hashed_password is not None})"
        )

    # 1) Drop cross-account duplicate rides (community copy wins).
    dropped = _resolve_activity_dedup(db, survivor_user_id, absorbed_user_id)
    result.dropped_duplicate_activities = dropped

    # 2) Reassign remaining activities (unique (user_id, provider,
    #    provider_activity_id) — safe now that colliding dups are gone).
    reassigned_activities = (
        db.query(Activity)
        .filter(Activity.user_id == absorbed_user_id)
        .update({Activity.user_id: survivor_user_id}, synchronize_session=False)
    )
    result.reassigned_activities = int(reassigned_activities or 0)

    # 3) Reassign every other owner-referencing table.
    for table, col in _OWNED_TABLES:
        if not _relation_exists(db, table):
            continue
        n = db.execute(
            sa_text(f"UPDATE {table} SET {col} = :survivor WHERE {col} = :absorbed"),
            {"absorbed": absorbed_user_id, "survivor": survivor_user_id},
        ).rowcount
        if n:
            result.reassigned_rows[table] = int(n)

    # 4) Delete the now-empty synthetic user row.
    db.execute(sa_text("DELETE FROM users WHERE id = :id"), {"id": absorbed_user_id})

    # 5) Private "Ma heatmap" files: the bulk UPDATE + raw DELETEs above bypass
    #    the ORM hook. Survivor → rebuild after commit; absorbed → its pointer
    #    row now, its objects after commit (docs/ma-heatmap-fichier-prive.md §7).
    from app.services import my_heatmap_files

    my_heatmap_files.mark_dirty(db, survivor_user_id)
    my_heatmap_files.mark_deleted(db, absorbed_user_id)

    result.merged = True
    result.reason = "merged"

    if commit:
        db.commit()
        # Reflect the merge on the community map. A merge can DROP duplicate
        # activities and REASSIGN geometry between users, changing what the
        # static PMTiles should show; the build reads consented activities
        # live, and there is no periodic rebuild scheduler, so
        # without this trigger the change would not surface until an unrelated
        # contribution rebuilt. Best-effort: never raises, env-gated no-op
        # locally / in TEST_MODE (run_jobs._is_enabled).
        try:
            from app.services.run_jobs import trigger_build_pmtiles_job

            trigger_build_pmtiles_job()
        except Exception:  # pragma: no cover - defense in depth
            logger.warning("build-pmtiles trigger failed after account merge", exc_info=True)

    logger.info(
        "account merge: absorbed=%s -> survivor=%s activities=%d dropped_dups=%d "
        "rows=%s",
        absorbed_user_id, survivor_user_id, result.reassigned_activities,
        result.dropped_duplicate_activities, result.reassigned_rows,
    )
    return result
