"""Tests for cross-user data isolation.

Verifies that user A's private routes, trips, and drafts are
inaccessible to user B — no read, update, or delete.
"""
import json
import uuid

import pytest

GEOJSON_LINE = json.dumps({
    "type": "LineString",
    "coordinates": [[4.83, 45.75], [4.84, 45.76], [4.85, 45.77]],
})


def _register(client, prefix="iso"):
    """Register a fresh user and return (auth_headers, user_id)."""
    email = f"{prefix}_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "testpass123", "username": f"u_{uuid.uuid4().hex[:6]}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    return {"Authorization": f"Bearer {data['access_token']}"}, data["user_id"]


@pytest.fixture
def two_users(client):
    """Create two independent users and return their auth headers."""
    headers_a, uid_a = _register(client, "alice")
    headers_b, uid_b = _register(client, "bob")
    return {"a": headers_a, "b": headers_b, "uid_a": uid_a, "uid_b": uid_b}


class TestRouteIsolation:
    """User B cannot modify or delete user A's routes."""

    def test_cannot_update_other_users_route(self, client, two_users):
        # User A creates a public route
        route = client.post(
            "/routes",
            json={"name": "Alice Route", "sport": "road", "visibility": "public",
                  "geometry_geojson": GEOJSON_LINE},
            headers=two_users["a"],
        ).json()

        # User B tries to update it
        resp = client.put(
            f"/routes/{route['id']}",
            json={"name": "Hacked", "sport": "road", "visibility": "public"},
            headers=two_users["b"],
        )
        assert resp.status_code == 403

    def test_cannot_delete_other_users_route(self, client, two_users):
        route = client.post(
            "/routes",
            json={"name": "Alice Route Del", "sport": "gravel", "visibility": "public"},
            headers=two_users["a"],
        ).json()

        resp = client.delete(f"/routes/{route['id']}", headers=two_users["b"])
        assert resp.status_code == 403

    def test_private_route_hidden_from_public_list(self, client, two_users):
        """Private routes should not appear in public listing."""
        client.post(
            "/routes",
            json={"name": "Secret Route", "sport": "mtb", "visibility": "private"},
            headers=two_users["a"],
        )
        resp = client.get("/routes?visibility=public")
        assert resp.status_code == 200
        names = [r["name"] for r in resp.json()]
        assert "Secret Route" not in names

    def test_cannot_create_tag_on_other_users_route(self, client, two_users):
        """User B cannot tag user A's route."""
        route = client.post(
            "/routes",
            json={"name": "TagTarget", "sport": "road", "visibility": "public",
                  "geometry_geojson": GEOJSON_LINE},
            headers=two_users["a"],
        ).json()

        resp = client.post(
            f"/routes/{route['id']}/tags",
            json={"tag": "v1.0", "version_id": route["current_version_id"]},
            headers=two_users["b"],
        )
        assert resp.status_code == 403


class TestDraftIsolation:
    """User B cannot see or modify user A's drafts."""

    def test_drafts_are_per_user(self, client, two_users):
        """GET /me/drafts only returns the current user's drafts."""
        # User A creates a draft
        client.post(
            "/routes",
            json={"name": "Alice Draft", "sport": "road", "visibility": "private", "status": "draft"},
            headers=two_users["a"],
        )

        # User B's drafts should not include Alice's draft
        resp = client.get("/me/drafts", headers=two_users["b"])
        assert resp.status_code == 200
        names = [d["name"] for d in resp.json()]
        assert "Alice Draft" not in names

    def test_cannot_sync_draft_as_other_user(self, client, two_users):
        """User A creates a draft via PUT /me/drafts/{id}, user B cannot overwrite it."""
        draft_id = str(uuid.uuid4())

        # User A creates draft
        client.put(
            f"/me/drafts/{draft_id}",
            json={"name": "Alice Sync", "sport": "road"},
            headers=two_users["a"],
        )

        # User B tries to overwrite it — should create their own or fail
        client.put(
            f"/me/drafts/{draft_id}",
            json={"name": "Bob Override", "sport": "mtb"},
            headers=two_users["b"],
        )
        # Even if Bob's request succeeds, verify Alice's draft is unchanged
        alice_drafts = client.get("/me/drafts", headers=two_users["a"]).json()
        alice_names = [d["name"] for d in alice_drafts]
        assert "Alice Sync" in alice_names


class TestTripIsolation:
    """User B cannot modify or delete user A's trips."""

    def test_cannot_update_other_users_trip(self, client, two_users):
        trip = client.post(
            "/trips",
            json={"name": "Alice Trip", "sport": "gravel", "visibility": "public", "status": "planned"},
            headers=two_users["a"],
        ).json()

        resp = client.put(
            f"/trips/{trip['id']}",
            json={"name": "Hacked Trip"},
            headers=two_users["b"],
        )
        assert resp.status_code == 403

    def test_cannot_delete_other_users_trip(self, client, two_users):
        trip = client.post(
            "/trips",
            json={"name": "Alice Trip Del", "sport": "road", "visibility": "public", "status": "planned"},
            headers=two_users["a"],
        ).json()

        resp = client.delete(f"/trips/{trip['id']}", headers=two_users["b"])
        assert resp.status_code == 403

    def test_private_trip_hidden_from_public_list(self, client, two_users):
        client.post(
            "/trips",
            json={"name": "Secret Trip", "sport": "mtb", "visibility": "private", "status": "planned"},
            headers=two_users["a"],
        )
        resp = client.get("/trips?visibility=public")
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()]
        assert "Secret Trip" not in names

    def test_cannot_add_stage_to_other_users_trip(self, client, two_users):
        trip = client.post(
            "/trips",
            json={"name": "Alice Stages", "sport": "road", "visibility": "public", "status": "planned"},
            headers=two_users["a"],
        ).json()

        resp = client.post(
            f"/trips/{trip['id']}/stages",
            json={"day_index": 0, "title": "Injected Stage"},
            headers=two_users["b"],
        )
        assert resp.status_code == 403

    def test_cannot_add_poi_to_other_users_trip(self, client, two_users):
        trip = client.post(
            "/trips",
            json={"name": "Alice POIs", "sport": "road", "visibility": "public", "status": "planned"},
            headers=two_users["a"],
        ).json()

        resp = client.post(
            f"/trips/{trip['id']}/pois",
            json={"type": "water", "lon": 3.87, "lat": 43.61, "name": "Injected"},
            headers=two_users["b"],
        )
        assert resp.status_code == 403
