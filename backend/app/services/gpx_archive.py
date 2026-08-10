"""Archive raw GPX/FIT/ZIP uploads to Cloud Storage for disaster recovery.

Safety net: if the heatmap needs to be rebuilt from scratch, the original
files are available in the uploads bucket (auto-deleted after 2 years by
GCS lifecycle policy).

Path convention:
    gpx-archive/{user_id}/{activity_id}/{original_filename}

In dev/test mode (no UPLOADS_BUCKET set): logs a skip message, never blocks.
"""
import logging
import os
import threading

log = logging.getLogger(__name__)

UPLOADS_BUCKET = os.environ.get("UPLOADS_BUCKET", "")


def archive_gpx(
    user_id: str,
    activity_id: str,
    filename: str,
    content: bytes,
) -> None:
    """Upload raw file bytes to the uploads bucket. Fire-and-forget.

    Failures are logged but never raised — archival must not block imports.
    """
    if not UPLOADS_BUCKET:
        log.debug("GPX archive skipped (UPLOADS_BUCKET not set)")
        return

    path = f"gpx-archive/{user_id}/{activity_id}/{filename}"
    try:
        client = _get_client()
        bucket = client.bucket(UPLOADS_BUCKET)
        blob = bucket.blob(path)
        blob.upload_from_string(content)
        log.info("Archived %s (%d bytes)", path, len(content))
    except Exception:
        log.warning("GPX archive failed for %s (non-critical)", path, exc_info=True)


def archive_bundle(
    user_id: str,
    bundle_hash: str,
    filename: str,
    content: bytes,
) -> None:
    """Upload a bundle archive (ZIP-of-GPX) keyed by content hash.

    Bundles carry many activities, so keying the archive path by a
    single activity_id (as `archive_gpx` does) leaves the other N-1
    activities un-archived. The bundle hash is stable and lets a
    future tool correlate activities ↔ source zip via a per-activity
    `file_hash` column when we add one.

    Path: ``gpx-archive/{user_id}/bundle-{hash}/{filename}``

    Failures are logged but never raised — archival must not block imports.
    """
    if not UPLOADS_BUCKET:
        log.debug("Bundle archive skipped (UPLOADS_BUCKET not set)")
        return

    path = f"gpx-archive/{user_id}/bundle-{bundle_hash[:16]}/{filename}"
    try:
        client = _get_client()
        bucket = client.bucket(UPLOADS_BUCKET)
        blob = bucket.blob(path)
        blob.upload_from_string(content)
        log.info("Archived bundle %s (%d bytes)", path, len(content))
    except Exception:
        log.warning(
            "Bundle archive failed for %s (non-critical)", path, exc_info=True,
        )


# ── Lazy GCS client (shared with cache_writer pattern) ──────────────────────

_gcs_client = None
_gcs_client_lock = threading.Lock()


def _get_client():
    global _gcs_client
    if _gcs_client is None:
        with _gcs_client_lock:
            if _gcs_client is None:
                from google.cloud import storage
                _gcs_client = storage.Client()
    return _gcs_client
