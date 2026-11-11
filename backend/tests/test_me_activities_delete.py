"""Tests for DELETE /me/activities/{id} — GDPR per-activity deletion.

Drives the REAL endpoint via TestClient (no inline mirrors) and checks the
real DB: a deletion removes the activity row AND its private ``activity_cells``
(FK cascade), leaves other users' activities intact, and requests the
community map rebuild so the trace leaves the public PMTiles.
"""
import uuid
import zlib
from datetime import UTC, datetime, timedelta

import gpxpy
import gpxpy.gpx
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal

MISSING_ID = "00000000-0000-0000-0000-000000000000"


_T0 = datetime(2024, 1, 1, 7, 0, tzinfo=UTC)

def _make_gpx_bytes(name: str, lon0: float, lat0: float, n_points: int = 8) -> bytes:
    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(track)
    seg = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(seg)
    # Timestamped: an undated trace never feeds the public heatmap (provenance SSOT).
    _start = _T0 + timedelta(minutes=zlib.crc32(f"{name}{lon0}{lat0}".encode()) % 500_000)
    for i in range(n_points):
        seg.points.append(
            gpxpy.gpx.GPXTrackPoint(
                latitude=lat0 + i * 0.001,
                longitude=lon0 + i * 0.001,
                elevation=50 + i * 5,
                time=_start + timedelta(seconds=10 * i),
            )
        )
    return gpx.to_xml().encode()


def _unique_start() -> tuple[float, float]:
    """Per-run unique (lon, lat) in the Atlantic, so nothing else in a shared
    local DB collides with this run's activities."""
    jitter = (uuid.uuid4().int % 100_000) * 1e-5  # 0 .. 1.0 degree
    return (-30.0 + jitter, 20.0 + jitter / 2)


