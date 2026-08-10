"""GPX backup service — store raw GPX files to cloud storage for disaster recovery.

Stores files keyed by SHA256 hash so duplicate uploads are idempotent (same content
= same key = overwrite is a no-op). Files are organized by user_id for easy browsing.

Storage layout:
    {bucket}/{user_id}/{file_hash}.gpx

Bucket resolution (in order):
    1. GPX_BACKUP_BUCKET — explicit override (gs://name or /local/path)
    2. UPLOADS_BUCKET — prod default (shared with gpx_archive.py, same bucket
       as Terraform-provisioned `${project_id}-common-trails-uploads-${env}`)
    3. (none) → backup disabled, no error

Usage:
    from app.services.gpx_backup import backup_gpx, gpx_exists

    # After successful parse + ingest:
    await backup_gpx(user_id="abc", file_hash="sha256...", content=raw_bytes)

    # Check if already backed up (skip re-upload):
    exists = await gpx_exists(user_id="abc", file_hash="sha256...")
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Bucket resolution priority:
#   1. GPX_BACKUP_BUCKET (explicit override, used in dev/local)
#   2. UPLOADS_BUCKET (prod default — same bucket as gpx_archive.py)
# Either may be a `gs://bucket-name` URI (GCS) or a local path (filesystem).
def _resolve_bucket() -> str:
    explicit = os.environ.get("GPX_BACKUP_BUCKET", "")
    if explicit:
        return explicit
    uploads = os.environ.get("UPLOADS_BUCKET", "")
    if uploads:
        # UPLOADS_BUCKET in prod Terraform contains just the name (no gs:// prefix)
        return uploads if uploads.startswith("gs://") or uploads.startswith("/") else f"gs://{uploads}"
    return ""


def _bucket() -> str:
    return _resolve_bucket()


def _is_gcs() -> bool:
    return _bucket().startswith("gs://")


def _gcs_bucket_name() -> str:
    return _bucket().removeprefix("gs://").rstrip("/")


def _local_dir() -> Path | None:
    b = _bucket()
    if not b or b.startswith("gs://"):
        return None
    return Path(b)


def _key(user_id: str, file_hash: str) -> str:
    return f"{user_id}/{file_hash}.gpx"


async def backup_gpx(user_id: str, file_hash: str, content: bytes) -> bool:
    """Store raw GPX bytes to backup storage. Returns True if stored, False if skipped/failed."""
    if not _bucket():
        return False

    key = _key(user_id, file_hash)

    if _is_gcs():
        return await _gcs_upload(key, content)
    else:
        return _local_save(key, content)


async def gpx_exists(user_id: str, file_hash: str) -> bool:
    """Check if a GPX file already exists in backup storage."""
    if not _bucket():
        return False

    key = _key(user_id, file_hash)

    if _is_gcs():
        return await _gcs_exists(key)
    else:
        return _local_exists(key)


async def list_user_backups(user_id: str) -> list[str]:
    """List all backed-up file hashes for a user."""
    if not _bucket():
        return []

    prefix = f"{user_id}/"

    if _is_gcs():
        return await _gcs_list(prefix)
    else:
        return _local_list(prefix)


# ── Local filesystem backend ─────────────────────────────────────────────────

def _local_save(key: str, content: bytes) -> bool:
    local = _local_dir()
    if not local:
        return False
    path = local / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    logger.debug("GPX backup (local): %s (%d bytes)", key, len(content))
    return True


def _local_exists(key: str) -> bool:
    local = _local_dir()
    if not local:
        return False
    return (local / key).exists()


def _local_list(prefix: str) -> list[str]:
    local = _local_dir()
    if not local:
        return []
    user_dir = local / prefix.rstrip("/")
    if not user_dir.is_dir():
        return []
    return [f.stem for f in user_dir.glob("*.gpx")]


# ── GCS backend ──────────────────────────────────────────────────────────────

async def _gcs_upload(key: str, content: bytes) -> bool:
    try:
        from google.cloud import storage  # type: ignore[import-untyped]

        client = storage.Client()
        bucket = client.bucket(_gcs_bucket_name())
        blob = bucket.blob(key)
        blob.upload_from_string(content, content_type="application/gpx+xml")
        logger.info("GPX backup (GCS): %s (%d bytes)", key, len(content))
        return True
    except ImportError:
        logger.warning("GPX backup: google-cloud-storage not installed, skipping GCS upload")
        return False
    except Exception:
        logger.warning("GPX backup (GCS) failed: %s", key, exc_info=True)
        return False


async def _gcs_exists(key: str) -> bool:
    try:
        from google.cloud import storage  # type: ignore[import-untyped]

        client = storage.Client()
        bucket = client.bucket(_gcs_bucket_name())
        return bucket.blob(key).exists()
    except ImportError:
        return False
    except Exception:
        logger.warning("GPX backup (GCS) exists check failed: %s", key, exc_info=True)
        return False


async def _gcs_list(prefix: str) -> list[str]:
    try:
        from google.cloud import storage  # type: ignore[import-untyped]

        client = storage.Client()
        bucket = client.bucket(_gcs_bucket_name())
        blobs = bucket.list_blobs(prefix=prefix)
        return [b.name.rsplit("/", 1)[-1].removesuffix(".gpx") for b in blobs]
    except ImportError:
        return []
    except Exception:
        logger.warning("GPX backup (GCS) list failed: %s", prefix, exc_info=True)
        return []
