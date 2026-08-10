"""GET /me/contribution-status — powers the /strava "already contributed?" banner.

Drives the REAL endpoint via TestClient. A fresh user reads as not-contributed;
after a consented GPX upload (a community-eligible manual_upload activity) they
read as contributed with count≥1 and a last_contribution_at. Auth is required.
"""
import uuid

import gpxpy
import gpxpy.gpx


def _gpx_bytes(name: str, lon0: float, lat0: float, n: int = 6) -> bytes:
    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=name)
    gpx.tracks.append(track)
    seg = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(seg)
    for i in range(n):
        seg.points.append(gpxpy.gpx.GPXTrackPoint(
            latitude=lat0 + i * 0.001, longitude=lon0 + i * 0.001, elevation=40 + i))
    return gpx.to_xml().encode()


def _unique_start() -> tuple[float, float]:
    jitter = (uuid.uuid4().int % 100_000) * 1e-5
    return (-28.0 + jitter, 15.0 + jitter / 2)


def test_requires_auth(client):
    assert client.get("/me/contribution-status").status_code == 401


def test_fresh_user_has_not_contributed(client, auth_headers):
    resp = client.get("/me/contribution-status", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["contributed"] is False
    assert body["count"] == 0
    assert body["last_contribution_at"] is None


def test_after_consented_upload_user_has_contributed(client, auth_headers):
    lon0, lat0 = _unique_start()
    resp = client.post(
        "/gpx/upload",
        headers=auth_headers,
        files={"file": ("ride.gpx", _gpx_bytes("ride", lon0, lat0), "application/gpx+xml")},
        data={"sport": "gravel", "contribute_heatmap": "true",
              "consent_version": "v-cs-test", "consent_text": "ODbL ok", "locale": "fr"},
    )
    assert resp.status_code == 202, resp.text

    status = client.get("/me/contribution-status", headers=auth_headers).json()
    assert status["contributed"] is True
    assert status["count"] >= 1
    assert status["last_contribution_at"] is not None


def test_non_contributed_upload_does_not_count(client, auth_headers):
    """An upload with contribute_heatmap=false is personal-only → not counted."""
    lon0, lat0 = _unique_start()
    resp = client.post(
        "/gpx/upload",
        headers=auth_headers,
        files={"file": ("perso.gpx", _gpx_bytes("perso", lon0, lat0), "application/gpx+xml")},
        data={"sport": "gravel", "contribute_heatmap": "false"},
    )
    assert resp.status_code == 202, resp.text
    status = client.get("/me/contribution-status", headers=auth_headers).json()
    assert status["contributed"] is False
    assert status["count"] == 0
