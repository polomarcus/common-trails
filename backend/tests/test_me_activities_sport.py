"""Tests for PATCH /me/activities/{id} and PATCH /me/activities/sport.

Drives the REAL endpoints via TestClient (no inline mirrors): a user fixes a
wrong sport after import so the ride stops polluting the wrong community layer.
The PMTiles rebuild trigger is mocked at its source module boundary
(``app.services.run_jobs.trigger_build_pmtiles_job``).
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from tests.test_me_activities_delete import (
    MISSING_ID,
    _register_second_user,
    _upload,
)


@pytest.fixture(autouse=True)
def _no_debounce(monkeypatch):
    """Every trigger fires in these tests (debounce is process-global)."""
    from app.services.run_jobs import reset_build_pmtiles_debounce

    monkeypatch.setenv("BUILD_PMTILES_DEBOUNCE_S", "0")
    reset_build_pmtiles_debounce()
    yield
    reset_build_pmtiles_debounce()


def _sport(activity_id: str) -> str:
    db = SessionLocal()
    try:
        return db.execute(sa_text("SELECT sport FROM activities WHERE id = :a"), {"a": activity_id}).scalar()
    finally:
        db.close()


def _unique_start() -> tuple[float, float]:
    """Per-run unique (lon, lat) in the Pacific — no OSM coverage, and a
    different area than the deletion tests' Atlantic box so the two suites'
    random tracks never share heat edges."""
    jitter = (uuid.uuid4().int % 100_000) * 1e-5  # 0 .. 1.0 degree
    return (-140.0 + jitter, 10.0 + jitter / 2)


def _new(client, headers, name="Sport Fix", contribute=True) -> str:
    lon0, lat0 = _unique_start()
    return _upload(client, headers, name, lon0, lat0, contribute=contribute)


class TestPatchSingle:
    def test_requires_auth(self, client):
        resp = client.patch(f"/me/activities/{MISSING_ID}", json={"sport": "mtb"})
        assert resp.status_code == 401

    def test_owner_can_change_sport(self, client, auth_headers):
        aid = _new(client, auth_headers)
        assert _sport(aid) == "gravel"
        resp = client.patch(f"/me/activities/{aid}", headers=auth_headers, json={"sport": "mtb"})
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"id": aid, "sport": "mtb", "changed": True}
        assert _sport(aid) == "mtb"
        # The by-id GET (what the map card reads) reflects it.
        got = client.get(f"/me/activities/{aid}", headers=auth_headers)
        assert got.json()["properties"]["sport"] == "mtb"

    def test_other_user_gets_404_and_row_unchanged(self, client, auth_headers):
        aid = _new(client, auth_headers)
        headers2 = _register_second_user(client)
        resp = client.patch(f"/me/activities/{aid}", headers=headers2, json={"sport": "mtb"})
        assert resp.status_code == 404
        assert _sport(aid) == "gravel"

    def test_missing_and_malformed_id_404(self, client, auth_headers):
        assert (
            client.patch(f"/me/activities/{MISSING_ID}", headers=auth_headers, json={"sport": "mtb"}).status_code == 404
        )
        assert client.patch("/me/activities/not-a-uuid", headers=auth_headers, json={"sport": "mtb"}).status_code == 404

    @pytest.mark.parametrize("bad", ["offroad", "ski", "", "MTB"])
    def test_invalid_sport_422(self, client, auth_headers, bad):
        aid = _new(client, auth_headers)
        resp = client.patch(f"/me/activities/{aid}", headers=auth_headers, json={"sport": bad})
        assert resp.status_code == 422
        assert _sport(aid) == "gravel"

    def test_change_triggers_rebuild(self, client, auth_headers):
        aid = _new(client, auth_headers)
        fake = MagicMock(return_value=True)
        with patch("app.services.run_jobs.trigger_build_pmtiles_job", fake):
            resp = client.patch(f"/me/activities/{aid}", headers=auth_headers, json={"sport": "road"})
        assert resp.status_code == 200
        fake.assert_called_once()

    def test_same_sport_is_noop_without_rebuild(self, client, auth_headers):
        aid = _new(client, auth_headers)
        fake = MagicMock(return_value=True)
        with patch("app.services.run_jobs.trigger_build_pmtiles_job", fake):
            resp = client.patch(f"/me/activities/{aid}", headers=auth_headers, json={"sport": "gravel"})
        assert resp.status_code == 200
        assert resp.json()["changed"] is False
        fake.assert_not_called()

    def test_personal_only_activity_does_not_trigger_rebuild(self, client, auth_headers):
        """A non-contributed ride never reaches the static display → no job."""
        aid = _new(client, auth_headers, contribute=False)
        fake = MagicMock(return_value=True)
        with patch("app.services.run_jobs.trigger_build_pmtiles_job", fake):
            resp = client.patch(f"/me/activities/{aid}", headers=auth_headers, json={"sport": "mtb"})
        assert resp.status_code == 200
        assert _sport(aid) == "mtb"
        fake.assert_not_called()


class TestPatchBulk:
    def test_bulk_changes_only_owned(self, client, auth_headers):
        a1 = _new(client, auth_headers, "Bulk 1")
        a2 = _new(client, auth_headers, "Bulk 2")
        headers2 = _register_second_user(client)
        foreign = _new(client, headers2, "Foreign")

        fake = MagicMock(return_value=True)
        with patch("app.services.run_jobs.trigger_build_pmtiles_job", fake):
            resp = client.patch(
                "/me/activities/sport",
                headers=auth_headers,
                json={"activity_ids": [a1, a2, foreign, MISSING_ID, "junk"], "sport": "mtb"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched"] == 2
        assert body["updated"] == 2
        assert set(body["updated_ids"]) == {a1, a2}
        assert _sport(a1) == _sport(a2) == "mtb"
        assert _sport(foreign) == "gravel"
        # One rebuild for the whole batch, not one per activity.
        fake.assert_called_once()

    def test_bulk_cap(self, client, auth_headers):
        from app.api.me_activities import BULK_SPORT_MAX_IDS

        ids = [str(uuid.uuid4()) for _ in range(BULK_SPORT_MAX_IDS + 1)]
        resp = client.patch("/me/activities/sport", headers=auth_headers, json={"activity_ids": ids, "sport": "mtb"})
        assert resp.status_code == 422

    def test_bulk_empty_list_422(self, client, auth_headers):
        resp = client.patch("/me/activities/sport", headers=auth_headers, json={"activity_ids": [], "sport": "mtb"})
        assert resp.status_code == 422

    def test_bulk_invalid_sport_422(self, client, auth_headers):
        aid = _new(client, auth_headers)
        resp = client.patch(
            "/me/activities/sport", headers=auth_headers, json={"activity_ids": [aid], "sport": "offroad"}
        )
        assert resp.status_code == 422
        assert _sport(aid) == "gravel"

    def test_bulk_nothing_owned_no_rebuild(self, client, auth_headers):
        fake = MagicMock(return_value=True)
        with patch("app.services.run_jobs.trigger_build_pmtiles_job", fake):
            resp = client.patch(
                "/me/activities/sport", headers=auth_headers, json={"activity_ids": [MISSING_ID], "sport": "mtb"}
            )
        assert resp.status_code == 200
        assert resp.json()["matched"] == 0
        fake.assert_not_called()
