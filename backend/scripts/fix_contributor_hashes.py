"""One-time cleanup: rebuild heat_edge_contributors and heat_cell_contributors
with deterministic hashes (hashlib.sha256 instead of Python hash()).

Run inside the backend container:
    python -m scripts.fix_contributor_hashes

What it does:
1. Truncates heat_edge_contributors and heat_cell_contributors
2. Resets user_count to 0 on heat_edges and heat_cells
3. Re-derives contributors from the activities table using the fixed hash
4. Recomputes user_count on heat_edges and heat_cells
"""
import hashlib
import json
import logging

from sqlalchemy import text as sa_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


def _user_id_hash(user_id) -> int:
    return int(hashlib.sha256(str(user_id).encode()).hexdigest()[:8], 16)


def _snap_key(lon: float, lat: float) -> str:
    """5-decimal-place grid snap, matching ingest.py logic."""
    return f"{round(lon, 5):.5f},{round(lat, 5):.5f}"


def main():
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        # ── Step 1: Truncate contributor tables ─────────────────────────
        log.info("Truncating heat_edge_contributors and heat_cell_contributors...")
        db.execute(sa_text("TRUNCATE heat_edge_contributors"))
        db.execute(sa_text("TRUNCATE heat_cell_contributors"))
        db.execute(sa_text("UPDATE heat_edges SET user_count = 0"))
        db.execute(sa_text("UPDATE heat_cells SET user_count = 0"))
        db.commit()
        log.info("Tables truncated.")

        # ── Step 2: Load heatmap-contributing activities ────────────────
        rows = db.execute(sa_text("""
            SELECT user_id, sport, geometry_geojson, activity_date
            FROM activities
            WHERE contribute_heatmap = true
              AND geometry_geojson IS NOT NULL
            ORDER BY created_at
        """)).fetchall()
        log.info("Found %d heatmap-contributing activities.", len(rows))

        # ── Step 3: Rebuild heat_edge_contributors ──────────────────────
        edge_contributor_count = 0
        for user_id, sport, geojson_str, activity_date in rows:
            uid_hash = _user_id_hash(user_id)
            try:
                coords = json.loads(geojson_str).get("coordinates", [])
            except (json.JSONDecodeError, AttributeError):
                continue

            # Find which heat_edges this activity touches
            # We match by sport and spatial proximity (edge keys that exist)
            # Simpler approach: query existing edge keys for this sport that
            # intersect with the activity's bounding box, then check point proximity
            if len(coords) < 2:
                continue

            # Get all edge_keys for this sport that this activity could touch
            # by matching grid-snap keys from coordinates
            seen_edges = set()
            for coord in coords:
                if len(coord) < 2:
                    continue
                # Check neighboring grid cells too (activity might have been
                # snapped to adjacent cells during ingest)
                for dlon in (-0.00001, 0, 0.00001):
                    for dlat in (-0.00001, 0, 0.00001):
                        key = f"{sport}/{_snap_key(coord[0] + dlon, coord[1] + dlat)}"
                        seen_edges.add(key)

            if not seen_edges:
                continue

            # Batch check which of these edge_keys actually exist
            existing = db.execute(sa_text("""
                SELECT edge_key FROM heat_edges
                WHERE edge_key = ANY(:keys)
            """), {"keys": list(seen_edges)}).fetchall()
            existing_keys = {r[0] for r in existing}

            for edge_key in existing_keys:
                db.execute(sa_text("""
                    INSERT INTO heat_edge_contributors (edge_key, user_id_hash, activity_date)
                    VALUES (:ek, :uid, :ad)
                    ON CONFLICT (edge_key, user_id_hash) DO UPDATE
                      SET activity_date = GREATEST(excluded.activity_date,
                          heat_edge_contributors.activity_date)
                """), {"ek": edge_key, "uid": uid_hash, "ad": activity_date})
                edge_contributor_count += 1

        db.commit()
        log.info("Inserted %d heat_edge_contributor rows.", edge_contributor_count)

        # ── Step 4: Recompute user_count on heat_edges ──────────────────
        db.execute(sa_text("""
            UPDATE heat_edges SET user_count = COALESCE(sub.cnt, 0)
            FROM (
                SELECT edge_key, COUNT(*) AS cnt
                FROM heat_edge_contributors
                GROUP BY edge_key
            ) sub
            WHERE heat_edges.edge_key = sub.edge_key
        """))
        db.commit()
        log.info("Recomputed heat_edges.user_count.")

        # ── Step 5: Rebuild heat_cell_contributors ──────────────────────
        cell_contributor_count = 0
        for user_id, sport, geojson_str, _activity_date in rows:
            uid_hash = _user_id_hash(user_id)
            try:
                coords = json.loads(geojson_str).get("coordinates", [])
            except (json.JSONDecodeError, AttributeError):
                continue

            # Compute z14 tile keys for each coordinate
            seen_cells = set()
            for coord in coords:
                if len(coord) < 2:
                    continue
                import math
                lon, lat = coord[0], coord[1]
                n = 2 ** 14
                x = int((lon + 180) / 360 * n)
                lat_rad = math.radians(lat)
                y = int((1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2 * n)
                cell_key = f"14/{x}/{y}"
                seen_cells.add(cell_key)

            for cell_key in seen_cells:
                db.execute(sa_text("""
                    INSERT INTO heat_cell_contributors (cell_key, sport, user_id_hash)
                    VALUES (:ck, :sport, :uid)
                    ON CONFLICT DO NOTHING
                """), {"ck": cell_key, "sport": sport, "uid": uid_hash})
                cell_contributor_count += 1

        db.commit()
        log.info("Inserted %d heat_cell_contributor rows.", cell_contributor_count)

        # ── Step 6: Recompute user_count on heat_cells ──────────────────
        db.execute(sa_text("""
            UPDATE heat_cells SET user_count = COALESCE(sub.cnt, 0)
            FROM (
                SELECT cell_key, sport, COUNT(*) AS cnt
                FROM heat_cell_contributors
                GROUP BY cell_key, sport
            ) sub
            WHERE heat_cells.cell_key = sub.cell_key
              AND heat_cells.sport = sub.sport
        """))
        db.commit()
        log.info("Recomputed heat_cells.user_count.")

        # ── Verify ──────────────────────────────────────────────────────
        total = db.execute(sa_text(
            "SELECT COUNT(DISTINCT user_id_hash) FROM heat_edge_contributors"
        )).scalar()
        log.info("Done. Total distinct contributors: %d", total)

    finally:
        db.close()


if __name__ == "__main__":
    main()