def _upload(client, headers, name: str, lon0: float, lat0: float,
            contribute: bool = True) -> str:
    gpx_bytes = _make_gpx_bytes(name, lon0, lat0)
    data = {"sport": "gravel", "contribute_heatmap": "true" if contribute else "false"}
    # Contributing to the community layer REQUIRES a consent row
    # (no-consent ⇒ ingested personal) — send it so these deletion tests
    # delete real community-eligible activities.
    if contribute:
        data.update({"consent_version": "v-del-seed", "consent_text": "ODbL ok", "locale": "fr"})
    resp = client.post(
        "/gpx/upload",
        headers=headers,
        files={"file": (f"{name}.gpx", gpx_bytes, "application/gpx+xml")},
        data=data,
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["activity_id"]


def _register_second_user(client) -> dict[str, str]:
    email = f"del_{uuid.uuid4().hex[:8]}@example.com"
    reg = client.post(
        "/auth/register",
        json={"email": email, "password": "password1234",
              "username": f"del_{uuid.uuid4().hex[:6]}"},
    )
    assert reg.status_code == 201
    return {"Authorization": f"Bearer {reg.json()['access_token']}"}


def _row_counts(activity_id: str) -> tuple[int, int]:
    """``(activities rows, activity_cells rows)`` for one activity id."""
    db = SessionLocal()
    try:
        acts = db.execute(
            sa_text("SELECT COUNT(*) FROM activities WHERE id = :a"), {"a": activity_id},
        ).scalar() or 0
        cells = db.execute(
            sa_text("SELECT COUNT(*) FROM activity_cells WHERE activity_id = :a"),
            {"a": activity_id},
        ).scalar() or 0
        return int(acts), int(cells)
    finally:
        db.close()


class TestDeleteActivity:
    def test_requires_auth(self, client):
        resp = client.delete(f"/me/activities/{MISSING_ID}")
        assert resp.status_code == 401

    def test_404_when_missing(self, client, auth_headers):
        resp = client.delete(f"/me/activities/{MISSING_ID}", headers=auth_headers)
        assert resp.status_code == 404

    def test_404_on_malformed_id(self, client, auth_headers):
        resp = client.delete("/me/activities/not-a-uuid", headers=auth_headers)
        assert resp.status_code == 404

    def test_404_when_not_owner_and_row_survives(self, client, auth_headers):
        lon0, lat0 = _unique_start()
        activity_id = _upload(client, auth_headers, "Owner Only Del", lon0, lat0)
        before = _row_counts(activity_id)
        assert before[0] == 1 and before[1] > 0

        headers2 = _register_second_user(client)
        resp = client.delete(f"/me/activities/{activity_id}", headers=headers2)
        assert resp.status_code == 404

        # The owner still sees it — nothing was deleted.
        still = client.get(f"/me/activities/{activity_id}", headers=auth_headers)
        assert still.status_code == 200
        assert _row_counts(activity_id) == before

    def test_delete_own_removes_row_and_private_cells(self, client, auth_headers):
        lon0, lat0 = _unique_start()
        activity_id = _upload(client, auth_headers, "Delete Me", lon0, lat0)
        acts, cells = _row_counts(activity_id)
        assert acts == 1 and cells > 0, "upload must index private activity_cells"

        resp = client.delete(f"/me/activities/{activity_id}", headers=auth_headers)
        assert resp.status_code == 204

        assert client.get(f"/me/activities/{activity_id}", headers=auth_headers).status_code == 404
        # Row gone AND its activity_cells cascaded with it.
        assert _row_counts(activity_id) == (0, 0)

    def test_second_delete_is_404(self, client, auth_headers):
        lon0, lat0 = _unique_start()
        activity_id = _upload(client, auth_headers, "Delete Twice", lon0, lat0)
        assert client.delete(f"/me/activities/{activity_id}", headers=auth_headers).status_code == 204
        assert client.delete(f"/me/activities/{activity_id}", headers=auth_headers).status_code == 404

    def test_delete_triggers_community_map_rebuild(self, client, auth_headers):
        """GDPR deletion MUST fire the build-pmtiles rebuild so the trace
        actually leaves the public map — there is no periodic scheduler, so
        without this the deleted trace would stay visible indefinitely.

        Patches the trigger at its source module (the service imports it at
        call time) — real handler, no inline mirror. FAILS on code that never
        triggers a rebuild.
        """
        from unittest.mock import MagicMock, patch

        lon0, lat0 = _unique_start()
        activity_id = _upload(client, auth_headers, "Del Trigger", lon0, lat0)
        fake = MagicMock(return_value=True)
        with patch("app.services.run_jobs.trigger_build_pmtiles_job", fake):
            resp = client.delete(f"/me/activities/{activity_id}", headers=auth_headers)
        assert resp.status_code == 204
        fake.assert_called_once()

    def test_noop_delete_does_not_trigger_rebuild(self, client, auth_headers):
        """A 404 (nothing deleted) must NOT fire a rebuild — the trigger sits
        after the ownership/existence guard."""
        from unittest.mock import MagicMock, patch

        fake = MagicMock(return_value=True)
        with patch("app.services.run_jobs.trigger_build_pmtiles_job", fake):
            resp = client.delete(f"/me/activities/{MISSING_ID}", headers=auth_headers)
        assert resp.status_code == 404
        fake.assert_not_called()

    def test_delete_preserves_other_users_contributions(self, client, auth_headers):
        """User A + B ride the same (unique) track; deleting A's activity must
        leave B's activity — and B's private cells — untouched."""
        lon0, lat0 = _unique_start()
        a_id = _upload(client, auth_headers, "Shared Track A", lon0, lat0)

        headers2 = _register_second_user(client)
        b_id = _upload(client, headers2, "Shared Track B", lon0, lat0)
        assert a_id != b_id
        b_before = _row_counts(b_id)
        assert b_before[0] == 1 and b_before[1] > 0

        resp = client.delete(f"/me/activities/{a_id}", headers=auth_headers)
        assert resp.status_code == 204

        assert _row_counts(a_id) == (0, 0)
        assert _row_counts(b_id) == b_before
        assert client.get(f"/me/activities/{b_id}", headers=headers2).status_code == 200

    def test_delete_without_heat_contribution(self, client, auth_headers):
        lon0, lat0 = _unique_start()
        activity_id = _upload(client, auth_headers, "No Heat", lon0, lat0, contribute=False)
        resp = client.delete(f"/me/activities/{activity_id}", headers=auth_headers)
        assert resp.status_code == 204
        assert client.get(f"/me/activities/{activity_id}", headers=auth_headers).status_code == 404
