"""Tests for Open Graph share endpoint."""
import json
import uuid
from io import BytesIO

import gpxpy
import gpxpy.gpx
from PIL import Image

GEOJSON_LINE = json.dumps({
    "type": "LineString",
    "coordinates": [[4.83, 45.75], [4.84, 45.76], [4.85, 45.77]],
})


def _make_gpx_bytes(name: str = "Test", n_points: int = 10) -> bytes:
    """Generate a minimal valid GPX file in memory."""
    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(track)
    segment = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(segment)
    for i in range(n_points):
        segment.points.append(
            gpxpy.gpx.GPXTrackPoint(
                latitude=45.75 + i * 0.001,
                longitude=4.83 + i * 0.001,
                elevation=180 + i,
            )
        )
    return gpx.to_xml().encode("utf-8")


def _make_second_user_headers(client) -> dict[str, str]:
    """Register a fresh, distinct user (a non-owner) and return its headers."""
    email = f"other_{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "testpass123", "username": f"user_{uuid.uuid4().hex[:6]}"},
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_owned_activity(client, auth_headers, name: str = "My Private Ride") -> str:
    """Upload a GPX so the auth_headers user owns a real activity; return its id."""
    resp = client.post(
        "/gpx/upload",
        headers=auth_headers,
        files={"file": ("ride.gpx", _make_gpx_bytes(name), "application/gpx+xml")},
        data={"sport": "road", "contribute_heatmap": "false"},
    )
    assert resp.status_code == 202, resp.text
    activity_id = resp.json()["activity_id"]
    assert activity_id
    return activity_id


