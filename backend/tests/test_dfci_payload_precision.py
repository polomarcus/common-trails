"""Non-regression: ``ingest.get_dfci_geojson`` shrinks its payload.

The full-precision DFCI FeatureCollection (~1000 forest tracks at PostGIS'
9-decimal default, no simplification) is ~34 MB uncompressed — OVER Cloud Run's
32 MiB response cap. Browsers send ``Accept-Encoding: gzip`` and get a 200, but
any client that does NOT (curl, uptime monitors, some proxies) gets a hard 500.

The fix simplifies (~11 m) and emits 5-decimal (~1 m) coordinates at source.
This test drives the REAL ``get_dfci_geojson`` against a seeded high-precision,
densely-verticed edge and asserts BOTH levers are applied:

- coordinates carry at most 5 decimal places (9-decimal input would fail), and
- near-collinear vertices are dropped (simplification reduced the vertex count).

Fails on the pre-fix query (``ST_AsGeoJSON(geometry)``); passes on the fix.
Needs the PostGIS DB with the ``dfci_edges`` table (migration 0015); skips
cleanly if absent.
"""
import uuid

import pytest
from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.ingest import get_dfci_geojson


def _db_available() -> bool:
    try:
        db = SessionLocal()
        try:
            db.execute(sa_text("SELECT 1 FROM dfci_edges LIMIT 1"))
            return True
        finally:
            db.close()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="needs the PostGIS DB with the dfci_edges table (migration 0015)",
)


def _max_decimals(coords) -> int:
    """Deepest decimal-place count across a nested coordinate array."""
    best = 0
    stack = [coords]
    while stack:
        item = stack.pop()
        if isinstance(item, (list, tuple)):
            stack.extend(item)
        elif isinstance(item, float):
            s = repr(item)
            if "." in s and "e" not in s and "E" not in s:
                best = max(best, len(s.split(".")[1]))
    return best


def _count_points(coords) -> int:
    return len(coords) if coords and isinstance(coords[0], (list, tuple)) else 0


def test_dfci_geojson_is_precision_reduced_and_simplified():
    ref = f"TEST-DFCI-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        # A near-straight west→east line with 6 vertices at 9-decimal precision:
        # the tiny lat wobble is well under the ~11 m simplify tolerance, so
        # ST_SimplifyPreserveTopology must collapse the interior points, and the
        # 9-decimal coords must be re-emitted at ≤5 decimals.
        db.execute(
            sa_text(
                """
                INSERT INTO dfci_edges (ref, surface, highway, trail_type, geometry)
                VALUES (:ref, 'unknown', 'track', 'DFCI',
                        ST_GeomFromText(:wkt, 4326))
                """
            ),
            {
                "ref": ref,
                "wkt": (
                    "LINESTRING("
                    "3.000000001 43.700000001, "
                    "3.020000002 43.700000002, "
                    "3.040000003 43.700000001, "
                    "3.060000004 43.700000003, "
                    "3.080000005 43.700000002, "
                    "3.100000006 43.700000001)"
                ),
            },
        )
        db.commit()

        fc = get_dfci_geojson(limit=0)
        feats = [f for f in fc["features"]
                 if f["properties"].get("ref") == ref]
        assert feats, "seeded DFCI edge missing from the FeatureCollection"
        coords = feats[0]["geometry"]["coordinates"]

        assert _max_decimals(coords) <= 5, (
            f"DFCI coords must be ≤5 decimals (got {_max_decimals(coords)}) — "
            "the 9-decimal default blows the Cloud Run 32 MiB cap for non-gzip clients"
        )
        assert _count_points(coords) < 6, (
            f"simplification must drop near-collinear vertices (kept {_count_points(coords)}/6)"
        )
    finally:
        db.execute(sa_text("DELETE FROM dfci_edges WHERE ref = :ref"), {"ref": ref})
        db.commit()
        db.close()
