"""CLI: MERGE the synthetic-account duplicates the diagnostic surfaces (#477).

One physical person can own two ``users`` rows (see ``list_dup_accounts``):
a real email account AND a credential-less synthetic ``strava_<id>@strava.local``
account minted by "login with Strava". This one-off reconciles the EXISTING
dups by absorbing each synthetic account into the real account of the same
person — the same transactional, idempotent, dedup-aware merge the link-time
self-merge uses (``app/services/account_merge.merge_synthetic_account``).

PAIRING (who is the survivor?). A synthetic account alone doesn't name its
real twin. The strongest in-DB signal is a TEMPORAL COLLISION: the same ride
logged at the same start time under both rows (the (user, start-time) idea the
cross-source dedup already relies on). For each synthetic account we pick the
REAL account it collides with on the most activities. Ambiguous cases (no real
collision, or a near-tie between two real accounts) are NOT auto-merged — they
are printed as UNRESOLVED for a human, and can be forced with ``--pair``.

SAFETY:
  * DRY-RUN by default — prints the plan, writes NOTHING. Add ``--apply``.
  * SYNTHETIC-ONLY — the merge fn refuses to absorb a real credentialed account.
  * Per-merge transaction — each merge commits on its own; idempotent, so a
    re-run resumes cleanly.

Usage::

    # inspect the plan (writes nothing)
    docker compose exec -T backend python -m app.cli.merge_dup_accounts
    # execute
    docker compose exec -T backend python -m app.cli.merge_dup_accounts --apply
    # force a specific pairing (absorbed synthetic = survivor real)
    docker compose exec -T backend python -m app.cli.merge_dup_accounts \
        --pair strava_10699414_uuid=real_uuid --apply

Exit codes: 0 on success (incl. a clean dry-run); non-zero only on an
unexpected DB error.
"""
from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("merge_dup_accounts")


def _plan_from_report(report: dict) -> tuple[list[dict], list[dict]]:
    """Turn the read-only diagnostic report into (planned, unresolved).

    ``planned``   — [{absorbed, survivor, colliding, absorbed_activities}]
    ``unresolved``— [{absorbed, reason, candidates}]
    """
    by_id = {u["user_id"]: u for u in report["users"]}
    synth_ids = {u["user_id"] for u in report["synthetic_strava_accounts"]}

    # absorbed synthetic -> {real_partner_id: colliding_activities}
    partners: dict[str, dict[str, int]] = {s: {} for s in synth_ids}
    for c in report["temporal_collisions"]:
        a, b, n = c["user_a"], c["user_b"], c["colliding_activities"]
        for s, other in ((a, b), (b, a)):
            if s in synth_ids and other not in synth_ids and other in by_id:
                partners[s][other] = partners[s].get(other, 0) + n

    planned: list[dict] = []
    unresolved: list[dict] = []
    for s in sorted(synth_ids):
        cands = sorted(partners[s].items(), key=lambda kv: kv[1], reverse=True)
        if not cands:
            unresolved.append({
                "absorbed": s,
                "reason": "no real account collides with this synthetic account",
                "candidates": [],
            })
            continue
        top_id, top_n = cands[0]
        # Ambiguous if a second real candidate is within 20 % of the top.
        if len(cands) > 1 and cands[1][1] >= max(1, int(top_n * 0.8)):
            unresolved.append({
                "absorbed": s,
                "reason": "multiple real accounts collide (near-tie) — pair manually",
                "candidates": cands,
            })
            continue
        planned.append({
            "absorbed": s,
            "survivor": top_id,
            "colliding": top_n,
            "absorbed_activities": by_id[s]["activities"],
        })
    return planned, unresolved


def _apply_manual_pairs(pairs: list[str], planned: list[dict]) -> list[dict]:
    """Merge ``--pair absorbed=survivor`` overrides into the plan (dedup by absorbed)."""
    forced_ids = set()
    forced: list[dict] = []
    for spec in pairs:
        if "=" not in spec:
            raise SystemExit(f"--pair expects absorbed=survivor, got {spec!r}")
        absorbed, survivor = (x.strip() for x in spec.split("=", 1))
        forced.append({"absorbed": absorbed, "survivor": survivor,
                       "colliding": None, "absorbed_activities": None})
        forced_ids.add(absorbed)
    # Manual pairs win over auto-planned ones for the same absorbed id.
    kept = [p for p in planned if p["absorbed"] not in forced_ids]
    return forced + kept


def run(db, *, apply: bool, tolerance_min: int, pairs: list[str]) -> int:
    from app.cli.list_dup_accounts import collect_report
    from app.services.account_merge import AccountMergeRefused, merge_synthetic_account

    report = collect_report(db, tolerance_min=tolerance_min)
    planned, unresolved = _plan_from_report(report)
    planned = _apply_manual_pairs(pairs, planned)

    logger.info("=== %d planned merge(s) ===", len(planned))
    for p in planned:
        logger.info(
            "  ABSORB %s  ->  SURVIVOR %s   (collisions=%s, absorbed_activities=%s)",
            p["absorbed"], p["survivor"], p["colliding"], p["absorbed_activities"],
        )
    logger.info("=== %d unresolved (NOT merged) ===", len(unresolved))
    for u in unresolved:
        logger.info("  %s — %s", u["absorbed"], u["reason"])
        for cid, n in u["candidates"]:
            logger.info("      candidate survivor %s (collisions=%d)", cid, n)

    if not apply:
        logger.info(
            "DRY-RUN — nothing written. Re-run with --apply to execute. "
            "Use --pair absorbed=survivor to resolve the ambiguous ones."
        )
        return 0

    merged = 0
    for p in planned:
        try:
            res = merge_synthetic_account(
                db, p["survivor"], p["absorbed"], commit=True
            )
        except AccountMergeRefused as e:
            logger.warning("SKIP %s — %s", p["absorbed"], e)
            db.rollback()
            continue
        except Exception:
            logger.error("merge failed for %s", p["absorbed"], exc_info=True)
            db.rollback()
            continue
        if res.merged:
            merged += 1
            logger.info(
                "MERGED %s -> %s: activities=%d dropped_dups=%d rows=%s",
                p["absorbed"], p["survivor"], res.reassigned_activities,
                res.dropped_duplicate_activities, res.reassigned_rows,
            )
        else:
            logger.info("NO-OP %s (%s)", p["absorbed"], res.reason)
    logger.info("=== applied %d/%d merge(s) ===", merged, len(planned))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.merge_dup_accounts",
        description="Merge synthetic Strava dup accounts into their real twin (#477).",
    )
    parser.add_argument("--apply", action="store_true",
                        help="Execute the merges (default: DRY-RUN, writes nothing).")
    parser.add_argument("--tolerance-min", type=int, default=5,
                        help="Same-start-time collision tolerance in minutes (default 5).")
    parser.add_argument("--pair", action="append", default=[], metavar="ABSORBED=SURVIVOR",
                        help="Force a pairing; repeatable. Overrides auto-pairing.")
    args = parser.parse_args()

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        return run(db, apply=args.apply, tolerance_min=args.tolerance_min, pairs=args.pair)
    except Exception as e:  # pragma: no cover - defensive top-level guard
        logger.error("merge job failed: %s", e, exc_info=True)
        db.rollback()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
