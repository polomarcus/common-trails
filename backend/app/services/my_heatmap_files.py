"""Per-user private "Ma heatmap" file — one PMTiles + one summary per user.

Design: docs/ma-heatmap-fichier-prive.md. In short:

- ``build_for_user`` streams the user's map activities (same filter as
  ``GET /me/activities``, NO limit), writes the compact display geometry
  (``activities.display_geojson``) to NDJSON, runs tippecanoe → PMTiles, writes
  a geometry-free summary (sidebar list + bbox/center/start), uploads both as
  IMMUTABLE objects ``users/{user_id}/{version}.*`` to a PRIVATE bucket, then
  publishes the version with a compare-and-set on ``my_heatmap_files.built_rev``.
- ``my_heatmap_files.data_rev`` is bumped by DB triggers (migration 0068) on
  every displayed-column change of ``activities`` → the file is ``ready`` only
  while ``built_rev = data_rev``; otherwise the frontend uses /me/activities.
- Rebuilds are scheduled OUT of the request path, debounced per user: a
  SQLAlchemy session hook (ORM writes) + explicit calls (raw-SQL paths) +
  the manifest itself (self-heal) call ``schedule_rebuild``, which enqueues a
  time-window-named Cloud Task (dedup) handled by the worker.

PRIVACY: these traces are NOT endpoint-masked and start at users' homes. The
bucket is private (public-access-prevention enforced, asserted by
scripts/deploy-prod.sh), never one of the public heatmap buckets
(``_resolve_bucket`` refuses), objects are uploaded with no ACL and
``Cache-Control: private, no-store``, and the only read path is the
authenticated same-origin proxy in ``app.api.me_heatmap`` (path derived from
the authenticated user, never from the URL).

Feature flag: ``MY_HEATMAP_FILES_ENABLED`` (default false) and optional
``MY_HEATMAP_FILES_USERS`` allow-list (comma-separated user ids) for a staged
rollout. Flag off → everything here is a no-op and the manifest says
``disabled``.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

USER_PREFIX = "users"
# r{data_rev}-{16 hex}: the random part makes a version URL unguessable (a
# browser-cached /me/heatmap/<version> URL can't be hit by another account).
VERSION_RE = re.compile(r"^r\d{1,18}-[0-9a-f]{16}$")
TILE_LAYER = "activities"
# Columns whose change alters the file — MUST match the trigger list in
# alembic 0068 (pinned by test_my_heatmap_files::test_hook_columns_match_trigger).
# geometry_geojson is checked separately, only when the display cache is NULL
# (the file then derives its geometry from it) — every writer that changes the
# geometry also rewrites display_geojson, and comparing two large TOASTed
# texts on every UPDATE would double the geometry I/O on db-f1-micro.
DISPLAYED_COLUMNS = (
    "user_id",
    "sport",
    "name",
    "display_geojson",
    "distance_m",
    "elevation_gain_m",
    "moving_time",
    "activity_date",
    "created_at",
    "provider",
    "provider_activity_id",
    "total_photo_count",
)


# ── Configuration (read at call time so tests can monkeypatch the env) ──────


def _env_true(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def flag_enabled() -> bool:
    return _env_true("MY_HEATMAP_FILES_ENABLED")


def _allow_list() -> set[str]:
    # ',' or ';' (jobs get ';' — ',' separates gcloud --update-env-vars pairs).
    raw = os.environ.get("MY_HEATMAP_FILES_USERS", "")
    return {u.strip() for u in re.split(r"[,; ]", raw) if u.strip()}


def is_enabled_for(user_id: str | None) -> bool:
    """Flag on, a storage backend configured, and (if an allow-list is set)
    the user is on it."""
    if not user_id or not flag_enabled() or get_store() is None:
        return False
    allow = _allow_list()
    return not allow or user_id in allow


def _debounce_s() -> int:
    return max(5, int(os.environ.get("MY_HEATMAP_DEBOUNCE_S", "60")))


def _zoom_range() -> tuple[int, int]:
    return (
        int(os.environ.get("MY_HEATMAP_MIN_ZOOM", "4")),
        int(os.environ.get("MY_HEATMAP_MAX_ZOOM", "14")),
    )


# ── Storage ──────────────────────────────────────────────────────────────────


class BucketConfigError(RuntimeError):
    """The private bucket env is misconfigured (e.g. points at a PUBLIC bucket)."""


def _resolve_bucket() -> str:
    bucket = os.environ.get("MY_HEATMAP_BUCKET", "").strip()
    if not bucket:
        return ""
    public = {
        os.environ.get("HEATMAP_GCS_BUCKET", "").strip(),
        os.environ.get("HEATMAP_ARTEFACTS_BUCKET", "").strip(),
    } - {""}
    if bucket in public or "heatmap-" in bucket and "my-heatmap" not in bucket:
        # Hard refusal: unmasked personal traces must never land in the
        # bucket served publicly behind tiles.chemins-communs.fr.
        raise BucketConfigError(f"MY_HEATMAP_BUCKET={bucket!r} is a PUBLIC heatmap bucket")
    return bucket


class _LocalStore:
    """Filesystem backend — dev + tests only (``MY_HEATMAP_LOCAL_DIR``)."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("key escapes the store root")
        return p

    def upload(self, key: str, src: str, content_type: str, content_encoding: str | None = None) -> None:
        dst = self._p(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)

    def read(self, key: str, start: int | None = None, end: int | None = None) -> bytes | None:
        p = self._p(key)
        if not p.is_file():
            return None
        with p.open("rb") as f:
            if start is None:
                return f.read()
            f.seek(start)
            return f.read((end - start + 1) if end is not None else -1)

    def delete(self, key: str) -> None:
        self._p(key).unlink(missing_ok=True)

    def list_keys(self, prefix: str) -> list[str]:
        base = self._p(prefix) if prefix else self.root
        if not base.exists():
            return []
        return [str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()]


