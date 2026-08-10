"""Account unification — self-merge a credential-less synthetic Strava account
into a real (survivor) account.

WHY (#477 / #520 Part B). One physical person can end up split across TWO
``users`` rows because account creation has two independent find-or-create
paths keyed on DIFFERENT identifiers (see ``app/cli/list_dup_accounts.py``):

  * email magic-link → a user keyed on the real email, ``hashed_password=NULL``;
  * "login with Strava" → a SYNTHETIC user ``strava_<athlete>@strava.local``,
    ``hashed_password=NULL``, minted by ``_find_or_create_strava_user``.

They never reconcile: the Strava ``/connect`` anti-theft guard REFUSES to
relink an athlete already bound elsewhere, and the ``@strava.local`` email is
unique so the nudge to set a real email is blocked. The result — Paul owns
activities under both ``paleclercq@gmail.com`` AND ``strava_10699414@strava.local``.

SAFE DESIGN (the one Paul approved). Merge is gated to CREDENTIAL-LESS
SYNTHETIC ``@strava.local`` accounts only. A real, credentialed account is
NEVER auto-absorbed — that keeps the anti-theft guarantee intact (a logged-in
user must not be able to swallow someone else's real account just by
authorizing its Strava athlete). The merge:

  1. Deduplicates rides (the same ride can exist under both accounts — a
     Strava-API copy AND a manual upload). Reuses the ingest cross-source
     match (same user, start-time ±5 min, distance ±5 %) + provenance
     preference (keep the community-eligible ``manual_upload`` copy).
  2. Reassigns every user-owned row (activities, integrations, consents,
     routes, trips, …) from the synthetic account to the survivor.
  3. Rewrites the absorbed account's K-anonymity contributor hash onto the
     survivor's so one person counts once, and recomputes the touched
     ``heat_edges`` counts.
  4. Deletes the now-empty synthetic ``users`` row.

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
from app.services.activity_deletion import (
    _recompute_touched_heat_edges,
    _relation_exists,
    purge_activity_heat,
)
from app.services.ingest import (
    DEDUP_DISTANCE_TOL,
    DEDUP_START_WINDOW_MIN,
    heat_user_id_hash,
)
from app.services.provenance import is_community_source

logger = logging.getLogger(__name__)

SYNTHETIC_EMAIL_SUFFIX = "@strava.local"

# Every table that carries an OWNER reference to ``users.id`` as (table, column).
# Reassigned wholesale from the absorbed account to the survivor. ``activities``
# and ``integration_accounts`` are handled SEPARATELY (they have UNIQUE
# constraints + heat side-effects) and are intentionally excluded here.
_OWNED_TABLES: tuple[tuple[str, str], ...] = (
    ("activity_cells", "user_id"),
    ("activity_photos", "user_id"),
    ("import_jobs", "user_id"),
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
    heat_edges_recomputed: int = 0
    reason: str = ""


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
) -> tuple[list[int], int]:
    """Drop rides duplicated across the two accounts BEFORE reassignment.

    Two activities are "the same ride" iff (reusing the ingest heuristic):
      * they share a non-null ``(provider, provider_activity_id)`` — the exact
        same-provider key; OR
      * same start time within ±``DEDUP_START_WINDOW_MIN`` min AND distance
        within ±``DEDUP_DISTANCE_TOL``.

    For each matched pair we keep the community-eligible (``manual_upload``,
    authoritative-geometry) copy and delete the other — routing its community
    heat through ``purge_activity_heat`` so no orphan ``heat_edges`` are left.
    Ties (both/neither community-eligible) keep the survivor's existing row.

    Returns ``(way_ids_touched, dropped_count)``. Does NOT commit.
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
    way_ids: list[int] = []

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
        way_ids.extend(purge_activity_heat(db, loser))
        db.execute(
            sa_text("DELETE FROM activities WHERE id = :id"), {"id": loser}
        )
        dropped.add(loser)
        consumed_survivors.add(surv_id)

    return way_ids, len(dropped)


