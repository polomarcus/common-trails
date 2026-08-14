"""Progressive intake of a user's OWN uploaded activity archive.

Flow (the "consented community-contribution" path):

    upload (ZIP / GPX / FIT)
      → record ContributionConsent (audit trail)
      → store each raw member to a PER-USER bucket prefix  (`<user_id>/…`)
      → enqueue one PendingArchiveFile row per member
      → return 202  (NOTHING coord-parsed / heat-computed on the request thread)

    …later, out of band…
      app.jobs.ingest_pending_archives  drains the queue in PACED batches,
      ingesting each member with provenance ``source="manual_upload"``.

WHY the queue + pacing: a big Strava archive is thousands of GPX. Parsing
+ heat-computing them all inline would spike CPU/RAM and OOM a
db-f1-micro. We only do bounded IO here (unzip member bytes → store);
the expensive work is deferred to a rate-limited worker, exactly like the
Strava resync/backfill pacing.

Storage backends:
  * ``gcs``   — when ``UPLOADS_BUCKET`` is set: ``<user_id>/<id>__<name>``
  * ``local`` — dev/test fallback under ``ARCHIVE_INTAKE_DIR``.

This module extracts members WITHOUT parsing coordinates, reusing the
hardened caps + CSV sport map from :mod:`app.services.gpx` so the two
paths can't drift on the zip-bomb / path-traversal guards.
"""
from __future__ import annotations

import contextlib
import io
import logging
import os
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import timedelta

import sentry_sdk
from sqlalchemy.orm import Session

from app.db.models import ContributionConsent, PendingArchiveFile
from app.services import gpx as gpx_service
from app.services.gpx_archive import UPLOADS_BUCKET, _get_client
from app.services.provenance import COMMUNITY_SOURCE

log = logging.getLogger(__name__)

# Provenance stamped on every activity ingested through this path. The
# canonical value lives in :mod:`app.services.provenance` (the community
# eligibility SSOT); re-exported here for the existing call sites.
ARCHIVE_PROVENANCE = COMMUNITY_SOURCE

# Local fallback root when no GCS bucket is configured (dev / tests).
ARCHIVE_INTAKE_DIR = os.environ.get(
    "ARCHIVE_INTAKE_DIR",
    os.path.join(tempfile.gettempdir(), "cc-archive-intake"),
)


@dataclass
class IntakeResult:
    consent_id: str
    enqueued: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


# ── Consent audit trail ─────────────────────────────────────────────────────

def record_consent(
    db: Session,
    *,
    user_id: str,
    consent_version: str,
    consent_text: str,
    locale: str | None,
    source: str = ARCHIVE_PROVENANCE,
) -> str:
    """Append a ContributionConsent row and return its id (committed)."""
    consent = ContributionConsent(
        user_id=user_id,
        source=source,
        consent_version=consent_version,
        consent_text=consent_text,
        locale=locale,
    )
    db.add(consent)
    db.commit()
    return consent.id


class ConsentError(ValueError):
    """A consent payload was malformed (unknown id, or partial version/text).
    Callers map this to HTTP 422."""


def resolve_or_record_consent(
    db: Session,
    *,
    user_id: str,
    consent_id: str | None = None,
    consent_version: str | None = None,
    consent_text: str | None = None,
    locale: str | None = None,
) -> str | None:
    """SSOT for the community-upload consent round-trip, shared by ALL
    community-write upload endpoints (``/imports/files`` + ``/gpx/upload``).

    * ``consent_id`` given → re-check ownership, return it (reuse the batch row);
    * else ``consent_version``/``consent_text`` given → record ONE row, return id;
    * else → ``None`` (no consent recorded → the caller must NOT publish to the
      community layer).

    Raises :class:`ConsentError` (→ 422) on an unknown id or partial fields.
    """
    if consent_id:
        row = db.get(ContributionConsent, consent_id)
        if row is None or row.user_id != user_id:
            raise ConsentError("Unknown consent_id.")
        return consent_id
    if consent_version is not None or consent_text is not None:
        if not (consent_version or "").strip() or not (consent_text or "").strip():
            raise ConsentError("Missing consent version/text.")
        return record_consent(
            db,
            user_id=user_id,
            consent_version=consent_version.strip()[:100],
            consent_text=consent_text.strip(),
            locale=(locale or None),
            source=COMMUNITY_SOURCE,
        )
    return None


