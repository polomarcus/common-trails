"""Public heatmap export endpoints — PRE-COMPUTED artifacts ONLY.

The community heatmap export is served entirely from STATIC artifacts the
``build_pmtiles`` job publishes to the public (ODbL) GCS bucket. There is NO
on-demand/per-request tile computation, NO async build queue, and NO cached
mbtiles — those were removed on 2026-07 (Paul's explicit decision) because:

- they re-ran heavy aggregation / tippecanoe / Pillow renders on the weak web
  instance (503 / OOM risk); and
- the cached mbtiles kept copies of traces that had since been DELETED — a
  GDPR wart. Pre-computed-only means a rebuild is the single source of truth
  and a deletion propagates on the next build.

Three pre-computed artifacts, all published by ``app.jobs.build_pmtiles``:

- **PMTiles** — single vector-tile archive, MapLibre / pmtiles.js compatible.
  ``GET /export/heatmap.pmtiles`` 302s to the canonical GCS URL. A mutable
  alias (always the latest build) plus an immutable per-build pinned snapshot
  advertised via the pointer JSON.
- **Raster calque** — an XYZ PNG tile pyramid + a TileJSON
  (``raster/tiles.json`` + ``raster/{z}/{x}/{y}.png``) so gpx.studio / VisuGPX
  can add the heatmap as a custom overlay ("calque") via one URL, no download.
- **GeoJSONL** — the full national newline-delimited-GeoJSON aggregation
  (gzipped) the raw PMTiles render from. ``GET /export/heatmap.geojsonl`` 302s
  to the static GCS object. gpx.studio / QGIS / web tooling.

Backend never proxies any binary — every download is a 302 to the public GCS
object, keeping Cloud Run free of multi-MB transfers.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/export", tags=["export"])
log = logging.getLogger(__name__)

# Match the heatmap router default; production sets this via the API
# Cloud Run service env. See ``app/api/heatmap.py`` for the parallel
# constant — kept independent so this module never imports heatmap.py
# (which has heavyweight side-effects at import time).
HEATMAP_K_ANONYMITY = int(os.environ.get("HEATMAP_K_ANONYMITY", "2"))

ODBL_LICENSE = "ODbL-1.0"
ODBL_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
ATTRIBUTION = "© CHEMINS COMMUNS contributors — ODbL 1.0"

# Default public bucket used when ``HEATMAP_GCS_BUCKET`` is unset — the prod
# bucket the build job publishes to. Keeps the geojsonl / raster / pmtiles
# discovery URLs pointing at real artifacts even in an unconfigured env.
_DEFAULT_HEATMAP_BUCKET = "common-trails-heatmap-prod"

# Pointer JSON filename in the heatmap-export GCS bucket. Written by
# ``build_pmtiles._upload_to_export_bucket`` after every successful
# build. Consumers fetch this to resolve the latest pinned snapshot URL.
_POINTER_FILENAME = "heatmap-display.json"

# Pre-built raw-display aggregation artifact, published (gzipped) by
# ``app.jobs.build_pmtiles._upload_geojsonl_to_export_bucket`` next to the
# PMTiles. This is the static full-national GeoJSONL the export path serves.
_GEOJSONL_BLOB = "heatmap-display.geojsonl.gz"

# Dev fallback URL — used when HEATMAP_GCS_BUCKET is unset (local dev,
# CI, etc.). Should point at the ABSOLUTE URL of the frontend's static
# export — typically `http://localhost:3787/heatmap-display.pmtiles` in
# docker-compose dev, or omitted in CI (the test client doesn't load
# the file, just inspects URL shape).
#
# Why absolute: the `/export/heatmap.pmtiles` redirect issues a 302 to
# this URL; the browser resolves the Location header against the REQUEST
# origin (i.e. the BACKEND on :8787 in dev), not the frontend. A
# relative path like `/heatmap-display.pmtiles` therefore 404s at the
# backend instead of fetching the file from the frontend on :3787.
#
# Falls back to the literal relative path only when the env var is not
# set, so existing test fixtures + the documentation-shape assertions
# keep working; a real download in dev requires the env var.
_DEV_FALLBACK_URL = os.environ.get(
    "HEATMAP_DEV_PMTILES_URL", "/heatmap-display.pmtiles"
)

# Local-dev path where build_pmtiles writes the binary by default. Used
# only to report size_bytes + generated_at in the discovery payload.
_LOCAL_PMTILES_PATH = "/app/frontend/public/heatmap-display.pmtiles"


@dataclass(frozen=True)
class HeatmapFormatPayload:
    """Single entry of the ``formats`` list in the discovery response.

    Every entry advertises a PRE-COMPUTED static artifact via ``url`` (a
    public GCS object). ``template`` carries the XYZ tile-URL template for
    the raster calque. ``note`` explains how to consume the artifact.
    """
    format: str
    mime: str
    url: str | None = None
    template: str | None = None
    size_bytes: int | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"format": self.format, "mime": self.mime}
        if self.url is not None:
            d["url"] = self.url
        if self.template is not None:
            d["template"] = self.template
        if self.size_bytes is not None:
            d["size_bytes"] = self.size_bytes
        if self.note is not None:
            d["note"] = self.note
        return d


def _heatmap_bucket() -> str:
    """The public heatmap GCS bucket — env-configured or the prod default."""
    return os.environ.get("HEATMAP_GCS_BUCKET", "").strip() or _DEFAULT_HEATMAP_BUCKET


def _canonical_pmtiles_url() -> str:
    """Return the canonical URL for the heatmap PMTiles binary.

    Prod: public GCS bucket URL derived from ``HEATMAP_GCS_BUCKET``.
    Dev / unconfigured: relative path served from the frontend's static
    export so ``curl http://localhost:3787/heatmap-display.pmtiles`` keeps
    working without any GCP credentials.

    This is the MUTABLE alias — always points at the latest build.
    Consumers who want an immutable snapshot fetch the pointer JSON (see
    ``_read_pointer``) and download ``pinned_url``.
    """
    bucket = os.environ.get("HEATMAP_GCS_BUCKET", "").strip()
    if bucket:
        return f"https://storage.googleapis.com/{bucket}/heatmap-display.pmtiles"
    return _DEV_FALLBACK_URL


def _canonical_geojsonl_url() -> str:
    """Public URL of the pre-built national GeoJSONL (gzipped) artifact.

    Always a real GCS URL — uses the configured bucket or the prod default
    so a download works even in an unconfigured env.
    """
    return f"https://storage.googleapis.com/{_heatmap_bucket()}/{_GEOJSONL_BLOB}"


def _raster_tilejson_url() -> str:
    """Public URL of the raster calque TileJSON (``raster/tiles.json``)."""
    return f"https://storage.googleapis.com/{_heatmap_bucket()}/raster/tiles.json"


def _raster_tile_template_url() -> str:
    """XYZ tile-URL template for the raster calque (``raster/{z}/{x}/{y}.png``)."""
    return f"https://storage.googleapis.com/{_heatmap_bucket()}/raster/{{z}}/{{x}}/{{y}}.png"


def _pointer_url() -> str | None:
    """URL of the pointer JSON in the heatmap-export GCS bucket.

    None in local dev (no bucket configured). The pointer file is
    written by ``app.jobs.build_pmtiles._upload_to_export_bucket`` after
    every successful build.
    """
    bucket = os.environ.get("HEATMAP_GCS_BUCKET", "").strip()
    if not bucket:
        return None
    return f"https://storage.googleapis.com/{bucket}/{_POINTER_FILENAME}"


def _read_pointer() -> dict[str, Any] | None:
    """Fetch + parse the pointer JSON. Returns None on any failure.

    Best-effort: a missing pointer (fresh bucket before the first
    build) or transient GCS read error degrades the discovery payload
    gracefully — we return None and the discovery omits the
    ``pinned_url`` / ``version`` fields.

    Uses the same ``google-cloud-storage`` SDK as the rest of this
    module so we can rely on the application-default credentials path
    that Cloud Run already configures.

    Callers should prefer ``_read_pointer_cached`` to avoid a GCS
    round-trip on every hit (with a 5s timeout per failure path).
    """
    bucket_name = os.environ.get("HEATMAP_GCS_BUCKET", "").strip()
    if not bucket_name:
        return None
    try:
        from google.cloud import storage  # type: ignore[import-not-found]

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(_POINTER_FILENAME)
        raw = blob.download_as_text(timeout=5.0)
        return json.loads(raw)
    except Exception as e:  # pragma: no cover — best-effort pointer
        log.debug("Pointer JSON read failed (bucket=%s): %s", bucket_name, e)
        return None


# In-process pointer cache. Per-Cloud-Run-instance (each instance has
# its own); a stale read of up to ``_POINTER_CACHE_TTL_S`` seconds is
# acceptable because the pointer is itself cached at the CDN for 5 min
# (see build_pmtiles.py). Previously every hit on ``GET /export/heatmap``
# did a ``storage.Client()`` instantiation + ``blob.download_as_text(
# timeout=5.0)``; at 100 beta users polling discovery that was 100 RPS of
# GCS metadata fetches.
_POINTER_CACHE_TTL_S = 60.0
_pointer_cache: tuple[float, dict[str, Any] | None] = (0.0, None)


def _read_pointer_cached() -> dict[str, Any] | None:
    """Cached wrapper around ``_read_pointer`` with a 60 s TTL.

    Stale reads for up to 60 s are acceptable: the pointer's
    ``Cache-Control: public, max-age=300`` upstream already implies
    a 5-minute staleness budget. The cache lives in module-level
    state and is shared across requests on a single Cloud Run
    instance.
    """
    global _pointer_cache
    now = time.monotonic()
    cached_at, value = _pointer_cache
    if now - cached_at < _POINTER_CACHE_TTL_S:
        return value
    value = _read_pointer()
    _pointer_cache = (now, value)
    return value


def _reset_pointer_cache() -> None:
    """Test-only helper to clear the pointer cache between cases."""
    global _pointer_cache
    _pointer_cache = (0.0, None)


def _local_pmtiles_stat() -> tuple[int | None, str]:
    """Best-effort ``(size_bytes, generated_at_iso)`` for the local file.

    Both fall back gracefully when the file is absent (fresh dev env, or
    a prod container that doesn't carry the binary): size becomes
    ``None`` and the timestamp falls back to ``now()`` so the response
    shape stays stable.
    """
    candidates = [
        # The build_pmtiles job writes here in local dev (Makefile target
        # `pmtiles` copies into the frontend public folder).
        _LOCAL_PMTILES_PATH,
        # Local dev — Docker compose mounts frontend at /app/frontend.
        os.path.join(os.getcwd(), "frontend", "public", "heatmap-display.pmtiles"),
        # CI / Playwright env runs from the repo root.
        "frontend/public/heatmap-display.pmtiles",
    ]
    for path in candidates:
        try:
            st = os.stat(path)
            return st.st_size, datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat()
        except OSError:
            continue
    return None, datetime.now(tz=UTC).isoformat()


# ── discovery ────────────────────────────────────────────────────────────────


@router.get("/heatmap")
async def heatmap_discovery() -> dict[str, Any]:
    """Discovery JSON for the heatmap export — PRE-COMPUTED artifacts only.

    Stable across builds — third-party consumers can poll this daily and
    re-download only when ``size_bytes`` / ``generated_at`` / the pointer
    ``version`` changes. Advertises the three static artifacts the
    ``build_pmtiles`` job publishes:

    - **pmtiles** — mutable ``url`` + (when the pointer JSON is available)
      an immutable ``pinned_url`` + ``version`` + ``pointer_url``.
    - **raster** — the calque ``url`` (``raster/tiles.json``) + the XYZ
      ``template`` (``raster/{z}/{x}/{y}.png``).
    - **geojsonl** — the full national gzipped GeoJSONL ``url``.
    """
    url = _canonical_pmtiles_url()
    size, generated_at = _local_pmtiles_stat()

    # Pointer JSON — best-effort. None in dev / fresh-bucket. When
    # present, exposes the immutable per-day snapshot URL for clients
    # who want a stable pin. 60 s in-process cache around the GCS read
    # so a hot discovery endpoint doesn't hammer GCS metadata (each
    # blob.download_as_text() is a 5 s timeout on slow paths).
    pointer = _read_pointer_cached()
    pmtiles_entry = HeatmapFormatPayload(
        format="pmtiles",
        url=url,
        mime="application/vnd.pmtiles",
        size_bytes=size,
        note="All sports, full region. Use with MapLibre / pmtiles.js.",
    ).as_dict()
    if pointer:
        pmtiles_entry["pinned_url"] = pointer.get("latest_url")
        pmtiles_entry["version"] = pointer.get("latest")
        if "captured_at" in pointer:
            pmtiles_entry["captured_at"] = pointer["captured_at"]
        if "size_bytes" in pointer:
            # Prefer the pointer's size (matches the canonical upload)
            # over the local stat (which can be missing on the API
            # container).
            pmtiles_entry["size_bytes"] = pointer["size_bytes"]
        if "edge_count" in pointer:
            pmtiles_entry["edge_count"] = pointer["edge_count"]
    if _pointer_url():
        pmtiles_entry["pointer_url"] = _pointer_url()

    raster_entry = HeatmapFormatPayload(
        format="raster",
        url=_raster_tilejson_url(),
        template=_raster_tile_template_url(),
        mime="image/png",
        note=(
            "Pre-rendered raster XYZ tile pyramid (the 'calque'). Add the "
            "TileJSON url — or the {z}/{x}/{y}.png template — as a custom "
            "raster overlay in gpx.studio / VisuGPX / QGIS."
        ),
    ).as_dict()

    geojsonl_entry = HeatmapFormatPayload(
        format="geojsonl",
        url=_canonical_geojsonl_url(),
        mime="application/geo+json",
        note=(
            "Full-region newline-delimited GeoJSON (gzipped), one LineString "
            "per aggregated way. Static pre-built artifact. gpx.studio / QGIS."
        ),
    ).as_dict()

    return {
        "license": ODBL_LICENSE,
        "license_url": ODBL_URL,
        "attribution": ATTRIBUTION,
        "k_anonymity": HEATMAP_K_ANONYMITY,
        "generated_at": generated_at,
        "formats": [
            pmtiles_entry,
            raster_entry,
            geojsonl_entry,
        ],
    }


# ── PMTiles passthrough (pre-computed) ────────────────────────────────────────


@router.get("/heatmap.pmtiles")
async def heatmap_pmtiles_redirect() -> RedirectResponse:
    """302 redirect to the canonical (pre-computed) PMTiles file.

    Existing static path ``/heatmap-display.pmtiles`` keeps working in
    parallel — this endpoint is the documented, stable, brand-able
    entry point for third-party tooling.
    """
    return RedirectResponse(url=_canonical_pmtiles_url(), status_code=302)


# ── GeoJSONL passthrough (pre-computed) ───────────────────────────────────────


@router.get("/heatmap.geojsonl")
async def heatmap_geojsonl_redirect() -> RedirectResponse:
    """302 redirect to the pre-built national GeoJSONL (gzipped) artifact.

    Mirrors the PMTiles redirect: the body is a STATIC object published by
    ``build_pmtiles`` — the backend never computes or proxies it.
    """
    return RedirectResponse(url=_canonical_geojsonl_url(), status_code=302)
