"""DB-free unit tests for the onboarding-friction fixes.

* ``_safe_redirect`` — the open-redirect guard on the magic link's
  ``redirect`` param (mirrors frontend/lib/safe-redirect.ts).
* ``render_archive_done_email`` — provider-neutral wording: the drain does not
  know whether a .zip came from Strava, Garmin or Komoot, so the
  "nothing new" copy must say "Ton archive", never "archive Strava".

Drives the REAL functions (no inline mirrors).
"""
import pytest

from app.api.auth import _safe_redirect
from app.services.email import render_archive_done_email


class TestSafeRedirect:
    @pytest.mark.parametrize("ok", ["/map", "/strava", "/map?activity=abc&x=1", "/stats#top"])
    def test_accepts_same_origin_relative_paths(self, ok):
        assert _safe_redirect(ok) == ok

    @pytest.mark.parametrize("bad", [
        None, "", "//evil.com", "///evil.com", "https://evil.com", "http:/evil.com",
        "javascript:alert(1)", "evil.com", "/\\evil.com", "\\\\evil.com",
        "/map\r\nLocation: https://evil.com", "/" + "a" * 600,
    ])
    def test_rejects_everything_else(self, bad):
        assert _safe_redirect(bad) is None


class TestMagicLinkLimits:
    def test_per_email_3_per_ip_20_both_layers_consistent(self):
        from app.api import auth

        assert auth._MAGIC_LINK_MAX_PER_EMAIL == 3
        assert auth._MAGIC_LINK_MAX_PER_IP == 20
        # The slowapi per-IP decorator uses the same figure as the DB layer.
        assert auth._MAGIC_LINK_IP_LIMIT_STR == "20/15minute"


class TestArchiveDoneEmailWording:
    @pytest.mark.parametrize("locale", ["fr", "en"])
    @pytest.mark.parametrize("imported", [0, 3])
    def test_never_names_strava_as_the_provider(self, locale, imported):
        subject, html = render_archive_done_email(
            imported=imported, skipped=2, failed=0, locale=locale,
        )
        assert "archive Strava" not in html
        assert "Strava archive" not in html
        assert "Strava" not in subject

    def test_nothing_new_copy_says_votre_archive(self):
        _subject, html = render_archive_done_email(imported=0, skipped=5, failed=0, locale="fr")
        assert "Ton archive a été traitée" in html

    def test_nothing_new_copy_says_your_archive_en(self):
        _subject, html = render_archive_done_email(imported=0, skipped=5, failed=0, locale="en")
        assert "Your archive has been processed" in html
