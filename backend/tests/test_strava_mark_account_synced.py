"""Audit 2026-05-29 ST-S2.8 — `_mark_account_synced` writes through.

`last_synced_at` on `IntegrationAccount` was previously only written
by the (decommissioned) monthly resync. The webhook-only path and
the `/import_all` bulk path never touched it — so the daily health-
check cron's `(sync_failures > 5 AND last_synced_at < NOW() - 7d)`
suppression gate never engaged for accounts that connected
post-PR-341.

This file pins the helper's contract: given a Strava account with
NULL last_synced_at AND sync_failures > 0, after a call to
`_mark_account_synced(user_id)` the column is populated AND
sync_failures is reset to 0.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.api.integrations_strava import _mark_account_synced
from app.db.models import IntegrationAccount, User
from app.db.session import SessionLocal


@pytest.fixture
def fresh_strava_account() -> tuple[str, str]:
    """Create a fresh user + Strava IntegrationAccount with NULL
    last_synced_at and sync_failures=3. Returns (user_id, account_id)."""
    db = SessionLocal()
    try:
        u = User(
            id=str(uuid.uuid4()),
            email=f"sync_{uuid.uuid4().hex[:8]}@example.com",
            username=f"sync_{uuid.uuid4().hex[:6]}",
            hashed_password=None,
        )
        db.add(u)
        acct = IntegrationAccount(
            id=str(uuid.uuid4()),
            user_id=u.id,
            provider="strava",
            access_token="enc-fake",
            refresh_token="enc-fake-r",
            expires_at=2147483647,
            external_user_id=str(uuid.uuid4()),
            athlete_name="Test Athlete",
            last_synced_at=None,
            sync_failures=3,
        )
        db.add(acct)
        db.commit()
        return u.id, acct.id
    finally:
        db.close()


def _read_acct(account_id: str) -> IntegrationAccount | None:
    db = SessionLocal()
    try:
        return db.query(IntegrationAccount).filter(
            IntegrationAccount.id == account_id,
        ).first()
    finally:
        db.close()


def test_mark_account_synced_writes_timestamp(fresh_strava_account):
    """Happy path: NULL last_synced_at → datetime now."""
    user_id, acct_id = fresh_strava_account
    before = datetime.now(UTC)
    _mark_account_synced(user_id)
    after = datetime.now(UTC)

    acct = _read_acct(acct_id)
    assert acct is not None
    assert acct.last_synced_at is not None, (
        "last_synced_at should be populated after _mark_account_synced"
    )
    # Within a generous window — the helper records `NOW()` in the
    # caller's clock; assert it's between the before/after timestamps.
    assert before - timedelta(seconds=1) <= acct.last_synced_at <= after + timedelta(seconds=1)


def test_mark_account_synced_resets_sync_failures(fresh_strava_account):
    """sync_failures > 0 → 0 after a successful sync."""
    user_id, acct_id = fresh_strava_account
    # Sanity: before call, sync_failures is 3
    assert _read_acct(acct_id).sync_failures == 3

    _mark_account_synced(user_id)

    assert _read_acct(acct_id).sync_failures == 0, (
        "sync_failures must reset to 0 after a successful sync"
    )


def test_mark_account_synced_only_touches_strava_provider(fresh_strava_account):
    """A user with multiple integration providers — only the Strava
    account is updated, not (e.g.) a hypothetical Garmin account."""
    user_id, _ = fresh_strava_account
    db = SessionLocal()
    try:
        # Add a second (non-Strava) integration for the same user
        other = IntegrationAccount(
            id=str(uuid.uuid4()),
            user_id=user_id,
            provider="garmin",
            access_token="enc-fake",
            refresh_token=None,
            expires_at=None,
            external_user_id="garmin-x",
            athlete_name="Test",
            last_synced_at=None,
            sync_failures=5,
        )
        db.add(other)
        db.commit()
        other_id = other.id
    finally:
        db.close()

    _mark_account_synced(user_id)

    other_after = _read_acct(other_id)
    assert other_after is not None
    assert other_after.last_synced_at is None, (
        "non-Strava integration account must not be touched"
    )
    assert other_after.sync_failures == 5


class TestCallSitesArePresent:
    """Source-grep regression. The audit S2.8 root cause was literally
    'the call site was missing' — these tests pin the three wire-up
    sites so a future refactor that removes `_mark_account_synced(...)`
    from any of them fails CI loudly. Cheaper than 3 full integration
    tests, same regression class caught."""

    def _read_src(self, relpath: str) -> str:
        from pathlib import Path
        return (
            Path(__file__).resolve().parents[1] / relpath
        ).read_text()

    def test_run_strava_import_calls_mark_account_synced(self):
        """API path: `integrations_strava._run_strava_import` end-of-Phase-2."""
        assert "_mark_account_synced(user_id)" in self._read_src(
            "app/api/integrations_strava.py",
        ), (
            "Call site missing in `integrations_strava.py`'s Phase-2 success "
            "path — audit S2.8 regression: webhook+import accounts stop "
            "refreshing `last_synced_at`."
        )

    def test_job_path_calls_mark_account_synced(self):
        """Cloud Run Job path: `jobs/import_strava.run_import`."""
        assert "_mark_account_synced(user_id)" in self._read_src(
            "app/jobs/import_strava.py",
        ), (
            "Call site missing in the Cloud Run Job's end-of-Phase-2 path — "
            "audit S2.8 regression."
        )

    def test_webhook_worker_calls_mark_account_synced(self):
        """Webhook worker path: `internal_strava_webhook._ingest_strava_activity`."""
        assert "_mark_account_synced(user_id)" in self._read_src(
            "app/api/internal_strava_webhook.py",
        ), (
            "Call site missing in the webhook worker — audit S2.8 "
            "regression: webhook-only accounts depend on this to satisfy "
            "the health-check cron's 7-day sync gate."
        )


def test_mark_account_synced_no_op_when_no_strava_account():
    """Calling for a user without a Strava IntegrationAccount must
    not crash and must not create one."""
    fake_user_id = str(uuid.uuid4())
    # Must not raise.
    _mark_account_synced(fake_user_id)

    db = SessionLocal()
    try:
        n = db.query(IntegrationAccount).filter(
            IntegrationAccount.user_id == fake_user_id,
        ).count()
        assert n == 0
    finally:
        db.close()
