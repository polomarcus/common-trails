"""Recompute ``activities.elevation_gain_m`` from stored coords.

After PR #259 introduced Strava-style smoothing (rolling median window=11
+ 3 m noise threshold) in `gpx.py:_smoothed_elevation_gain`, existing
activity rows still carry the OLD inflated D+ value computed with naive
monotonic summation.

This CLI re-runs the smoothing over the stored `geometry_geojson` coords
in place. **Crouzet invariant preserved**: only `elevation_gain_m` is
written; coords / geometry are never touched.

## Usage

    # Preview without writes
    python -m app.cli.recompute_elevation_gain --dry-run

    # Recompute for all 3D activities (those with elevation in coords)
    python -m app.cli.recompute_elevation_gain

    # Limit to a specific user (e.g. testing first)
    python -m app.cli.recompute_elevation_gain --user-id <uuid>

    # Throttle batch size for db-f1-micro
    python -m app.cli.recompute_elevation_gain --batch 100

## Idempotent

Running twice produces the same `elevation_gain_m` value (the smoothing
is deterministic). Safe to re-run if interrupted.
"""
from __future__ import annotations

import argparse
import json
import logging
import time

from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.gpx import _smoothed_elevation_gain

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _elevations_from_geojson(raw: str | None) -> list[float | None]:
    """Pull the z-coordinate from a GeoJSON LineString. Returns [] if the
    geometry has no elevation (2D track)."""
    if not raw:
        return []
    try:
        geom = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    coords = geom.get("coordinates") or []
    if not coords:
        return []
    # Coordinates can be [lon, lat] (2D) or [lon, lat, ele] (3D).
    return [c[2] if len(c) >= 3 else None for c in coords]


def run(dry_run: bool = False, user_id: str | None = None, batch: int = 200) -> None:
    db = SessionLocal()
    try:
        # Only iterate over 3D activities — 2D ones have no ele to smooth.
        # ``geometry_geojson IS NOT NULL`` filters out broken legacy rows.
        params: dict[str, object] = {}
        filt = "geometry_geojson IS NOT NULL"
        if user_id:
            filt += " AND user_id = :uid"
            params["uid"] = user_id
        total = db.execute(
            sa_text(f"SELECT COUNT(*) FROM activities WHERE {filt}"),
            params,
        ).scalar() or 0
        logger.info("Candidate activities: %d (user_id filter=%s)", total, user_id or "none")

        offset = 0
        updated = 0
        unchanged = 0
        skipped_2d = 0
        t0 = time.monotonic()

        while True:
            rows = db.execute(
                sa_text(
                    f"SELECT id, elevation_gain_m, geometry_geojson "
                    f"FROM activities WHERE {filt} "
                    "ORDER BY id "
                    "LIMIT :batch OFFSET :off"
                ),
                {**params, "batch": batch, "off": offset},
            ).fetchall()
            if not rows:
                break

            for row in rows:
                eles = _elevations_from_geojson(row.geometry_geojson)
                # No elevation in coords → can't recompute.
                if not any(e is not None for e in eles):
                    skipped_2d += 1
                    continue
                new_gain = round(_smoothed_elevation_gain(eles), 1)
                old_gain = row.elevation_gain_m
                if old_gain is not None and abs(old_gain - new_gain) < 0.5:
                    unchanged += 1
                    continue
                if not dry_run:
                    db.execute(
                        sa_text(
                            "UPDATE activities SET elevation_gain_m = :g WHERE id = :id"
                        ),
                        {"g": new_gain, "id": row.id},
                    )
                updated += 1
                if updated <= 10:
                    logger.info(
                        "  %s: %s → %s m",
                        str(row.id)[:8], old_gain, new_gain,
                    )

            if not dry_run:
                db.commit()
            offset += batch
            if offset % (batch * 10) == 0:
                logger.info(
                    "  progress %d/%d (updated=%d, unchanged=%d, skipped_2d=%d, %.0fs)",
                    offset, total, updated, unchanged, skipped_2d, time.monotonic() - t0,
                )

        elapsed = time.monotonic() - t0
        logger.info(
            "%s: updated=%d, unchanged=%d, skipped_2d=%d / %d total in %.0fs",
            "DRY-RUN" if dry_run else "DONE",
            updated, unchanged, skipped_2d, total, elapsed,
        )
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-apply smoothed D+ to existing activities")
    parser.add_argument("--dry-run", action="store_true", help="Don't write, just preview")
    parser.add_argument("--user-id", help="Limit to one user (uuid)")
    parser.add_argument("--batch", type=int, default=200,
                        help="Rows per batch / commit (default 200)")
    args = parser.parse_args()
    run(dry_run=args.dry_run, user_id=args.user_id, batch=args.batch)
