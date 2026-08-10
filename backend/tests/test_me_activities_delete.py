"""Tests for DELETE /me/activities/{id} — GDPR per-activity deletion.

Drives the REAL endpoint via TestClient (no inline mirrors). Heat-layer
assertions hit the real DB: per-activity attribution exists since
migration 0052 (heat_edge_contributors PK includes activity_id), so a
deletion must remove the activity's contributor rows, recount the
touched heat_edges, and drop edges left with zero contributors — while
leaving other users' contributions intact.

Uses per-run unique coordinates (grid-fallback territory, far from any
OSM import) so assertions are deterministic on a shared local DB.
"""
import uuid

import gpxpy
import gpxpy.gpx
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal

MISSING_ID = "00000000-0000-0000-0000-000000000000"


def _make_gpx_bytes(name: str, lon0: float, lat0: float, n_points: int = 8) -> bytes:
    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(track)
    seg = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(seg)
    for i in range(n_points):
        seg.points.append(
            gpxpy.gpx.GPXTrackPoint(
                latitude=lat0 + i * 0.001,
                longitude=lon0 + i * 0.001,
                elevation=50 + i * 5,
            )
        )
    return gpx.to_xml().encode()


def _unique_start() -> tuple[float, float]:
    """Per-run unique (lon, lat) in the Atlantic — no OSM coverage, so the
    heat edges are grid-fallback and owned exclusively by this test run."""
    jitter = (uuid.uuid4().int % 100_000) * 1e-5  # 0 .. 1.0 degree
    return (-30.0 + jitter, 20.0 + jitter / 2)


def _upload(client, headers, name: str, lon0: float, lat0: float,
            contribute: bool = True) -> str:
    gpx_bytes = _make_gpx_bytes(name, lon0, lat0)
    data = {"sport": "gravel", "contribute_heatmap": "true" if contribute else "false"}
    # Contributing to the community layer now REQUIRES a consent row
    # (no-consent ⇒ ingested personal, no heat) — send it so these
    # deletion tests still seed real heat_edge_contributors.
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


def _contrib_count(db, activity_id: str) -> int:
    return db.execute(
        sa_text("SELECT COUNT(*) FROM heat_edge_contributors WHERE activity_id = :a"),
        {"a": activity_id},
    ).scalar() or 0


def _contrib_edge_keys(db, activity_id: str) -> list[str]:
    return [r[0] for r in db.execute(
        sa_text("SELECT edge_key FROM heat_edge_contributors WHERE activity_id = :a"),
        {"a": activity_id},
    )]


def _edges(db, edge_keys: list[str]) -> list[tuple[str, int, int]]:
    return [tuple(r) for r in db.execute(
        sa_text("SELECT edge_key, user_count, pass_count FROM heat_edges "
                "WHERE edge_key = ANY(:keys)"),
        {"keys": edge_keys},
    )]


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

        headers2 = _register_second_user(client)
        resp = client.delete(f"/me/activities/{activity_id}", headers=headers2)
        assert resp.status_code == 404

        # The owner still sees it — nothing was deleted.
        still = client.get(f"/me/activities/{activity_id}", headers=auth_headers)
        assert still.status_code == 200
        db = SessionLocal()
        try:
            assert _contrib_count(db, activity_id) > 0
        finally:
            db.close()

    def test_delete_own_removes_row_and_heat_contributions(self, client, auth_headers):
        lon0, lat0 = _unique_start()
        activity_id = _upload(client, auth_headers, "Delete Me", lon0, lat0)

        db = SessionLocal()
        try:
            edge_keys = _contrib_edge_keys(db, activity_id)
            assert edge_keys, "upload with contribute_heatmap=true wrote no contributors"
            assert len(_edges(db, edge_keys)) == len(set(edge_keys))
        finally:
            db.close()

        resp = client.delete(f"/me/activities/{activity_id}", headers=auth_headers)
        assert resp.status_code == 204

        assert client.get(f"/me/activities/{activity_id}", headers=auth_headers).status_code == 404

        db = SessionLocal()
        try:
            assert _contrib_count(db, activity_id) == 0
            # Sole contributor on unique coords → every touched edge is gone.
            assert _edges(db, edge_keys) == []
        finally:
            db.close()

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
        """User A + B ride the same (unique) track; deleting A's activity
        must keep the shared edges alive with B's counts recounted."""
        lon0, lat0 = _unique_start()
        a_id = _upload(client, auth_headers, "Shared Track A", lon0, lat0)

        headers2 = _register_second_user(client)
        b_id = _upload(client, headers2, "Shared Track B", lon0, lat0)

        db = SessionLocal()
        try:
            a_keys = set(_contrib_edge_keys(db, a_id))
            b_keys = set(_contrib_edge_keys(db, b_id))
            shared = sorted(a_keys & b_keys)
            assert shared, "identical tracks produced no shared edge_keys"
            for _, uc, pc in _edges(db, shared):
                assert uc == 2 and pc == 2
        finally:
            db.close()

        resp = client.delete(f"/me/activities/{a_id}", headers=auth_headers)
        assert resp.status_code == 204

        db = SessionLocal()
        try:
            assert _contrib_count(db, a_id) == 0
            assert _contrib_count(db, b_id) == len(b_keys)
            rows = _edges(db, shared)
            assert len(rows) == len(shared), "shared edges must survive B's contribution"
            for _, uc, pc in rows:
                assert uc == 1 and pc == 1
        finally:
            db.close()

    def test_delete_without_heat_contribution(self, client, auth_headers):
        lon0, lat0 = _unique_start()
        activity_id = _upload(client, auth_headers, "No Heat", lon0, lat0, contribute=False)
        resp = client.delete(f"/me/activities/{activity_id}", headers=auth_headers)
        assert resp.status_code == 204
        assert client.get(f"/me/activities/{activity_id}", headers=auth_headers).status_code == 404
