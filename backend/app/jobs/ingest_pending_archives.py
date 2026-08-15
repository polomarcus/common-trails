"""Paced drain of the ``pending_archive_files`` queue.

A user's uploaded archive is stored member-by-member + enqueued by
:mod:`app.services.archive_intake` WITHOUT any coord parsing (see that
module + migration 0059 for the why). This job absorbs that backlog
PROGRESSIVELY — one member at a time, with a small pause between items —
so a multi-thousand-GPX archive is ingested over time instead of in a
single CPU/RAM spike that would OOM a db-f1-micro.

Same pacing philosophy as the Strava resync/backfill jobs. Each member:

    load raw bytes from per-user storage
      → parse (GPX/FIT, gunzip .gz with a streaming budget)
      → ingest_activity(source="manual_upload", contribute_heatmap=…)
      → mark the queue row done / skipped / failed

Idempotent + concurrency-safe: rows are claimed with
``FOR UPDATE SKIP LOCKED`` so two workers never grab the same member, and
a re-run only picks up rows still ``status='pending'``.

Run: ``python -m app.jobs.ingest_pending_archives [--limit N] [--pace SECS]``

FOLLOW-UP (not in this PR): wire a Cloud Scheduler → Cloud Run Job trigger
so this drains automatically in prod. Until then it is invoked manually /
by the caller in TEST_MODE.
"""
from __future__ import annotations

import argparse
import contextlib
import gzip
import io
import logging
import os
import re
import time

from sqlalchemy import text as sa_text

from app.config import VALID_SPORTS
from app.services import archive_intake
from app.services import email as email_service
from app.services import gpx as gpx_service
from app.services import ingest as ingest_service
from app.services.run_jobs import trigger_build_pmtiles_job

log = logging.getLogger(__name__)

# ── DB-gentleness knobs (2026-07-20 incident: ONE 2,813-activity archive
#    pegged db-f1-micro for ~7 h → Cloud Run 429 → site DOWN) ────────────────

# Per-way chunk size for the end-of-archive heat_edges_agg recompute.
HEAT_AGG_WAYS_PER_CHUNK = 500

# Inter-member pacing — the drain's f1-micro breathing room. A fixed sleep was
# always a GUESS at the DB's speed: 1.0 s was tuned for the OSM-matching era
# (30 s–3 min of DB work per activity); post raw-trace pivot a member ingest is a
# light parse + dedup-SELECT + INSERT (measured ~2.5 ms on docker Postgres, more
# on a loaded f1-micro), so any fixed guess is either wasteful (a 16k-member
# Garmin archive spent HOURS asleep) or unsafe on a slow instance.
#
# ADAPTIVE pacing removes the guess: sleep proportional to the PREVIOUS member's
# REAL ingest wall-time (× DRAIN_PACE_RATIO, capped at DRAIN_PACE_MAX_SECONDS), so
# the drain self-throttles to whatever DB it's hitting. ratio 1.0 → sleep ≈ the
# work took → ~50 % DB duty cycle; a fast DB barely pauses, a slow/loaded one (or
# a heavy legacy matched-mode member) automatically gets proportionally MORE room.
# An explicit DRAIN_PACE_SECONDS (or a pace_seconds arg) pins a FIXED sleep
# instead — the ops escape hatch and how tests force pace=0.
_DEFAULT_PACE_SECONDS = 0.25   # legacy fixed fallback (only when adaptive is off)
_DEFAULT_PACE_RATIO = 1.0      # adaptive: sleep ≈ last ingest's wall-time
_DEFAULT_PACE_MAX_SECONDS = 1.0  # cap so one slow/stalled ingest can't sleep forever
_DEFAULT_STATEMENT_TIMEOUT_MS = 120_000

# A pending_archives row is claimed (status→'processing') then terminally
# transitioned (done/failed) by every code path EXCEPT a JOB KILL mid-drain
# (task-timeout / OOM / eviction — Cloud Run even reports the execution as
# "succeeded"). Such a row is stranded in 'processing'; the claim query re-picks
# it while attempts < _MAX_DRAIN_ATTEMPTS, but once it's crashed the job that
# many times it used to sit in 'processing' FOREVER — permanently consuming the
# per-user open-archive slot (MAX_OPEN_ARCHIVES_PER_USER) and never emailing the
# owner. The reaper (_reap_stuck_archives) terminally FAILS those rows.
_MAX_DRAIN_ATTEMPTS = int(os.environ.get("MAX_DRAIN_ATTEMPTS", "5"))
_STUCK_PROCESSING_HOURS = int(os.environ.get("ARCHIVE_STUCK_PROCESSING_HOURS", "3"))

_statement_timeout_registered = False
_statement_timeout_listener = None  # kept for test cleanup (event.remove)


def _drain_pace_seconds() -> float:
    """Legacy FIXED inter-member pacing (``DRAIN_PACE_SECONDS``). Only consulted
    when adaptive pacing is disabled — see ``_resolve_pace_mode``. Kept as the
    fixed fallback + ops escape hatch."""
    try:
        return float(os.environ.get("DRAIN_PACE_SECONDS", _DEFAULT_PACE_SECONDS))
    except ValueError:
        return _DEFAULT_PACE_SECONDS