def _rewrite_contributor_hash(
    db: Session, survivor_id: str, absorbed_id: str
) -> list[int]:
    """Rewrite the absorbed account's ``user_id_hash`` onto the survivor's for
    all its REMAINING contributions, then recompute the touched ``heat_edges``
    so a single person counts ONCE toward K-anonymity. No-op (returns ``[]``)
    when the community heat tables are gone (raw-trace pivot). Does NOT commit."""
    if not _relation_exists(db, "heat_edge_contributors"):
        return []
    old_hash = heat_user_id_hash(absorbed_id)
    new_hash = heat_user_id_hash(survivor_id)
    if old_hash == new_hash:
        return []
    # The absorbed account's contributions are those on its (still-owned)
    # activities. After dedup the losing copies are already gone.
    edge_keys = [
        row[0]
        for row in db.execute(
            sa_text(
                "SELECT DISTINCT edge_key FROM heat_edge_contributors "
                "WHERE user_id_hash = :old AND activity_id IN "
                "(SELECT id FROM activities WHERE user_id = :absorbed)"
            ),
            {"old": old_hash, "absorbed": absorbed_id},
        )
    ]
    if not edge_keys:
        return []
    # Collapse onto the survivor's hash. A survivor row already present on the
    # same (edge_key, activity_id) is impossible (activity_id is unique to one
    # owner), so no PK collision — but guard defensively against a pre-existing
    # (edge_key, new_hash, activity_id) by deleting the old row when it clashes.
    db.execute(
        sa_text(
            """
            DELETE FROM heat_edge_contributors old
            USING heat_edge_contributors new
            WHERE old.user_id_hash = :old
              AND new.user_id_hash = :new
              AND old.edge_key = new.edge_key
              AND old.activity_id = new.activity_id
              AND old.activity_id IN (SELECT id FROM activities WHERE user_id = :absorbed)
            """
        ),
        {"old": old_hash, "new": new_hash, "absorbed": absorbed_id},
    )
    db.execute(
        sa_text(
            "UPDATE heat_edge_contributors SET user_id_hash = :new "
            "WHERE user_id_hash = :old AND activity_id IN "
            "(SELECT id FROM activities WHERE user_id = :absorbed)"
        ),
        {"old": old_hash, "new": new_hash, "absorbed": absorbed_id},
    )
    return _recompute_touched_heat_edges(db, edge_keys)


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
    dedup_ways, dropped = _resolve_activity_dedup(db, survivor_user_id, absorbed_user_id)
    result.dropped_duplicate_activities = dropped

    # 2) Rewrite the K-anonymity hash of the absorbed account's REMAINING
    #    contributions onto the survivor (before reassigning activity ownership,
    #    while `activities.user_id = absorbed` still selects them).
    hash_ways = _rewrite_contributor_hash(db, survivor_user_id, absorbed_user_id)

    # 3) Reassign remaining activities (unique (user_id, provider,
    #    provider_activity_id) — safe now that colliding dups are gone).
    reassigned_activities = (
        db.query(Activity)
        .filter(Activity.user_id == absorbed_user_id)
        .update({Activity.user_id: survivor_user_id}, synchronize_session=False)
    )
    result.reassigned_activities = int(reassigned_activities or 0)

    # 4) Reassign integration_accounts, guarding UNIQUE(user_id, provider):
    #    if the survivor already owns a row for a provider, drop the absorbed's.
    if _relation_exists(db, "integration_accounts"):
        db.execute(
            sa_text(
                """
                DELETE FROM integration_accounts a
                WHERE a.user_id = :absorbed
                  AND EXISTS (
                      SELECT 1 FROM integration_accounts b
                      WHERE b.user_id = :survivor AND b.provider = a.provider
                  )
                """
            ),
            {"absorbed": absorbed_user_id, "survivor": survivor_user_id},
        )
        n = db.execute(
            sa_text(
                "UPDATE integration_accounts SET user_id = :survivor "
                "WHERE user_id = :absorbed"
            ),
            {"absorbed": absorbed_user_id, "survivor": survivor_user_id},
        ).rowcount
        result.reassigned_rows["integration_accounts"] = int(n or 0)

    # 5) Reassign every other owner-referencing table.
    for table, col in _OWNED_TABLES:
        if not _relation_exists(db, table):
            continue
        n = db.execute(
            sa_text(f"UPDATE {table} SET {col} = :survivor WHERE {col} = :absorbed"),
            {"absorbed": absorbed_user_id, "survivor": survivor_user_id},
        ).rowcount
        if n:
            result.reassigned_rows[table] = int(n)

    # 6) Delete the now-empty synthetic user row.
    db.execute(sa_text("DELETE FROM users WHERE id = :id"), {"id": absorbed_user_id})

    all_ways = list({*dedup_ways, *hash_ways})
    result.heat_edges_recomputed = len(all_ways)
    result.merged = True
    result.reason = "merged"

    if commit:
        db.commit()
        if all_ways:
            # Best-effort agg refresh (self-healing); never undoes the merge.
            try:
                from app.services.ingest import _maintain_heat_agg

                _maintain_heat_agg(db, all_ways)
            except Exception:  # pragma: no cover - defensive
                logger.warning("heat_agg refresh after merge failed", exc_info=True)

        # Reflect the merge on the community map. A merge can DROP duplicate
        # activities and REASSIGN geometry between users, changing what the
        # static PMTiles should show; in raw mode the build reads consented
        # activities live, and there is no periodic rebuild scheduler, so
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
        "rows=%s heat_ways=%d",
        absorbed_user_id, survivor_user_id, result.reassigned_activities,
        result.dropped_duplicate_activities, result.reassigned_rows, len(all_ways),
    )
    return result
