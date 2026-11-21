"""Client-supplied consent fields are bounded on every endpoint that stores
them: an oversize ``consent_text`` or a ``locale`` that does not fit its
column answers 422 (not 500) and records nothing."""
from __future__ import annotations

import pytest

from app.services.archive_intake import MAX_CONSENT_TEXT_CHARS

VALID_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>tiny</name><trkseg>
    <trkpt lat="43.61" lon="3.87"><ele>100</ele></trkpt>
    <trkpt lat="43.62" lon="3.88"><ele>110</ele></trkpt>
  </trkseg></trk>
</gpx>"""

TOO_LONG_TEXT = "x" * (MAX_CONSENT_TEXT_CHARS + 1)


@pytest.fixture(autouse=True)
def _local_intake_dir(tmp_path, monkeypatch):
    from app.services import archive_intake

    monkeypatch.setattr(archive_intake, "ARCHIVE_INTAKE_DIR", str(tmp_path / "intake"))
    monkeypatch.setattr(archive_intake, "UPLOADS_BUCKET", "")


def _consent_rows(client, auth_headers) -> int:
    from app.db.models import ContributionConsent
    from app.db.session import SessionLocal

    uid = client.get("/auth/me", headers=auth_headers).json()["user_id"]
    db = SessionLocal()
    try:
        return db.query(ContributionConsent).filter(ContributionConsent.user_id == uid).count()
    finally:
        db.close()


def _multipart(endpoint: str, client, auth_headers, **consent):
    return client.post(
        endpoint, headers=auth_headers,
        files={"file": ("ride.gpx", VALID_GPX, "application/gpx+xml")},
        data={"sport": "road", "contribute_heatmap": "true", "consent_version": "v-test", **consent},
    )


def _init(client, auth_headers, **overrides):
    body = {"consent": True, "consent_version": "v-test", "consent_text": "Je consens (ODbL).",
            "locale": "fr", "filename": "export.zip"}
    body.update(overrides)
    return client.post("/imports/strava-archive/init", headers=auth_headers, json=body)


@pytest.mark.parametrize("endpoint", ["/imports/files", "/gpx/upload"])
class TestMultipartConsentBounds:
    def test_oversize_consent_text_is_422(self, endpoint, client, auth_headers):
        resp = _multipart(endpoint, client, auth_headers, consent_text=TOO_LONG_TEXT, locale="fr")
        assert resp.status_code == 422, resp.text
        assert _consent_rows(client, auth_headers) == 0

    def test_long_locale_is_422_not_500(self, endpoint, client, auth_headers):
        resp = _multipart(endpoint, client, auth_headers, consent_text="ok", locale="fr-FR-x-verylong")
        assert resp.status_code == 422, resp.text
        assert _consent_rows(client, auth_headers) == 0

    def test_garbage_locale_is_422(self, endpoint, client, auth_headers):
        resp = _multipart(endpoint, client, auth_headers, consent_text="ok", locale="f r")
        assert resp.status_code == 422, resp.text

    def test_bounded_values_still_record_consent(self, endpoint, client, auth_headers):
        resp = _multipart(
            endpoint, client, auth_headers, consent_text="x" * MAX_CONSENT_TEXT_CHARS, locale="fr-FR",
        )
        assert resp.status_code == 202, resp.text
        assert _consent_rows(client, auth_headers) == 1


class TestArchiveInitConsentBounds:
    def test_oversize_consent_text_is_422(self, client, auth_headers):
        resp = _init(client, auth_headers, consent_text=TOO_LONG_TEXT)
        assert resp.status_code == 422, resp.text
        assert _consent_rows(client, auth_headers) == 0

    def test_long_locale_is_422(self, client, auth_headers):
        resp = _init(client, auth_headers, locale="fr-FR-x-verylong")
        assert resp.status_code == 422, resp.text
        assert _consent_rows(client, auth_headers) == 0

    def test_garbage_locale_is_422(self, client, auth_headers):
        resp = _init(client, auth_headers, locale="f r")
        assert resp.status_code == 422, resp.text
        assert _consent_rows(client, auth_headers) == 0

    def test_bounded_values_pass(self, client, auth_headers):
        resp = _init(client, auth_headers, consent_text="x" * MAX_CONSENT_TEXT_CHARS, locale="en")
        assert resp.status_code == 201, resp.text
