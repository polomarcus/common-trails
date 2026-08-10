"""Pins `app/cli/promote_admin.py` — the prod admin-promote path.

Prod seeds an admin ONLY under TEST_MODE (main.py lifespan); with
TEST_MODE=false there is NO admin and /admin (#443/#447) is 403 for
everyone. This CLI flips `users.is_admin`, resolving the target by email
OR by Strava `integration_accounts.external_user_id`.

Tests drive the REAL `promote()` / `resolve_user()` handlers (no inline
mirror) against a real session, per CLAUDE.md. Coverage:
  - promote by email
  - promote by Strava athlete id
  - no match → ResolveError
  - already-admin → no-op (changed=False, no write)
  - ambiguous identifier → ResolveError
"""
from __future__ import annotations

import contextlib
import uuid

import pytest

from app.cli.promote_admin import ResolveError, promote, resolve_user
from app.db.models import IntegrationAccount, User
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


def _link_strava(db_session, created, user: User, external_user_id: str) -> IntegrationAccount:
    acct = IntegrationAccount(
        id=str(uuid.uuid4()),
        user_id=user.id,
        provider="strava",
        access_token="tok",
        external_user_id=external_user_id,
    )
    db_session.add(acct)
    db_session.commit()
    created.append(acct)
    return acct


def test_promote_by_email(db):
    session, created = db
    u = _mk_user(session, created)
    assert u.is_admin is False

    user, changed = promote(session, u.email)

    assert changed is True
    assert user.id == u.id
    session.refresh(u)
    assert u.is_admin is True


def test_promote_by_strava_external_user_id(db):
    session, created = db
    u = _mk_user(session, created)
    athlete_id = str(uuid.uuid4().int % 10_000_000)  # unique-ish numeric id
    _link_strava(session, created, u, athlete_id)

    user, changed = promote(session, athlete_id)

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


def test_ambiguous_identifier_raises(db):
    """An identifier that resolves to two DISTINCT users is rejected.

    Constructed by giving user B a Strava external_user_id equal to user
    A's email — the email branch matches A, the Strava branch matches B.
    """
    session, created = db
    a = _mk_user(session, created)
    b = _mk_user(session, created)
    _link_strava(session, created, b, a.email)

    with pytest.raises(ResolveError):
        resolve_user(session, a.email)

    # neither user was promoted
    session.refresh(a)
    session.refresh(b)
    assert a.is_admin is False
    assert b.is_admin is False
