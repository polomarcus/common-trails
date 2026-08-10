"""Tests for the public heatmap export endpoints — PRE-COMPUTED artifacts ONLY.

The export surface was reduced to static, pre-built artifacts (2026-07): the
PMTiles, the raster calque (``raster/tiles.json`` + ``{z}/{x}/{y}.png``), and
the national GeoJSONL. There is NO on-demand geojson/mbtiles/kml/gpx build and
NO async pipeline anymore, so those tests are gone.
"""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def _env(**overrides: str | None):
    """Patch env vars for the duration of a test, restoring previous values.

    A ``None`` value deletes the key (useful to assert dev-fallback paths).
    """
    sentinel = object()
    previous: dict[str, object] = {k: os.environ.get(k, sentinel) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, prev in previous.items():
            if prev is sentinel:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev  # type: ignore[assignment]


# ── Discovery ────────────────────────────────────────────────────────────────


def test_heatmap_discovery_returns_license_and_precomputed_formats(client):
    """GET /export/heatmap returns 200 + license metadata + the 3 static formats."""
    resp = client.get("/export/heatmap")
    assert resp.status_code == 200
    data = resp.json()
    assert data["license"] == "ODbL-1.0"
    assert data["license_url"] == "https://opendatacommons.org/licenses/odbl/1-0/"
    assert "CHEMINS COMMUNS" in data["attribution"]
    assert "ODbL" in data["attribution"]
    assert isinstance(data["k_anonymity"], int)
    assert data["k_anonymity"] >= 1
    assert isinstance(data["generated_at"], str)

    formats = data["formats"]
    assert isinstance(formats, list) and len(formats) == 3
    by_format = {f["format"]: f for f in formats}
    # ONLY pre-computed artifacts — the on-demand / async formats are gone.
    assert set(by_format) == {"pmtiles", "raster", "geojsonl"}

    pmt = by_format["pmtiles"]
    assert pmt["mime"] == "application/vnd.pmtiles"
    assert isinstance(pmt["url"], str) and pmt["url"]
    # size_bytes is optional — present only when the local file exists

    raster = by_format["raster"]
    assert raster["mime"] == "image/png"
    assert raster["url"].endswith("/raster/tiles.json")
    assert raster["template"].endswith("/raster/{z}/{x}/{y}.png")

    gjl = by_format["geojsonl"]
    assert gjl["mime"] == "application/geo+json"
    assert gjl["url"].endswith("/heatmap-display.geojsonl.gz")


def test_discovery_no_ondemand_or_async_entries(client):
    """The removed formats + the async block must NOT appear anymore."""
    resp = client.get("/export/heatmap")
    assert resp.status_code == 200
    data = resp.json()
    assert "async" not in data
    fmts = {f["format"] for f in data["formats"]}
    assert fmts.isdisjoint({"mbtiles", "mbtiles-raster", "geojson", "gpx", "kml"})


def test_heatmap_discovery_dev_fallback_url(client):
    """When HEATMAP_GCS_BUCKET is unset, PMTiles URL is the relative dev fallback.

    Note: ``_DEV_FALLBACK_URL`` is captured at import time from the
    ``HEATMAP_DEV_PMTILES_URL`` env var; we patch the module-level
    constant directly here so the assertion is stable regardless of
    what the surrounding docker-compose / pytest env set.
    """
    with _env(HEATMAP_GCS_BUCKET=None):
        from app.api import export as export_module
        original = export_module._DEV_FALLBACK_URL
        export_module._DEV_FALLBACK_URL = "/heatmap-display.pmtiles"
        try:
            resp = client.get("/export/heatmap")
        finally:
            export_module._DEV_FALLBACK_URL = original
    assert resp.status_code == 200
    formats = {f["format"]: f for f in resp.json()["formats"]}
    assert formats["pmtiles"]["url"] == "/heatmap-display.pmtiles"


def test_heatmap_discovery_uses_gcs_bucket_when_set(client):
    """When HEATMAP_GCS_BUCKET is set, PMTiles URL is the public GCS HTTPS URL."""
    with _env(HEATMAP_GCS_BUCKET="common-trails-heatmap-test"):
        resp = client.get("/export/heatmap")
    assert resp.status_code == 200
    formats = {f["format"]: f for f in resp.json()["formats"]}
    assert formats["pmtiles"]["url"] == (
        "https://storage.googleapis.com/common-trails-heatmap-test/heatmap-display.pmtiles"
    )
    # Raster + geojsonl derive from the same bucket.
    assert formats["raster"]["url"] == (
        "https://storage.googleapis.com/common-trails-heatmap-test/raster/tiles.json"
    )
    assert formats["geojsonl"]["url"] == (
        "https://storage.googleapis.com/common-trails-heatmap-test/heatmap-display.geojsonl.gz"
    )


# ── PMTiles redirect (pre-computed, kept) ─────────────────────────────────────


def test_heatmap_pmtiles_redirect_to_dev_fallback(client):
    """GET /export/heatmap.pmtiles 302s to the dev-fallback URL when unconfigured."""
    with _env(HEATMAP_GCS_BUCKET=None):
        from app.api import export as export_module
        original = export_module._DEV_FALLBACK_URL
        export_module._DEV_FALLBACK_URL = "/heatmap-display.pmtiles"
        try:
            resp = client.get("/export/heatmap.pmtiles", follow_redirects=False)
        finally:
            export_module._DEV_FALLBACK_URL = original
    assert resp.status_code == 302
    assert resp.headers["location"] == "/heatmap-display.pmtiles"


def test_heatmap_pmtiles_redirect_to_gcs(client):
    """GET /export/heatmap.pmtiles 302s to the GCS URL when HEATMAP_GCS_BUCKET is set."""
    with _env(HEATMAP_GCS_BUCKET="common-trails-heatmap-prod"):
        resp = client.get("/export/heatmap.pmtiles", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == (
        "https://storage.googleapis.com/common-trails-heatmap-prod/heatmap-display.pmtiles"
    )


# ── GeoJSONL redirect (pre-computed, new) ─────────────────────────────────────


def test_heatmap_geojsonl_redirect_to_gcs(client):
    """GET /export/heatmap.geojsonl 302s to the static GCS geojsonl.gz object."""
    with _env(HEATMAP_GCS_BUCKET="common-trails-heatmap-prod"):
        resp = client.get("/export/heatmap.geojsonl", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == (
        "https://storage.googleapis.com/common-trails-heatmap-prod/heatmap-display.geojsonl.gz"
    )


def test_heatmap_geojsonl_redirect_defaults_to_prod_bucket(client):
    """With no bucket configured, the geojsonl redirect uses the prod default bucket."""
    with _env(HEATMAP_GCS_BUCKET=None):
        resp = client.get("/export/heatmap.geojsonl", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == (
        "https://storage.googleapis.com/common-trails-heatmap-prod/heatmap-display.geojsonl.gz"
    )


# ── Pointer JSON (pinned snapshot) surfacing in discovery ─────────────────────


def test_discovery_with_pointer_surfaces_pinned_url(client):
    """When the pointer JSON is readable, discovery exposes pinned_url + version."""
    from app.api.export import _reset_pointer_cache

    _reset_pointer_cache()
    fake_pointer = {
        "latest": "v20606",
        "latest_url": "https://storage.googleapis.com/test-bucket/heatmap-display-v20606.pmtiles",
        "size_bytes": 12345678,
        "captured_at": "2026-06-02T00:00:00+00:00",
        "edge_count": 1700000,
    }
    with _env(HEATMAP_GCS_BUCKET="test-bucket"), patch(
        "app.api.export._read_pointer", return_value=fake_pointer,
    ):
        resp = client.get("/export/heatmap")
    assert resp.status_code == 200
    pmt = next(f for f in resp.json()["formats"] if f["format"] == "pmtiles")
    assert pmt["pinned_url"] == fake_pointer["latest_url"]
    assert pmt["version"] == "v20606"
    assert pmt["captured_at"] == "2026-06-02T00:00:00+00:00"
    assert pmt["edge_count"] == 1700000
    # Mutable URL preserved.
    assert pmt["url"].endswith("/heatmap-display.pmtiles")
    _reset_pointer_cache()


def test_discovery_without_pointer_still_works(client):
    """Missing pointer is harmless — discovery just omits the pinned fields."""
    from app.api.export import _reset_pointer_cache

    _reset_pointer_cache()
    with _env(HEATMAP_GCS_BUCKET=None), patch(
        "app.api.export._read_pointer", return_value=None,
    ):
        resp = client.get("/export/heatmap")
    assert resp.status_code == 200
    pmt = next(f for f in resp.json()["formats"] if f["format"] == "pmtiles")
    assert "pinned_url" not in pmt
    assert "version" not in pmt
    _reset_pointer_cache()


def test_pointer_cache_hits_gcs_once_in_ttl(client):
    """Discovery hits GCS exactly once across N calls within the TTL.

    Previously every discovery hit instantiated a ``storage.Client()`` and
    called ``blob.download_as_text(timeout=5.0)``. Now a 60 s in-process
    cache wraps the GCS read. We mock the inner ``_read_pointer`` and assert
    it's called once across 5 discovery hits.
    """
    from app.api.export import _reset_pointer_cache

    fake_pointer = {
        "latest": "v494544-deadbeef",
        "latest_url": "https://storage.googleapis.com/test-bucket/heatmap-display-v494544-deadbeef.pmtiles",
        "size_bytes": 1234,
    }
    _reset_pointer_cache()
    with _env(HEATMAP_GCS_BUCKET="test-bucket"), patch(
        "app.api.export._read_pointer", return_value=fake_pointer,
    ) as mock_read:
        for _ in range(5):
            resp = client.get("/export/heatmap")
            assert resp.status_code == 200
    # Five discovery hits, ONE GCS round-trip (the cache absorbed
    # the other four).
    assert mock_read.call_count == 1, (
        f"expected 1 GCS read across 5 discovery hits, got {mock_read.call_count}"
    )
    _reset_pointer_cache()


# ── build_pmtiles version-id pin (independent of the export routes) ───────────


def test_version_id_unique_per_build_content_hash():
    """``_pmtiles_version_id`` produces distinct ids for distinct bytes.

    ``v{epoch_day}`` collided for any same-day rebuild → broke the
    ``Cache-Control: public, max-age=31536000, immutable`` contract. Fix is
    ``v{epoch_hour}-{sha8}`` so:

    - same bytes in the same hour → same id (idempotent re-upload OK).
    - different bytes (in the same hour or not) → different id (the
      immutable promise holds).
    """
    from app.jobs.build_pmtiles import _pmtiles_version_id

    id_a = _pmtiles_version_id(b"the-bytes-from-build-A")
    id_b = _pmtiles_version_id(b"the-bytes-from-build-B")
    id_a_repeat = _pmtiles_version_id(b"the-bytes-from-build-A")

    # Different bytes → different id (the contract we broke).
    assert id_a != id_b, "intra-hour rebuilds must NOT collide"
    # Same bytes → same id (idempotent — re-upload of identical
    # content lands on the same blob name, which is desirable).
    assert id_a == id_a_repeat

    # Shape: ``v{epoch_hour}-{sha8}``.
    assert id_a.startswith("v")
    parts = id_a.split("-")
    assert len(parts) == 2, f"expected v<hour>-<sha8>, got {id_a!r}"
    assert len(parts[1]) == 8, f"sha8 must be 8 hex chars, got {parts[1]!r}"
    # Sanity: hour component is an int.
    int(parts[0][1:])


# ── ExportRequest cleanup job (model + job still exist) ───────────────────────


def _insert_export_request(**overrides) -> str:
    """Insert a row directly via SessionLocal. Returns the id."""
    from datetime import UTC, datetime, timedelta

    from app.db.models import ExportRequest
    from app.db.session import SessionLocal

    rid = str(uuid.uuid4())
    db = SessionLocal()
    try:
        defaults = {
            "id": rid,
            "user_id": None,
            "format": "geojson",
            "bbox": [3.85, 43.59, 3.95, 43.65],
            "sport": "gravel",
            "min_uc": 2,
            "days": None,
            "status": "queued",
            "progress": 0.0,
            "expires_at": datetime.now(UTC) + timedelta(hours=24),
        }
        defaults.update(overrides)
        row = ExportRequest(**defaults)
        db.add(row)
        db.commit()
    finally:
        db.close()
    return rid


def test_cleanup_export_requests_deletes_expired_rows():
    """The cleanup job removes rows past expires_at and keeps the live ones."""
    from datetime import UTC, datetime, timedelta

    from app.db.models import ExportRequest
    from app.db.session import SessionLocal
    from app.jobs.cleanup_export_requests import main as cleanup

    rid_live = _insert_export_request(
        expires_at=datetime.now(UTC) + timedelta(hours=12),
    )
    rid_dead = _insert_export_request(
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )

    deleted = cleanup()
    assert deleted >= 1

    db = SessionLocal()
    try:
        live = db.query(ExportRequest).filter(ExportRequest.id == rid_live).first()
        dead = db.query(ExportRequest).filter(ExportRequest.id == rid_dead).first()
        assert live is not None
        assert dead is None
    finally:
        db.close()
