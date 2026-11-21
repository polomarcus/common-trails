"""A trip stage only uses routes its owner can see, and listing by visibility
only enumerates public routes (plus the caller's own).

Same visibility rule as GET /routes/{id}: a live route that is public /
unlisted, or owned by the user. Drives the real /trips and /routes handlers.
"""
from __future__ import annotations

import json
import uuid

GEOJSON_LINE = json.dumps({
    "type": "LineString",
    "coordinates": [[3.870, 43.610, 62], [3.874, 43.617, 75], [3.878, 43.624, 95]],
})


def _register(client) -> dict[str, str]:
    resp = client.post(
        "/auth/register",
        json={"email": f"vis_{uuid.uuid4().hex[:8]}@example.com", "password": "testpass123",
              "username": f"u_{uuid.uuid4().hex[:6]}"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _route(client, headers, visibility: str, name: str = "R") -> str:
    resp = client.post(
        "/routes",
        json={"name": name, "sport": "gravel", "visibility": visibility,
              "geometry_geojson": GEOJSON_LINE, "distance_m": 12000, "elevation_gain_m": 150},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _trip(client, headers, visibility: str = "public") -> str:
    resp = client.post(
        "/trips", json={"name": "T", "sport": "gravel", "visibility": visibility, "status": "planned"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class TestStageRouteMustBeVisible:
    def test_add_stage_with_someone_elses_private_route_is_404(self, client, auth_headers):
        other = _register(client)
        private_id = _route(client, other, "private", name="Secret line")
        trip_id = _trip(client, auth_headers)
        resp = client.post(f"/trips/{trip_id}/stages", json={"route_id": private_id}, headers=auth_headers)
        assert resp.status_code == 404, resp.text
        assert "Secret line" not in resp.text

    def test_add_stage_with_a_deleted_route_is_404(self, client, auth_headers):
        other = _register(client)
        route_id = _route(client, other, "public")
        assert client.delete(f"/routes/{route_id}", headers=other).status_code == 204
        trip_id = _trip(client, auth_headers)
        resp = client.post(f"/trips/{trip_id}/stages", json={"route_id": route_id}, headers=auth_headers)
        assert resp.status_code == 404, resp.text

    def test_public_unlisted_and_own_private_routes_are_accepted(self, client, auth_headers):
        other = _register(client)
        trip_id = _trip(client, auth_headers)
        for route_id in (
            _route(client, other, "public"),
            _route(client, other, "unlisted"),
            _route(client, auth_headers, "private"),
        ):
            resp = client.post(f"/trips/{trip_id}/stages", json={"route_id": route_id}, headers=auth_headers)
            assert resp.status_code == 201, resp.text
            assert resp.json()["route_geometry_geojson"]

    def test_update_stage_to_someone_elses_private_route_is_404(self, client, auth_headers):
        other = _register(client)
        trip_id = _trip(client, auth_headers)
        stage = client.post(
            f"/trips/{trip_id}/stages", json={"route_id": _route(client, auth_headers, "public")},
            headers=auth_headers,
        ).json()
        private_id = _route(client, other, "private")
        resp = client.put(
            f"/trips/{trip_id}/stages/{stage['id']}", json={"route_id": private_id}, headers=auth_headers,
        )
        assert resp.status_code == 404, resp.text
        detail = client.get(f"/trips/{trip_id}", headers=auth_headers).json()
        assert detail["stages"][0]["route_id"] == stage["route_id"]


class TestReadsHideRoutesNoLongerVisible:
    def test_route_made_private_by_its_author_disappears_from_the_trip(self, client, auth_headers):
        """The author of a route used in someone else's public trip makes it
        private: the trip (detail, stage GPX, merged GPX, ZIP) stops serving
        its geometry, to anyone."""
        author = _register(client)
        route_id = _route(client, author, "public", name="Was public")
        trip_id = _trip(client, auth_headers, visibility="public")
        stage = client.post(
            f"/trips/{trip_id}/stages", json={"route_id": route_id, "title": "Day 1"}, headers=auth_headers,
        ).json()
        assert stage["route_geometry_geojson"]

        resp = client.patch(f"/routes/{route_id}", json={"visibility": "private"}, headers=author)
        assert resp.status_code == 200, resp.text

        for headers in (auth_headers, {}):
            detail = client.get(f"/trips/{trip_id}", headers=headers).json()
            s = detail["stages"][0]
            assert s["route_geometry_geojson"] is None
            assert s["route_name"] is None
            assert client.get(
                f"/trips/{trip_id}/stages/{stage['id']}/gpx", headers=headers,
            ).status_code == 404
            assert "<trkpt" not in client.get(f"/trips/{trip_id}/gpx", headers=headers).text
            assert client.get(f"/trips/{trip_id}/gpx/zip", headers=headers).content.count(b".gpx") == 0

    def test_own_private_route_stays_visible_in_own_trip(self, client, auth_headers):
        trip_id = _trip(client, auth_headers)
        route_id = _route(client, auth_headers, "private")
        stage = client.post(f"/trips/{trip_id}/stages", json={"route_id": route_id}, headers=auth_headers).json()
        assert client.get(f"/trips/{trip_id}/stages/{stage['id']}/gpx", headers=auth_headers).status_code == 200


class TestRouteListingByVisibility:
    def test_unlisted_listing_is_own_routes_only(self, client, auth_headers):
        other = _register(client)
        theirs = _route(client, other, "unlisted", name="Their unlisted")
        mine = _route(client, auth_headers, "unlisted", name="My unlisted")

        resp = client.get("/routes?visibility=unlisted", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        ids = {r["id"] for r in resp.json()}
        assert mine in ids
        assert theirs not in ids

    def test_unlisted_listing_requires_auth(self, client):
        assert client.get("/routes?visibility=unlisted").status_code == 401

    def test_unknown_visibility_is_422(self, client):
        assert client.get("/routes?visibility=everything").status_code == 422

    def test_public_listing_unchanged(self, client, auth_headers):
        public_id = _route(client, auth_headers, "public")
        unlisted_id = _route(client, auth_headers, "unlisted")
        ids = {r["id"] for r in client.get("/routes?visibility=public").json()}
        assert public_id in ids
        assert unlisted_id not in ids
