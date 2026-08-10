"""CLI: promote a user to admin (set ``users.is_admin = TRUE``).

WHY THIS EXISTS
    Prod seeds an admin user ONLY when ``TEST_MODE=true`` — see the
    ``main.py`` lifespan (``_seed_default_user`` runs in dev/test; prod
    computes a deterministic id but never inserts a row with
    ``is_admin=True``). In prod (``TEST_MODE=false``) there is therefore
    NO admin, and the ``/admin`` dashboard (#443/#447) is unreachable
    with 403 for everyone. This CLI is the promote path: it flips
    ``is_admin`` on an existing account.

    Register/login normally first (or connect Strava), then promote that
    account.

RESOLUTION
    The identifier is resolved against BOTH:
      - ``users.email`` (exact match), and
      - a Strava ``integration_accounts.external_user_id`` (the athlete
        id) → its owning user.
    Whichever matches wins. A no-match is a clear error; an identifier
    that somehow resolves to two DIFFERENT users is rejected as ambiguous
    rather than silently promoting one.

IDEMPOTENT
    Re-running on an already-admin user is a no-op (exit 0, nothing
    written).

Usage::

    # by email
    docker compose exec -T backend python -m app.cli.promote_admin paul@example.com
    # by Strava athlete id
    docker compose exec -T backend python -m app.cli.promote_admin 28707

Exit codes: 0 success (promoted OR already-admin), 1 no match / ambiguous / error.
"""
from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("promote_admin")


class ResolveError(Exception):
    """No user (or more than one distinct user) matched the identifier."""


def resolve_user(db, identifier: str):
    """Resolve a ``User`` by email OR Strava ``external_user_id``.

    Returns the matched ``User`` ORM instance. Raises :class:`ResolveError`
    when nothing matches, or when the identifier resolves to two or more
    DISTINCT users (ambiguous).
    """
    from app.db.models import IntegrationAccount, User

    ident = (identifier or "").strip()
    if not ident:
        raise ResolveError("empty identifier")

    # 1) exact email match
    by_email = db.query(User).filter(User.email == ident).all()

    # 2) Strava athlete-id match → owning user(s)
    strava_owner_ids = [
        row[0]
        for row in db.query(IntegrationAccount.user_id)
        .filter(
            IntegrationAccount.provider == "strava",
            IntegrationAccount.external_user_id == ident,
        )
        .all()
    ]
    by_strava = (
        db.query(User).filter(User.id.in_(strava_owner_ids)).all()
        if strava_owner_ids
        else []
    )

    # union, distinct by user id
    matches = {u.id: u for u in (*by_email, *by_strava)}
    if not matches:
        raise ResolveError(
            f"no user matches {ident!r} (tried users.email and "
            f"Strava integration_accounts.external_user_id)"
        )
    if len(matches) > 1:
        ids = ", ".join(sorted(matches))
        raise ResolveError(
            f"{ident!r} is ambiguous — resolves to {len(matches)} distinct "
            f"users ({ids}); promote by a unique identifier"
        )
    return next(iter(matches.values()))


def promote(db, identifier: str) -> tuple[object, bool]:
    """Resolve ``identifier`` and set ``is_admin = True``.

    Returns ``(user, changed)`` where ``changed`` is False when the user
    was already an admin (no write performed). Raises
    :class:`ResolveError` on no-match / ambiguity.
    """
    user = resolve_user(db, identifier)
    was_admin = bool(getattr(user, "is_admin", False))
    if not was_admin:
        user.is_admin = True
        db.commit()
    return user, (not was_admin)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.promote_admin",
        description="Promote a user to admin (set users.is_admin=TRUE). "
        "Resolves the identifier by email or Strava athlete id.",
    )
    parser.add_argument(
        "identifier",
        help="User email OR Strava external_user_id (athlete id)",
    )
    args = parser.parse_args()

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        try:
            user, changed = promote(db, args.identifier)
        except ResolveError as e:
            logger.error("%s", e)
            return 1

        logger.info(
            "Resolved user: id=%s email=%s username=%s",
            user.id,
            user.email,
            user.username,
        )
        if changed:
            logger.info("is_admin: False -> True (promoted)")
        else:
            logger.info("is_admin: True -> True (already admin, no-op)")
        return 0
    except Exception as e:  # pragma: no cover - defensive top-level guard
        logger.error("promote failed: %s", e)
        db.rollback()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
