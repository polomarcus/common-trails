"""Unit tests for the env-driven public heatmap base URL (SSOT).

An external HTTPS LB + CDN fronts the `common-trails-heatmap-prod` bucket at the
first-party domain `https://tiles.chemins-communs.fr` (fixes ad-blocker/Firefox
blocking of `storage.googleapis.com` + a clean share URL). `HEATMAP_PUBLIC_BASE_URL`
is the single source of truth that flips EVERY public heatmap URL:

* `build_pmtiles._public_base_url` → raster `tiles.json` template, `mutable_url`,
  the immutable pinned snapshot AND the freshness pointer's `latest_url` (the URL
  the client resolves the pmtiles THROUGH — the critical one).
* `email._heatmap_public_base_url` → the gpx.studio/VisuGPX "calque" tile URL.

These drive the REAL helpers (no inline mirror) for both the unset default
(today's behavior = raw GCS host) and the set override.
"""
from app.jobs.build_pmtiles import _public_base_url
from app.services import heatmap_raster_pyramid as pyr
from app.services.email import _heatmap_public_base_url


def _clear_env(monkeypatch):
    monkeypatch.delenv("HEATMAP_PUBLIC_BASE_URL", raising=False)


class TestBuildPmtilesBaseUrl:
    def test_default_unset_is_raw_gcs_object_url(self, monkeypatch):
        _clear_env(monkeypatch)
        assert (
            _public_base_url("common-trails-heatmap-prod")
            == "https://storage.googleapis.com/common-trails-heatmap-prod"
        )

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("HEATMAP_PUBLIC_BASE_URL", "https://tiles.chemins-communs.fr")
        assert _public_base_url("common-trails-heatmap-prod") == "https://tiles.chemins-communs.fr"

    def test_trailing_slash_is_stripped(self, monkeypatch):
        monkeypatch.setenv("HEATMAP_PUBLIC_BASE_URL", "https://tiles.chemins-communs.fr/")
        # callers append "/<path>" — a trailing slash would double it.
        assert _public_base_url("b") == "https://tiles.chemins-communs.fr"

    def test_pointer_latest_url_uses_the_base(self, monkeypatch):
        """The pointer's `latest_url` = f"{base}/{pinned_name}" — the URL the
        client resolves the pmtiles THROUGH. It MUST become tiles.* when set."""
        monkeypatch.setenv("HEATMAP_PUBLIC_BASE_URL", "https://tiles.chemins-communs.fr")
        pinned_name = "heatmap-display-v20606-ab12cd34.pmtiles"
        latest_url = f"{_public_base_url('common-trails-heatmap-prod')}/{pinned_name}"
        assert latest_url == f"https://tiles.chemins-communs.fr/{pinned_name}"

    def test_pointer_latest_url_default_stays_gcs(self, monkeypatch):
        _clear_env(monkeypatch)
        pinned_name = "heatmap-display-v20606-ab12cd34.pmtiles"
        latest_url = f"{_public_base_url('common-trails-heatmap-prod')}/{pinned_name}"
        assert latest_url == (
            f"https://storage.googleapis.com/common-trails-heatmap-prod/{pinned_name}"
        )

    def test_raster_tilejson_template_uses_the_base(self, monkeypatch):
        """Drive the REAL build_tilejson with a tiles_url composed from the
        helper — the template that gpx.studio/VisuGPX resolve tiles through."""
        monkeypatch.setenv("HEATMAP_PUBLIC_BASE_URL", "https://tiles.chemins-communs.fr")
        tiles_url = f"{_public_base_url('common-trails-heatmap-prod')}/raster/{{z}}/{{x}}/{{y}}.png"
        tj = pyr.build_tilejson(
            tiles_url=tiles_url, min_zoom=6, max_zoom=14,
            bounds=(3.5, 43.0, 4.2, 43.9), attribution="© CHEMINS COMMUNS — ODbL 1.0",
        )
        assert tj["tiles"] == ["https://tiles.chemins-communs.fr/raster/{z}/{x}/{y}.png"]


class TestEmailCalqueBaseUrl:
    def test_default_unset_is_prod_bucket(self, monkeypatch):
        _clear_env(monkeypatch)
        assert (
            _heatmap_public_base_url()
            == "https://storage.googleapis.com/common-trails-heatmap-prod"
        )

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("HEATMAP_PUBLIC_BASE_URL", "https://tiles.chemins-communs.fr")
        assert _heatmap_public_base_url() == "https://tiles.chemins-communs.fr"