class _GcsStore:
    def __init__(self, bucket: str) -> None:
        self.bucket_name = bucket
        self._bucket = None

    @property
    def bucket(self):
        if self._bucket is None:
            from google.cloud import storage  # type: ignore[attr-defined]

            self._bucket = storage.Client().bucket(self.bucket_name)
        return self._bucket

    def upload(self, key: str, src: str, content_type: str, content_encoding: str | None = None) -> None:
        blob = self.bucket.blob(key)
        # PRIVATE: never public-cacheable, no ACL argument (uniform bucket-level
        # access + public-access-prevention on the bucket).
        blob.cache_control = "private, no-store"
        if content_encoding:
            blob.content_encoding = content_encoding
        # if_generation_match=0: versions are immutable, never overwrite.
        blob.upload_from_filename(src, content_type=content_type, if_generation_match=0)

    def read(self, key: str, start: int | None = None, end: int | None = None) -> bytes | None:
        from google.api_core.exceptions import NotFound

        try:
            # raw_download: serve the stored (gzip) bytes as-is.
            return self.bucket.blob(key).download_as_bytes(start=start, end=end, raw_download=True)
        except NotFound:
            return None

    def delete(self, key: str) -> None:
        from google.api_core.exceptions import NotFound

        with contextlib.suppress(NotFound):
            self.bucket.blob(key).delete()

    def list_keys(self, prefix: str) -> list[str]:
        return [b.name for b in self.bucket.list_blobs(prefix=prefix)]


def get_store():
    """The configured backend, or ``None`` (feature unusable)."""
    bucket = _resolve_bucket()
    if bucket:
        return _GcsStore(bucket)
    local = os.environ.get("MY_HEATMAP_LOCAL_DIR", "").strip()
    if local:
        return _LocalStore(local)
    return None


def pmtiles_key(user_id: str, version: str) -> str:
    return f"{USER_PREFIX}/{user_id}/{version}.pmtiles"


