"""CLI: list LIKELY-DUPLICATE user accounts (READ-ONLY — no mutation).

WHY THIS EXISTS
    One physical person can end up with TWO ``users`` rows because account
    creation has two independent find-or-create paths keyed on DIFFERENT
    identifiers, and they are never reconciled (the account-unification gap,
    #477):

      * email magic-link (``api/auth.request_magic_link``) — find-or-create
        by ``users.email`` = the real address (e.g. ``paleclercq@gmail.com``),
        ``hashed_password=NULL``;
      * "login with Strava" (``integrations_strava._find_or_create_strava_user``)
        — find-or-create by ``integration_accounts.external_user_id`` (the
        athlete id), minting a user with a SYNTHETIC email
        ``strava_<athlete_id>@strava.local``, ``hashed_password=NULL``.

    So a user who logged in with Strava FIRST, then later with their email,
    owns activities under BOTH rows. The Strava ``/connect`` (link-to-session)
    flow can't merge them: its anti-theft guard REFUSES to relink an athlete
    already bound to another account (``status=conflict``). The home
    "contributeurs" count over-counted partly because of this split (that
    stat is separately fixed to count community-eligible users only).

    This tool SURFACES the likely dups so a human (Paul) can decide on a
    merge. It NEVER writes — pure ``SELECT``.

SIGNALS REPORTED (both read-only, no external identity needed)
    1. SYNTHETIC Strava-login accounts (``email LIKE '%@strava.local'``,
       no password) — the canonical fragmenting row: it exists only because
       a Strava login predated (or ran independently of) an email login.
    2. TEMPORAL COLLISIONS — the same activity start time (``activity_date``
       within a tolerance) recorded under TWO DIFFERENT ``user_id``s. Two
       accounts logging the exact same ride at the same minute are almost
       certainly the same person (this reuses the (user, start-time) idea the
       cross-source dedup already relies on). This is the strongest
       "same person, two rows" signal available in-DB.

Usage::

    docker compose exec -T backend python -m app.cli.list_dup_accounts
    docker compose exec -T backend python -m app.cli.list_dup_accounts --tolerance-min 5 --json

Exit codes: 0 always (a diagnostic never fails a pipeline); non-zero only on
an unexpected DB error.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from sqlalchemy import text as sa_text

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("list_dup_accounts")


def collect_report(db, tolerance_min: int = 5) -> dict:
    """Build the read-only duplicate-accounts report.

    Returns a plain dict (JSON-serialisable) with:
      * ``users`` — one entry per account with its type + activity breakdown;
      * ``synthetic_strava_accounts`` — the ``@strava.local`` rows;
      * ``temporal_collisions`` — pairs of DISTINCT users that logged an
        activity at the same start time (± ``tolerance_min`` minutes).

    Pure ``SELECT`` — performs NO writes, NO commits.
    """
    # 1) Per-user overview: type + activity counts by provenance source.
    #    activities.user_id is varchar; users.id is uuid → ::text to join.
    users = [
        {
            "user_id": str(r.user_id),
            "email": r.email,
            "synthetic_strava": bool(r.email and r.email.endswith("@strava.local")),
            "has_password": r.has_password,
            "strava_athlete_id": r.strava_athlete_id,
            "activities": int(r.activities or 0),
            "manual_upload": int(r.manual_upload or 0),
            "strava_api": int(r.strava_api or 0),
            "legacy_null": int(r.legacy_null or 0),
        }
        for r in db.execute(sa_text(
            """
            SELECT u.id AS user_id,
                   u.email AS email,
                   (u.hashed_password IS NOT NULL) AS has_password,
                   ia.external_user_id AS strava_athlete_id,
                   COUNT(a.id) AS activities,
                   COUNT(a.id) FILTER (WHERE a.source = 'manual_upload') AS manual_upload,
                   COUNT(a.id) FILTER (WHERE a.source = 'strava_api') AS strava_api,
                   COUNT(a.id) FILTER (WHERE a.source IS NULL) AS legacy_null
            FROM users u
            LEFT JOIN integration_accounts ia
                   ON ia.user_id = u.id::text AND ia.provider = 'strava'
            LEFT JOIN activities a ON a.user_id = u.id::text
            GROUP BY u.id, u.email, u.hashed_password, ia.external_user_id
            ORDER BY activities DESC, u.email
            """
        ))
    ]

    synthetic = [u for u in users if u["synthetic_strava"]]

    # 2) Temporal collisions: same activity start time (within tolerance)
    #    under two DIFFERENT users. Self-join on activity_date; the a1<a2
    #    user_id ordering de-dupes the symmetric pair and excludes same-user.
    collisions = [
        {
            "user_a": r.user_a,
            "user_b": r.user_b,
            "colliding_activities": int(r.n),
        }
        for r in db.execute(sa_text(
            """
            SELECT a1.user_id AS user_a,
                   a2.user_id AS user_b,
                   COUNT(*) AS n
            FROM activities a1
            JOIN activities a2
              ON a1.user_id < a2.user_id
             AND a1.activity_date IS NOT NULL
             AND a2.activity_date IS NOT NULL
             AND a2.activity_date BETWEEN
                 a1.activity_date - MAKE_INTERVAL(mins => :tol)
             AND a1.activity_date + MAKE_INTERVAL(mins => :tol)
            GROUP BY a1.user_id, a2.user_id
            ORDER BY n DESC
            """
        ), {"tol": int(tolerance_min)})
    ]

    return {
        "users": users,
        "synthetic_strava_accounts": synthetic,
        "temporal_collisions": collisions,
        "tolerance_min": int(tolerance_min),
    }


def _print_human(report: dict) -> None:
    users = report["users"]
    logger.info("=== %d user account(s) ===", len(users))
    for u in users:
        kind = (
            "synthetic-strava" if u["synthetic_strava"]
            else "password" if u["has_password"]
            else "email/magic-link"
        )
        logger.info(
            "  %s  <%s>  [%s]  activities=%d (manual=%d strava_api=%d null=%d)  athlete=%s",
            u["user_id"], u["email"], kind, u["activities"],
            u["manual_upload"], u["strava_api"], u["legacy_null"],
            u["strava_athlete_id"] or "-",
        )

    synth = report["synthetic_strava_accounts"]
    logger.info("=== %d synthetic @strava.local account(s) ===", len(synth))
    for u in synth:
        logger.info("  %s  <%s>  activities=%d", u["user_id"], u["email"], u["activities"])

    coll = report["temporal_collisions"]
    logger.info(
        "=== %d cross-user temporal collision pair(s) (±%d min) ===",
        len(coll), report["tolerance_min"],
    )
    for c in coll:
        logger.info(
            "  %s  <->  %s   (%d activities at the same start time — LIKELY same person)",
            c["user_a"], c["user_b"], c["colliding_activities"],
        )
    if synth or coll:
        logger.info(
            "NOTE: this is a READ-ONLY diagnostic. Merging accounts mutates user "
            "data + reassigns activity ownership — it is a deliberate, human-gated "
            "operation. Do NOT auto-merge from this report."
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.list_dup_accounts",
        description="List likely-duplicate user accounts (READ-ONLY).",
    )
    parser.add_argument(
        "--tolerance-min", type=int, default=5,
        help="Minutes tolerance for the same-start-time collision heuristic (default 5).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit the raw report as JSON instead of a human summary.",
    )
    args = parser.parse_args()

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        report = collect_report(db, tolerance_min=args.tolerance_min)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            _print_human(report)
        return 0
    except Exception as e:  # pragma: no cover - defensive top-level guard
        logger.error("diagnostic failed: %s", e)
        db.rollback()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
