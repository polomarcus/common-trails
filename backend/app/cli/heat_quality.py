"""One-shot heat-edge spaghetti audit — ops tool.

Usage:
    docker compose exec backend python -m app.cli.heat_quality
    docker compose exec backend python -m app.cli.heat_quality --bbox 3.85 43.58 3.95 43.65
    docker compose exec backend python -m app.cli.heat_quality --sport gravel
    docker compose exec backend python -m app.cli.heat_quality --json

Prints a per-region table of spaghetti metrics and flags any region
crossing the alert thresholds defined in app.services.heat_quality.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from app.db.session import SessionLocal
from app.services.heat_quality import (
    ALERT_GRID_FALLBACK_RATIO,
    ALERT_ISOLATED_RATIO,
    MONITORED_REGIONS,
    compute_disconnection_metrics,
)

if TYPE_CHECKING:
    from app.services.heat_quality import DisconnectionMetrics


def _human_table(rows: list[tuple[str, DisconnectionMetrics]]) -> str:
    """Render the metrics as a fixed-width table."""
    header = f"{'region':<22} {'total':>8} {'grid%':>8} {'dang%':>8} {'iso%':>8} {'alert':>8}"
    lines = [header, "-" * len(header)]
    for label, m in rows:
        alert = ""
        if m.grid_fallback_ratio > ALERT_GRID_FALLBACK_RATIO:
            alert += "G"
        if m.isolated_ratio > ALERT_ISOLATED_RATIO:
            alert += "I"
        lines.append(
            f"{label:<22} {m.edges_total:>8d} "
            f"{m.grid_fallback_ratio:>7.1%} "
            f"{m.dangling_ratio:>7.1%} "
            f"{m.isolated_ratio:>7.1%} "
            f"{alert or '-':>8}"
        )
    if any(m.grid_fallback_ratio > ALERT_GRID_FALLBACK_RATIO
           or m.isolated_ratio > ALERT_ISOLATED_RATIO
           for _, m in rows):
        lines.append("")
        lines.append(
            f"G = grid_fallback > {ALERT_GRID_FALLBACK_RATIO:.0%}, "
            f"I = fully_isolated > {ALERT_ISOLATED_RATIO:.0%}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--bbox", nargs=4, type=float, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"),
        help="Override the bbox (default: monitored regions in heat_quality.MONITORED_REGIONS)",
    )
    p.add_argument("--sport", default=None, help="Sport filter (road/gravel/mtb/offroad/running)")
    p.add_argument("--json", action="store_true", help="Emit JSON lines instead of a human table")
    args = p.parse_args(argv)

    db = SessionLocal()
    try:
        regions = [("custom_bbox", tuple(args.bbox))] if args.bbox else MONITORED_REGIONS

        rows = [(label, compute_disconnection_metrics(db, bbox, sport=args.sport))
                for label, bbox in regions]

        if args.json:
            for label, m in rows:
                obj = m.as_log_dict()
                obj["region"] = label
                print(json.dumps(obj))
        else:
            print(_human_table(rows))

        # Exit non-zero if any region is in alert state — useful in cron / CI.
        if any(m.grid_fallback_ratio > ALERT_GRID_FALLBACK_RATIO
               or m.isolated_ratio > ALERT_ISOLATED_RATIO
               for _, m in rows):
            return 2
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
