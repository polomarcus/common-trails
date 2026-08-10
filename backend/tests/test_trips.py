"""Tests for Trip Collections API (CRUD, stages, POIs, export, fork)."""
import io
import json
import zipfile

import pytest

pytestmark = pytest.mark.slow

GEOJSON_LINE = json.dumps({
    "type": "LineString",
    "coordinates": [
        [3.870, 43.610, 62], [3.874, 43.617, 75], [3.878, 43.624, 95],
        [3.882, 43.631, 130], [3.887, 43.638, 175],
    ],
})


def _create_route(client, auth_headers, name="Test Route"):
    """Helper: create a public route with geometry and return its data."""
    resp = client.post(
        "/routes",
        json={"name": name, "sport": "gravel", "visibility": "public",
              "geometry_geojson": GEOJSON_LINE, "distance_m": 35000, "elevation_gain_m": 320},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()


def _create_trip(client, auth_headers, **overrides):
    """Helper: create a trip and return its data."""
    body = {"name": "Test Trip", "sport": "gravel", "visibility": "private", "status": "draft"}
    body.update(overrides)
    resp = client.post("/trips", json=body, headers=auth_headers)
    assert resp.status_code == 201
    return resp.json()


class TestTripCRUD:
    def test_create_trip(self, client, auth_headers):
        data = _create_trip(client, auth_headers)
        assert data["name"] == "Test Trip"
        assert data["sport"] == "gravel"
        assert data["status"] == "draft"
        assert "id" in data

    def test_list_trips_public(self, client, auth_headers):
        _create_trip(client, auth_headers, name="Public Trip", visibility="public", status="planned")
        resp = client.get("/trips?visibility=public")
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()]
        assert "Public Trip" in names

    def test_get_trip_detail_with_stages(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        route = _create_route(client, auth_headers)
        client.post(
            f"/trips/{trip['id']}/stages",
            json={"route_id": route["id"], "day_index": 0, "title": "Étape 1"},
            headers=auth_headers,
        )
        resp = client.get(f"/trips/{trip['id']}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["stages"]) == 1
        assert data["stages"][0]["route_name"] == route["name"]

    def test_update_trip(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        resp = client.put(
            f"/trips/{trip['id']}",
            json={"name": "Updated Trip", "status": "planned"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Trip"
        assert resp.json()["status"] == "planned"

    def test_delete_trip(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        resp = client.delete(f"/trips/{trip['id']}", headers=auth_headers)
        assert resp.status_code == 204
        assert client.get(f"/trips/{trip['id']}", headers=auth_headers).status_code == 404

    def test_draft_excluded_from_public(self, client, auth_headers):
        _create_trip(client, auth_headers, name="Draft Hidden", visibility="public", status="draft")
        client.cookies.clear()  # simulate anonymous access
        resp = client.get("/trips?visibility=public")
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()]
        assert "Draft Hidden" not in names

    def test_owner_required_for_update(self, client, auth_headers):
        trip = _create_trip(client, auth_headers, visibility="public", status="planned")
        # Register a second user
        import uuid
        email2 = f"other_{uuid.uuid4().hex[:8]}@example.com"
        resp2 = client.post(
            "/auth/register",
            json={"email": email2, "password": "password1234", "username": "other"},
        )
        other_headers = {"Authorization": f"Bearer {resp2.json()['access_token']}"}
        resp = client.put(
            f"/trips/{trip['id']}",
            json={"name": "Hacked"},
            headers=other_headers,
        )
        assert resp.status_code == 403


class TestTripStages:
    def test_add_stage_with_route(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        route = _create_route(client, auth_headers, name="Stage Route")
        resp = client.post(
            f"/trips/{trip['id']}/stages",
            json={"route_id": route["id"], "day_index": 0, "title": "Day 1", "lodging_type": "camping"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Day 1"
        assert data["route_name"] == "Stage Route"
        assert data["lodging_type"] == "camping"

    def test_reorder_stages(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        s1 = client.post(
            f"/trips/{trip['id']}/stages",
            json={"day_index": 0, "title": "First"},
            headers=auth_headers,
        ).json()
        s2 = client.post(
            f"/trips/{trip['id']}/stages",
            json={"day_index": 1, "title": "Second"},
            headers=auth_headers,
        ).json()

        # Swap order
        resp = client.put(
            f"/trips/{trip['id']}/stages/reorder",
            json={"stage_ids": [s2["id"], s1["id"]]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        stages = resp.json()
        assert stages[0]["id"] == s2["id"]
        assert stages[0]["day_index"] == 0
        assert stages[1]["id"] == s1["id"]
        assert stages[1]["day_index"] == 1

    def test_remove_stage(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        stage = client.post(
            f"/trips/{trip['id']}/stages",
            json={"day_index": 0, "title": "Removable"},
            headers=auth_headers,
        ).json()
        resp = client.delete(f"/trips/{trip['id']}/stages/{stage['id']}", headers=auth_headers)
        assert resp.status_code == 204

    def test_stage_enriched_with_route_data(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        route = _create_route(client, auth_headers, name="Enriched Route")
        stage = client.post(
            f"/trips/{trip['id']}/stages",
            json={"route_id": route["id"], "day_index": 0},
            headers=auth_headers,
        ).json()
        assert stage["route_distance_m"] == 35000
        assert stage["route_elevation_gain_m"] == 320

    def test_stats_recomputed_on_stage_change(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        route = _create_route(client, auth_headers, name="Stats Route")
        client.post(
            f"/trips/{trip['id']}/stages",
            json={"route_id": route["id"], "day_index": 0},
            headers=auth_headers,
        )
        detail = client.get(f"/trips/{trip['id']}", headers=auth_headers).json()
        assert detail["total_distance_m"] == 35000
        assert detail["total_dplus_m"] == 320


class TestTripPOIs:
    def test_add_poi_global(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        resp = client.post(
            f"/trips/{trip['id']}/pois",
            json={"type": "water", "lon": 3.87, "lat": 43.61, "name": "Source"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "water"
        assert data["name"] == "Source"

    def test_add_poi_to_stage(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        stage = client.post(
            f"/trips/{trip['id']}/stages",
            json={"day_index": 0, "title": "Day 1"},
            headers=auth_headers,
        ).json()
        resp = client.post(
            f"/trips/{trip['id']}/pois",
            json={"type": "food", "lon": 3.88, "lat": 43.62, "name": "Boulangerie", "stage_id": stage["id"]},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["stage_id"] == stage["id"]

    def test_update_poi(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        poi = client.post(
            f"/trips/{trip['id']}/pois",
            json={"type": "water", "lon": 3.87, "lat": 43.61},
            headers=auth_headers,
        ).json()
        resp = client.put(
            f"/trips/{trip['id']}/pois/{poi['id']}",
            json={"name": "Updated Source", "type": "food"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Source"
        assert resp.json()["type"] == "food"

    def test_delete_poi(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        poi = client.post(
            f"/trips/{trip['id']}/pois",
            json={"type": "camp", "lon": 3.87, "lat": 43.61},
            headers=auth_headers,
        ).json()
        resp = client.delete(f"/trips/{trip['id']}/pois/{poi['id']}", headers=auth_headers)
        assert resp.status_code == 204

    def test_poi_types_validated(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        resp = client.post(
            f"/trips/{trip['id']}/pois",
            json={"type": "invalid_type", "lon": 3.87, "lat": 43.61},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestTripExport:
    def test_merged_gpx_valid_xml(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        route = _create_route(client, auth_headers, name="GPX Route")
        client.post(
            f"/trips/{trip['id']}/stages",
            json={"route_id": route["id"], "day_index": 0, "title": "Étape 1"},
            headers=auth_headers,
        )
        resp = client.get(f"/trips/{trip['id']}/gpx", headers=auth_headers)
        assert resp.status_code == 200
        assert "application/gpx" in resp.headers["content-type"]
        body = resp.text
        assert "<gpx" in body
        assert "<trk>" in body
        assert "<trkpt" in body

    def test_zip_contains_one_file_per_stage(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        route1 = _create_route(client, auth_headers, name="ZIP Route 1")
        route2 = _create_route(client, auth_headers, name="ZIP Route 2")
        client.post(
            f"/trips/{trip['id']}/stages",
            json={"route_id": route1["id"], "day_index": 0, "title": "Jour 1"},
            headers=auth_headers,
        )
        client.post(
            f"/trips/{trip['id']}/stages",
            json={"route_id": route2["id"], "day_index": 1, "title": "Jour 2"},
            headers=auth_headers,
        )
        resp = client.get(f"/trips/{trip['id']}/gpx/zip", headers=auth_headers)
        assert resp.status_code == 200
        assert "application/zip" in resp.headers["content-type"]
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
            assert len(names) == 2
            assert all(n.endswith(".gpx") for n in names)

    def test_gpx_includes_elevation(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        route = _create_route(client, auth_headers, name="Elev Route")
        client.post(
            f"/trips/{trip['id']}/stages",
            json={"route_id": route["id"], "day_index": 0},
            headers=auth_headers,
        )
        resp = client.get(f"/trips/{trip['id']}/gpx", headers=auth_headers)
        assert "<ele>" in resp.text


class TestTripFork:
    def test_fork_copies_stages_and_pois(self, client, auth_headers):
        trip = _create_trip(client, auth_headers, visibility="public", status="planned")
        route = _create_route(client, auth_headers, name="Fork Route")
        client.post(
            f"/trips/{trip['id']}/stages",
            json={"route_id": route["id"], "day_index": 0, "title": "Day 1"},
            headers=auth_headers,
        )
        client.post(
            f"/trips/{trip['id']}/pois",
            json={"type": "water", "lon": 3.87, "lat": 43.61, "name": "Source"},
            headers=auth_headers,
        )
        resp = client.post(f"/trips/{trip['id']}/fork", headers=auth_headers)
        assert resp.status_code == 201
        fork = resp.json()
        assert fork["id"] != trip["id"]
        assert len(fork["stages"]) == 1
        assert len(fork["pois"]) == 1

    def test_fork_sets_private_visibility(self, client, auth_headers):
        trip = _create_trip(client, auth_headers, visibility="public", status="planned")
        fork = client.post(f"/trips/{trip['id']}/fork", headers=auth_headers).json()
        assert fork["visibility"] == "private"
        assert fork["status"] == "draft"

    def test_fork_preserves_route_links(self, client, auth_headers):
        trip = _create_trip(client, auth_headers, visibility="public", status="planned")
        route = _create_route(client, auth_headers, name="Linked Route")
        client.post(
            f"/trips/{trip['id']}/stages",
            json={"route_id": route["id"], "day_index": 0},
            headers=auth_headers,
        )
        fork = client.post(f"/trips/{trip['id']}/fork", headers=auth_headers).json()
        # Forked stage should reference the same route
        assert fork["stages"][0]["route_id"] == route["id"]
        assert fork["forked_from_id"] == trip["id"]


class TestTripListFiltering:
    """Verify SQL-level filtering in list_trips."""

    def test_private_trips_hidden_from_anon(self, client, auth_headers):
        _create_trip(client, auth_headers, name="My Private", visibility="private", status="planned")
        client.cookies.clear()
        resp = client.get("/trips")
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()]
        assert "My Private" not in names

    def test_draft_hidden_from_anon(self, client, auth_headers):
        _create_trip(client, auth_headers, name="My Draft", visibility="public", status="draft")
        client.cookies.clear()
        resp = client.get("/trips")
        names = [t["name"] for t in resp.json()]
        assert "My Draft" not in names

    def test_owner_sees_own_draft(self, client, auth_headers):
        _create_trip(client, auth_headers, name="Owner Draft", visibility="public", status="draft")
        resp = client.get("/trips", headers=auth_headers)
        names = [t["name"] for t in resp.json()]
        assert "Owner Draft" in names

    def test_owner_sees_own_private(self, client, auth_headers):
        _create_trip(client, auth_headers, name="Owner Private", visibility="private", status="planned")
        resp = client.get("/trips", headers=auth_headers)
        names = [t["name"] for t in resp.json()]
        assert "Owner Private" in names

    def test_sport_filter(self, client, auth_headers):
        _create_trip(client, auth_headers, name="Road Trip", sport="road", visibility="public", status="planned")
        _create_trip(client, auth_headers, name="MTB Trip", sport="mtb", visibility="public", status="planned")
        resp = client.get("/trips?sport=road")
        names = [t["name"] for t in resp.json()]
        assert "Road Trip" in names
        assert "MTB Trip" not in names

    def test_status_filter(self, client, auth_headers):
        _create_trip(client, auth_headers, name="Planned", visibility="public", status="planned")
        _create_trip(client, auth_headers, name="Completed", visibility="public", status="completed")
        resp = client.get("/trips?status=completed")
        names = [t["name"] for t in resp.json()]
        assert "Completed" in names
        assert "Planned" not in names

    def test_visibility_filter_public(self, client, auth_headers):
        _create_trip(client, auth_headers, name="Pub", visibility="public", status="planned")
        _create_trip(client, auth_headers, name="Unl", visibility="unlisted", status="planned")
        resp = client.get("/trips?visibility=public")
        names = [t["name"] for t in resp.json()]
        assert "Pub" in names
        assert "Unl" not in names

    def test_update_visibility(self, client, auth_headers):
        trip = _create_trip(client, auth_headers, visibility="private")
        resp = client.put(
            f"/trips/{trip['id']}",
            json={"visibility": "public"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["visibility"] == "public"

    def test_update_status(self, client, auth_headers):
        trip = _create_trip(client, auth_headers, status="draft")
        resp = client.put(
            f"/trips/{trip['id']}",
            json={"status": "completed"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    def test_update_description(self, client, auth_headers):
        trip = _create_trip(client, auth_headers)
        resp = client.put(
            f"/trips/{trip['id']}",
            json={"description": "New description"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "New description"


class TestPrivateTripVisibility:
    """Verify private trips return 404 (not 403) and GPX exports are protected."""

    def _create_private_trip_with_stage(self, client, auth_headers):
        route = _create_route(client, auth_headers, name="Private Trip Route")
        trip = _create_trip(client, auth_headers, visibility="private", status="planned")
        stage = client.post(
            f"/trips/{trip['id']}/stages",
            json={"route_id": route["id"], "day_index": 0, "title": "Étape 1"},
            headers=auth_headers,
        ).json()
        return trip, stage

    def test_get_private_trip_returns_404_not_403(self, client, auth_headers):
        trip = _create_trip(client, auth_headers, visibility="private")
        # Anonymous access
        client.cookies.clear()
        resp = client.get(f"/trips/{trip['id']}")
        assert resp.status_code == 404

    def test_private_trip_gpx_404_for_anon(self, client, auth_headers):
        trip, stage = self._create_private_trip_with_stage(client, auth_headers)
        client.cookies.clear()
        resp = client.get(f"/trips/{trip['id']}/gpx")
        assert resp.status_code == 404

    def test_private_trip_stage_gpx_404_for_anon(self, client, auth_headers):
        trip, stage = self._create_private_trip_with_stage(client, auth_headers)
        client.cookies.clear()
        resp = client.get(f"/trips/{trip['id']}/stages/{stage['id']}/gpx")
        assert resp.status_code == 404

    def test_private_trip_gpx_zip_404_for_anon(self, client, auth_headers):
        trip, stage = self._create_private_trip_with_stage(client, auth_headers)
        client.cookies.clear()
        resp = client.get(f"/trips/{trip['id']}/gpx/zip")
        assert resp.status_code == 404

    def test_private_trip_gpx_ok_for_owner(self, client, auth_headers):
        trip, stage = self._create_private_trip_with_stage(client, auth_headers)
        resp = client.get(f"/trips/{trip['id']}/gpx", headers=auth_headers)
        assert resp.status_code == 200
        assert "<gpx" in resp.text