def _pace_ratio() -> float:
    """Adaptive pace multiplier (``DRAIN_PACE_RATIO``): sleep = last ingest's
    wall-time × ratio. 1.0 → ~50 % DB duty cycle; raise to back off harder on a
    struggling f1-micro, lower to drain faster."""
    try:
        return max(0.0, float(os.environ.get("DRAIN_PACE_RATIO", _DEFAULT_PACE_RATIO)))
    except ValueError:
        return _DEFAULT_PACE_RATIO


def _pace_cap_seconds() -> float:
    """Upper bound on any single adaptive sleep (``DRAIN_PACE_MAX_SECONDS``) so a
    one-off slow/stalled ingest can't translate into a multi-second pause."""
    try:
        return max(0.0, float(os.environ.get("DRAIN_PACE_MAX_SECONDS", _DEFAULT_PACE_MAX_SECONDS)))
    except ValueError:
        return _DEFAULT_PACE_MAX_SECONDS


def _resolve_pace_mode(explicit_pace: float | None) -> tuple[str, float]:
    """Decide FIXED vs ADAPTIVE pacing ONCE per drain run.

    - An explicit ``pace_seconds`` arg, or a set ``DRAIN_PACE_SECONDS`` env, pins
      a FIXED sleep → ``("fixed", seconds)`` (ops escape hatch; tests force 0).
    - Otherwise ADAPTIVE → ``("adaptive", ratio)``: the loop sleeps the previous
      member's real ingest time × ratio (capped), self-tuning to the live DB.
    """
    if explicit_pace is not None:
        return ("fixed", max(0.0, explicit_pace))
    env = os.environ.get("DRAIN_PACE_SECONDS")
    if env is not None and env.strip() != "":
        try:
            return ("fixed", max(0.0, float(env)))
        except ValueError:
            pass
    return ("adaptive", _pace_ratio())


def _next_pace(mode: str, value: float, last_work_s: float | None, cap: float) -> float:
    """The sleep to apply BEFORE the next member. Fixed mode → the pinned value;
    adaptive → ``last_work_s × ratio`` capped (0 for the first member, which has
    no prior measurement)."""
    if mode == "fixed":
        return value
    if last_work_s is None:
        return 0.0
    return min(cap, last_work_s * value)