class TestShareRoute:
    def test_share_public_route(self, client, auth_headers):
        """GET /share/{id} returns HTML with OG meta tags for public routes."""
        resp = client.post(
            "/routes",
            json={
                "name": "Col du Galibier",
                "sport": "road",
                "visibility": "public",
                "geometry_geojson": GEOJSON_LINE,
            },
            headers=auth_headers,
        )
        route_id = resp.json()["id"]

        resp = client.get(f"/share/{route_id}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        html = resp.text
        assert 'og:title' in html
        assert "Col du Galibier" in html
        assert 'og:description' in html
        assert f"route={route_id}" in html  # redirect URL
        assert 'twitter:card' in html

    def test_share_unlisted_route(self, client, auth_headers):
        """Unlisted routes should be shareable."""
        resp = client.post(
            "/routes",
            json={"name": "Secret Trail", "sport": "gravel", "visibility": "unlisted"},
            headers=auth_headers,
        )
        route_id = resp.json()["id"]

        resp = client.get(f"/share/{route_id}")
        assert resp.status_code == 200
        assert "Secret Trail" in resp.text

    def test_share_private_route_404(self, client, auth_headers):
        """Private routes should not be shareable."""
        resp = client.post(
            "/routes",
            json={"name": "Private Route", "sport": "mtb", "visibility": "private"},
            headers=auth_headers,
        )
        route_id = resp.json()["id"]

        resp = client.get(f"/share/{route_id}")
        assert resp.status_code == 404

    def test_share_nonexistent_route_404(self, client):
        """Non-existent route returns 404."""
        resp = client.get("/share/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_share_route_description_parts(self, client, auth_headers):
        """Description includes sport, distance, and elevation."""
        resp = client.post(
            "/routes",
            json={
                "name": "Full Stats Route",
                "sport": "gravel",
                "visibility": "public",
                "geometry_geojson": GEOJSON_LINE,
                "distance_m": 42500,
                "elevation_gain_m": 850,
            },
            headers=auth_headers,
        )
        route_id = resp.json()["id"]

        resp = client.get(f"/share/{route_id}")
        html = resp.text
        assert "Gravel" in html
        assert "42.5 km" in html
        assert "D+ 850 m" in html

    def test_share_route_sport_emoji(self, client, auth_headers):
        """Each sport gets the correct emoji in og:title."""
        resp = client.post(
            "/routes",
            json={"name": "MTB Trail", "sport": "mtb", "visibility": "public"},
            headers=auth_headers,
        )
        route_id = resp.json()["id"]

        resp = client.get(f"/share/{route_id}")
        html = resp.text
        # MTB emoji is 🚵
        assert "\U0001f6b5" in html or "🚵" in html


class TestShareOgImage:
    def test_route_og_image_png(self, client, auth_headers):
        """GET /share/{id}/og-image returns a PNG card.

        Switched from SVG to PNG on 2026-05-16 — WhatsApp / Twitter /
        iMessage / Slack drop SVG og-images silently. PNG is the
        format the open-graph ecosystem agrees on.
        """
        resp = client.post(
            "/routes",
            json={"name": "Ventoux", "sport": "road", "visibility": "public",
                   "distance_m": 21500, "elevation_gain_m": 1610},
            headers=auth_headers,
        )
        route_id = resp.json()["id"]

        resp = client.get(f"/share/{route_id}/og-image")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        # Validate the bytes are an actual PNG with the right dimensions —
        # if Pillow ever stops emitting valid output this is the canary.
        img = Image.open(BytesIO(resp.content))
        assert img.format == "PNG"
        assert img.size == (1200, 630)

    def test_route_og_image_private_404(self, client, auth_headers):
        """Private route og-image returns 404."""
        resp = client.post(
            "/routes",
            json={"name": "Secret", "sport": "mtb", "visibility": "private"},
            headers=auth_headers,
        )
        route_id = resp.json()["id"]
        resp = client.get(f"/share/{route_id}/og-image")
        assert resp.status_code == 404

    def test_share_html_points_to_og_image(self, client, auth_headers):
        """Share HTML og:image should point to the dynamic og-image endpoint."""
        resp = client.post(
            "/routes",
            json={"name": "OG Test", "sport": "gravel", "visibility": "public"},
            headers=auth_headers,
        )
        route_id = resp.json()["id"]
        resp = client.get(f"/share/{route_id}")
        assert f"/share/{route_id}/og-image" in resp.text


class TestShareActivityVisibility:
    """Activities are PRIVATE data (no per-activity visibility column). The
    activity-share endpoints must require owner authentication — otherwise UUID
    enumeration leaks any user's activity name/sport/distance/elevation (S1 IDOR).
    """

    def test_activity_share_html_anonymous_401(self, client, auth_headers):
        """Anonymous request to the HTML share endpoint is rejected (no auth)."""
        activity_id = _create_owned_activity(client, auth_headers)
        resp = client.get(f"/share/activity/{activity_id}")
        assert resp.status_code == 401

    def test_activity_share_html_non_owner_404(self, client, auth_headers):
        """A different authenticated user gets 404 (existence not leaked)."""
        activity_id = _create_owned_activity(client, auth_headers)
        other = _make_second_user_headers(client)
        resp = client.get(f"/share/activity/{activity_id}", headers=other)
        assert resp.status_code == 404

    def test_activity_share_html_owner_200(self, client, auth_headers):
        """The owner can render the share HTML."""
        activity_id = _create_owned_activity(client, auth_headers, name="Owner Ride")
        resp = client.get(f"/share/activity/{activity_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Owner Ride" in resp.text
        assert f"id={activity_id}" in resp.text  # redirect URL

    def test_activity_share_html_nonexistent_404(self, client, auth_headers):
        """A non-existent activity id returns 404 for an authenticated user."""
        resp = client.get(
            "/share/activity/00000000-0000-0000-0000-000000000000",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_activity_og_image_anonymous_401(self, client, auth_headers):
        """Anonymous request to the og-image endpoint is rejected (no auth)."""
        activity_id = _create_owned_activity(client, auth_headers)
        resp = client.get(f"/share/activity/{activity_id}/og-image")
        assert resp.status_code == 401

    def test_activity_og_image_non_owner_404(self, client, auth_headers):
        """A different authenticated user gets 404 on the og-image."""
        activity_id = _create_owned_activity(client, auth_headers)
        other = _make_second_user_headers(client)
        resp = client.get(f"/share/activity/{activity_id}/og-image", headers=other)
        assert resp.status_code == 404

    def test_activity_og_image_owner_200(self, client, auth_headers):
        """The owner gets a valid PNG card."""
        activity_id = _create_owned_activity(client, auth_headers)
        resp = client.get(f"/share/activity/{activity_id}/og-image", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        img = Image.open(BytesIO(resp.content))
        assert img.format == "PNG"
        assert img.size == (1200, 630)
