"""One-shot ops CLI: ingest a user's OWN Strava-export ``.zip`` (already in
the uploads bucket) as their consented ODbL community contribution — WITHOUT
the browser signed-URL flow.

WHY THIS EXISTS
    The account owner has explicitly authorized ingesting his OWN downloaded
    Strava export as his consented community (ODbL) contribution, but the
    browser upload UX (signed-URL init → PUT → complete → paced job) is
    friction-y for a single, known, operator-driven import. This CLI is the
    reliable ops path: point it at an object an operator has already placed in
    ``UPLOADS_BUCKET`` (e.g. ``founder-import/<user_id>.zip``) and it records
    consent + streams + ingests every member — reusing the SAME internals as
    the #451/#455 archive importer so provenance, idempotence and the #453
    cross-source promotion behave identically.

WHAT IT REUSES (no logic duplicated — everything via import)
    * ``archive_intake.record_consent``      — the ContributionConsent audit row.
    * ``archive_intake.archive_size`` / ``too_large_message`` / ``MAX_ARCHIVE_BYTES``
                                             — the same size guard the job honours.
    * ``archive_intake.open_archive_zip``    — STREAMS the .zip from the bucket
      + ``archive_intake.iter_zip_members``    to a temp file, one member at a
                                               time (never the whole payload in
                                               RAM), with the hardened zip-bomb /
                                               path-traversal caps + activities.csv
                                               sport map.
    * ``ingest_pending_archives._ingest_member_bytes`` — the EXACT per-member
      parse→ingest path the paced worker uses, stamping ``source="manual_upload"``,
      the file_hash idempotence, and the #453 promotion. Reusing this call means
      the ops path can never drift from the browser path.

IDEMPOTENT
    Re-running skips already-ingested members (file_hash dedup inside
    ``ingest_activity``). A partially-processed import re-runs cleanly.

Run (locally / as a Cloud Run job execution)::

    python -m app.cli.founder_import \\
        --bucket-key founder-import/<user_id>.zip \\
        --user-id <user_id> \\
        --consent-text "Je consens à contribuer mes traces (ODbL)." \\
        --consent-version founder-2026-07-13

Exit codes: 0 when nothing failed, 1 when one or more members failed to ingest
(or a fatal error — missing object, oversize archive, unreadable zip).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter

import sentry_sdk

from app.jobs.ingest_pending_archives import (
    _ingest_member_bytes,
    _recompute_heat_agg_batched,
)
from app.services import archive_intake

log = logging.getLogger("founder_import")

# Sport used when a member has neither a FIT device sport, an in-GPX
# ``<trk><type>``, nor an activities.csv hint. Mirrors the archive worker's
# ``fallback_sport`` default; ``_effective_sport`` inside ``_ingest_member_bytes``
# applies the same final "road" floor, so this is belt-and-braces.
_FALLBACK_SPORT = "road"


def _backend() -> str:
    """The storage backend the bucket-key lives in (matches archive_intake)."""
    return "gcs" if archive_intake.UPLOADS_BUCKET else "local"


def import_founder_archive(
    *,
    bucket_key: str,
    user_id: str,
    consent_text: str,
    consent_version: str,
    locale: str = "fr",
    pace_seconds: float = 0.1,
    dry_run: bool = False,
    contribute_heatmap: bool = True,
    skip_heat_computation: bool = False,
) -> dict:
    """Ingest every ingestible member of ``bucket_key`` for ``user_id`` as a
    consented ``manual_upload`` community contribution.

    Records ONE ContributionConsent row (unless ``dry_run``), then STREAMS the
    zip from the bucket and ingests each member via the shared worker helper.
    Never raises on a single bad member — it is counted as ``failed`` and the
    caller returns a non-zero exit.

    Returns a summary dict::

        {"processed", "imported", "skipped", "failed", "would_import",
         "skipped_reasons": {reason: count}, "consent_id", "dry_run"}
    """
    from app.db.session import SessionLocal

    backend = _backend()
    summary: dict = {
        "processed": 0,
        "imported": 0,
        "skipped": 0,
        "failed": 0,
        "would_import": 0,
        "skipped_reasons": Counter(),
        "consent_id": None,
        "dry_run": dry_run,
    }

    # Existence + size guard BEFORE streaming anything — the same defense the
    # job applies (metadata-only; never downloads an oversize object).
    if not archive_intake.archive_exists(backend, bucket_key):
        raise FileNotFoundError(
            f"archive object not found: backend={backend} key={bucket_key!r}"
        )
    size = archive_intake.archive_size(backend, bucket_key)
    if size is not None and size > archive_intake.MAX_ARCHIVE_BYTES:
        raise ValueError(archive_intake.too_large_message(size))

    # Same archive-scale batching as the paced drain (2026-07-20 incident):
    # defer the per-activity heat_edges_agg recompute, run ONE deduplicated
    # batch at the end — even when members failed mid-way.
    touched_ways: set[int] = set()
    db = SessionLocal()
    try:
        if not dry_run:
            summary["consent_id"] = archive_intake.record_consent(
                db,
                user_id=user_id,
                consent_version=consent_version,
                consent_text=consent_text,
                locale=locale,
                source=archive_intake.ARCHIVE_PROVENANCE,
            )
            log.info("recorded consent %s for user %s", summary["consent_id"], user_id)
        else:
            log.info("[dry-run] would record consent for user %s", user_id)

        with archive_intake.open_archive_zip(backend, bucket_key) as zf:
            # Iterate INSIDE the zip context so ZipFile reads one member into
            # RAM at a time — the ~400 MB decompressed payload is never
            # materialised whole.
            first = True
            for name, raw, csv_sport in archive_intake.iter_zip_members(zf):
                if pace_seconds > 0 and not first:
                    time.sleep(pace_seconds)
                first = False
                summary["processed"] += 1

                if csv_sport is archive_intake._OVERSIZE:
                    summary["skipped"] += 1
                    summary["skipped_reasons"]["oversize_member"] += 1
                    continue
                if csv_sport is None:
                    # activities.csv marked this out-of-scope (yoga/virtual/…).
                    summary["skipped"] += 1
                    summary["skipped_reasons"]["out_of_scope"] += 1
                    continue
                resolved_sport = (
                    csv_sport if isinstance(csv_sport, str) else _FALLBACK_SPORT
                )

                if dry_run:
                    summary["would_import"] += 1
                    continue

                try:
                    outcome, activity_id, error = _ingest_member_bytes(
                        db,
                        user_id=user_id,
                        filename=name,
                        raw=raw,
                        resolved_sport=resolved_sport,
                        contribute_heatmap=contribute_heatmap,
                        source=archive_intake.ARCHIVE_PROVENANCE,
                        skip_heat_computation=skip_heat_computation,
                        collect_touched_ways=touched_ways,
                    )
                    if outcome == "imported":
                        summary["imported"] += 1
                    else:
                        summary["skipped"] += 1
                        reason = (
                            "already_exists" if activity_id else (error or "skipped")
                        )
                        summary["skipped_reasons"][reason] += 1
                except Exception as exc:  # noqa: BLE001 — one bad member ≠ abort
                    summary["failed"] += 1
                    sentry_sdk.capture_exception(exc)
                    log.error("member %s failed to ingest", name, exc_info=True)
    finally:
        db.close()
        _recompute_heat_agg_batched(touched_ways)

    summary["skipped_reasons"] = dict(summary["skipped_reasons"])
    return summary


def _print_summary(summary: dict) -> None:
    tag = "[dry-run] " if summary.get("dry_run") else ""
    log.info(
        "%ssummary: processed=%d imported=%d would_import=%d skipped=%d failed=%d",
        tag,
        summary["processed"],
        summary["imported"],
        summary["would_import"],
        summary["skipped"],
        summary["failed"],
    )
    if summary["skipped_reasons"]:
        for reason, count in sorted(summary["skipped_reasons"].items()):
            log.info("  skipped[%s] = %d", reason, count)
    if summary.get("consent_id"):
        log.info("  consent_id = %s", summary["consent_id"])


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.founder_import",
        description="Ingest a user's OWN Strava-export .zip (already in the "
        "uploads bucket) as their consented ODbL community contribution.",
    )
    parser.add_argument(
        "--bucket-key",
        required=True,
        help="Object key of the .zip in UPLOADS_BUCKET "
        "(e.g. founder-import/<user_id>.zip), or the local path relative to "
        "ARCHIVE_INTAKE_DIR when no bucket is configured.",
    )
    parser.add_argument(
        "--user-id", required=True, help="UUID of the contributing user."
    )
    parser.add_argument(
        "--consent-text",
        required=True,
        help="Verbatim consent text to record in the audit trail.",
    )
    parser.add_argument(
        "--consent-version",
        default="founder-2026-07-13",
        help="Consent version tag (default: founder-2026-07-13).",
    )
    parser.add_argument("--locale", default="fr", help="Consent locale (default: fr).")
    parser.add_argument(
        "--pace",
        type=float,
        default=0.1,
        help="Seconds to pause between members (default: 0.1).",
    )
    parser.add_argument(
        "--skip-heat-computation",
        action="store_true",
        help="Store activities without computing heat inline (rebuild heatmap "
        "as a separate ops step). Off by default.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Stream + count members WITHOUT recording consent or writing "
        "anything to the DB.",
    )
    args = parser.parse_args()

    try:
        summary = import_founder_archive(
            bucket_key=args.bucket_key,
            user_id=args.user_id,
            consent_text=args.consent_text,
            consent_version=args.consent_version,
            locale=args.locale,
            pace_seconds=args.pace,
            dry_run=args.dry_run,
            skip_heat_computation=args.skip_heat_computation,
        )
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        log.error("founder import failed: %s", exc, exc_info=True)
        return 1

    _print_summary(summary)
    return 1 if summary["failed"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