def _apply_drain_statement_timeout() -> None:
    """Give EVERY connection this drain process opens a Postgres
    ``statement_timeout`` (env ``DRAIN_STATEMENT_TIMEOUT_MS``, default 120 s,
    ``0`` disables) so no single ingest query can camp on the shared DB.

    Registered as an engine ``connect`` listener + ``engine.dispose()`` so
    pre-existing pooled connections are recycled into budgeted ones — this
    covers the sessions ``_update_heat_edges`` opens internally, not just the
    drain's own. A timed-out statement raises inside the member's ingest and
    the existing per-member error isolation records the row ``failed``.
    Idempotent: registers once per process.
    """
    global _statement_timeout_registered, _statement_timeout_listener
    if _statement_timeout_registered:
        return
    try:
        timeout_ms = int(
            os.environ.get("DRAIN_STATEMENT_TIMEOUT_MS", _DEFAULT_STATEMENT_TIMEOUT_MS)
        )
    except ValueError:
        timeout_ms = _DEFAULT_STATEMENT_TIMEOUT_MS
    if timeout_ms <= 0:
        return

    from sqlalchemy import event

    from app.db.session import engine

    @event.listens_for(engine, "connect")
    def _set_statement_timeout(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        try:
            cur.execute(f"SET statement_timeout = {timeout_ms}")
        finally:
            cur.close()

    engine.dispose()
    _statement_timeout_listener = _set_statement_timeout
    _statement_timeout_registered = True
    log.info("drain statement_timeout set to %dms for all DB connections", timeout_ms)


def _recompute_heat_agg_batched(touched_ways: set[int]) -> None:
    """ONE deduplicated ``heat_edges_agg`` recompute for a whole archive/batch.

    The per-activity incremental hook is deferred during a drain (see
    ``ingest_activity(collect_touched_ways=…)``): across an archive the same
    OSM ways are touched dozens of times, and recomputing them per activity
    (5-46 s of SQL each) is what pegged prod. Here the union is recomputed
    once, chunked so a huge archive never issues one giant statement.

    Best-effort like ``ingest._maintain_heat_agg``: the heat_edges rows are
    already committed, so a failure here must NOT fail the drain — the drift
    check / next backfill are the safety net. Commits per chunk.
    """
    if not touched_ways:
        return
    from app.db.session import SessionLocal
    from app.jobs.rebuild_heat_agg import (
        agg_maintenance_enabled,
        recompute_heat_agg_for_ways,
    )

    if not agg_maintenance_enabled():
        return
    ordered = sorted(touched_ways)
    db = SessionLocal()
    try:
        t0 = time.time()
        deleted = upserted = 0
        for i in range(0, len(ordered), HEAT_AGG_WAYS_PER_CHUNK):
            d, u = recompute_heat_agg_for_ways(db, ordered[i:i + HEAT_AGG_WAYS_PER_CHUNK])
            db.commit()
            deleted += d
            upserted += u
        log.info(
            "heat_agg batched recompute: ways=%d upserted=%d deleted=%d took=%.0fms",
            len(ordered), upserted, deleted, (time.time() - t0) * 1000,
        )
    except Exception:
        db.rollback()
        log.warning(
            "batched heat_agg recompute failed for %d ways (heat_edges already "
            "committed; drift check will catch it)", len(ordered), exc_info=True,
        )
        try:
            import sentry_sdk
            sentry_sdk.capture_exception()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


def _ping_pmtiles_rebuild(imported: int, skip_heat_computation: bool) -> None:
    """After a drain that ingested >=1 activity WITH heat computation on, fire a
    single best-effort PMTiles rebuild so the new contribution appears on the
    public heatmap (the small ``/imports/files`` path already does the
    equivalent via ``enqueue_artefact_rebuild``).

    Once per drain batch — NOT per activity — to avoid stampeding the rebuild.
    NEVER raises: the ``heat_edges``/``heat_edges_agg`` writes are already
    committed, so a failed ping must not fail the drain (the row stays ``done``);
    the next drain or an unrelated rebuild catches up. No-ops when heat was
    skipped (nothing new to display) or nothing was imported.
    """
    if skip_heat_computation or imported < 1:
        return
    try:
        fired = trigger_build_pmtiles_job()
        log.info(
            "pmtiles rebuild ping after drain: fired=%s (imported=%d)",
            fired, imported,
        )
    except Exception:
        log.warning("pmtiles rebuild ping failed after drain", exc_info=True)


# ── Spatial-locality ordering (2026-07-20 perf) ──────────────────────────────
# A user's archive lists activities in UPLOAD/date order, so consecutive members
# jump between regions (a 2015 Spain trip, then 2016 Montpellier rides, …). The
# OSM matcher reloads 17k–65k segments PER activity when the tiles aren't warm in
# the segment cache, so region-hopping thrashes the LRU → constant DB reloads
# (the 1–2 min/activity prod symptom). Ordering members by a COARSE spatial key
# (the z14 tile of the first GPS point) groups nearby activities together so the
# now-large 8 Gi-job segment cache stays warm across the run. The key is probed
# cheaply from the GPX head (no full parse); FIT / unparseable members get None
# and cluster at the end (still processed, just not locality-optimized).

# How many decompressed head bytes to scan for the first lat/lon. A GPX header +
# first <trkpt> is well under this; bounding the read keeps the probe O(1)/member
# and caps the .gz decompression.
_SPATIAL_PROBE_HEAD_BYTES = 65_536
_GPX_LAT_RE = re.compile(rb"""lat=["'](-?\d+(?:\.\d+)?)["']""")
_GPX_LON_RE = re.compile(rb"""lon=["'](-?\d+(?:\.\d+)?)["']""")
# Sort key for members whose tile could not be probed — sorts AFTER every known
# tile so the unknowns are handled last, together.
_UNKNOWN_SORT_KEY = (1, 0)


def _member_spatial_key(raw: bytes, filename: str) -> int | None:
    """Coarse z14 tile key from a member's FIRST GPS point — for cache-locality
    ordering ONLY (never for ingest correctness).

    Cheap by design: gunzips a bounded head for ``.gz`` and regex-scans for the
    first ``lat="…"`` / ``lon="…"`` (any attribute order; ``<bounds>`` near the
    track is an acceptable approximation). Returns ``None`` for FIT / non-GPX
    members or any probe failure — callers treat ``None`` as "unknown tile".
    """
    low = filename.lower()
    head = raw
    if low.endswith(".gz"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                head = gz.read(_SPATIAL_PROBE_HEAD_BYTES)
        except Exception:
            return None
        low = low[:-3]
    else:
        head = raw[:_SPATIAL_PROBE_HEAD_BYTES]
    if not low.endswith((".gpx", ".tcx", ".xml")):
        return None  # FIT (binary) and friends: no cheap probe
    m_lat = _GPX_LAT_RE.search(head)
    m_lon = _GPX_LON_RE.search(head)
    if not (m_lat and m_lon):
        return None
    try:
        from app.services.tile_keys import tile_key_from_latlon

        return tile_key_from_latlon(float(m_lat.group(1)), float(m_lon.group(1)))
    except Exception:
        return None


def _spatial_sort_key(tile_key: int | None) -> tuple[int, int]:
    """Total order for :func:`sorted`: known tiles first (ascending by the
    BIGINT key, which is ``x*100000+y`` → a raster scan that keeps a metro's
    tiles adjacent), unknown-tile members last."""
    if tile_key is None:
        return _UNKNOWN_SORT_KEY
    return (0, tile_key)


def _parse_member(raw: bytes, filename: str) -> dict:
    """Parse one stored member into a parsed-activity dict.

    Decompresses ``.gz`` with the same streaming budget the ZIP parser
    uses (a 10 MB gzip of repeated bytes can expand to ~10 GB otherwise).
    """
    low = filename.lower()
    if low.endswith(".gz"):
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
            raw = gz.read(gpx_service.MAX_GPX_SIZE + 1)
        if len(raw) > gpx_service.MAX_GPX_SIZE:
            raise ValueError(f"decompressed size > {gpx_service.MAX_GPX_SIZE} bytes")
        low = low[:-3]
    if low.endswith(".fit"):
        from app.services.fit_parser import parse_fit
        return parse_fit(raw)
    return gpx_service.parse_gpx(raw)


def _effective_sport(parsed: dict, pending_sport: str | None) -> str:
    """Sport cascade for the archive path: FIT device sport → in-GPX
    ``<trk><type>`` → the queue row's pre-resolved sport (which already
    carries the authoritative activities.csv value when the export had one,
    else the form fallback). Mirrors the imports.py cascade ranking.
    """
    fit_sport = parsed.get("sport")
    if fit_sport in VALID_SPORTS:
        return fit_sport
    gpx_sport = parsed.get("sport_from_gpx")
    if gpx_sport in VALID_SPORTS:
        return gpx_sport
    if pending_sport in VALID_SPORTS:
        return pending_sport
    return "road"


def _claim_batch(db, limit: int) -> list[dict]:
    """Claim up to ``limit`` pending rows (oldest first), locking them so a
    concurrent worker skips them. Marks them ``processing`` inside the txn.
    """
    rows = db.execute(sa_text("""
        SELECT id, user_id, storage_backend, storage_key, original_filename,
               sport, contribute_heatmap, source
        FROM pending_archive_files
        WHERE status = 'pending'
        ORDER BY created_at
        LIMIT :lim
        FOR UPDATE SKIP LOCKED
    """), {"lim": limit}).mappings().all()
    if rows:
        db.execute(sa_text("""
            UPDATE pending_archive_files
            SET status = 'processing', attempts = attempts + 1, updated_at = now()
            WHERE id = ANY(:ids)
        """), {"ids": [r["id"] for r in rows]})
    db.commit()
    return [dict(r) for r in rows]


def _order_pending_batch(batch: list[dict]) -> None:
    """Sort a claimed per-member batch in place by first-point spatial locality
    (see the ``_member_spatial_key`` block). Best-effort: a member whose bytes
    can't be loaded/probed sorts last, unordered. Never raises — ordering is a
    perf optimization, never a correctness gate."""
    if len(batch) < 2:
        return
    for row in batch:
        key: int | None = None
        try:
            raw = archive_intake.load_member(row["storage_backend"], row["storage_key"])
            key = _member_spatial_key(raw, row["original_filename"] or "")
        except Exception:
            log.debug("spatial probe failed for pending row %s", row.get("id"), exc_info=True)
        row["_spatial_key"] = key
    batch.sort(key=lambda r: _spatial_sort_key(r.get("_spatial_key")))


def _finish(db, row_id: str, status: str, *, activity_id: str | None = None, error: str | None = None) -> None:
    db.execute(sa_text("""
        UPDATE pending_archive_files
        SET status = :st, activity_id = :aid, last_error = :err, updated_at = now()
        WHERE id = :id
    """), {"st": status, "aid": activity_id, "err": (error or None), "id": row_id})
    db.commit()


def _ingest_member_bytes(
    db,
    *,
    user_id: str,
    filename: str,
    raw: bytes,
    resolved_sport: str | None,
    contribute_heatmap: bool,
    source: str | None,
    skip_heat_computation: bool,
    collect_touched_ways: set[int] | None = None,
) -> tuple[str, str | None, str | None]:
    """Parse + ingest ONE archive member. Shared by both drains (per-member
    queue AND the whole-archive streaming drain) so provenance stamping +
    the sport cascade + #453 promotion can't drift between the two paths.

    ``collect_touched_ways``: when set, the per-activity ``heat_edges_agg``
    recompute is deferred and the touched osm_way_ids accumulate there — the
    drain batches ONE recompute per archive (see ``_recompute_heat_agg_batched``).

    Returns ``(outcome, activity_id, error)`` where ``outcome`` is
    ``"imported"`` (``created``/``promoted``) or ``"skipped"``. Raises on a
    hard parse/ingest error (the caller records it as ``failed``).
    """
    parsed = _parse_member(raw, filename or "member.gpx")
    if parsed.get("skip_reason"):
        return "skipped", None, parsed["skip_reason"]

    activity_data = {
        "provider": "file",
        "provider_activity_id": None,
        "source": source or archive_intake.ARCHIVE_PROVENANCE,
        "sport": _effective_sport(parsed, resolved_sport),
        "name": parsed.get("name"),
        "geometry_geojson": parsed.get("geometry_geojson"),
        "distance_m": parsed.get("distance_m"),
        "elevation_gain_m": parsed.get("elevation_gain_m"),
        "file_hash": parsed.get("file_hash"),
        "activity_date": parsed.get("activity_date"),
    }
    result = ingest_service.ingest_activity(
        user_id=user_id,
        activity_data=activity_data,
        contribute_heatmap=contribute_heatmap,
        skip_heat_computation=skip_heat_computation,
        collect_touched_ways=collect_touched_ways,
    )
    # "promoted" (③): a manual-upload member that upgraded a pre-existing
    # Strava-API twin to the community layer — counts as a successful import.
    if result["status"] in ("created", "promoted"):
        return "imported", result["activity_id"], None
    return "skipped", result.get("activity_id"), None


def drain_pending(
    *,
    limit: int = 50,
    pace_seconds: float | None = None,
    skip_heat_computation: bool = False,
) -> dict:
    """Drain up to ``limit`` pending members, one at a time with ADAPTIVE pacing
    between them (sleep ≈ the previous member's real ingest time ×
    ``DRAIN_PACE_RATIO``, capped; a fixed ``pace_seconds`` / ``DRAIN_PACE_SECONDS``
    overrides). Returns a summary dict. Never raises on a single bad member.
    """
    import sentry_sdk

    from app.db.session import SessionLocal

    pace_mode, pace_value = _resolve_pace_mode(pace_seconds)
    pace_cap = _pace_cap_seconds()
    _apply_drain_statement_timeout()

    summary = {"processed": 0, "imported": 0, "skipped": 0, "failed": 0}
    touched_ways: set[int] = set()
    db = SessionLocal()
    try:
        batch = _claim_batch(db, limit)
    finally:
        db.close()

    # Spatial-locality ordering: probe each claimed member's first-point tile
    # (loading its bytes once) and sort so consecutive ingests reuse warm OSM
    # tiles instead of thrashing the segment cache. The ingest loop below
    # re-loads the bytes — a probe-then-reload keeps peak RAM bounded to one
    # member (the per-member queue is the legacy small-zip path; batches are
    # tiny, default 50). A probe failure just leaves the member unordered.
    _order_pending_batch(batch)

    last_work_s: float | None = None
    for i, row in enumerate(batch):
        # Pace BEFORE this ingest, from the previous member's real cost.
        if i > 0:
            pace = _next_pace(pace_mode, pace_value, last_work_s, pace_cap)
            if pace > 0:
                time.sleep(pace)
        summary["processed"] += 1
        db = SessionLocal()
        _t0 = time.monotonic()
        try:
            raw = archive_intake.load_member(row["storage_backend"], row["storage_key"])
            outcome, activity_id, error = _ingest_member_bytes(
                db,
                user_id=row["user_id"],
                filename=row["original_filename"] or "member.gpx",
                raw=raw,
                resolved_sport=row["sport"],
                contribute_heatmap=bool(row["contribute_heatmap"]),
                source=row["source"],
                skip_heat_computation=skip_heat_computation,
                collect_touched_ways=touched_ways,
            )
            if outcome == "imported":
                summary["imported"] += 1
                _finish(db, row["id"], "done", activity_id=activity_id)
            else:
                summary["skipped"] += 1
                _finish(db, row["id"], "skipped", activity_id=activity_id, error=error)
        except Exception as exc:
            summary["failed"] += 1
            sentry_sdk.capture_exception(exc)
            log.error("pending archive member %s failed", row.get("id"), exc_info=True)
            try:
                _finish(db, row["id"], "failed", error=str(exc)[:1000])
            except Exception:
                log.warning("could not mark pending row failed", exc_info=True)
        finally:
            db.close()
            last_work_s = time.monotonic() - _t0

    # ONE deduplicated agg recompute for the whole batch (deferred per-member).
    _recompute_heat_agg_batched(touched_ways)
    _ping_pmtiles_rebuild(summary["imported"], skip_heat_computation)
    log.info("drain_pending summary: %s", summary)
    return summary


# ── Whole-archive drain (signed-URL upload, migration 0060) ──────────────────


def _reap_stuck_archives(db) -> list[str]:
    """Terminally FAIL pending_archives stranded in 'processing' by a killed
    drain (job timeout/OOM/eviction — no except block ran to transition them)
    once they've exhausted retries. Without this they sit in 'processing'
    forever, permanently consuming the owner's open-archive slot and never
    notifying them. Returns the reaped archive ids (caller emails them).
    Best-effort — never raises (a reap hiccup must not block the drain)."""
    try:
        rows = db.execute(sa_text(f"""
            UPDATE pending_archives
            SET status = 'failed',
                last_error = 'drain exceeded {_MAX_DRAIN_ATTEMPTS} attempts '
                             '(job repeatedly killed/timed out mid-drain — the '
                             'archive is likely too large; split it into smaller .zip)',
                updated_at = now()
            WHERE status = 'processing'
              AND updated_at < now() - interval '{_STUCK_PROCESSING_HOURS} hours'
              AND attempts >= :maxa
            RETURNING id
        """), {"maxa": _MAX_DRAIN_ATTEMPTS}).mappings().all()
        db.commit()
        reaped = [str(r["id"]) for r in rows]
        if reaped:
            log.warning("reaped %d archive(s) stuck in 'processing' → failed: %s",
                        len(reaped), reaped)
        return reaped
    except Exception:  # noqa: BLE001 — reaping must never break the drain
        with contextlib.suppress(Exception):
            db.rollback()
        log.warning("reap of stuck archives failed", exc_info=True)
        return []


def _claim_archives(db, limit: int) -> list[dict]:
    """Claim up to ``limit`` drainable whole-archive rows (oldest first),
    locking them so a concurrent worker skips them. Marks them ``processing``.

    Drainable = ``uploaded`` OR a STALE ``processing`` row (no update for
    3 h): a job killed mid-drain (task-timeout / OOM / eviction) leaves its
    claimed rows stranded in ``processing`` forever — Cloud Run even reports
    the execution as successful on a timeout kill. Re-processing a partially
    drained archive is safe: per-member ``file_hash`` dedup + the #453
    cross-source promotion in ``ingest_activity`` make the re-run idempotent
    (already-ingested members dedup, the rest get their first pass). The
    ``attempts < 5`` guard stops a poison archive (crashes the job every
    time) from looping forever — it stays visibly stuck in ``processing``
    for the admin requeue path instead.
    """
    rows = db.execute(sa_text(f"""
        SELECT id, user_id, storage_backend, bucket_key, fallback_sport,
               contribute_heatmap, source
        FROM pending_archives
        WHERE status = 'uploaded'
           OR (status = 'processing'
               AND updated_at < now() - interval '{_STUCK_PROCESSING_HOURS} hours'
               AND attempts < :maxa)
        ORDER BY created_at
        LIMIT :lim
        FOR UPDATE SKIP LOCKED
    """), {"lim": limit, "maxa": _MAX_DRAIN_ATTEMPTS}).mappings().all()
    if rows:
        db.execute(sa_text("""
            UPDATE pending_archives
            SET status = 'processing', attempts = attempts + 1, updated_at = now()
            WHERE id = ANY(:ids)
        """), {"ids": [r["id"] for r in rows]})
    db.commit()
    return [dict(r) for r in rows]


def _finish_archive(db, archive_id: str, status: str, counts: dict, error: str | None = None) -> None:
    db.execute(sa_text("""
        UPDATE pending_archives
        SET status = :st, members_total = :total, imported = :imp,
            skipped = :skp, failed = :fail, last_error = :err, updated_at = now()
        WHERE id = :id
    """), {
        "st": status,
        "total": counts.get("members_total", 0),
        "imp": counts.get("imported", 0),
        "skp": counts.get("skipped", 0),
        "fail": counts.get("failed", 0),
        "err": (error or None),
        "id": archive_id,
    })
    db.commit()


def _notify_archive_terminal(
    archive_id: str, status: str, counts: dict, error: str | None = None
) -> None:
    """Email the archive's owner when the whole archive reaches a TERMINAL
    state (``done`` / ``failed``) — the only user feedback after the UI's
    "vos traces seront ajoutées progressivement" promise.

    Best-effort by construction: the row transition is already committed when
    this runs, and NOTHING here may undo it — every failure is swallowed into
    a log line. Skips silently for synthetic ``@strava.local`` addresses
    (Strava-OAuth accounts without a real email) and missing users. Locale
    comes from the consent row recorded at ``/init`` (FR default — the site is
    FR-first; ``pending_archives`` itself has no locale column, on purpose).

    One email per terminal TRANSITION: a requeued archive that finishes again
    sends again — acceptable, each drain completion is fresh news to the user.
    """
    from app.db.session import SessionLocal

    try:
        db = SessionLocal()
        try:
            row = db.execute(sa_text("""
                SELECT u.email AS email, cc.locale AS locale
                FROM pending_archives pa
                JOIN users u ON u.id::text = pa.user_id
                LEFT JOIN contribution_consents cc ON cc.id = pa.consent_id
                WHERE pa.id = :id
            """), {"id": archive_id}).mappings().first()
        finally:
            db.close()
        if row is None or not row["email"] or row["email"].endswith("@strava.local"):
            return
        locale = "en" if (row["locale"] or "fr").lower().startswith("en") else "fr"
        if status == "done":
            subject, html = email_service.render_archive_done_email(
                imported=counts.get("imported", 0),
                skipped=counts.get("skipped", 0),
                failed=counts.get("failed", 0),
                locale=locale,
            )
        else:
            subject, html = email_service.render_archive_failed_email(error, locale=locale)
        email_service.send_email(row["email"], subject, html)
    except Exception:
        log.warning("terminal email for archive %s failed", archive_id, exc_info=True)


def drain_pending_archives(
    *,
    limit: int = 5,
    pace_seconds: float | None = None,
    skip_heat_computation: bool = False,
) -> dict:
    """Drain up to ``limit`` ``uploaded`` whole archives, STREAMING each .zip
    from its bucket and ingesting every ingestible member (GPX / FIT / .gz).

    This is where the unzip + per-member parse happens — in the scale-to-zero
    8 Gi Cloud Run job, NEVER on the 512 Mi web instance. The archive is
    streamed to a temp file (``open_archive_zip``); ``ZipFile`` then reads one
    member into memory at a time, so the ~400 MB decompressed payload is never
    fully materialised.

    Members are ingested in ONE streaming pass, in iteration order (the OSM-era
    spatial-locality pre-sort was dropped with the raw-trace pivot — there is no
    segment cache to keep warm). Inter-member pacing is ADAPTIVE by default
    (``_resolve_pace_mode`` / ``_next_pace``): each sleep is the previous member's
    real ingest wall-time × ``DRAIN_PACE_RATIO`` (capped), so the drain self-tunes
    to the live DB — trivial in raw mode (~ms/member), backing off automatically
    on a slow/loaded instance. A fixed ``DRAIN_PACE_SECONDS`` overrides it.

    Idempotent + concurrency-safe: rows are claimed ``FOR UPDATE SKIP LOCKED``,
    and per-member ``file_hash`` dedup (+ the #453 cross-source promotion) in
    ``ingest_activity`` means a re-run of a partially-processed archive
    re-ingests members without creating duplicates.
    """
    import sentry_sdk

    from app.db.session import SessionLocal

    pace_mode, pace_value = _resolve_pace_mode(pace_seconds)
    pace_cap = _pace_cap_seconds()
    _apply_drain_statement_timeout()

    summary = {"archives": 0, "imported": 0, "skipped": 0, "failed": 0, "archives_failed": 0}
    # Self-heal rows a killed prior drain stranded in 'processing' (frees the
    # owner's open-archive slot + emails them) BEFORE claiming this batch.
    reap_db = SessionLocal()
    try:
        for reaped_id in _reap_stuck_archives(reap_db):
            _notify_archive_terminal(
                reaped_id, "failed", {},
                error="archive could not be processed after repeated attempts",
            )
    finally:
        reap_db.close()
    db = SessionLocal()
    try:
        batch = _claim_archives(db, limit)
    finally:
        db.close()

    for arch in batch:
        summary["archives"] += 1
        counts = {"members_total": 0, "imported": 0, "skipped": 0, "failed": 0}
        # Touched osm_way_ids for THIS archive — the per-activity agg
        # recompute is deferred; ONE deduplicated batch runs at end-of-archive
        # (including on failure mid-way: the heat_edges rows for the members
        # already ingested are committed, so the agg must not drift).
        touched_ways: set[int] = set()
        db = SessionLocal()
        try:
            # Defense-in-depth size guard (SSOT ``MAX_ARCHIVE_BYTES``). ``/complete``
            # already re-checks size, but a race or a direct-bucket PUT that bypasses
            # ``/complete`` could still leave an oversize object marked ``uploaded``.
            # Check storage METADATA (no download) BEFORE ``open_archive_zip`` streams
            # it into the 2 Gi job's tmpfs — otherwise a multi-GB object OOMs the batch.
            size = archive_intake.archive_size(arch["storage_backend"], arch["bucket_key"])
            if size is not None and size > archive_intake.MAX_ARCHIVE_BYTES:
                raise ValueError(archive_intake.too_large_message(size))
            with archive_intake.open_archive_zip(
                arch["storage_backend"], arch["bucket_key"]
            ) as zf:
                # ONE pass — ingest each member straight from the bytes
                # iter_zip_members already yields (holds only ONE member at a
                # time; ``ZipFile.read`` is lazy). The pre-pivot code did TWO
                # passes: scan → sort by spatial tile-key → re-read each member,
                # to keep the OSM segment cache warm. That cache died with the
                # raw-trace pivot (no matcher), so the sort was vestigial and the
                # re-read only DOUBLED the zip I/O — worst for big nested Garmin
                # archives, where PASS 2 re-opened every inner UploadedFiles_*.zip.
                # One pass reads each member exactly once. Pace is ADAPTIVE —
                # each sleep is the PREVIOUS member's real ingest time × ratio
                # (capped), so the drain self-tunes to the DB; iter_zip_members
                # enforces the zip-bomb caps.
                first = True
                ingested = 0
                last_work_s: float | None = None
                for name, raw, csv_sport in archive_intake.iter_zip_members(zf):
                    counts["members_total"] += 1
                    if csv_sport is archive_intake._OVERSIZE:
                        counts["skipped"] += 1
                        continue
                    if csv_sport is None:
                        # activities.csv marked this out-of-scope (yoga/virtual/…).
                        counts["skipped"] += 1
                        continue
                    resolved_sport = (
                        csv_sport if isinstance(csv_sport, str) else arch["fallback_sport"]
                    )
                    if not first:
                        pace = _next_pace(pace_mode, pace_value, last_work_s, pace_cap)
                        if pace > 0:
                            time.sleep(pace)
                    first = False
                    ingested += 1
                    # Heartbeat: a real archive is 1000s of members — without
                    # this a long drain is a silent black box and a timeout kill
                    # is indistinguishable from a hang.
                    if ingested % 100 == 0:
                        log.info(
                            "archive %s progress: %d ingested "
                            "(imported=%d skipped=%d failed=%d)",
                            arch["id"], ingested,
                            counts["imported"], counts["skipped"], counts["failed"],
                        )
                    _t0 = time.monotonic()
                    try:
                        outcome, _aid, _err = _ingest_member_bytes(
                            db,
                            user_id=arch["user_id"],
                            filename=name,
                            raw=raw,
                            resolved_sport=resolved_sport,
                            contribute_heatmap=bool(arch["contribute_heatmap"]),
                            source=arch["source"],
                            skip_heat_computation=skip_heat_computation,
                            collect_touched_ways=touched_ways,
                        )
                        counts[outcome] += 1
                    except Exception as exc:  # noqa: BLE001 — one bad member ≠ fail the archive
                        counts["failed"] += 1
                        sentry_sdk.capture_exception(exc)
                        log.error("archive %s member %s failed", arch["id"], name, exc_info=True)
                    finally:
                        last_work_s = time.monotonic() - _t0
            # End-of-archive: ONE deduplicated agg recompute, BEFORE the
            # terminal transition so status='done' implies a fresh aggregate.
            _recompute_heat_agg_batched(touched_ways)
            _finish_archive(db, arch["id"], "done", counts)
            _notify_archive_terminal(arch["id"], "done", counts)
        except archive_intake.StorageConfigError as exc:
            # OPS misconfiguration (e.g. the job env lost UPLOADS_BUCKET),
            # NOT a bad user archive. Do NOT burn the row as ``failed`` —
            # put it back to ``uploaded`` so it drains once ops fix the env
            # (2026-07 prod incident: a user archive was marked failed for
            # an env mistake). Sentry still fires so ops SEE the problem.
            summary["archives_failed"] += 1
            sentry_sdk.capture_exception(exc)
            log.error(
                "pending archive %s hit a storage CONFIG error — leaving the "
                "row 'uploaded' for retry after the ops fix: %s",
                arch.get("id"), exc, exc_info=True,
            )
            try:
                db.execute(sa_text("""
                    UPDATE pending_archives
                    SET status = 'uploaded', updated_at = now()
                    WHERE id = :id
                """), {"id": arch["id"]})
                db.commit()
            except Exception:
                log.warning("could not reset archive row to uploaded", exc_info=True)
        except gpx_service.ZipBombError as exc:
            # The archive tripped a hardened cap (uncompressed size / member
            # count / unsafe path). Mark it ``failed`` with a HUMAN-READABLE
            # reason — never leak the opaque exception class name — so it never
            # re-drains and the UI can tell the user to split their export.
            summary["archives_failed"] += 1
            reason = f"archive rejected: {exc}"
            log.warning("pending archive %s rejected: %s", arch.get("id"), exc)
            # Members ingested before the cap tripped already wrote heat_edges —
            # recompute their ways so the aggregate doesn't drift.
            _recompute_heat_agg_batched(touched_ways)
            try:
                _finish_archive(db, arch["id"], "failed", counts, error=reason[:1000])
            except Exception:
                log.warning("could not mark archive failed", exc_info=True)
            else:
                _notify_archive_terminal(arch["id"], "failed", counts, error=reason)
        except Exception as exc:
            summary["archives_failed"] += 1
            sentry_sdk.capture_exception(exc)
            log.error("pending archive %s failed", arch.get("id"), exc_info=True)
            # Partial-drain flush: heat_edges rows for the already-ingested
            # members are committed — the agg must not drift (golden asserts
            # agg freshness for every touched way).
            _recompute_heat_agg_batched(touched_ways)
            try:
                _finish_archive(db, arch["id"], "failed", counts, error=str(exc)[:1000])
            except Exception:
                log.warning("could not mark archive failed", exc_info=True)
            else:
                _notify_archive_terminal(arch["id"], "failed", counts, error=str(exc))
        finally:
            db.close()
        summary["imported"] += counts["imported"]
        summary["skipped"] += counts["skipped"]
        summary["failed"] += counts["failed"]

    _ping_pmtiles_rebuild(summary["imported"], skip_heat_computation)
    log.info("drain_pending_archives summary: %s", summary)
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Drain pending archive uploads (paced)")
    parser.add_argument("--limit", type=int, default=50, help="Max members per run (per-member queue).")
    parser.add_argument(
        "--pace", type=float, default=None,
        help="Fixed seconds between members. Omit for ADAPTIVE pacing "
             "(sleep ≈ last ingest's time × DRAIN_PACE_RATIO); DRAIN_PACE_SECONDS "
             "pins a fixed sleep instead.",
    )
    parser.add_argument("--archive-limit", type=int, default=5, help="Max whole archives per run.")
    args = parser.parse_args()
    # Drain BOTH queues: the whole-archive queue (signed-URL flow, migration
    # 0060) AND the legacy per-member queue (small-zip direct endpoint, 0059).
    drain_pending_archives(limit=args.archive_limit, pace_seconds=args.pace)
    drain_pending(limit=args.limit, pace_seconds=args.pace)


if __name__ == "__main__":
    main()
