"""Non-regression: signed-URL minting must work with token-only credentials.

Prod bug (2026-07): on Cloud Run the runtime SA credentials
(``google.auth.compute_engine.credentials.Credentials``) carry only a TOKEN —
no private key — and ``blob.generate_signed_url(version="v4", ...)`` raised
``AttributeError: you need a private key to sign credentials``, turning every
``POST /imports/strava-archive/init`` into a 503 ("Upload URL unavailable").
The fix retries the signing through the IAM signBlob API by passing
``service_account_email`` + ``access_token`` from the refreshed ambient
credentials.

These tests drive the REAL ``generate_signed_put_url`` and mock only at the
google-library boundary (storage client + ``google.auth.default``).
"""
from unittest.mock import MagicMock, patch

import pytest

from app.services import archive_intake

# The exact error google-auth raises on Cloud Run (message shape matters:
# the fallback keys off the "private key" substring).
_SIGN_ERROR = AttributeError(
    "you need a private key to sign credentials.the credentials you are "
    "currently using <class 'google.auth.compute_engine.credentials."
    "Credentials'> just contains a token. see https://googleapis.dev/python/"
    "google-api-core/latest/auth.html#setting-up-a-service-account for more "
    "details."
)


@pytest.fixture()
def gcs_blob(monkeypatch):
    blob = MagicMock(name="blob")
    client = MagicMock(name="client")
    client.bucket.return_value.blob.return_value = blob
    monkeypatch.setattr(archive_intake, "_get_client", lambda: client)
    monkeypatch.setattr(archive_intake, "UPLOADS_BUCKET", "test-uploads-bucket")
    # raising=False so the fixture also runs against pre-fix code (where the
    # flag doesn't exist) and the non-regression test fails on the REAL bug.
    monkeypatch.setattr(archive_intake, "_signblob_fallback_logged", False, raising=False)
    return blob


def test_token_only_credentials_fall_back_to_iam_signblob(gcs_blob):
    """FAILS on pre-fix code: the AttributeError propagated straight out."""
    gcs_blob.generate_signed_url.side_effect = [
        _SIGN_ERROR,
        "https://storage.example/signed-put",
    ]
    creds = MagicMock(name="credentials")
    creds.service_account_email = (
        "common-trails-api@common-trails.iam.gserviceaccount.com"
    )
    creds.token = "ya29.ambient-token"

    with patch("google.auth.default", return_value=(creds, "common-trails")) as default_mock:
        url = archive_intake.generate_signed_put_url("archive-intake/u1/a.zip")

    assert url == "https://storage.example/signed-put"
    default_mock.assert_called_once()
    creds.refresh.assert_called_once()

    assert gcs_blob.generate_signed_url.call_count == 2
    first, second = gcs_blob.generate_signed_url.call_args_list
    assert "access_token" not in first.kwargs
    assert second.kwargs["service_account_email"] == creds.service_account_email
    assert second.kwargs["access_token"] == "ya29.ambient-token"
    # The retry preserves the V4 PUT + content-type binding.
    assert second.kwargs["version"] == "v4"
    assert second.kwargs["method"] == "PUT"
    assert second.kwargs["content_type"] == archive_intake.ARCHIVE_CONTENT_TYPE
    assert second.kwargs["expiration"] == archive_intake.SIGNED_URL_TTL


def test_private_key_happy_path_does_not_touch_ambient_credentials(gcs_blob):
    """Local dev (key-file credentials): behaviour must be byte-identical."""
    gcs_blob.generate_signed_url.return_value = "https://storage.example/signed-put"

    with patch("google.auth.default") as default_mock:
        url = archive_intake.generate_signed_put_url("archive-intake/u1/a.zip")

    assert url == "https://storage.example/signed-put"
    default_mock.assert_not_called()
    assert gcs_blob.generate_signed_url.call_count == 1
    assert "access_token" not in gcs_blob.generate_signed_url.call_args.kwargs


def test_unrelated_attributeerror_still_raises(gcs_blob):
    gcs_blob.generate_signed_url.side_effect = AttributeError("no attribute 'foo'")

    with patch("google.auth.default") as default_mock, \
            pytest.raises(AttributeError, match="foo"):
        archive_intake.generate_signed_put_url("archive-intake/u1/a.zip")

    default_mock.assert_not_called()
