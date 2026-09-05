"""CLI: migrate existing contributors to consent v5 (dual-licensing grant).

WHY THIS EXISTS
    Consent v5 (``contribution-2026-09-v5``, 2026-09-05) added the
    dual-licensing grant: the contributor grants the project a
    NON-EXCLUSIVE right to also license their contribution under a
    separate commercial licence (the "share-alike or pay" model — the
    point is that industry giants don't get to use the community's data
    without an agreement). Contributions consented under v1–v4 carry NO
    such grant, so they are ODbL-only until the contributor re-consents.

    The project owner collected the explicit OK of every pre-v5
    contributor out-of-band (2026-09-05, beta cohort of a handful of
    users). This CLI materialises that re-consent in the audit trail:
    ONE new ``contribution_consents`` row per contributor, version v5,
    with wording that names the out-of-band collection. Existing v1–v4
    rows are NEVER touched — the table is append-only by design.

IDEMPOTENT
    A contributor who already has a v5 row is skipped, so re-running is
    a no-op. Dry-run by default; ``--apply`` writes.

Usage::

    # dry-run (prints the plan, writes nothing)
    docker compose exec -T backend python -m app.cli.reconsent_contributions
    # write the v5 rows
    docker compose exec -T backend python -m app.cli.reconsent_contributions --apply

Exit codes: 0 success (including empty plan), 1 unexpected error.
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass

from sqlalchemy import select

from app.db.models import ContributionConsent, User
from app.services.archive_intake import record_consent

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("reconsent_contributions")

# Mirror of the frontend CONTRIBUTION_CONSENT_VERSION
# (frontend/lib/strava-archive-consent.ts) — keep in sync on every bump.
V5_VERSION = "contribution-2026-09-v5"

# The v5 wording (mirror of fr.ts `strava.archive.consentLabel`), plus an
# explicit note that THIS row records an out-of-band re-consent — the audit
# trail must never pretend the contributor checked a box in the UI.
V5_TEXT_FR = (
    "Je consens à contribuer mes traces à la carte de popularité communautaire "
    "ouverte, publiée sous licence ODbL 1.0. J'accorde à Chemins Communs le "
    "droit non exclusif de les proposer aussi sous licence commerciale (les "
    "revenus financent le projet ; mes traces restent libres en ODbL). Le début "
    "et la fin de chaque activité sont masqués pour protéger mes lieux "
    "sensibles (domicile, travail). "
    "[Re-consentement v1–v4 → v5 recueilli hors-ligne par le responsable du "
    "projet, accord explicite du contributeur obtenu le 2026-09-05.]"
)

# Distinct provenance so an audit can tell an offline re-consent from a
# checkbox the user ticked in the UI (source 'manual_upload').
RECONSENT_SOURCE = "offline_reconsent"


@dataclass(frozen=True)
class ReconsentCandidate:
    user_id: str
    email: str | None
    prior_versions: tuple[str, ...]


def plan_reconsent(db) -> list[ReconsentCandidate]:
    """Contributors with at least one consent row but NO v5 row yet.

    Pure read — safe to call in dry-run. Users whose only rows are already
    v5 (or later re-runs) are excluded, which makes ``apply`` idempotent.
    """
    rows = db.execute(
        select(ContributionConsent.user_id, ContributionConsent.consent_version)
    ).all()
    by_user: dict[str, set[str]] = {}
    for user_id, version in rows:
        by_user.setdefault(user_id, set()).add(version)
    candidates = []
    for user_id, versions in sorted(by_user.items()):
        if V5_VERSION in versions:
            continue
        user = db.get(User, user_id)
        candidates.append(ReconsentCandidate(
            user_id=user_id,
            email=getattr(user, "email", None),
            prior_versions=tuple(sorted(versions)),
        ))
    return candidates


def apply_reconsent(db, candidates: list[ReconsentCandidate]) -> list[str]:
    """Append ONE v5 consent row per candidate; returns the new row ids."""
    ids = []
    for c in candidates:
        consent_id = record_consent(
            db,
            user_id=c.user_id,
            consent_version=V5_VERSION,
            consent_text=V5_TEXT_FR,
            locale="fr",
            source=RECONSENT_SOURCE,
        )
        logger.info("v5 consent %s recorded for %s (%s; prior: %s)",
                    consent_id, c.user_id, c.email or "no email",
                    ", ".join(c.prior_versions))
        ids.append(consent_id)
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the v5 rows (default: dry-run)")
    args = parser.parse_args(argv)

    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        candidates = plan_reconsent(db)
        if not candidates:
            logger.info("Nothing to do — every contributor already has a v5 consent row.")
            return 0
        for c in candidates:
            logger.info("PLAN: %s (%s) — prior consents: %s",
                        c.user_id, c.email or "no email", ", ".join(c.prior_versions))
        if not args.apply:
            logger.info("Dry-run: %d contributor(s) would get a v5 row. "
                        "Re-run with --apply to write.", len(candidates))
            return 0
        ids = apply_reconsent(db, candidates)
        logger.info("Done: %d v5 consent row(s) written.", len(ids))
        return 0
    except Exception:
        logger.exception("reconsent failed")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
