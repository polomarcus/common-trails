"""Pins `app/cli/promote_admin.py` — the prod admin-promote path.

Prod seeds an admin ONLY under TEST_MODE (main.py lifespan); with
TEST_MODE=false there is NO admin and /admin (#443/#447) is 403 for
everyone. This CLI flips `users.is_admin`, resolving the target by email.

Tests drive the REAL `promote()` / `resolve_user()` handlers (no inline
mirror) against a real session, per CLAUDE.md. Coverage:
  - promote by email
  - no match → ResolveError (a Strava athlete id no longer resolves)
  - already-admin → no-op (changed=False, no write)
"""
from __future__ import annotations

import contextlib
import uuid

import pytest

from app.cli.promote_admin import ResolveError, promote, resolve_user
from app.db.models import User
from app.db.session import SessionLocal


@pytest.fixture
def db():
    session = SessionLocal()
    created: list = []
    try:
        yield session, created
    finally:
        # tidy up anything the test created so the shared local DB stays clean
        for obj in reversed(created):
            with contextlib.suppress(Exception):
                session.delete(obj)
        session.commit()
        session.close()


def _mk_user(db_session, created, *, is_admin: bool = False) -> User:
    u = User(
        id=str(uuid.uuid4()),
        email=f"promo_{uuid.uuid4().hex[:8]}@example.com",
        username=f"promo_{uuid.uuid4().hex[:6]}",
        hashed_password=None,
        is_admin=is_admin,
    )
    db_session.add(u)
    db_session.commit()
    created.append(u)
    return u


def test_promote_by_email(db):
    session, created = db
    u = _mk_user(session, created)
    assert u.is_admin is False

    user, changed = promote(session, u.email)

    assert changed is True
    assert user.id == u.id
    session.refresh(u)
    assert u.is_admin is True


def test_no_match_raises(db):
    session, created = db
    missing = f"nobody_{uuid.uuid4().hex}@example.com"
    with pytest.raises(ResolveError):
        resolve_user(session, missing)


def test_already_admin_is_noop(db):
    session, created = db
    u = _mk_user(session, created, is_admin=True)

    user, changed = promote(session, u.email)

    assert changed is False  # no write performed
    assert user.id == u.id
    session.refresh(u)
    assert u.is_admin is True


def test_strava_athlete_id_no_longer_resolves(db):
    """Resolution by Strava athlete id is gone with ``integration_accounts``
    (migration 0070): a numeric id is just an unknown email."""
    session, created = db
    _mk_user(session, created)
    with pytest.raises(ResolveError):
        resolve_user(session, "28707")
