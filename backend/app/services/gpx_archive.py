"""Archive raw GPX/FIT/ZIP uploads to Cloud Storage for disaster recovery.

Safety net: if the heatmap needs to be rebuilt from scratch, the original
files are available in the uploads bucket (auto-deleted after 2 years by
GCS lifecycle policy).

Path convention:
    gpx-archive/{user_id}/{activity_id}/{safe_filename(original_filename)}

In dev/test mode (no UPLOADS_BUCKET set): logs a skip message, never blocks.
"""
import logging
import os
import re
import threading

log = logging.getLogger(__name__)

UPLOADS_BUCKET = os.environ.get("UPLOADS_BUCKET", "")

# Client-supplied file / member names reach log lines and object keys only
# through safe_filename(): safe characters, bounded length.
SAFE_NAME_MAX_LEN = 100
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9._/!-]+")


def safe_filename(
    name: str | None,
    *,
    default: str = "upload.bin",
    max_len: int = SAFE_NAME_MAX_LEN,
    allow_path: bool = False,
) -> str:
    """Return ``name`` reduced to ``[A-Za-z0-9._-]`` (plus ``/`` and ``!`` when
    ``allow_path``, for archive member paths in logs), every other run of
    characters replaced by ``_``, truncated to ``max_len`` (the extension is
    kept when it is short). Empty / dot-only names fall back to ``default``.
    """
    pattern = _UNSAFE_PATH_CHARS if allow_path else _UNSAFE_NAME_CHARS
    clean = pattern.sub("_", name or "").strip("._")
    if not allow_path:
        clean = clean.replace("..", "_")
    if not clean:
        return default
    if len(clean) <= max_len:
        return clean
    base, dot, ext = clean.rpartition(".")
    keep_ext = dot and base and 0 < len(ext) <= 10
    return base[: max_len - len(ext) - 1] + "." + ext if keep_ext else clean[:max_len]


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

    path = f"gpx-archive/{user_id}/{activity_id}/{safe_filename(filename)}"
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

    path = f"gpx-archive/{user_id}/bundle-{bundle_hash[:16]}/{safe_filename(filename, default='upload.zip')}"
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
