"""Audit surface classification on minigraph edges.

Usage:
    python -m app.cli.surfaces_audit
"""
import os
import sys

from app.services.surface_classification import SurfaceClass, classify_surface


def audit(database_url: str | None = None) -> dict[SurfaceClass, int]:
    url = database_url or os.environ.get(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5487/common_trails"
    )

    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT surface, highway_type, tracktype, smoothness FROM minigraph_edges")
        ).fetchall()

    counts: dict[SurfaceClass, int] = {
        "asphalt": 0, "gravel": 0, "dirt": 0, "rock": 0, "unknown": 0,
    }
    for row in rows:
        tags = {
            "surface": row[0] or "",
            "highway": row[1] or "",
            "tracktype": row[2] or "",
            "smoothness": row[3] or "",
        }
        cls, _ = classify_surface(tags)
        counts[cls] += 1

    total = sum(counts.values())
    unknown_ratio = counts["unknown"] / total if total else 1.0

    print(f"Surface distribution ({total} edges):")
    for cls, n in counts.items():
        pct = 100 * n / total if total else 0
        print(f"  {cls:>10s}: {n:3d}  ({pct:5.1f}%)")

    if unknown_ratio > 0.8:
        print(f"\nWARNING: {unknown_ratio:.0%} unknown — surface data is insufficient")

    return counts


if __name__ == "__main__":
    try:
        audit()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
