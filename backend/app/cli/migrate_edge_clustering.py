"""One-time migration: merge near-duplicate heat_edges using neighbor-cell clustering.

Uses the same algorithm as ingestion-time clustering (_find_canonical_edge):
- 8-neighbor grid cell lookup
- Bearing check ±30°
- Union-find to build merge groups
- Picks canonical edge (highest pass_count)

Usage:
    python -m app.cli.migrate_edge_clustering [--dry-run] [--batch-size=1000]
"""
import argparse
import logging

from sqlalchemy import text as sa_text

from app.db.session import SessionLocal
from app.services.ingest import _GRID_STEP, _bearing_diff, _edge_key, _snap

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── Union-Find ───────────────────────────────────────────────────────────────

class UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}
        self.rank: dict[str, int] = {}

    def find(self, x: str) -> str:
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # path compression
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_edge_key(edge_key: str) -> tuple[str, tuple[float, float], tuple[float, float]]:
    """Parse 'sport/lat1,lon1/lat2,lon2' into (sport, (lat1,lon1), (lat2,lon2))."""
    parts = edge_key.split("/")
    sport = parts[0]
    lat1, lon1 = float(parts[1].split(",")[0]), float(parts[1].split(",")[1])
    lat2, lon2 = float(parts[2].split(",")[0]), float(parts[2].split(",")[1])
    return sport, (lat1, lon1), (lat2, lon2)


def _find_neighbors(
    key: str,
    sport: str,
    p1: tuple[float, float],
    p2: tuple[float, float],
    all_keys: set[str],
    all_endpoints: dict[str, tuple[tuple[float, float], tuple[float, float]]],
) -> list[str]:
    """Find neighbor edges using the same algorithm as ingestion."""
    if p1 == p2:
        return []

    offsets = (-_GRID_STEP, 0, _GRID_STEP)
    neighbors = []
    for d1lat in offsets:
        for d1lon in offsets:
            np1 = _snap(p1[0] + d1lat, p1[1] + d1lon)
            for d2lat in offsets:
                for d2lon in offsets:
                    if d1lat == 0 and d1lon == 0 and d2lat == 0 and d2lon == 0:
                        continue
                    np2 = _snap(p2[0] + d2lat, p2[1] + d2lon)
                    nkey = _edge_key(sport, np1, np2)
                    if nkey in all_keys and nkey != key:
                        ep1, ep2 = all_endpoints[nkey]
                        if _bearing_diff(p1, p2, ep1, ep2) <= 30:
                            neighbors.append(nkey)
    return neighbors


def _distance_grid_steps(
    p1a: tuple[float, float], p1b: tuple[float, float],
    p2a: tuple[float, float], p2b: tuple[float, float],
) -> float:
    """Max endpoint distance in grid steps between two edges."""
    d1 = max(abs(p1a[0] - p2a[0]), abs(p1a[1] - p2a[1])) / _GRID_STEP
    d2 = max(abs(p1b[0] - p2b[0]), abs(p1b[1] - p2b[1])) / _GRID_STEP
    return max(d1, d2)


# ── Main migration ───────────────────────────────────────────────────────────