def summary_key(user_id: str, version: str) -> str:
    return f"{USER_PREFIX}/{user_id}/{version}.summary.json"


# ── State (the DB-side pointer) ──────────────────────────────────────────────


@dataclass
class FileState:
    data_rev: int
    built_rev: int
    version: str | None
    previous_version: str | None
    activity_count: int | None
    pmtiles_bytes: int | None
    built_at: str | None

    @property
    def fresh(self) -> bool:
        return self.version is not None and self.built_rev == self.data_rev


def read_state(db, user_id: str) -> FileState | None:
    row = db.execute(
        sa_text(
            "SELECT data_rev, built_rev, version, previous_version, activity_count, "
            "pmtiles_bytes, built_at FROM my_heatmap_files WHERE user_id = :u"
        ),
        {"u": user_id},
    ).first()
    if row is None:
        return None
    return FileState(
        data_rev=int(row[0]),
        built_rev=int(row[1]),
        version=row[2],
        previous_version=row[3],
        activity_count=row[4],
        pmtiles_bytes=row[5],
        built_at=row[6].isoformat() if row[6] else None,
    )


def manifest(db, user_id: str) -> dict:
    """What the frontend needs to decide file vs JSON fallback."""
    if not is_enabled_for(user_id):
        return {"status": "disabled"}
    state = read_state(db, user_id)
    if state is None or state.version is None:
        schedule_rebuild(user_id, reason="manifest-missing")
        return {"status": "missing"}
    if not state.fresh:
        schedule_rebuild(user_id, reason="manifest-stale")
        return {"status": "stale", "version": state.version}
    v = state.version
    return {
        "status": "ready",
        "version": v,
        "activity_count": state.activity_count or 0,
        "built_at": state.built_at,
        "tiles_url": f"/me/heatmap/{v}.pmtiles" if state.pmtiles_bytes else None,
        "summary_url": f"/me/heatmap/{v}.summary.json",
    }


# ── Build ────────────────────────────────────────────────────────────────────


def _geometry_of(act: dict) -> dict | None:
    """The display geometry — the same one /me/activities serves (cache, or
    computed on the fly for a not-yet-backfilled row)."""
    from app.services.display_geometry import compact_display_geojson

    raw = act.get("display_geojson") or compact_display_geojson(act.get("geometry_geojson"))
    if not raw:
        return None
    try:
        geom = json.loads(raw)
    except ValueError:
        return None
    return geom if isinstance(geom, dict) else None


def _parts(geom: dict) -> list[list]:
    coords = geom.get("coordinates") or []
    if geom.get("type") == "LineString":
        return [coords] if len(coords) >= 2 else []
    if geom.get("type") == "MultiLineString":
        return [p for p in coords if isinstance(p, list) and len(p) >= 2]
    return []


