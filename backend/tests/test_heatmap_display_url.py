"""Members-only heatmap ASSET GATE — GET /heatmap/display-url.

The 2026-07 cutover makes the community map members-only. The page gate alone
does not protect the PMTiles DATA (a public GCS object), so logged-in users
fetch the binary through a short-lived SIGNED GET URL minted here. These tests
pin the gate + the signing reuse:

- anonymous → 401 (the gate),
- authed but no display bucket configured → 503 (dev / not-a-prod-feature),
- authed + bucket → a signed URL is returned (signing mocked at the boundary),
- the GET signer reuses the exact signBlob fallback fixed for PUT in #491.

They drive the REAL endpoint + REAL ``generate_signed_get_url`` and mock only
at the google-library boundary, per the no-inline-mirror rule.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services import archive_intake

_SIGN_ERROR = AttributeError(
    "you need a private key to sign credentials.the credentials you are "
    "currently using just contains a token."
)


def test_display_url_requires_auth(client: TestClient):
    """The gate: an anonymous caller never gets a URL."""
    resp = client.get("/heatmap/display-url")
    assert resp.status_code == 401, resp.text


def test_display_url_503_without_bucket(client: TestClient, auth_headers, monkeypatch):
    """Authed but no display bucket (local dev) → 503, no crash."""
    monkeypatch.delenv("HEATMAP_GCS_BUCKET", raising=False)
    resp = client.get("/heatmap/display-url", headers=auth_headers)
    assert resp.status_code == 503, resp.text


def test_display_url_returns_signed_url(client: TestClient, auth_headers, monkeypatch):
    """Authed + bucket → a signed URL + TTL, with a private no-store cache header."""
    monkeypatch.setenv("HEATMAP_GCS_BUCKET", "common-trails-heatmap-prod")
    monkeypatch.setenv("HEATMAP_DISPLAY_URL_TTL_MIN", "120")
    with patch.object(
        archive_intake, "generate_signed_get_url",
        return_value="https://storage.example/signed-get?X-Goog-Signature=abc",
    ) as sign_mock:
        resp = client.get("/heatmap/display-url", headers=auth_headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"].startswith("https://storage.example/signed-get")
    assert body["expires_in"] == 120 * 60
    assert "no-store" in resp.headers.get("Cache-Control", "")
    # Signed the configured bucket + default object key.
    args, _ = sign_mock.call_args
    assert args[0] == "common-trails-heatmap-prod"
    assert args[1] == "heatmap-display.pmtiles"


@pytest.fixture()
def gcs_blob(monkeypatch):
    blob = MagicMock(name="blob")
    gcs_client = MagicMock(name="client")
    gcs_client.bucket.return_value.blob.return_value = blob
    monkeypatch.setattr(archive_intake, "_get_client", lambda: gcs_client)
    monkeypatch.setattr(archive_intake, "_signblob_fallback_logged", False, raising=False)
    return blob


def test_get_signer_reuses_signblob_fallback(gcs_blob):
    """The GET signer shares the token-only signBlob fallback with the PUT path."""
    from datetime import timedelta

    gcs_blob.generate_signed_url.side_effect = [
        _SIGN_ERROR,
        "https://storage.example/signed-get",
    ]
    creds = MagicMock(name="credentials")
    creds.service_account_email = "sa@common-trails.iam.gserviceaccount.com"
    creds.token = "ya29.ambient-token"

    with patch("google.auth.default", return_value=(creds, "common-trails")):
        url = archive_intake.generate_signed_get_url(
            "common-trails-heatmap-prod", "heatmap-display.pmtiles", timedelta(minutes=60)
        )

    assert url == "https://storage.example/signed-get"
    assert gcs_blob.generate_signed_url.call_count == 2
    first, second = gcs_blob.generate_signed_url.call_args_list
    assert first.kwargs["method"] == "GET"
    # No range binding — Range requests from the PMTiles client must all resolve.
    assert "content_type" not in first.kwargs
    assert second.kwargs["access_token"] == "ya29.ambient-token"
    assert second.kwargs["method"] == "GET"
