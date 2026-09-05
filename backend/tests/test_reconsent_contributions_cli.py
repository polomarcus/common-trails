"""Pins `app/cli/reconsent_contributions.py` — the v1–v4 → v5 consent migration.

Consent v5 added the dual-licensing grant; pre-v5 contributions are ODbL-only
until re-consented. The CLI appends ONE v5 row per pre-v5 contributor (offline
re-consent collected by the project owner) and must be idempotent and
append-only (never touch existing rows).

Tests drive the REAL plan/apply handlers against a real session (no inline
mirror). Coverage:
  - a v4-only contributor is planned; apply writes exactly one v5 row
    (version/text/source pinned) and leaves the v4 row untouched
  - idempotence: after apply, the plan is empty for that user
  - a contributor already on v5 is never planned
  - a user with no consent rows at all is not planned
"""
from __future__ import annotations

import contextlib
import uuid

import pytest

from app.cli.reconsent_contributions import (
    RECONSENT_SOURCE,
    V5_TEXT_FR,
    V5_VERSION,
    apply_reconsent,
    plan_reconsent,
)
from app.db.models import ContributionConsent, User
from app.db.session import SessionLocal


@pytest.fixture
def db():
    session = SessionLocal()
    created: list = []
    try:
        yield session, created
    finally:
        for obj in reversed(created):
            with contextlib.suppress(Exception):
                session.delete(obj)
        session.commit()
        session.close()


def _mk_user(session, created) -> User:
    u = User(
        id=str(uuid.uuid4()),
        email=f"reconsent-{uuid.uuid4().hex[:8]}@test.local",
        username=f"reconsent-{uuid.uuid4().hex[:8]}",
        hashed_password=None,
    )
    session.add(u)
    session.commit()
    created.append(u)
    return u


def _mk_consent(session, created, user: User, version: str) -> ContributionConsent:
    c = ContributionConsent(
        user_id=user.id,
        source="manual_upload",
        consent_version=version,
        consent_text="wording as shown at the time",
        locale="fr",
    )
    session.add(c)
    session.commit()
    created.append(c)
    return c


def _candidates_for(session, user: User):
    return [c for c in plan_reconsent(session) if c.user_id == user.id]


def test_v4_contributor_is_planned_and_gets_exactly_one_v5_row(db):
    session, created = db
    user = _mk_user(session, created)
    v4 = _mk_consent(session, created, user, "contribution-2026-08-v4")

    planned = _candidates_for(session, user)
    assert len(planned) == 1
    assert planned[0].prior_versions == ("contribution-2026-08-v4",)

    ids = apply_reconsent(session, planned)
    assert len(ids) == 1
    row = session.get(ContributionConsent, ids[0])
    created.append(row)
    assert row.user_id == user.id
    assert row.consent_version == V5_VERSION
    assert row.consent_text == V5_TEXT_FR
    # The audit trail must say this was an OFFLINE re-consent, and the v5
    # wording must state it too — never pretend a box was ticked in the UI.
    assert row.source == RECONSENT_SOURCE
    assert "hors-ligne" in row.consent_text

    # Append-only: the original v4 row is untouched.
    session.refresh(v4)
    assert v4.consent_version == "contribution-2026-08-v4"
    assert v4.source == "manual_upload"

    # Idempotent: the user is no longer planned.
    assert _candidates_for(session, user) == []


def test_v5_contributor_is_never_planned(db):
    session, created = db
    user = _mk_user(session, created)
    _mk_consent(session, created, user, V5_VERSION)
    assert _candidates_for(session, user) == []


def test_user_without_any_consent_is_not_planned(db):
    session, created = db
    user = _mk_user(session, created)
    assert _candidates_for(session, user) == []