def _summary_entry(act: dict, parts: list[list]) -> dict:
    flat = [c for p in parts for c in p]
    lons = [c[0] for c in flat]
    lats = [c[1] for c in flat]
    mid = flat[len(flat) // 2]
    return {
        "id": act["id"],
        "name": act.get("name") or "",
        "sport": act.get("sport") or "road",
        "distance_m": act.get("distance_m"),
        "elevation_gain_m": act.get("elevation_gain_m"),
        "moving_time": act.get("moving_time"),
        "activity_date": act.get("activity_date"),
        "created_at": act.get("created_at"),
        "undated": bool(act.get("undated", False)),
        "provider": act.get("provider") or "file",
        "provider_activity_id": act.get("provider_activity_id"),
        "total_photo_count": act.get("total_photo_count") or 0,
        "bbox": [min(lons), min(lats), max(lons), max(lats)],
        "center": [mid[0], mid[1]],
        "start": [flat[0][0], flat[0][1]],
    }


def _write_inputs(user_id: str, ndjson_path: str, summary_path: str) -> int:
    """Stream the user's activities → NDJSON features + gzip summary.
    Returns the activity count. O(window) memory for geometries."""
    from app.services.ingest import iter_user_map_activities

    summary: list[dict] = []
    with open(ndjson_path, "w") as out:
        for act in iter_user_map_activities(user_id, limit=None):
            geom = _geometry_of(act)
            parts = _parts(geom) if geom else []
            if not parts:
                continue
            summary.append(_summary_entry(act, parts))
            out.write(
                json.dumps(
                    {
                        "type": "Feature",
                        "geometry": geom,
                        "properties": {"id": act["id"], "sport": act.get("sport") or "road"},
                    },
                    separators=(",", ":"),
                )
            )
            out.write("\n")
    body = json.dumps(
        {"total": len(summary), "activities": summary}, separators=(",", ":")
    ).encode()
    with open(summary_path, "wb") as f:
        f.write(gzip.compress(body, compresslevel=6))
    return len(summary)


def _run_tippecanoe(ndjson_path: str, out_path: str) -> None:
    if not shutil.which("tippecanoe"):
        raise RuntimeError("tippecanoe not installed")
    zmin, zmax = _zoom_range()
    cmd = [
        "tippecanoe",
        "-o", out_path,
        "--force",
        "--quiet",
        "-l", TILE_LAYER,
        "-Z", str(zmin),
        "-z", str(zmax),
        "-P",  # NDJSON → parallel read
        # One rider's traces overlap massively: when a tile exceeds the 500 KB
        # budget, drop the most-overlapped ones rather than fail. Measured
        # (design doc §8): 2,300 rides → nothing dropped from z11; 14,000 rides
        # around 5 homes → dense-core pruning up to z12, z13–z14 complete.
        "--drop-densest-as-needed",
        "--extend-zooms-if-still-dropping",
        "--no-tile-stats",
        ndjson_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"tippecanoe failed: {res.stderr[-500:]}")


def _activate_triggers(db) -> None:
    """Switch the staleness triggers on (alembic 0068 ships them as no-ops so
    the flag-off prod behaves exactly as before). Idempotent; done by the first
    build, i.e. only once the feature is actually used.

    The one-time flip takes ``LOCK TABLE activities IN SHARE MODE`` first: it
    waits for every transaction still writing activities (whose triggers ran
    while disabled) to finish, so no write can commit un-bumped AFTER a file
    snapshot. ``lock_timeout`` (5 s) bounds the wait — writers queue behind
    it; on timeout the build returns ``failed`` and is retried later. Runs in the caller's
    transaction, which must commit before ``data_rev`` is read."""
    if db.execute(sa_text("SELECT enabled FROM my_heatmap_config WHERE id = 1")).scalar():
        return
    db.execute(sa_text("SET LOCAL lock_timeout = '5s'"))
    db.execute(sa_text("LOCK TABLE activities IN SHARE MODE"))
    db.execute(sa_text("UPDATE my_heatmap_config SET enabled = true WHERE id = 1 AND NOT enabled"))


def _ensure_row(db, user_id: str) -> None:
    db.execute(
        sa_text(
            "INSERT INTO my_heatmap_files (user_id) VALUES (:u) ON CONFLICT (user_id) DO NOTHING"
        ),
        {"u": user_id},
    )


def build_for_user(user_id: str, force: bool = False) -> dict:
    """Build + publish the user's file. Idempotent; safe to run concurrently
    (the CAS on ``built_rev`` decides). Never raises for an ordinary failure:
    the error is recorded in ``last_error`` and returned."""
    from app.db.session import SessionLocal

    store = get_store()
    if store is None:
        return {"status": "skipped", "reason": "no-store"}

    db = SessionLocal()
    try:
        # Triggers must be live BEFORE rev0 is read, or a write landing now
        # would not mark this file stale.
        _activate_triggers(db)
        _ensure_row(db, user_id)
        db.commit()
        state = read_state(db, user_id)
    except Exception as exc:  # noqa: BLE001 — e.g. lock_timeout on the one-time activation
        db.rollback()
        log.warning("my-heatmap build could not start user=%s: %s", user_id, exc)
        return {"status": "failed", "error": str(exc)[:300]}
    finally:
        db.close()
    assert state is not None
    if state.fresh and not force:
        return {"status": "fresh", "version": state.version}
    rev0 = state.data_rev
    version = f"r{rev0}-{secrets.token_hex(8)}"

    t0 = time.time()
    tmp = tempfile.mkdtemp(prefix="myheatmap_")
    uploaded: list[str] = []
    try:
        ndjson = os.path.join(tmp, "activities.ndjson")
        summary = os.path.join(tmp, "summary.json.gz")
        pmtiles = os.path.join(tmp, "activities.pmtiles")
        count = _write_inputs(user_id, ndjson, summary)
        pm_bytes = 0
        if count:
            _run_tippecanoe(ndjson, pmtiles)
            pm_bytes = os.path.getsize(pmtiles)
            store.upload(pmtiles_key(user_id, version), pmtiles, "application/vnd.pmtiles")
            uploaded.append(pmtiles_key(user_id, version))
        sum_bytes = os.path.getsize(summary)
        store.upload(summary_key(user_id, version), summary, "application/json", "gzip")
        uploaded.append(summary_key(user_id, version))

        published, previous = _publish_cas(user_id, rev0, version, count, pm_bytes, sum_bytes, force)
        if not published:
            for k in uploaded:
                store.delete(k)
            return {"status": "superseded", "rev": rev0}
        _cleanup_old_versions(store, user_id, keep={version, previous} - {None}, upto_rev=rev0)
        took = round(time.time() - t0, 2)
        log.info(
            "my-heatmap built user=%s version=%s activities=%d pmtiles=%dB summary=%dB in %.1fs",
            user_id, version, count, pm_bytes, sum_bytes, took,
        )
        return {
            "status": "published",
            "version": version,
            "activity_count": count,
            "pmtiles_bytes": pm_bytes,
            "summary_bytes": sum_bytes,
            "seconds": took,
        }
    except Exception as exc:  # noqa: BLE001 — recorded, never crashes the worker
        log.exception("my-heatmap build failed user=%s", user_id)
        for k in uploaded:
            with contextlib.suppress(Exception):
                store.delete(k)
        _record_error(user_id, f"{type(exc).__name__}: {exc}"[:1000])
        return {"status": "failed", "error": str(exc)[:300]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _publish_cas(
    user_id: str, rev0: int, version: str, count: int, pm_bytes: int, sum_bytes: int,
    force: bool = False,
) -> tuple[bool, str | None]:
    """Point the user's row at ``version`` iff no build of a newer snapshot
    published meanwhile. Returns (published, previous_version)."""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        row = db.execute(
            sa_text(
                """
                UPDATE my_heatmap_files
                   SET previous_version = version,
                       version = :v,
                       built_rev = :rev,
                       activity_count = :n,
                       pmtiles_bytes = :pb,
                       summary_bytes = :sb,
                       built_at = now(),
                       last_error = NULL
                 WHERE user_id = :u
                   AND (built_rev < :rev OR (:force AND built_rev = :rev))
                   -- a row deleted + recreated meanwhile (support delete,
                   -- merge) restarts data_rev at 1: never publish a snapshot
                   -- "newer" than the data, it would never be fresh again.
                   AND data_rev >= :rev
             RETURNING previous_version
                """
            ),
            {"v": version, "rev": rev0, "n": count, "pb": pm_bytes, "sb": sum_bytes, "u": user_id,
             "force": force},
        ).first()
        db.commit()
        return (row is not None, row[0] if row is not None else None)
    finally:
        db.close()


def _record_error(user_id: str, msg: str) -> None:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(
            sa_text("UPDATE my_heatmap_files SET last_error = :e WHERE user_id = :u"),
            {"e": msg, "u": user_id},
        )
        db.commit()
    except Exception:  # noqa: BLE001
        log.warning("could not record my-heatmap error for %s", user_id, exc_info=True)
    finally:
        db.close()


def _version_rev(version: str) -> int | None:
    m = re.match(r"^r(\d+)-", version)
    return int(m.group(1)) if m else None


def _cleanup_old_versions(store, user_id: str, keep: set[str], upto_rev: int) -> int:
    """Delete this user's objects of versions not in ``keep`` whose snapshot is
    not newer than ``upto_rev``. A NEWER build running concurrently may have
    uploaded but not yet CAS-published its objects: those are left alone."""
    removed = 0
    for key in store.list_keys(f"{USER_PREFIX}/{user_id}/"):
        version = key.rsplit("/", 1)[-1].split(".", 1)[0]
        rev = _version_rev(version)
        if version in keep or (rev is not None and rev > upto_rev):
            continue
        store.delete(key)
        removed += 1
    return removed


def delete_for_user(user_id: str, db=None) -> int:
    """GDPR / account merge: delete EVERY object of the user + the row.
    Returns the number of objects deleted. Works with the flag off too (a
    file built while it was on must still be erased)."""
    from app.db.session import SessionLocal

    removed = _delete_objects(user_id)
    own = db is None
    s = SessionLocal() if own else db
    try:
        s.execute(sa_text("DELETE FROM my_heatmap_files WHERE user_id = :u"), {"u": user_id})
        if own:
            s.commit()
    finally:
        if own:
            s.close()
    return removed


def sweep_orphans(dry_run: bool = False) -> list[str]:
    """Delete the files of users that no longer exist (missed GDPR step)."""
    from app.db.session import SessionLocal

    store = get_store()
    if store is None:
        return []
    user_ids = sorted({k.split("/")[1] for k in store.list_keys(f"{USER_PREFIX}/") if k.count("/") >= 2})
    if not user_ids:
        return []
    db = SessionLocal()
    try:
        existing = {
            str(r[0])
            for r in db.execute(
                sa_text("SELECT id::text FROM users WHERE id::text = ANY(:ids)"), {"ids": user_ids}
            )
        }
    finally:
        db.close()
    orphans = [u for u in user_ids if u not in existing]
    if not dry_run:
        for u in orphans:
            delete_for_user(u)
    return orphans


# ── Scheduling (debounced per user, never in the request path) ──────────────

# Test seam: when set, schedule_rebuild records instead of enqueuing.
_recorder: list[tuple[str, str]] | None = None
_last_window: dict[str, int] = {}
_timers: dict[str, threading.Timer] = {}
_lock = threading.Lock()


def schedule_rebuild(user_id: str | None, reason: str = "") -> str:
    """Ask for a (debounced) rebuild of ``user_id``'s file. Never raises."""
    try:
        if not is_enabled_for(user_id):
            return "disabled"
        assert user_id is not None
        if _recorder is not None:
            _recorder.append((user_id, reason))
            return "recorded"
        if os.environ.get("TEST_MODE", "false").lower() == "true":
            return "test-mode"
        w = _debounce_s()
        now = time.time()
        window = int(now // w)
        with _lock:
            if _last_window.get(user_id) == window:
                return "deduped"
            _last_window[user_id] = window
        delay = int((window + 1) * w - now) + 5
        from app.services.cloud_tasks import _enqueue_with_const_name

        status = _enqueue_with_const_name(
            queue_env="CLOUD_TASKS_ARTEFACT_QUEUE",
            handler_url_env="INTERNAL_MY_HEATMAP_HANDLER_URL",
            task_name=f"my-heatmap-{user_id}-{window}",
            schedule_seconds=delay,
            payload={"user_id": user_id},
        )
        if status == "inline":
            _schedule_local(user_id, delay)
        log.info("my-heatmap rebuild %s user=%s reason=%s in %ds", status, user_id, reason, delay)
        return status
    except Exception:  # noqa: BLE001 — scheduling is best-effort (manifest self-heals)
        log.warning("my-heatmap schedule failed user=%s", user_id, exc_info=True)
        return "failed"


def _schedule_local(user_id: str, delay: int) -> None:
    """Dev path (no Cloud Tasks): in-process debounced timer."""
    with _lock:
        t = _timers.get(user_id)
        if t is not None and t.is_alive():
            return
        t = threading.Timer(delay, build_for_user, args=(user_id,))
        t.daemon = True
        _timers[user_id] = t
        t.start()


# ── ORM hook: any Activity add/delete/displayed-column change → schedule ─────

_INFO_KEY = "_my_heatmap_dirty_users"


def _changed_users(session: Session) -> Iterable[str]:
    from app.db.models import Activity

    for obj in session.new:
        if isinstance(obj, Activity) and obj.user_id:
            yield obj.user_id
    for obj in session.deleted:
        if isinstance(obj, Activity) and obj.user_id:
            yield obj.user_id
    for obj in session.dirty:
        if not isinstance(obj, Activity):
            continue
        state = sa_inspect(obj)
        cols = DISPLAYED_COLUMNS + (("geometry_geojson",) if obj.display_geojson is None else ())
        for col in cols:
            hist = state.attrs[col].history
            if hist.has_changes():
                if obj.user_id:
                    yield obj.user_id
                if col == "user_id":
                    yield from (u for u in hist.deleted if u)
                break


@event.listens_for(Session, "after_flush")
def _after_flush(session: Session, _ctx) -> None:
    if not flag_enabled():
        return
    try:
        users = set(_changed_users(session))
    except Exception:  # noqa: BLE001 — never break a flush for a cache
        log.debug("my-heatmap after_flush inspection failed", exc_info=True)
        return
    if users:
        session.info.setdefault(_INFO_KEY, set()).update(users)


_DELETE_KEY = "_my_heatmap_deleted_users"


def mark_dirty(session: Session, user_id: str) -> None:
    """Schedule a rebuild of ``user_id``'s file once ``session`` commits — for
    write paths that bypass the ORM unit of work (bulk ``update()``, raw SQL).
    The trigger already marked the file stale; this only makes it prompt."""
    session.info.setdefault(_INFO_KEY, set()).add(user_id)


def mark_deleted(session: Session, user_id: str) -> None:
    """The user is going away (account merge / deletion): drop the pointer row
    in ``session``'s transaction, delete the objects once it commits (nothing
    is lost on rollback). Works with the flag off."""
    session.execute(sa_text("DELETE FROM my_heatmap_files WHERE user_id = :u"), {"u": user_id})
    session.info.setdefault(_DELETE_KEY, set()).add(user_id)


def _delete_objects(user_id: str) -> int:
    store = get_store()
    if store is None:
        return 0
    try:
        keys = store.list_keys(f"{USER_PREFIX}/{user_id}/")
    except Exception as exc:  # bucket never created (flag never turned on)
        if type(exc).__name__ == "NotFound":
            return 0
        raise
    for key in keys:
        store.delete(key)
    return len(keys)


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    deleted = session.info.pop(_DELETE_KEY, None) or set()
    for u in deleted:
        try:
            _delete_objects(u)
        except Exception:  # noqa: BLE001 — sweep_orphans is the backstop
            log.warning("my-heatmap object deletion failed user=%s", u, exc_info=True)
    users = session.info.pop(_INFO_KEY, None) or set()
    for u in users - deleted:
        schedule_rebuild(u, reason="activity-write")


@event.listens_for(Session, "after_soft_rollback")
def _after_rollback(session: Session, previous_transaction) -> None:
    # A SAVEPOINT rolling back (ingest wraps each activity in begin_nested)
    # must not drop what the OUTER transaction already collected. Users seen
    # only inside the rolled-back savepoint stay listed: at worst a no-op
    # rebuild (the build skips fresh files).
    if getattr(previous_transaction, "nested", False):
        return
    session.info.pop(_INFO_KEY, None)
    session.info.pop(_DELETE_KEY, None)