def run_migration(dry_run: bool = True, batch_size: int = 1000) -> None:
    db = SessionLocal()
    try:
        # Step 1: Create merge log table (idempotent)
        if not dry_run:
            db.execute(sa_text("""
                CREATE TABLE IF NOT EXISTS edge_merge_log (
                    old_key TEXT NOT NULL,
                    canonical_key TEXT NOT NULL,
                    merged_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            db.execute(sa_text("""
                CREATE INDEX IF NOT EXISTS idx_edge_merge_log_old_key
                ON edge_merge_log (old_key)
            """))
            db.commit()

        # Step 2: Load all edges per sport
        rows = db.execute(sa_text("""
            SELECT edge_key, sport, pass_count, forward_count, backward_count,
                   ele_delta_m, slope_grade
            FROM heat_edges
            ORDER BY sport, pass_count DESC
        """)).fetchall()

        total_before = len(rows)
        logger.info("Total edges before migration: %d", total_before)

        # Build lookup structures
        edge_data: dict[str, dict] = {}
        all_keys: set[str] = set()
        all_endpoints: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {}
        sports: set[str] = set()

        for row in rows:
            key = row[0]
            sport, p1, p2 = _parse_edge_key(key)
            edge_data[key] = {
                "sport": sport, "pass_count": row[2],
                "forward_count": row[3], "backward_count": row[4],
                "ele_delta_m": row[5], "slope_grade": row[6],
                "p1": p1, "p2": p2,
            }
            all_keys.add(key)
            all_endpoints[key] = (p1, p2)
            sports.add(sport)

        # Step 3-4: Build merge groups using union-find per sport
        uf = UnionFind()
        for key, data in edge_data.items():
            neighbors = _find_neighbors(
                key, data["sport"], data["p1"], data["p2"],
                all_keys, all_endpoints,
            )
            for nkey in neighbors:
                uf.union(key, nkey)

        # Collect groups
        groups: dict[str, list[str]] = {}
        for key in all_keys:
            root = uf.find(key)
            groups.setdefault(root, []).append(key)

        # Filter to groups with >1 member (actual merges)
        merge_groups = {root: members for root, members in groups.items() if len(members) > 1}

        # Step 5: Diameter check — evict members too far from canonical
        total_evicted = 0
        for root, members in list(merge_groups.items()):
            # Pick canonical = highest pass_count
            canonical = max(members, key=lambda k: edge_data[k]["pass_count"])
            canon_p1, canon_p2 = all_endpoints[canonical]

            valid = [canonical]
            for m in members:
                if m == canonical:
                    continue
                m_p1, m_p2 = all_endpoints[m]
                # Check both orderings (direction-agnostic)
                d1 = _distance_grid_steps(canon_p1, canon_p2, m_p1, m_p2)
                d2 = _distance_grid_steps(canon_p1, canon_p2, m_p2, m_p1)
                if min(d1, d2) <= 1.5:  # within ~1 grid step tolerance
                    valid.append(m)
                else:
                    total_evicted += 1

            if len(valid) > 1:
                merge_groups[root] = valid
            else:
                del merge_groups[root]

        total_to_merge = sum(len(m) - 1 for m in merge_groups.values())
        largest_group = max((len(m) for m in merge_groups.values()), default=0)

        logger.info("Merge groups: %d, edges to merge: %d, largest group: %d, evicted: %d",
                     len(merge_groups), total_to_merge, largest_group, total_evicted)

        # Log large groups
        for root, members in merge_groups.items():
            if len(members) > 20:
                logger.warning("Large merge group (%d members): canonical=%s", len(members), root)

        if dry_run:
            logger.info("DRY RUN — no changes made. Expected reduction: %d edges (%.1f%%)",
                        total_to_merge, total_to_merge / total_before * 100 if total_before else 0)
            return

        # Step 6: Execute merges in batches
        merged_total = 0
        batch_count = 0

        for _root, members in merge_groups.items():
            canonical = max(members, key=lambda k: edge_data[k]["pass_count"])
            others = [m for m in members if m != canonical]

            # Idempotent: check canonical still exists
            exists = db.execute(sa_text(
                "SELECT 1 FROM heat_edges WHERE edge_key = :key"
            ), {"key": canonical}).fetchone()
            if not exists:
                continue

            # Sum counts into canonical
            sum_pass = sum(edge_data[m]["pass_count"] for m in others)
            sum_fwd = sum(edge_data[m]["forward_count"] for m in others)
            sum_bwd = sum(edge_data[m]["backward_count"] for m in others)

            # Keep elevation from highest pass_count (canonical already has it)

            db.execute(sa_text("""
                UPDATE heat_edges SET
                    pass_count = pass_count + :add_pass,
                    forward_count = forward_count + :add_fwd,
                    backward_count = backward_count + :add_bwd
                WHERE edge_key = :key
            """), {
                "key": canonical,
                "add_pass": sum_pass, "add_fwd": sum_fwd, "add_bwd": sum_bwd,
            })

            for old_key in others:
                # Check old key still exists (idempotent)
                old_exists = db.execute(sa_text(
                    "SELECT 1 FROM heat_edges WHERE edge_key = :key"
                ), {"key": old_key}).fetchone()
                if not old_exists:
                    continue

                # Redirect contributors (preserve activity_date via GREATEST)
                db.execute(sa_text("""
                    INSERT INTO heat_edge_contributors (edge_key, user_id_hash, activity_date)
                    SELECT :canonical, user_id_hash, activity_date
                    FROM heat_edge_contributors
                    WHERE edge_key = :old
                    ON CONFLICT (edge_key, user_id_hash) DO UPDATE
                      SET activity_date = GREATEST(excluded.activity_date, heat_edge_contributors.activity_date)
                """), {"canonical": canonical, "old": old_key})

                # Delete old contributors
                db.execute(sa_text(
                    "DELETE FROM heat_edge_contributors WHERE edge_key = :old"
                ), {"old": old_key})

                # Log merge
                db.execute(sa_text(
                    "INSERT INTO edge_merge_log (old_key, canonical_key) VALUES (:old, :canonical)"
                ), {"old": old_key, "canonical": canonical})

                # Delete old edge
                db.execute(sa_text(
                    "DELETE FROM heat_edges WHERE edge_key = :old"
                ), {"old": old_key})

                merged_total += 1

            # Recompute user_count on canonical. COUNT(DISTINCT
            # user_id_hash) — since migration 0052 the contributors PK
            # is (edge_key, user_id_hash, activity_id), so a user with
            # N activities on the same edge has N contributor rows.
            # user_count drives K-anonymity → must DISTINCT.
            db.execute(sa_text("""
                UPDATE heat_edges SET user_count = (
                    SELECT COUNT(DISTINCT user_id_hash) FROM heat_edge_contributors
                    WHERE edge_key = :key
                ) WHERE edge_key = :key
            """), {"key": canonical})

            batch_count += 1
            if batch_count % batch_size == 0:
                db.commit()
                logger.info("Progress: %d groups processed, %d edges merged", batch_count, merged_total)

        db.commit()

        # Final stats
        total_after = db.execute(sa_text("SELECT COUNT(*) FROM heat_edges")).fetchone()[0]
        logger.info("Migration complete: %d → %d edges (%d merged, %.1f%% reduction)",
                     total_before, total_after, merged_total,
                     merged_total / total_before * 100 if total_before else 0)

    except Exception:
        db.rollback()
        logger.exception("Migration failed — rolled back current batch")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge near-duplicate heat_edges")
    parser.add_argument("--dry-run", action="store_true", help="Report without modifying DB")
    parser.add_argument("--batch-size", type=int, default=1000, help="Commit every N groups")
    args = parser.parse_args()
    run_migration(dry_run=args.dry_run, batch_size=args.batch_size)
