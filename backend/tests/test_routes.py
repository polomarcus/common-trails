"""Tests for GitHub-like routes API (fork / PR / tags / soft delete / collections)."""
import json

import pytest

pytestmark = pytest.mark.slow

GEOJSON_LINE = json.dumps({
    "type": "LineString",
    "coordinates": [[4.83, 45.75], [4.84, 45.76], [4.85, 45.77]],
})


class TestRouteCRUD:
    def test_create_route(self, client, auth_headers):
        resp = client.post(
            "/routes",
            json={"name": "Test Route", "sport": "road", "visibility": "public"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Route"
        assert data["sport"] == "road"
        assert "id" in data

    def test_create_route_with_geometry(self, client, auth_headers):
        resp = client.post(
            "/routes",
            json={
                "name": "Gravel Route",
                "sport": "gravel",
                "visibility": "public",
                "geometry_geojson": GEOJSON_LINE,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["current_version_id"] is not None

    def test_list_routes(self, client, auth_headers):
        client.post(
            "/routes",
            json={"name": "Listed Route", "sport": "mtb", "visibility": "public"},
            headers=auth_headers,
        )
        resp = client.get("/routes?visibility=public")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_route(self, client, auth_headers):
        cr = client.post(
            "/routes",
            json={"name": "GetMe", "sport": "road", "visibility": "public"},
            headers=auth_headers,
        )
        route_id = cr.json()["id"]
        resp = client.get(f"/routes/{route_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == route_id

    def test_get_route_not_found(self, client):
        resp = client.get("/routes/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_update_route(self, client, auth_headers):
        cr = client.post(
            "/routes",
            json={"name": "Before Update", "sport": "road", "visibility": "public"},
            headers=auth_headers,
        )
        route_id = cr.json()["id"]
        resp = client.put(
            f"/routes/{route_id}",
            json={"name": "After Update", "sport": "gravel", "visibility": "public"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "After Update"

    def test_delete_route(self, client, auth_headers):
        cr = client.post(
            "/routes",
            json={"name": "ToDelete", "sport": "road", "visibility": "public"},
            headers=auth_headers,
        )
        route_id = cr.json()["id"]
        resp = client.delete(f"/routes/{route_id}", headers=auth_headers)
        assert resp.status_code == 204
        assert client.get(f"/routes/{route_id}").status_code == 404


class TestFork:
    def test_fork_public_route(self, client, auth_headers):
        # Create parent
        parent = client.post(
            "/routes",
            json={"name": "Parent", "sport": "road", "visibility": "public",
                  "geometry_geojson": GEOJSON_LINE},
            headers=auth_headers,
        ).json()

        # Fork (different user would be ideal, but same user allowed for test)
        resp = client.post(f"/routes/{parent['id']}/fork", headers=auth_headers)
        assert resp.status_code == 201
        fork = resp.json()
        assert fork["forked_from_id"] == parent["id"]
        assert fork["id"] != parent["id"]

    def test_fork_not_found(self, client, auth_headers):
        resp = client.post(
            "/routes/00000000-0000-0000-0000-000000000000/fork",
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestTags:
    def test_create_tag_and_export_gpx(self, client, auth_headers):
        route = client.post(
            "/routes",
            json={"name": "Tagged", "sport": "gravel", "visibility": "public",
                  "geometry_geojson": GEOJSON_LINE},
            headers=auth_headers,
        ).json()
        version_id = route["current_version_id"]
        assert version_id, "Route should have a version after creation with geometry"

        # Create tag
        tag_resp = client.post(
            f"/routes/{route['id']}/tags",
            json={"tag": "v1.0", "version_id": version_id, "message": "First release"},
            headers=auth_headers,
        )
        assert tag_resp.status_code == 201
        assert tag_resp.json()["tag"] == "v1.0"

        # Export GPX
        gpx_resp = client.get(f"/routes/{route['id']}/tags/v1.0/gpx")
        assert gpx_resp.status_code == 200
        assert "application/gpx" in gpx_resp.headers["content-type"]
        body = gpx_resp.text
        assert "<gpx" in body
        assert "<trkpt" in body

    def test_duplicate_tag_rejected(self, client, auth_headers):
        route = client.post(
            "/routes",
            json={"name": "DupTag", "sport": "road", "visibility": "public",
                  "geometry_geojson": GEOJSON_LINE},
            headers=auth_headers,
        ).json()
        vid = route["current_version_id"]
        client.post(
            f"/routes/{route['id']}/tags",
            json={"tag": "v1", "version_id": vid},
            headers=auth_headers,
        )
        resp = client.post(
            f"/routes/{route['id']}/tags",
            json={"tag": "v1", "version_id": vid},
            headers=auth_headers,
        )
        assert resp.status_code == 409

    def test_list_tags(self, client, auth_headers):
        route = client.post(
            "/routes",
            json={"name": "ListTags", "sport": "road", "visibility": "public",
                  "geometry_geojson": GEOJSON_LINE},
            headers=auth_headers,
        ).json()
        vid = route["current_version_id"]
        for tag in ["v1", "v2", "v3"]:
            client.post(
                f"/routes/{route['id']}/tags",
                json={"tag": tag, "version_id": vid},
                headers=auth_headers,
            )
        tags = client.get(f"/routes/{route['id']}/tags").json()
        assert len(tags) >= 3


class TestDrafts:
    def test_create_draft(self, client, auth_headers):
        """POST with status='draft' creates a draft route."""
        resp = client.post(
            "/routes",
            json={"name": "Draft Route", "sport": "road", "visibility": "private", "status": "draft"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "draft"

    def test_drafts_excluded_from_public(self, client, auth_headers):
        """Draft routes don't appear in GET /routes public listing."""
        client.post(
            "/routes",
            json={"name": "Hidden Draft", "sport": "road", "visibility": "public", "status": "draft"},
            headers=auth_headers,
        )
        resp = client.get("/routes?visibility=public")
        assert resp.status_code == 200
        names = [r["name"] for r in resp.json()]
        assert "Hidden Draft" not in names

    def test_list_my_drafts(self, client, auth_headers):
        """GET /me/drafts returns user's draft routes."""
        client.post(
            "/routes",
            json={"name": "My Draft", "sport": "gravel", "visibility": "private", "status": "draft"},
            headers=auth_headers,
        )
        resp = client.get("/me/drafts", headers=auth_headers)
        assert resp.status_code == 200
        drafts = resp.json()
        assert any(d["name"] == "My Draft" for d in drafts)

    def test_sync_draft_upsert(self, client, auth_headers):
        """PUT /me/drafts/{id} creates or updates a draft."""
        import uuid
        draft_id = str(uuid.uuid4())

        # Create
        resp = client.put(
            f"/me/drafts/{draft_id}",
            json={"name": "New Draft", "sport": "road"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == draft_id
        assert data["status"] == "draft"
        assert data["visibility"] == "private"

        # Update
        resp2 = client.put(
            f"/me/drafts/{draft_id}",
            json={"name": "Updated Draft", "sport": "mtb"},
            headers=auth_headers,
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["name"] == "Updated Draft"
        assert data2["sport"] == "mtb"

    def test_update_route_geometry(self, client, auth_headers):
        """PUT /routes/{id} can update geometry and status."""
        cr = client.post(
            "/routes",
            json={"name": "GeoRoute", "sport": "road", "visibility": "public",
                  "geometry_geojson": GEOJSON_LINE},
            headers=auth_headers,
        )
        route_id = cr.json()["id"]
        new_geojson = json.dumps({
            "type": "LineString",
            "coordinates": [[4.90, 45.80], [4.91, 45.81]],
        })
        resp = client.put(
            f"/routes/{route_id}",
            json={"name": "GeoRoute", "sport": "road", "visibility": "public",
                  "geometry_geojson": new_geojson, "distance_m": 1500},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["distance_m"] == 1500


class TestBatchRoutes:
    """Tests for GET /routes/batch?ids=..."""

    def test_batch_two_public_routes(self, client, auth_headers):
        r1 = client.post("/routes", json={"name": "Batch1", "sport": "road", "visibility": "public"}, headers=auth_headers).json()
        r2 = client.post("/routes", json={"name": "Batch2", "sport": "gravel", "visibility": "public"}, headers=auth_headers).json()
        resp = client.get(f"/routes/batch?ids={r1['id']},{r2['id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = {d["name"] for d in data}
        assert names == {"Batch1", "Batch2"}

    def test_batch_filters_private_routes(self, client, auth_headers):
        r1 = client.post("/routes", json={"name": "Public", "sport": "road", "visibility": "public"}, headers=auth_headers).json()
        r2 = client.post("/routes", json={"name": "Private", "sport": "road", "visibility": "private"}, headers=auth_headers).json()
        resp = client.get(f"/routes/batch?ids={r1['id']},{r2['id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Public"

    def test_batch_max_four(self, client):
        ids = ",".join(f"00000000-0000-0000-0000-00000000000{i}" for i in range(5))
        resp = client.get(f"/routes/batch?ids={ids}")
        assert resp.status_code == 400

    def test_batch_ignores_invalid_uuids(self, client, auth_headers):
        r1 = client.post("/routes", json={"name": "Valid", "sport": "road", "visibility": "public"}, headers=auth_headers).json()
        resp = client.get(f"/routes/batch?ids={r1['id']},not-a-uuid")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_batch_empty_ids(self, client):
        resp = client.get("/routes/batch?ids=")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_batch_includes_unlisted(self, client, auth_headers):
        r1 = client.post("/routes", json={"name": "Unlisted", "sport": "road", "visibility": "unlisted"}, headers=auth_headers).json()
        resp = client.get(f"/routes/batch?ids={r1['id']}")
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestSoftDelete:
    def test_soft_delete_sets_deleted_at(self, client, auth_headers):
        r = client.post("/routes", json={"name": "SoftDel", "sport": "road", "visibility": "public"}, headers=auth_headers).json()
        resp = client.delete(f"/routes/{r['id']}", headers=auth_headers)
        assert resp.status_code == 204
        # Route should be 404 in normal GET
        assert client.get(f"/routes/{r['id']}").status_code == 404

    def test_soft_deleted_not_in_list(self, client, auth_headers):
        r = client.post("/routes", json={"name": "SoftDelList", "sport": "road", "visibility": "public"}, headers=auth_headers).json()
        client.delete(f"/routes/{r['id']}", headers=auth_headers)
        routes = client.get("/routes?visibility=public").json()
        assert not any(x["id"] == r["id"] for x in routes)

    def test_restore_soft_deleted(self, client, auth_headers):
        r = client.post("/routes", json={"name": "Restorable", "sport": "road", "visibility": "public"}, headers=auth_headers).json()
        client.delete(f"/routes/{r['id']}", headers=auth_headers)
        resp = client.post(f"/routes/{r['id']}/restore", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Restorable"
        # Should be accessible again
        assert client.get(f"/routes/{r['id']}").status_code == 200

    def test_restore_not_deleted_fails(self, client, auth_headers):
        r = client.post("/routes", json={"name": "NotDel", "sport": "road", "visibility": "public"}, headers=auth_headers).json()
        resp = client.post(f"/routes/{r['id']}/restore", headers=auth_headers)
        assert resp.status_code == 400

    def test_soft_deleted_not_in_batch(self, client, auth_headers):
        r = client.post("/routes", json={"name": "BatchDel", "sport": "road", "visibility": "public"}, headers=auth_headers).json()
        client.delete(f"/routes/{r['id']}", headers=auth_headers)
        resp = client.get(f"/routes/batch?ids={r['id']}")
        assert len(resp.json()) == 0


class TestForkCount:
    def test_fork_count_reflects_forks(self, client, auth_headers):
        parent = client.post("/routes", json={"name": "ForkCountParent", "sport": "road", "visibility": "public", "geometry_geojson": GEOJSON_LINE}, headers=auth_headers).json()
        # Fork twice
        f1 = client.post(f"/routes/{parent['id']}/fork", headers=auth_headers).json()
        client.post(f"/routes/{parent['id']}/fork", headers=auth_headers)
        # Check parent fork_count
        parent_fresh = client.get(f"/routes/{parent['id']}").json()
        assert parent_fresh["fork_count"] == 2
        # Delete one fork → count decreases
        client.delete(f"/routes/{f1['id']}", headers=auth_headers)
        parent_fresh2 = client.get(f"/routes/{parent['id']}").json()
        assert parent_fresh2["fork_count"] == 1

    def test_center_computed(self, client, auth_headers):
        r = client.post("/routes", json={"name": "CenterTest", "sport": "road", "visibility": "public", "geometry_geojson": GEOJSON_LINE}, headers=auth_headers).json()
        assert r["center"] is not None
        assert len(r["center"]) == 2


class TestMyRoutes:
    def test_list_my_routes(self, client, auth_headers):
        client.post("/routes", json={"name": "MyRoute1", "sport": "road", "visibility": "public"}, headers=auth_headers)
        client.post("/routes", json={"name": "MyRoute2", "sport": "gravel", "visibility": "private"}, headers=auth_headers)
        resp = client.get("/me/routes", headers=auth_headers)
        assert resp.status_code == 200
        names = [r["name"] for r in resp.json()]
        assert "MyRoute1" in names
        assert "MyRoute2" in names

    def test_filter_by_sport(self, client, auth_headers):
        client.post("/routes", json={"name": "RoadOnly", "sport": "road", "visibility": "public"}, headers=auth_headers)
        client.post("/routes", json={"name": "GravelOnly", "sport": "gravel", "visibility": "public"}, headers=auth_headers)
        resp = client.get("/me/routes?sport=gravel", headers=auth_headers)
        names = [r["name"] for r in resp.json()]
        assert "GravelOnly" in names
        assert "RoadOnly" not in names

    def test_include_deleted(self, client, auth_headers):
        r = client.post("/routes", json={"name": "WillDelete", "sport": "road", "visibility": "public"}, headers=auth_headers).json()
        client.delete(f"/routes/{r['id']}", headers=auth_headers)
        # Without include_deleted
        resp1 = client.get("/me/routes", headers=auth_headers)
        assert not any(x["id"] == r["id"] for x in resp1.json())
        # With include_deleted
        resp2 = client.get("/me/routes?include_deleted=true", headers=auth_headers)
        assert any(x["id"] == r["id"] for x in resp2.json())


class TestPatchRoute:
    def test_patch_name(self, client, auth_headers):
        r = client.post("/routes", json={"name": "Original", "sport": "road", "visibility": "public"}, headers=auth_headers).json()
        resp = client.patch(f"/routes/{r['id']}", json={"name": "Renamed"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"
        assert resp.json()["sport"] == "road"  # unchanged

    def test_patch_visibility(self, client, auth_headers):
        r = client.post("/routes", json={"name": "VisPatch", "sport": "road", "visibility": "public"}, headers=auth_headers).json()
        resp = client.patch(f"/routes/{r['id']}", json={"visibility": "private"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["visibility"] == "private"

    def test_patch_403_non_owner(self, client, auth_headers):
        r = client.post("/routes", json={"name": "OwnedRoute", "sport": "road", "visibility": "public"}, headers=auth_headers).json()
        # Create a different user
        import uuid
        email = f"other_{uuid.uuid4().hex[:8]}@example.com"
        other = client.post("/auth/register", json={"email": email, "password": "testpass", "username": "other"})
        other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
        resp = client.patch(f"/routes/{r['id']}", json={"name": "Stolen"}, headers=other_headers)
        assert resp.status_code == 403


class TestCollections:
    def test_crud_collection(self, client, auth_headers):
        # Create
        resp = client.post("/me/collections", json={"name": "Favourites"}, headers=auth_headers)
        assert resp.status_code == 201
        c = resp.json()
        assert c["name"] == "Favourites"
        cid = c["id"]

        # List
        resp = client.get("/me/collections", headers=auth_headers)
        assert any(x["id"] == cid for x in resp.json())

        # Update
        resp = client.put(f"/me/collections/{cid}", json={"name": "Top Routes"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Top Routes"

        # Delete
        resp = client.delete(f"/me/collections/{cid}", headers=auth_headers)
        assert resp.status_code == 204

    def test_add_remove_route(self, client, auth_headers):
        c = client.post("/me/collections", json={"name": "WithRoutes"}, headers=auth_headers).json()
        r = client.post("/routes", json={"name": "CollRoute", "sport": "road", "visibility": "public"}, headers=auth_headers).json()

        # Add route
        resp = client.post(f"/me/collections/{c['id']}/routes", json={"route_id": r["id"]}, headers=auth_headers)
        assert resp.status_code == 201

        # Duplicate add fails
        resp = client.post(f"/me/collections/{c['id']}/routes", json={"route_id": r["id"]}, headers=auth_headers)
        assert resp.status_code == 409

        # Get collection with routes
        detail = client.get(f"/me/collections/{c['id']}", headers=auth_headers).json()
        assert detail["route_count"] == 1
        assert detail["routes"][0]["id"] == r["id"]

        # Remove route
        resp = client.delete(f"/me/collections/{c['id']}/routes/{r['id']}", headers=auth_headers)
        assert resp.status_code == 204

    def test_compare_max_four(self, client, auth_headers):
        c = client.post("/me/collections", json={"name": "Compare"}, headers=auth_headers).json()
        for i in range(5):
            r = client.post("/routes", json={"name": f"Cmp{i}", "sport": "road", "visibility": "public", "geometry_geojson": GEOJSON_LINE}, headers=auth_headers).json()
            client.post(f"/me/collections/{c['id']}/routes", json={"route_id": r["id"]}, headers=auth_headers)
        resp = client.get(f"/me/collections/{c['id']}/compare", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) <= 4


class TestRouteAnnotations:
    def _create_route(self, client, auth_headers):
        resp = client.post(
            "/routes",
            json={"name": "Annotated Route", "sport": "mtb", "visibility": "public", "geometry_geojson": GEOJSON_LINE},
            headers=auth_headers,
        )
        return resp.json()["id"]

    def test_create_annotation(self, client, auth_headers):
        route_id = self._create_route(client, auth_headers)
        resp = client.post(
            f"/routes/{route_id}/annotations",
            json={"lon": 4.83, "lat": 45.75, "icon": "water", "text": "Fontaine"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["icon"] == "water"
        assert data["text"] == "Fontaine"
        assert data["lon"] == 4.83

    def test_list_annotations(self, client, auth_headers):
        route_id = self._create_route(client, auth_headers)
        client.post(
            f"/routes/{route_id}/annotations",
            json={"lon": 4.83, "lat": 45.75, "icon": "water", "text": "Fontaine", "dist_m": 100},
            headers=auth_headers,
        )
        client.post(
            f"/routes/{route_id}/annotations",
            json={"lon": 4.84, "lat": 45.76, "icon": "danger", "text": "Ravin", "dist_m": 500},
            headers=auth_headers,
        )
        resp = client.get(f"/routes/{route_id}/annotations")
        assert resp.status_code == 200
        anns = resp.json()
        assert len(anns) == 2
        # Ordered by dist_m
        assert anns[0]["dist_m"] == 100
        assert anns[1]["dist_m"] == 500

    def test_delete_annotation(self, client, auth_headers):
        route_id = self._create_route(client, auth_headers)
        resp = client.post(
            f"/routes/{route_id}/annotations",
            json={"lon": 4.83, "lat": 45.75, "icon": "info"},
            headers=auth_headers,
        )
        ann_id = resp.json()["id"]
        resp = client.delete(f"/routes/{route_id}/annotations/{ann_id}", headers=auth_headers)
        assert resp.status_code == 204
        # Verify deleted
        resp = client.get(f"/routes/{route_id}/annotations")
        assert len(resp.json()) == 0

    def test_invalid_icon(self, client, auth_headers):
        route_id = self._create_route(client, auth_headers)
        resp = client.post(
            f"/routes/{route_id}/annotations",
            json={"lon": 4.83, "lat": 45.75, "icon": "invalid"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_gpx_export_includes_annotations(self, client, auth_headers):
        route_id = self._create_route(client, auth_headers)
        client.post(
            f"/routes/{route_id}/annotations",
            json={"lon": 4.83, "lat": 45.75, "icon": "water", "text": "Source"},
            headers=auth_headers,
        )
        resp = client.get(f"/routes/{route_id}/gpx")
        assert resp.status_code == 200
        gpx = resp.text
        assert "<wpt" in gpx
        assert "Source" in gpx


class TestPrivateRouteVisibility:
    """Verify that private route sub-endpoints return 404 for non-owners."""

    def _create_private_route(self, client, auth_headers):
        resp = client.post(
            "/routes",
            json={"name": "Private Route", "sport": "road", "visibility": "private",
                  "geometry_geojson": GEOJSON_LINE},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        return resp.json()

    def _other_headers(self, client):
        import uuid
        email = f"other_{uuid.uuid4().hex[:8]}@example.com"
        resp = client.post("/auth/register", json={"email": email, "password": "testpass123", "username": "other"})
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    def test_private_route_versions_404_for_anon(self, client, auth_headers):
        route = self._create_private_route(client, auth_headers)
        resp = client.get(f"/routes/{route['id']}/versions")
        assert resp.status_code == 404

    def test_private_route_versions_404_for_other_user(self, client, auth_headers):
        route = self._create_private_route(client, auth_headers)
        other = self._other_headers(client)
        resp = client.get(f"/routes/{route['id']}/versions", headers=other)
        assert resp.status_code == 404

    def test_private_route_versions_ok_for_owner(self, client, auth_headers):
        route = self._create_private_route(client, auth_headers)
        resp = client.get(f"/routes/{route['id']}/versions", headers=auth_headers)
        assert resp.status_code == 200

    def test_private_route_suggestions_404_for_anon(self, client, auth_headers):
        route = self._create_private_route(client, auth_headers)
        resp = client.get(f"/routes/{route['id']}/suggestions")
        assert resp.status_code == 404

    def test_private_route_create_suggestion_404_for_other(self, client, auth_headers):
        route = self._create_private_route(client, auth_headers)
        other = self._other_headers(client)
        resp = client.post(
            f"/routes/{route['id']}/suggestions",
            json={"title": "My suggestion"},
            headers=other,
        )
        assert resp.status_code == 404
