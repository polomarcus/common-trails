"""Tests for Route Collection Sharing API (visibility, positions, annotations, limits)."""
import json
import uuid

GEOJSON_LINE = json.dumps({
    "type": "LineString",
    "coordinates": [
        [3.870, 43.610, 62], [3.874, 43.617, 75], [3.878, 43.624, 95],
        [3.882, 43.631, 130], [3.887, 43.638, 175],
    ],
})


def _create_route(client, auth_headers, name="Test Route", visibility="public"):
    resp = client.post(
        "/routes",
        json={"name": name, "sport": "gravel", "visibility": visibility,
              "geometry_geojson": GEOJSON_LINE, "distance_m": 35000, "elevation_gain_m": 320},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()


def _create_collection(client, auth_headers, name="Test Collection", visibility="private"):
    resp = client.post(
        "/me/collections",
        json={"name": name, "visibility": visibility},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()


def _add_route_to_collection(client, auth_headers, collection_id, route_id, position=None):
    body = {"route_id": route_id}
    if position is not None:
        body["position"] = position
    resp = client.post(
        f"/me/collections/{collection_id}/routes",
        json=body,
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()


class TestCollectionVisibility:
    def test_public_collection_accessible_without_auth(self, client, auth_headers):
        coll = _create_collection(client, auth_headers, visibility="public")
        route = _create_route(client, auth_headers)
        _add_route_to_collection(client, auth_headers, coll["id"], route["id"])
        # No auth
        resp = client.get(f"/collections/{coll['id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Collection"
        assert len(data["routes"]) == 1
        assert data["stats"]["route_count"] == 1

    def test_private_collection_returns_404_without_auth(self, client, auth_headers):
        coll = _create_collection(client, auth_headers, visibility="private")
        resp = client.get(f"/collections/{coll['id']}")
        assert resp.status_code == 404

    def test_unlisted_collection_accessible_without_auth(self, client, auth_headers):
        coll = _create_collection(client, auth_headers, visibility="unlisted")
        resp = client.get(f"/collections/{coll['id']}")
        assert resp.status_code == 200

    def test_nonexistent_collection_returns_404(self, client):
        resp = client.get(f"/collections/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestCollectionPositions:
    def test_positions_auto_increment(self, client, auth_headers):
        coll = _create_collection(client, auth_headers, visibility="public")
        r1 = _create_route(client, auth_headers, name="Route 1")
        r2 = _create_route(client, auth_headers, name="Route 2")
        r3 = _create_route(client, auth_headers, name="Route 3")
        res1 = _add_route_to_collection(client, auth_headers, coll["id"], r1["id"])
        res2 = _add_route_to_collection(client, auth_headers, coll["id"], r2["id"])
        res3 = _add_route_to_collection(client, auth_headers, coll["id"], r3["id"])
        assert res1["position"] == 0
        assert res2["position"] == 1
        assert res3["position"] == 2

    def test_update_route_position(self, client, auth_headers):
        coll = _create_collection(client, auth_headers, visibility="public")
        route = _create_route(client, auth_headers)
        _add_route_to_collection(client, auth_headers, coll["id"], route["id"])
        resp = client.put(
            f"/me/collections/{coll['id']}/routes/{route['id']}",
            json={"position": 5},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["position"] == 5

    def test_public_endpoint_returns_routes_ordered_by_position(self, client, auth_headers):
        coll = _create_collection(client, auth_headers, visibility="public")
        r1 = _create_route(client, auth_headers, name="Route A")
        r2 = _create_route(client, auth_headers, name="Route B")
        _add_route_to_collection(client, auth_headers, coll["id"], r1["id"], position=2)
        _add_route_to_collection(client, auth_headers, coll["id"], r2["id"], position=0)
        resp = client.get(f"/collections/{coll['id']}")
        routes = resp.json()["routes"]
        assert routes[0]["name"] == "Route B"
        assert routes[1]["name"] == "Route A"


class TestCollectionAnnotations:
    def test_create_collection_level_annotation(self, client, auth_headers):
        coll = _create_collection(client, auth_headers, visibility="public")
        resp = client.post(
            f"/me/collections/{coll['id']}/annotations",
            json={"icon": "danger", "text": "Portage 50m", "lat": 43.65, "lon": 3.87},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["icon"] == "danger"
        assert data["text"] == "Portage 50m"
        assert data["collection_id"] == coll["id"]
        assert data["route_id"] is None

    def test_annotation_visible_in_public_response(self, client, auth_headers):
        coll = _create_collection(client, auth_headers, visibility="public")
        client.post(
            f"/me/collections/{coll['id']}/annotations",
            json={"icon": "water", "text": "Source", "lat": 43.6, "lon": 3.8},
            headers=auth_headers,
        )
        resp = client.get(f"/collections/{coll['id']}")
        assert resp.status_code == 200
        assert len(resp.json()["annotations"]) == 1

    def test_invalid_icon_returns_422(self, client, auth_headers):
        coll = _create_collection(client, auth_headers)
        resp = client.post(
            f"/me/collections/{coll['id']}/annotations",
            json={"icon": "invalid_icon", "text": "test", "lat": 43.6, "lon": 3.8},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_update_annotation(self, client, auth_headers):
        coll = _create_collection(client, auth_headers)
        ann = client.post(
            f"/me/collections/{coll['id']}/annotations",
            json={"icon": "info", "text": "original", "lat": 43.6, "lon": 3.8},
            headers=auth_headers,
        ).json()
        resp = client.put(
            f"/me/collections/{coll['id']}/annotations/{ann['id']}",
            json={"text": "updated"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["text"] == "updated"

    def test_delete_annotation(self, client, auth_headers):
        coll = _create_collection(client, auth_headers)
        ann = client.post(
            f"/me/collections/{coll['id']}/annotations",
            json={"icon": "food", "text": "Café", "lat": 43.6, "lon": 3.8},
            headers=auth_headers,
        ).json()
        resp = client.delete(
            f"/me/collections/{coll['id']}/annotations/{ann['id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

    def test_annotation_author_check(self, client, auth_headers):
        """Non-author cannot update/delete annotation."""
        coll = _create_collection(client, auth_headers)
        ann = client.post(
            f"/me/collections/{coll['id']}/annotations",
            json={"icon": "info", "text": "test", "lat": 43.6, "lon": 3.8},
            headers=auth_headers,
        ).json()
        # Create second user
        email2 = f"test_{uuid.uuid4().hex[:8]}@example.com"
        resp2 = client.post(
            "/auth/register",
            json={"email": email2, "password": "testpass123", "username": f"user_{uuid.uuid4().hex[:6]}"},
        )
        other_headers = {"Authorization": f"Bearer {resp2.json()['access_token']}"}
        # Other user can't see the private collection to update the annotation
        resp = client.put(
            f"/me/collections/{coll['id']}/annotations/{ann['id']}",
            json={"text": "hacked"},
            headers=other_headers,
        )
        assert resp.status_code == 404


class TestPrivateRouteInCollection:
    def test_private_route_shows_null_geometry(self, client, auth_headers):
        coll = _create_collection(client, auth_headers, visibility="public")
        route = _create_route(client, auth_headers, name="Secret Route", visibility="private")
        _add_route_to_collection(client, auth_headers, coll["id"], route["id"])
        # View as unauthenticated
        resp = client.get(f"/collections/{coll['id']}")
        data = resp.json()
        assert len(data["routes"]) == 1
        r = data["routes"][0]
        assert r["name"] == "Route privée"
        assert r["geometry_geojson"] is None
        assert r["visible"] is False

    def test_stats_exclude_private_routes(self, client, auth_headers):
        coll = _create_collection(client, auth_headers, visibility="public")
        pub_route = _create_route(client, auth_headers, name="Public", visibility="public")
        priv_route = _create_route(client, auth_headers, name="Private", visibility="private")
        _add_route_to_collection(client, auth_headers, coll["id"], pub_route["id"])
        _add_route_to_collection(client, auth_headers, coll["id"], priv_route["id"])
        resp = client.get(f"/collections/{coll['id']}")
        stats = resp.json()["stats"]
        assert stats["route_count"] == 1  # only public


class TestCollectionLimits:
    def test_route_limit(self, client, auth_headers):
        coll = _create_collection(client, auth_headers)
        routes = []
        for i in range(21):
            r = _create_route(client, auth_headers, name=f"Route {i}")
            routes.append(r)
        # Add 20 routes
        for i in range(20):
            _add_route_to_collection(client, auth_headers, coll["id"], routes[i]["id"])
        # 21st should fail
        resp = client.post(
            f"/me/collections/{coll['id']}/routes",
            json={"route_id": routes[20]["id"]},
            headers=auth_headers,
        )
        assert resp.status_code == 409

    def test_annotation_limit(self, client, auth_headers):
        coll = _create_collection(client, auth_headers)
        for i in range(200):
            resp = client.post(
                f"/me/collections/{coll['id']}/annotations",
                json={"icon": "info", "text": f"Ann {i}", "lat": 43.6, "lon": 3.8},
                headers=auth_headers,
            )
            assert resp.status_code == 201, f"Failed on annotation {i}: {resp.text}"
        # 201st should fail
        resp = client.post(
            f"/me/collections/{coll['id']}/annotations",
            json={"icon": "info", "text": "overflow", "lat": 43.6, "lon": 3.8},
            headers=auth_headers,
        )
        assert resp.status_code == 409


class TestCollectionUpdatedAt:
    def test_updated_at_changes_on_route_add(self, client, auth_headers):
        coll = _create_collection(client, auth_headers, visibility="public")
        initial_updated_at = coll["updated_at"]
        route = _create_route(client, auth_headers)
        _add_route_to_collection(client, auth_headers, coll["id"], route["id"])
        resp = client.get(f"/collections/{coll['id']}")
        assert resp.json()["updated_at"] != initial_updated_at

    def test_updated_at_changes_on_annotation_create(self, client, auth_headers):
        coll = _create_collection(client, auth_headers, visibility="public")
        resp1 = client.get(f"/collections/{coll['id']}")
        initial = resp1.json()["updated_at"]
        client.post(
            f"/me/collections/{coll['id']}/annotations",
            json={"icon": "info", "text": "test", "lat": 43.6, "lon": 3.8},
            headers=auth_headers,
        )
        resp2 = client.get(f"/collections/{coll['id']}")
        assert resp2.json()["updated_at"] != initial


class TestCollectionCRUDExtended:
    def test_create_with_visibility(self, client, auth_headers):
        resp = client.post(
            "/me/collections",
            json={"name": "Public Coll", "visibility": "public"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["visibility"] == "public"

    def test_update_visibility(self, client, auth_headers):
        coll = _create_collection(client, auth_headers, visibility="private")
        resp = client.put(
            f"/me/collections/{coll['id']}",
            json={"visibility": "public"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["visibility"] == "public"

    def test_collection_detail_includes_position(self, client, auth_headers):
        coll = _create_collection(client, auth_headers)
        route = _create_route(client, auth_headers)
        _add_route_to_collection(client, auth_headers, coll["id"], route["id"])
        resp = client.get(f"/me/collections/{coll['id']}", headers=auth_headers)
        assert resp.status_code == 200
        routes = resp.json()["routes"]
        assert routes[0]["position"] == 0