# ── Storage backend (per-user bucket prefix) ────────────────────────────────

def _safe_basename(name: str) -> str:
    base = (name or "").replace("\\", "/").rstrip("/").split("/")[-1]
    base = base.strip() or "upload.gpx"
    # Belt-and-braces: strip any residual traversal tokens.
    return base.replace("..", "_")[:200]


def store_member(user_id: str, member_id: str, filename: str, content: bytes) -> tuple[str, str]:
    """Persist raw member bytes under the per-user prefix.

    Returns ``(backend, storage_key)``. ``storage_key`` is relative to the
    backend root so the worker can reload it regardless of backend.
    """
    key = f"{user_id}/{member_id}__{_safe_basename(filename)}"
    if UPLOADS_BUCKET:
        client = _get_client()
        bucket = client.bucket(UPLOADS_BUCKET)
        bucket.blob(f"archive-intake/{key}").upload_from_string(content)
        return "gcs", key
    # Local fallback.
    path = os.path.join(ARCHIVE_INTAKE_DIR, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    return "local", key


def load_member(backend: str, storage_key: str) -> bytes:
    """Reload raw member bytes stored by :func:`store_member`."""
    if backend == "gcs":
        client = _get_client()
        bucket = client.bucket(UPLOADS_BUCKET)
        return bucket.blob(f"archive-intake/{storage_key}").download_as_bytes()
    path = os.path.join(ARCHIVE_INTAKE_DIR, storage_key)
    with open(path, "rb") as fh:
        return fh.read()


# ── Member extraction (no coord parsing) ────────────────────────────────────

def _member_iter(content: bytes, filename: str):
    """Yield ``(member_name, raw_bytes, csv_sport)`` for each ingestible
    member of the upload, WITHOUT parsing coordinates.

    ``csv_sport`` is:
      * a sport string  — the Strava activities.csv mapped this file
      * ``None`` + skip  — the CSV marked it out-of-scope (yielded as skip)
      * ``KeyError`` sentinel handled by caller — no CSV hint at all

    Reuses the hardened caps + CSV parser from :mod:`app.services.gpx` so
    the zip-bomb / path-traversal guards stay in one place.
    """
    lower_name = (filename or "").lower()
    if lower_name.endswith(".gpx") or lower_name.endswith(".fit"):
        # Single-file archive: one member, no CSV.
        yield _safe_basename(filename), content, _NO_CSV
        return
    if not lower_name.endswith(".zip"):
        raise ValueError("Supported formats: .gpx, .fit, .zip")

    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        yield from iter_zip_members(zf)


# Junk detection (macOS __MACOSX/ mirror + AppleDouble ._*) lives with the
# other zip-bomb guards in gpx_service so the two zip paths can't drift.
_is_junk_member = gpx_service.is_junk_zip_member

# Nested-zip recursion bounds (Garmin "Export All" nests .fit in inner zips).
# Depth is capped to 1 (see iter_zip_members _depth). These bound the fan-out:
_MAX_NESTED_ZIPS = 5000                       # inner zips opened per archive
_MAX_NESTED_ZIP_BYTES = 300 * 1024 * 1024     # skip an inner zip bigger than this (RAM)


def iter_zip_members(zf: zipfile.ZipFile, *, _depth: int = 0):
    """Yield ``(member_name, raw_bytes, csv_sport)`` for each ingestible
    member of an ALREADY-OPEN :class:`zipfile.ZipFile`, WITHOUT parsing
    coordinates.

    Recurses ONE level into nested ``.zip`` members (``_depth`` guards against
    deeper nesting = zip-bomb depth): a Garmin "Export All Data" archive nests
    the activity ``.fit``/``.gpx`` files inside inner zips, so a flat top-level
    walk would find 0. Each inner zip is bounded by this function's OWN caps
    (applied recursively), plus a per-inner-zip compressed-size cap and a global
    running member/byte guard below.

    Split out from :func:`_member_iter` so the cold-start importer can walk
    a .zip STREAMED from the bucket (opened from a temp file on disk) instead
    of a fully-buffered ``bytes`` object — a real Strava export decompresses
    to ~400 MB and must not be held in RAM on the web. ``ZipFile`` reads the
    central directory then seeks per member, so opening from a file path
    reads one member into memory at a time.

    Handles the "re-zipped folder" trap: a macOS right-click → Compress on
    the unzipped export folder nests everything under a root prefix
    (``strava-export/activities.csv`` + ``strava-export/activities/1.gpx``).
    The activities.csv is looked up at ANY depth (shallowest wins) and its
    directory becomes the archive root, stripped from member paths before
    the CSV lookup — so the authoritative sport map still applies (sport
    ambiguous → skip, never pollute). Root-level archives behave
    identically (prefix = empty).

    Junk members (``__MACOSX/`` mirror, AppleDouble ``._*``) are dropped
    BEFORE any cap or guard: they can DOUBLE the entry count of a re-zipped
    export, so counting them against MAX_ZIP_MEMBERS rejected legitimate
    archives (Paul's real 2813-activity export = 6194 raw entries). They are
    never read, so excluding them from the caps is not a zip-bomb weakening.

    Same reasoning one step further: the member + total-uncompressed caps
    count INGESTIBLE members only (``gpx_service.is_ingestible_zip_member``)
    — a real Strava export ships thousands of ``media/*.jpg`` that are never
    ``zf.read()`` either, and they pushed legitimate archives over both
    caps. Every actual read stays bounded by MAX_ZIP_MEMBER_UNCOMPRESSED.
    """
    infos = [i for i in zf.infolist() if not _is_junk_member(i.filename)]
    ingestible = [i for i in infos if gpx_service.is_ingestible_zip_member(i.filename)]
    # Nested .zip members (Garmin "Export All" nests .fit inside inner zips) —
    # recursed ONE level below (depth-1 only). NOT counted as ingestible here.
    nested_zips = [
        i for i in infos
        if i.filename.lower().endswith(".zip")
        and not gpx_service.is_ingestible_zip_member(i.filename)
    ]
    if len(ingestible) > gpx_service.MAX_ZIP_MEMBERS:
        raise gpx_service.ZipBombError(
            f"ZIP has too many members ({len(ingestible)} > "
            f"{gpx_service.MAX_ZIP_MEMBERS}); split before uploading."
        )
    total_uncompressed = sum(i.file_size for i in ingestible)
    if total_uncompressed > gpx_service.MAX_ZIP_TOTAL_UNCOMPRESSED:
        raise gpx_service.ZipBombError(
            f"ZIP decompresses to {total_uncompressed:,} bytes "
            f"(> {gpx_service.MAX_ZIP_TOTAL_UNCOMPRESSED:,}); possible zip-bomb."
        )

    # Reject unsafe paths up front (attacker signal → whole-archive reject).
    for info in infos:
        if info.filename.startswith("/") or ".." in info.filename.split("/"):
            raise gpx_service.ZipBombError(f"ZIP member has unsafe path: {info.filename!r}")

    # activities.csv → sport-by-filename map (authoritative sport). Found at
    # ANY depth: a re-zipped export folder nests it under a root prefix. The
    # shallowest match wins and its directory becomes the archive root.
    sport_by_filename: dict[str, str | None] = {}
    root_prefix = ""
    csv_candidates = [
        info for info in infos
        if info.filename.replace("\\", "/").rsplit("/", 1)[-1].lower() == "activities.csv"
        and info.file_size <= gpx_service.MAX_ZIP_MEMBER_UNCOMPRESSED
    ]
    if csv_candidates:
        csv_info = min(csv_candidates, key=lambda i: i.filename.count("/"))
        if "/" in csv_info.filename:
            root_prefix = csv_info.filename.rsplit("/", 1)[0] + "/"
        try:
            raw_map = gpx_service.parse_strava_activities_csv(zf.read(csv_info.filename))
            sport_by_filename = {
                gpx_service._normalize_archive_path(k): v for k, v in raw_map.items()
            }
        except Exception:
            sport_by_filename = {}

    root_prefix_low = root_prefix.lower()
    for info in infos:
        name = info.filename
        low = name.lower()
        if not gpx_service.is_ingestible_zip_member(name):
            continue
        if info.file_size > gpx_service.MAX_ZIP_MEMBER_UNCOMPRESSED:
            # One oversize member: skip it, keep the rest (same policy as
            # the parser). Surfaced as a skip, not a whole-archive reject.
            yield name, None, _OVERSIZE
            continue
        # Read the member bytes AS-IS (still gzipped if .gz). The worker
        # decompresses with the streaming budget at parse time, so no
        # gzip-bomb is materialised here.
        # Strip the archive root (the activities.csv directory) so the
        # member path matches the CSV's relative `Filename` column.
        lookup_name = name
        if root_prefix_low and low.startswith(root_prefix_low):
            lookup_name = name[len(root_prefix_low):]
        lookup = gpx_service._normalize_archive_path(lookup_name)
        csv_sport = sport_by_filename.get(lookup, _NO_CSV)
        yield name, zf.read(name), csv_sport

    # ── Recurse ONE level into nested .zip members (Garmin "Export All") ──
    # Depth-1 only (_depth guard): a nested zip's OWN nested zips are ignored,
    # so a maliciously deep zip-bomb can't fan out. Each inner zip is bounded by
    # its own recursive caps; the running aggregate guard below bounds the sum;
    # oversize inner zips are skipped (RAM). Garmin has no activities.csv → the
    # .fit/.gpx sport is inferred at parse time (_NO_CSV).
    if _depth == 0 and nested_zips:
        emitted = len(ingestible)
        emitted_bytes = total_uncompressed
        opened = 0
        for zinfo in nested_zips:
            if opened >= _MAX_NESTED_ZIPS:
                log.warning("archive: >%d nested zips — ignoring the rest", _MAX_NESTED_ZIPS)
                break
            if zinfo.file_size > _MAX_NESTED_ZIP_BYTES:
                log.warning("archive: nested zip %r too large (%d B) — skipped",
                            zinfo.filename, zinfo.file_size)
                continue
            opened += 1
            try:
                with zipfile.ZipFile(io.BytesIO(zf.read(zinfo.filename))) as zin:
                    for nm, raw, _cs in iter_zip_members(zin, _depth=1):
                        emitted += 1
                        emitted_bytes += len(raw) if isinstance(raw, (bytes, bytearray)) else 0
                        if (emitted > gpx_service.MAX_ZIP_MEMBERS
                                or emitted_bytes > gpx_service.MAX_ZIP_TOTAL_UNCOMPRESSED):
                            raise gpx_service.ZipBombError(
                                "nested-zip contents exceed the archive caps; possible zip-bomb."
                            )
                        # Prefix with the inner-zip name → unique + traceable.
                        yield f"{zinfo.filename}!{nm}", raw, _cs if raw is None else _NO_CSV
            except gpx_service.ZipBombError:
                raise
            except Exception as exc:  # noqa: BLE001 — a bad inner zip skips, never aborts
                log.warning("archive: nested zip %r unreadable — skipped: %s", zinfo.filename, exc)
                continue


# Sentinels distinguishing "no CSV hint" from a real ``None`` (out-of-scope).
_NO_CSV = object()
_OVERSIZE = object()


# ── PASS-2 member reader — resolves nested-zip composite names ────────────────
# iter_zip_members emits Garmin "Export All" members as ``<inner.zip>!<member>``
# (activities live INSIDE inner ``UploadedFiles_*.zip`` zips, not flat in the
# outer archive). The drain's PASS 2 re-reads each planned member by name to hold
# only one member's bytes in RAM at a time; a plain ``zf.read("<inner>!<member>")``
# KeyErrors because that composite name is not an outer-zip entry — this was the
# root cause of a Garmin archive draining with ~100% member failures. This reader
# descends into the inner zip instead, memoising a few opened inner zips so a
# multi-MB inner zip is not re-read once per member.
_INNER_ZIP_CACHE_MAX = int(os.environ.get("ARCHIVE_INNER_ZIP_CACHE_MAX", "6"))


def read_planned_member(
    zf: zipfile.ZipFile, name: str, inner_cache: dict[str, zipfile.ZipFile]
) -> bytes:
    """Read a member's bytes, resolving one level of nested-zip composite names
    (``<inner.zip>!<member>``, as :func:`iter_zip_members` emits for Garmin).

    Flat members read straight from ``zf``. ``inner_cache`` is owned by the
    caller for the archive's lifetime and bounded to ``_INNER_ZIP_CACHE_MAX``
    opened inner zips (FIFO eviction) so we don't re-read a multi-MB inner zip
    once per member.
    """
    inner_name, sep, member = name.partition("!")
    # Only a genuine '<inner.zip>!<member>' composite (what iter_zip_members
    # emits for Garmin nested zips) is treated as nested — a FLAT member whose
    # own filename happens to contain '!' still reads straight from the outer zip.
    if not sep or not inner_name.lower().endswith(".zip"):
        return zf.read(name)
    zin = inner_cache.get(inner_name)
    if zin is None:
        if len(inner_cache) >= _INNER_ZIP_CACHE_MAX:
            oldest = next(iter(inner_cache))
            with contextlib.suppress(Exception):
                inner_cache.pop(oldest).close()
        zin = zipfile.ZipFile(io.BytesIO(zf.read(inner_name)))
        inner_cache[inner_name] = zin
    return zin.read(member)


def close_inner_cache(inner_cache: dict[str, zipfile.ZipFile]) -> None:
    """Close every inner zip memoised by :func:`read_planned_member`."""
    for zin in inner_cache.values():
        with contextlib.suppress(Exception):
            zin.close()
    inner_cache.clear()


def enqueue_archive(
    db: Session,
    *,
    user_id: str,
    content: bytes,
    filename: str,
    fallback_sport: str,
    contribute_heatmap: bool,
    consent_id: str,
    source: str = ARCHIVE_PROVENANCE,
) -> IntakeResult:
    """Extract members, store each raw to the per-user prefix, and enqueue a
    PendingArchiveFile per member. Never parses coordinates (no ingest spike).
    """
    result = IntakeResult(consent_id=consent_id)
    for name, raw, csv_sport in _member_iter(content, filename):
        if csv_sport is _OVERSIZE:
            result.skipped += 1
            result.errors.append(f"{name}: oversize member skipped")
            continue
        if csv_sport is None:
            # activities.csv marked this out-of-scope (yoga/virtual/etc.).
            # Skip beats pollute — never store or ingest it.
            result.skipped += 1
            continue
        # csv_sport is either a sport string or the _NO_CSV sentinel.
        resolved_sport = csv_sport if isinstance(csv_sport, str) else fallback_sport
        member_id = str(uuid.uuid4())
        try:
            backend, storage_key = store_member(user_id, member_id, name, raw)
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            log.error("archive intake store failed for %s", name, exc_info=True)
            result.errors.append(f"{name}: storage failed")
            continue
        db.add(PendingArchiveFile(
            id=member_id,
            user_id=user_id,
            consent_id=consent_id,
            source=source,
            storage_backend=backend,
            storage_key=storage_key,
            original_filename=name[:512],
            sport=resolved_sport,
            contribute_heatmap=contribute_heatmap,
            status="pending",
        ))
        result.enqueued += 1
    db.commit()
    return result


# ── Whole-archive intake (signed-URL direct-to-GCS upload, migration 0060) ───
#
# The #451 flow above buffers the whole .zip on the web request thread. A REAL
# Strava export (~70 MB zipped, ~400 MB unzipped) can't route through Cloud Run
# (~32 MB request cap) and must never be held in the 512 Mi web instance. So the
# browser PUTs the .zip STRAIGHT to a per-user bucket key via a V4 signed URL,
# and the scale-to-zero 2 Gi importer streams it back later. These helpers back
# the ``/imports/strava-archive/init`` + ``/complete`` endpoints and the job.

# Object-path prefix (shared with the per-member store above).
ARCHIVE_INTAKE_PREFIX = "archive-intake"

# Signed PUT URL time-to-live. Short: the browser starts the PUT immediately.
SIGNED_URL_TTL = timedelta(minutes=int(os.environ.get("ARCHIVE_SIGNED_URL_TTL_MIN", "30")))

# Sanity ceiling for a single uploaded archive. The signed PUT URL binds the
# content-TYPE but NOT the size, so an authenticated user could PUT a multi-GB
# object; the importer's ``download_to_filename`` lands it in the 2 Gi job's
# tmpfs BEFORE any zip-content guard runs → OOM/DoS of the whole drain batch.
# This cap is the SSOT re-checked at ``/complete`` (reject 413 + mark the row
# ``failed`` + delete the object) AND in the job (skip an oversize object
# WITHOUT downloading it, from storage metadata only). Generous — Paul's real
# export is ~70 MB — so real athletes fit; overridable via env for a genuinely
# huge export. Preferred env ``MAX_ARCHIVE_UPLOAD_BYTES``; the legacy
# ``MAX_ARCHIVE_BYTES`` still works. NB: keep this <= the importer job's tmpfs.
MAX_ARCHIVE_BYTES = int(
    os.environ.get(
        "MAX_ARCHIVE_UPLOAD_BYTES",
        os.environ.get("MAX_ARCHIVE_BYTES", str(512 * 1024 * 1024)),
    )
)  # 512 MB

# Content-type the browser must PUT with (bound into the V4 signature).
ARCHIVE_CONTENT_TYPE = "application/zip"


class StorageConfigError(RuntimeError):
    """The row says ``storage_backend='gcs'`` but ``UPLOADS_BUCKET`` is not
    configured — an OPS mistake, not a bad user archive. The drain must NOT
    mark the user's row ``failed`` on this (2026-07 prod incident: a user
    archive was burned as ``failed`` because the job env lost the bucket
    var); it leaves the row ``uploaded`` so it drains once ops fix the env.
    """


def _require_bucket() -> str:
    """Return UPLOADS_BUCKET or raise a CLEAR config error (pre-guard: the
    google client otherwise crashes deep inside bucket-name parsing)."""
    if not UPLOADS_BUCKET:
        raise StorageConfigError(
            "UPLOADS_BUCKET not configured — cannot access GCS-backed archive objects"
        )
    return UPLOADS_BUCKET

# One-shot flag so the signBlob fallback logs a single clear line per process.
_signblob_fallback_logged = False


def new_archive_key(user_id: str) -> tuple[str, str]:
    """Allocate a fresh per-user object path for a whole-archive upload.

    Returns ``(backend, bucket_key)``. ``bucket_key`` is the FULL object path
    including the ``archive-intake/`` prefix so it is self-describing (the
    ownership check keys off ``archive-intake/<user_id>/``).
    """
    backend = "gcs" if UPLOADS_BUCKET else "local"
    bucket_key = f"{ARCHIVE_INTAKE_PREFIX}/{user_id}/{uuid.uuid4()}.zip"
    return backend, bucket_key


def owns_key(user_id: str, bucket_key: str) -> bool:
    """True iff ``bucket_key`` sits under this user's archive-intake prefix."""
    return bucket_key.startswith(f"{ARCHIVE_INTAKE_PREFIX}/{user_id}/")


def _generate_signed_url(blob, **kwargs) -> str:
    """Sign ``blob`` for a V4 signed URL, with the Cloud Run signBlob fallback.

    Locally (key-file credentials) the client signs with its private key. On
    Cloud Run the runtime SA credentials carry only a TOKEN — no private key —
    and, contrary to what the google-auth docs suggest, ``generate_signed_url``
    does NOT automatically route to the IAM ``signBlob`` API: it raises
    ``AttributeError: you need a private key to sign credentials``. In that case
    we retry with ``service_account_email`` + ``access_token`` from the refreshed
    ambient credentials so signing goes through IAM ``signBlob`` (requires
    ``roles/iam.serviceAccountTokenCreator`` granted to the runtime SA on
    itself). Fixed for the archive PUT path in #491 — reused here so every
    signed URL in the app shares one signing SSOT.
    """
    try:
        return blob.generate_signed_url(**kwargs)
    except AttributeError as exc:
        if "private key" not in str(exc):
            raise
        global _signblob_fallback_logged
        if not _signblob_fallback_logged:
            log.warning(
                "Signing credentials have no private key; "
                "falling back to IAM signBlob for signed URLs (%s)", exc,
            )
            _signblob_fallback_logged = True
        import google.auth
        from google.auth.transport import requests as ga_requests

        credentials, _ = google.auth.default()
        credentials.refresh(ga_requests.Request())
        return blob.generate_signed_url(
            **kwargs,
            service_account_email=credentials.service_account_email,
            access_token=credentials.token,
        )


def generate_signed_put_url(bucket_key: str) -> str:
    """Return a V4 signed PUT URL for ``bucket_key`` (GCS backend only).

    The signature binds the HTTP method (PUT) and Content-Type so the browser
    can only upload a zip to exactly this key.
    """
    client = _get_client()
    blob = client.bucket(UPLOADS_BUCKET).blob(bucket_key)
    return _generate_signed_url(
        blob,
        version="v4",
        expiration=SIGNED_URL_TTL,
        method="PUT",
        content_type=ARCHIVE_CONTENT_TYPE,
    )


def generate_signed_get_url(
    bucket_name: str, bucket_key: str, ttl: timedelta
) -> str:
    """Return a V4 signed GET URL for ``bucket_key`` in ``bucket_name``.

    Used by the members-only heatmap asset gate: the community PMTiles binary
    lives in a (soon-to-be) PRIVATE GCS bucket, and logged-in users fetch a
    short-lived signed URL to load it. The signature binds only the GET method
    + resource — NOT a byte range — so the PMTiles client's HTTP Range requests
    (header, directory, tile fetches) all resolve against the same signed URL
    for the URL's lifetime. Shares the signBlob fallback with the archive PUT
    path via ``_generate_signed_url``.
    """
    client = _get_client()
    blob = client.bucket(bucket_name).blob(bucket_key)
    return _generate_signed_url(
        blob,
        version="v4",
        expiration=ttl,
        method="GET",
    )


def local_archive_path(bucket_key: str) -> str:
    """Filesystem path for a whole archive under the local fallback root."""
    return os.path.join(ARCHIVE_INTAKE_DIR, bucket_key)


def store_archive_local(bucket_key: str, content: bytes) -> None:
    """Write a whole archive to the local fallback root (dev / TEST_MODE).

    Backs the local stand-in for the signed-URL PUT: when no GCS bucket is
    configured, the browser PUTs to a backend endpoint that lands here, and
    the importer reads it from the same path.
    """
    path = local_archive_path(bucket_key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)


def archive_exists(backend: str, bucket_key: str) -> bool:
    """True iff the uploaded archive object is present in its backend."""
    if backend == "gcs":
        client = _get_client()
        return client.bucket(UPLOADS_BUCKET).blob(bucket_key).exists()
    return os.path.exists(local_archive_path(bucket_key))


def archive_size(backend: str, bucket_key: str) -> int | None:
    """Size in bytes of the uploaded archive, from storage METADATA only.

    Never downloads the object — GCS ``blob.reload()`` is a metadata (HEAD-like)
    fetch, local uses ``os.path.getsize``. This is the guard the size cap keys
    off BEFORE ``open_archive_zip`` would stream a multi-GB payload into the
    2 Gi job's tmpfs. Returns ``None`` when the object is absent or the size is
    unknown (caller treats ``None`` as "can't determine" → does not reject on
    size, having already verified existence).
    """
    if backend == "gcs":
        bucket_name = _require_bucket()
        client = _get_client()
        blob = client.bucket(bucket_name).blob(bucket_key)
        try:
            blob.reload()  # metadata only — populates blob.size
        except Exception:  # noqa: BLE001 — absent / transient → unknown size
            return None
        return blob.size
    path = local_archive_path(bucket_key)
    return os.path.getsize(path) if os.path.exists(path) else None


def delete_archive(backend: str, bucket_key: str) -> None:
    """Best-effort delete of a stored archive object (oversize reject / cleanup).

    Never raises — freeing the bucket is a courtesy; a residual object is swept
    later. Failing here must not turn a clean 413 into a 500.
    """
    try:
        if backend == "gcs":
            client = _get_client()
            client.bucket(UPLOADS_BUCKET).blob(bucket_key).delete()
        else:
            path = local_archive_path(bucket_key)
            if os.path.exists(path):
                os.remove(path)
    except Exception:  # noqa: BLE001
        log.warning("could not delete archive object %s", bucket_key, exc_info=True)


def too_large_message(size_bytes: int) -> str:
    """Human-readable 413 reason shared by ``/complete`` and the job (SSOT)."""
    mb = size_bytes / (1024 * 1024)
    limit_mb = MAX_ARCHIVE_BYTES / (1024 * 1024)
    return (
        f"archive too large ({mb:.0f} MB > {limit_mb:.0f} MB limit); "
        "split your export into smaller archives or contact us."
    )


@contextlib.contextmanager
def open_archive_zip(backend: str, bucket_key: str):
    """Yield an open :class:`zipfile.ZipFile` for a stored whole archive,
    STREAMING it from the backend to a temp file first (never buffering the
    ~400 MB decompressed payload in RAM).

    GCS: ``download_to_filename`` to a temp file, open, delete on exit.
    Local: open the file in place.
    """
    if backend == "gcs":
        bucket_name = _require_bucket()
        client = _get_client()
        blob = client.bucket(bucket_name).blob(bucket_key)
        fd, tmp = tempfile.mkstemp(suffix=".zip", prefix="archive-intake-")
        os.close(fd)
        try:
            blob.download_to_filename(tmp)
            with zipfile.ZipFile(tmp) as zf:
                yield zf
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)
    else:
        with zipfile.ZipFile(local_archive_path(bucket_key)) as zf:
            yield zf
