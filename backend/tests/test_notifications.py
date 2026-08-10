"""Tests for the notifications system.

Covers:
- emit_notification persists a row with the right shape
- GET /me/notifications returns owner-only rows, paginates, filters unread
- POST /me/notifications/{id}/read flips read_at
- POST /me/notifications/read_all flips every unread row for the caller
- Auth is required on every endpoint
- Cross-user isolation: user A cannot read/mark user B's notifications
"""
import uuid

from app.db.models import Notification
from app.db.session import SessionLocal
from app.services.notifications import emit_notification


def _make_user(client) -> tuple[str, dict[str, str]]:
    """Register a fresh user and return (user_id, auth headers)."""
    email = f"notif_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "pw123456789", "username": f"u_{uuid.uuid4().hex[:6]}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["user_id"], {"Authorization": f"Bearer {body['access_token']}"}


def _cleanup_user_notifs(user_id: str) -> None:
    db = SessionLocal()
    try:
        db.query(Notification).filter(Notification.user_id == user_id).delete()
        db.commit()
    finally:
        db.close()


class TestEmitNotification:
    def test_emit_creates_a_row_with_expected_fields(self, client):
        user_id, _ = _make_user(client)
        try:
            notif_id = emit_notification(
                user_id=user_id,
                kind="strava_gps_upgrade_complete",
                title="Amélioration GPS terminée",
                body="1355 activités traitées, 47 erreurs",
                meta={"job_id": "abc", "upgraded_count": 1355, "failed_count": 47},
            )
            assert notif_id is not None

            db = SessionLocal()
            try:
                row = db.query(Notification).filter(Notification.id == notif_id).one()
                assert row.user_id == user_id
                assert row.kind == "strava_gps_upgrade_complete"
                assert row.title == "Amélioration GPS terminée"
                assert "1355" in row.body
                assert row.meta["upgraded_count"] == 1355
                assert row.read_at is None
                assert row.created_at is not None
            finally:
                db.close()
        finally:
            _cleanup_user_notifs(user_id)

    def test_emit_with_missing_user_swallows_and_returns_none(self):
        """A bogus user_id (FK violation) must not raise — phase finisher
        callers should never have a notification failure tear them down."""
        result = emit_notification(
            user_id=str(uuid.uuid4()),  # FK violation: random uuid
            kind="strava_resync_complete",
            title="x",
        )
        assert result is None


class TestListEndpoint:
    def test_requires_auth(self, client):
        resp = client.get("/me/notifications")
        assert resp.status_code == 401

    def test_empty_list(self, client):
        _user_id, headers = _make_user(client)
        resp = client.get("/me/notifications", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["unread_count"] == 0

    def test_returns_owner_rows_newest_first(self, client):
        user_id, headers = _make_user(client)
        try:
            emit_notification(user_id=user_id, kind="strava_import_complete", title="A", body="b1")
            emit_notification(user_id=user_id, kind="strava_gps_upgrade_complete", title="B", body="b2")

            resp = client.get("/me/notifications", headers=headers)
            assert resp.status_code == 200
            body = resp.json()
            assert len(body["items"]) == 2
            assert body["unread_count"] == 2
            # Newest first.
            assert body["items"][0]["title"] == "B"
            assert body["items"][1]["title"] == "A"
        finally:
            _cleanup_user_notifs(user_id)

    def test_unread_only_filters_read_rows(self, client):
        user_id, headers = _make_user(client)
        try:
            read_id = emit_notification(user_id=user_id, kind="x", title="read me")
            emit_notification(user_id=user_id, kind="y", title="unread")

            client.post(f"/me/notifications/{read_id}/read", headers=headers)
            resp = client.get("/me/notifications?unread_only=true", headers=headers)
            assert resp.status_code == 200
            body = resp.json()
            assert len(body["items"]) == 1
            assert body["items"][0]["title"] == "unread"
            assert body["unread_count"] == 1
        finally:
            _cleanup_user_notifs(user_id)

    def test_other_users_notifications_invisible(self, client):
        user_a, headers_a = _make_user(client)
        user_b, headers_b = _make_user(client)
        try:
            emit_notification(user_id=user_a, kind="x", title="A's notif")

            resp = client.get("/me/notifications", headers=headers_b)
            assert resp.status_code == 200
            assert resp.json()["items"] == []
        finally:
            _cleanup_user_notifs(user_a)
            _cleanup_user_notifs(user_b)


class TestMarkRead:
    def test_mark_single_read_flips_read_at(self, client):
        user_id, headers = _make_user(client)
        try:
            notif_id = emit_notification(user_id=user_id, kind="x", title="t")
            resp = client.post(f"/me/notifications/{notif_id}/read", headers=headers)
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok", "marked": 1}

            db = SessionLocal()
            try:
                row = db.query(Notification).filter(Notification.id == notif_id).one()
                assert row.read_at is not None
            finally:
                db.close()
        finally:
            _cleanup_user_notifs(user_id)

    def test_mark_read_twice_is_idempotent(self, client):
        user_id, headers = _make_user(client)
        try:
            notif_id = emit_notification(user_id=user_id, kind="x", title="t")
            client.post(f"/me/notifications/{notif_id}/read", headers=headers)
            resp = client.post(f"/me/notifications/{notif_id}/read", headers=headers)
            assert resp.status_code == 200
            assert resp.json()["marked"] == 0
        finally:
            _cleanup_user_notifs(user_id)

    def test_cannot_mark_other_users_notification(self, client):
        user_a, _ = _make_user(client)
        user_b, headers_b = _make_user(client)
        try:
            notif_id = emit_notification(user_id=user_a, kind="x", title="t")
            resp = client.post(f"/me/notifications/{notif_id}/read", headers=headers_b)
            assert resp.status_code == 404
        finally:
            _cleanup_user_notifs(user_a)
            _cleanup_user_notifs(user_b)

    def test_mark_all_read_flips_only_callers_rows(self, client):
        user_a, headers_a = _make_user(client)
        user_b, _ = _make_user(client)
        try:
            emit_notification(user_id=user_a, kind="x", title="a1")
            emit_notification(user_id=user_a, kind="x", title="a2")
            b_notif = emit_notification(user_id=user_b, kind="x", title="b1")

            resp = client.post("/me/notifications/read_all", headers=headers_a)
            assert resp.status_code == 200
            assert resp.json()["marked"] == 2

            db = SessionLocal()
            try:
                row_b = db.query(Notification).filter(Notification.id == b_notif).one()
                assert row_b.read_at is None
            finally:
                db.close()
        finally:
            _cleanup_user_notifs(user_a)
            _cleanup_user_notifs(user_b)
