"""Cloud Run Job: rebuild heatmap from all activities.

Run locally (full rebuild):
    python -m app.jobs.rebuild_heatmap

Run locally (incremental — only new activities since last 24h):
    python -m app.jobs.rebuild_heatmap --since 24h

Run locally (incremental — since specific timestamp):
    python -m app.jobs.rebuild_heatmap --since 2026-03-28T00:00:00

Run locally (bbox filter — only Montpellier area):
    python -m app.jobs.rebuild_heatmap --bbox "3.5,43.4,4.1,43.8"

Run via Cloud Run:
    gcloud run jobs execute common-trails-rebuild-heatmap-prod --region europe-west1

Environment variables:
    HEATMAP_WORKERS: parallel workers (default: 4)
    TRUNCATE_FIRST: set to "true" to wipe heatmap tables before rebuild
    SKIP_CANONICAL_MERGE: set to "true" to skip bbox query + neighbor merge (10x faster)
    REBUILD_SINCE: same as --since flag (e.g. "24h", "7d", ISO timestamp)
"""
import argparse
import logging
import os
import re
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import text as sa_text

from app.services.provenance import COMMUNITY_SOURCE

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _parse_since(value: str) -> datetime:
    """Parse a --since value into a UTC datetime.

    Accepts:
      - Duration: "24h", "7d", "2w", "30m"
      - ISO timestamp: "2026-03-28T00:00:00"
    """
    # Try duration pattern first
    m = re.match(r"^(\d+)\s*([mhdw])$", value.strip().lower())
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        delta = {
            "m": timedelta(minutes=amount),
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
            "w": timedelta(weeks=amount),
        }[unit]
        return datetime.now(UTC) - delta

    # Try ISO timestamp
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        pass

    raise ValueError(f"Cannot parse --since value: {value!r}. Use '24h', '7d', or ISO timestamp.")


