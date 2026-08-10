"""Audit 2026-05-27 S2.1 regression — Phase 2 ingest emits a user notification.

Pre-fix: only Phase 3 (GPS upgrade) and Phase 4 (photo import) emitted
`emit_notification(...)`. A user with no polyline-only activities and
no photos got zero toasts despite the UI promise "Vous recevrez une
notification". This test pins the `_emit_bulk_import_complete` helper
behaviour at the boundary — given a result dict, it writes exactly one
`strava_bulk_import_complete` Notification row.

Behavioural test — uses a real `User` + reads the DB to assert the
notification landed. The helper opens its own short session via
`emit_notification`, no monkeypatch needed.
"""
from __future__ import annotations

import uuid

import pytest

from app.api.integrations_strava import _emit_bulk_import_complete
from app.db.models import Notification, User
from app.db.session import SessionLocal


@pytest.fixture
def fresh_user() -> str:
    db = SessionLocal()
    try:
        u = User(
            id=str(uuid.uuid4()),
            email=f"phase2notif_{uuid.uuid4().hex[:8]}@example.com",
            username=f"p2n_{uuid.uuid4().hex[:6]}",
            hashed_password=None,
        )
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def _count_bulk_notifs(user_id: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(Notification)
            .filter(Notification.user_id == user_id)
            .filter(Notification.kind == "strava_bulk_import_complete")
            .count()
        )
    finally:
        db.close()


def _read_bulk_notif(user_id: str) -> Notification | None:
    db = SessionLocal()
    try:
        return (
            db.query(Notification)
            .filter(Notification.user_id == user_id)
            .filter(Notification.kind == "strava_bulk_import_complete")
            .first()
        )
    finally:
        db.close()


def test_emit_bulk_import_complete_writes_one_notification(fresh_user):
    """Happy path — Phase 2 finished with new activities AND no GPS work
    pending. User gets one toast with a count summary."""
    _emit_bulk_import_complete(
        fresh_user,
        job_id="job-test",
        result={"created": 42, "skipped": 3, "failed": 0},
        gps_total=0,
    )

    assert _count_bulk_notifs(fresh_user) == 1
    notif = _read_bulk_notif(fresh_user)
    assert notif is not None
    assert notif.title == "Import Strava terminé"
    # Body must reflect the counts.
    assert "42 activités importées" in notif.body
    assert "3 ignorées" in notif.body
    # No GPS-upgrade hint when gps_total=0.
    assert "GPS" not in notif.body
    # Meta carries the structured data the UI/ops can grep.
    assert notif.meta["job_id"] == "job-test"
    assert notif.meta["imported_count"] == 42
    assert notif.meta["gps_total"] == 0


def test_emit_bulk_import_complete_hints_at_gps_upgrade_when_pending(fresh_user):
    """When gps_total > 0, the body MUST mention the background work so
    the user doesn't think the import is incomplete when Phase 3 is
    still running."""
    _emit_bulk_import_complete(
        fresh_user,
        job_id="job-test",
        result={"created": 1000, "skipped": 0, "failed": 0},
        gps_total=120,
    )
    notif = _read_bulk_notif(fresh_user)
    assert notif is not None
    assert "amélioration gps" in notif.body.lower()
    assert "arrière-plan" in notif.body


def test_emit_bulk_import_complete_handles_singular_plural(fresh_user):
    """French agreement: 0 and 1 are both SINGULAR (Académie rule);
    2+ are plural. PR #347 review #2 caught the original code emitting
    '0 activités importées' which is grammatically wrong.

    This test covers all three cases via separate fresh users (so
    notifications don't collide on the assert)."""
    # created=1 — singular
    _emit_bulk_import_complete(
        fresh_user,
        job_id="job-test",
        result={"created": 1, "skipped": 0, "failed": 0},
        gps_total=0,
    )
    notif = _read_bulk_notif(fresh_user)
    assert notif is not None
    assert "1 activité importée" in notif.body
    assert "activités" not in notif.body
    assert "importées" not in notif.body


def test_emit_bulk_import_complete_zero_is_singular(fresh_user):
    """`created=0` must use singular agreement in French."""
    _emit_bulk_import_complete(
        fresh_user,
        job_id="job-test",
        result={"created": 0, "skipped": 0, "failed": 0},
        gps_total=0,
    )
    notif = _read_bulk_notif(fresh_user)
    assert notif is not None
    assert "0 activité importée" in notif.body, (
        f"Expected '0 activité importée' (French singular for 0), got: {notif.body}"
    )


def test_emit_bulk_import_complete_two_is_plural(fresh_user):
    """`created=2` must use plural agreement."""
    _emit_bulk_import_complete(
        fresh_user,
        job_id="job-test",
        result={"created": 2, "skipped": 0, "failed": 0},
        gps_total=0,
    )
    notif = _read_bulk_notif(fresh_user)
    assert notif is not None
    assert "2 activités importées" in notif.body
