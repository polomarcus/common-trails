"""Behavioral tests for the Cloud Run Job's `run_import` (audit S3.6).

The Job's `run_import` is what hit OOM on 2026-05-27. It was previously
only covered by source-greps in `test_strava_job_lifecycle.py` — no
test actually called it. This file adds the minimum coverage that
exercises the function end-to-end with mocked Strava + DB-backed
fixtures.

Two tests cover the boot path:
- `test_run_import_with_missing_job_exits_clean` — calling with an
  unknown job_id must sys.exit(1) without leaving a hanging session.
- `test_run_import_phase1_empty_completes` — a Strava account with
  zero activities flows through all four phases and lands at
  status='COMPLETED', proving every import + helper resolves at
  module-import time (the JWT_SECRET-style regression class).

Heavier behavioral coverage (Phase 3 streams, Phase 4 photos,
rate-limit handling) belongs to a separate fixture suite that does
not exist yet — flagged as follow-up.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import ImportJob, IntegrationAccount, User
from app.db.session import SessionLocal


@pytest.fixture
def fresh_user() -> str:
    db = SessionLocal()
    try:
        u = User(
            id=str(uuid.uuid4()),
            email=f"runimp_{uuid.uuid4().hex[:8]}@example.com",
            username=f"runimp_{uuid.uuid4().hex[:6]}",
            hashed_password=None,
        )
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def _cleanup_user(user_id: str) -> None:
    db = SessionLocal()
    try:
        db.query(ImportJob).filter(ImportJob.user_id == user_id).delete()
        db.query(IntegrationAccount).filter(IntegrationAccount.user_id == user_id).delete()
        db.query(User).filter(User.id == user_id).delete()
        db.commit()
    finally:
        db.close()


def test_run_import_with_missing_job_exits_clean():
    """Calling `run_import` with a non-existent job_id must sys.exit(1)
    and not crash with NameError / ImportError / leaked session.

    The very thing the audit flagged (S3.6) — pre-PR-348 we had no
    test that proved the function is *callable* end-to-end. A
    JWT_SECRET-style regression (top-level `raise` at import time)
    would not have been caught by the existing source-grep tests."""
    from app.jobs import import_strava

    missing_job_id = str(uuid.uuid4())
    with pytest.raises(SystemExit) as exc_info:
        asyncio.run(import_strava.run_import(missing_job_id))
    assert exc_info.value.code == 1


def test_run_import_phase1_empty_completes(fresh_user):
    """A user with zero Strava activities must flow through all four
    phases and land at status='COMPLETED'.

    Pins (a) every transitive import in `run_import` resolves at
    module-load (JWT_SECRET-class regression), (b) the queue-guard /
    zombie-cleanup logic doesn't trip on a fresh user, (c) the new
    Phase-2-status-COMPLETED flip from PR #347 review #1 still works,
    (d) Phase 3 + Phase 4 handle gps_total=0 + photos=0 without
    crashing.
    """
    # Set up the account + job.
    job_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        from app.services.secrets import encrypt_token

        acct = IntegrationAccount(
            id=str(uuid.uuid4()),
            user_id=fresh_user,
            provider="strava",
            access_token=encrypt_token("fake-token"),
            refresh_token=encrypt_token("fake-refresh"),
            expires_at=2147483647,  # year 2038
            external_user_id="999999",
        )
        db.add(acct)
        job = ImportJob(
            id=job_id,
            user_id=fresh_user,
            provider="strava",
            status="PENDING",
            cursor=json.dumps({"contribute_heatmap": False}),
        )
        db.add(job)
        db.commit()
    finally:
        db.close()

    # Mock httpx + token refresh. Phase 1 returns an empty list ⇒ the
    # discovery loop exits immediately ⇒ activities_data is empty ⇒
    # ingest_activities_bulk returns {created:0, skipped:0, failed:0}.
    fake_resp = AsyncMock()
    fake_resp.status_code = 200
    fake_resp.is_success = True
    fake_resp.json = lambda: []
    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=fake_resp)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=fake_client), \
         patch(
             "app.api.integrations_strava.refresh_strava_token",
             new=AsyncMock(return_value="fake-token"),
         ):
        from app.jobs import import_strava
        asyncio.run(import_strava.run_import(job_id))

    db = SessionLocal()
    try:
        final = db.query(ImportJob).filter(ImportJob.id == job_id).first()
        assert final is not None
        assert final.status == "COMPLETED", (
            f"Expected COMPLETED after empty Phase 1, got {final.status}; "
            f"last_error={final.last_error!r}"
        )
        assert final.imported_count == 0
    finally:
        db.close()
        _cleanup_user(fresh_user)