def main(since: datetime | None = None, bbox: tuple[float, float, float, float] | None = None) -> None:
    from app.db.session import SessionLocal
    from app.services.ingest import rebuild_heatmap_parallel

    # F12: default false — require explicit opt-in to destructive truncate
    truncate = os.environ.get("TRUNCATE_FIRST", "false").lower() == "true"

    # Footgun guard (2026-05-16 incident): running this script with
    # `TRUNCATE_FIRST=false` and no `--since` re-processes every activity
    # on top of existing heat_edges rows — `_upsert_edges_batch` does
    # `pass_count = pass_count + 1` per edge, so every rerun doubles the
    # counts (and concurrent runs triple). It's only safe when paired
    # with truncate OR an incremental since-window.
    #
    # If the operator really wants this behaviour (e.g. testing in a
    # disposable env), they set ALLOW_UNGATED_REBUILD=true. Production
    # paths must always set one of TRUNCATE_FIRST or --since.
    if not truncate and since is None:
        if os.environ.get("ALLOW_UNGATED_REBUILD", "").lower() != "true":
            raise SystemExit(
                "ABORT: rebuild_heatmap was launched without TRUNCATE_FIRST=true "
                "AND without --since. That config double-counts pass_count on "
                "every existing edge (incident 2026-05-16).\n"
                "  Full rebuild   → set TRUNCATE_FIRST=true (terraform default).\n"
                "  Incremental    → pass --since=24h (or similar).\n"
                "  Override (rare)→ ALLOW_UNGATED_REBUILD=true (e.g. dev DB)."
            )
        log.warning(
            "ALLOW_UNGATED_REBUILD=true — re-ingesting all activities WITHOUT "
            "truncate. pass_count will increment for every edge that already "
            "exists. This is only correct if heat_edges is empty."
        )

    db = SessionLocal()
    try:
        if truncate:
            if since:
                log.warning("TRUNCATE_FIRST ignored in incremental mode (--since)")
            else:
                log.info("Truncating heatmap tables...")
                db.execute(sa_text("TRUNCATE heat_edge_contributors"))
                db.execute(sa_text("TRUNCATE heat_edges CASCADE"))
                # heat_cells / heat_cell_contributors live until
                # migration 0036 drops them. After that the TRUNCATE is
                # a no-op so guard with to_regclass to keep the rebuild
                # job working through the migration window.
                db.execute(sa_text(
                    "DO $$ BEGIN "
                    "IF to_regclass('public.heat_cell_contributors') IS NOT NULL "
                    "THEN TRUNCATE heat_cell_contributors; END IF; END $$"
                ))
                db.execute(sa_text(
                    "DO $$ BEGIN "
                    "IF to_regclass('public.heat_cells') IS NOT NULL "
                    "THEN TRUNCATE heat_cells CASCADE; END IF; END $$"
                ))
                db.commit()
                log.info("Truncated.")

        # Build query with optional filters.
        # PROVENANCE GATE (②): the community heatmap is rebuilt ONLY from
        # community-eligible uploads (source == "manual_upload"). Strava-API
        # syncs (strava_api / legacy NULL) are personal-only and excluded
        # under Strava's 2026 API Policy §5.4/§5.10. See app/services/provenance.py.
        # ⚠️ OPS: after this ships, a full rebuild on the CURRENT prod DB (whose
        # heat_edges are entirely Strava-API-sourced) will EMPTY the community
        # map until manual_upload archives arrive — the intended compliance
        # outcome (docs/strava/community-contribution-ux.md §5).
        where_parts = [
            "contribute_heatmap = true",
            "geometry_geojson IS NOT NULL",
            "source = :community_source",
        ]
        params: dict = {"community_source": COMMUNITY_SOURCE}
        if since:
            where_parts.append("created_at > :since")
            params["since"] = since
            log.info("Incremental rebuild: activities since %s", since.isoformat())
        if bbox:
            # Filter activities whose center falls within the bbox
            # geometry_geojson is stored as GeoJSON text — extract center via PostGIS
            where_parts.append(
                "ST_Intersects("
                "  ST_Envelope(ST_GeomFromGeoJSON(geometry_geojson)),"
                "  ST_MakeEnvelope(:lon_min, :lat_min, :lon_max, :lat_max, 4326))"
            )
            params["lon_min"], params["lat_min"], params["lon_max"], params["lat_max"] = bbox
            log.info("Bbox filter: %.4f,%.4f → %.4f,%.4f", *bbox)

        where_clause = " AND ".join(where_parts)
        rows = db.execute(sa_text(f"""
            SELECT user_id, sport, geometry_geojson, activity_date, contribute_heatmap, id
            FROM activities
            WHERE {where_clause}
            ORDER BY created_at
        """), params).fetchall()

        # Tuple shape: (user_id, sport, geojson, activity_date, contribute, activity_id)
        # activity_id is plumbed since migration 0052 so re-runs of the
        # rebuild path are no-ops on pass_count (was: every rebuild
        # double-counted because the contributors UPSERT only deduped
        # at (edge_key, user_id_hash) — same user, same edge, same
        # activity → no new contributor row but pass_count += 1 every
        # time).
        activities = [(r[0], r[1] or "road", r[2], r[3], r[4], r[5]) for r in rows]
        mode = "incremental" if since else "full"
        log.info("Found %d heatmap-contributing activities (%s rebuild)", len(activities), mode)

        if not activities:
            log.info("Nothing to rebuild.")
            return

        # rebuild_heatmap_parallel disables the per-activity heat_edges_agg
        # refresh internally (the 4 workers would contend on the agg table);
        # we do ONE backfill at the end (below) instead.
        from app.jobs.rebuild_heat_agg import backfill_heat_agg

        t0 = time.time()
        loaded = rebuild_heatmap_parallel(activities)
        elapsed = time.time() - t0

        # Refresh planner statistics AFTER the bulk reload. A TRUNCATE +
        # multi-million-row INSERT leaves stale stats until autovacuum
        # eventually catches up — and any query that runs in that window
        # (e.g. the live MVT tile pre-warm) can get a catastrophic plan: a
        # single z11 Montpellier tile that runs in ~160 ms with fresh stats
        # took 27 MINUTES at 2026-06-13 10:44 because ANALYZE hadn't run yet.
        # Explicit ANALYZE on the parent cascades to every sport partition.
        log.info("Analyzing heat_edges (fresh planner stats post-reload)...")
        db.execute(sa_text("ANALYZE heat_edges"))
        db.execute(sa_text("ANALYZE heat_edge_contributors"))
        db.commit()

        # Repopulate the display aggregate in one heavy pass now that
        # heat_edges is final (the per-activity refresh was disabled above).
        # This leaves heat_edges_agg correct after a full rebuild.
        log.info("Backfilling heat_edges_agg (one full aggregation)...")
        agg_rows = backfill_heat_agg(db)
        log.info("heat_edges_agg: %d rows", agg_rows)

        # Verify
        total = db.execute(sa_text("SELECT COUNT(*) FROM heat_edges")).scalar()
        contribs = db.execute(sa_text("SELECT COUNT(DISTINCT user_id_hash) FROM heat_edge_contributors")).scalar()
        log.info("Done: %d/%d activities in %.0fs (%.1f min) [%s]", loaded, len(activities), elapsed, elapsed / 60, mode)
        log.info("Result: %d heat_edges, %d distinct contributors", total, contribs)

        # The matched-era CDN "api-cache" publish (publish_heatmap_cache) was
        # removed with the raw-trace pivot — the display artefact is now the
        # static heatmap-display.pmtiles built by app.jobs.build_pmtiles.
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild heatmap from activities")
    parser.add_argument(
        "--since",
        type=str,
        default=os.environ.get("REBUILD_SINCE"),
        help='Incremental: only activities since this time. E.g. "24h", "7d", "2026-03-28T00:00:00"',
    )
    parser.add_argument(
        "--bbox",
        type=str,
        default=None,
        help='Only rebuild activities intersecting this bbox. Format: "lon_min,lat_min,lon_max,lat_max". '
             'E.g. "3.5,43.4,4.1,43.8" for Montpellier area.',
    )
    args = parser.parse_args()

    since_dt = _parse_since(args.since) if args.since else None
    bbox_tuple = None
    if args.bbox:
        parts = [float(x.strip()) for x in args.bbox.split(",")]
        if len(parts) != 4:
            parser.error("--bbox requires exactly 4 comma-separated values: lon_min,lat_min,lon_max,lat_max")
        bbox_tuple = (parts[0], parts[1], parts[2], parts[3])
    main(since=since_dt, bbox=bbox_tuple)
