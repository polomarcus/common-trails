"""Cloud Storage client + bucket constants for the CDN manifest health check.

Historically this module also published the matched-era CDN "api-cache"
(``publish_heatmap_cache`` + versioned manifest + MVT pre-generation). That
whole write-through subsystem was matched-only — it read the now-dropped
``heat_edges`` substrate and rendered trails/graph GeoJSON via the removed
``graph_builder`` service. With the raw-trace pivot
(``HEATMAP_DISPLAY_SOURCE=raw``) the display artefact is the static
``heatmap-display.pmtiles`` built by ``app.jobs.build_pmtiles``, so the publish
subsystem became dead code and was removed.

What remains is the small live surface still used by ``app.api.health`` to
report freshness of any legacy ``manifest.json`` (it gracefully degrades to
``no_cache`` when none exists under raw mode): the GCS client singleton plus the
bucket/prefix constants.
"""
import logging
import os
import threading

log = logging.getLogger(__name__)

BUCKET_NAME = os.environ.get("FRONTEND_BUCKET", "common-trails-frontend")
CACHE_PREFIX = "api-cache"

_gcs_client = None
_gcs_client_lock = threading.Lock()


def _get_client():
    """Lazy thread-safe singleton GCS client."""
    global _gcs_client
    if _gcs_client is None:
        with _gcs_client_lock:
            if _gcs_client is None:
                from google.cloud import storage
                _gcs_client = storage.Client()
    return _gcs_client
