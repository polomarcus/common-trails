---
title: 'Heatmap Edge Clustering'
slug: 'heatmap-edge-clustering'
created: '2026-03-22'
status: 'implementation-complete'
stepsCompleted: [1, 2, 3, 4]
tech_stack: [python, postgresql, postgis, fastapi]
files_to_modify: [backend/app/services/ingest.py, backend/tests/test_ingest_direction.py, backend/app/cli/migrate_edge_clustering.py]
code_patterns: [upsert-on-conflict, direction-agnostic-edge-key, 4dp-snap-grid, k-anonymity, neighbor-cell-lookup]
test_patterns: [pytest, isolated-sport-name, geojson-fixture-helpers]
---

# Tech-Spec: Heatmap Edge Clustering

**Created:** 2026-03-22

## Overview

### Problem Statement

GPS jitter (5-15m) creates multiple near-parallel edges on the same physical road. The current snap resolution (`round(lat, 4)` = ~11m grid) is too fine — traces from different rides on the same road land on different grid cells, creating duplicate edges. Each shows "1 contributeur · 1 passage" instead of the correct aggregated count. This also pollutes the routing graph with many low-count edges on the same road.

### Solution

**Keep the current ~11m grid (`round(lat, 4)`) but add a neighbor-cell check** that looks at 8 adjacent grid cells for existing edges. If a neighbor already has an edge, merge into it instead of creating a new one.

This gives an effective merge radius of ~22m (11m grid + 1 neighbor = 2 cells) which:
- Merges GPS jitter on the same road (5-15m apart) ✓
- Keeps tight switchbacks separate (>22m at turn points) ✓
- Preserves single-track resolution (~11m base grid) ✓

No grid size change needed — the neighbor check alone solves the problem.

### Algorithm Comparison (from elicitation)

| Approach | Complexity | Accuracy | Performance | Verdict |
|---|---|---|---|---|
| A: Coarse Grid only | ★★★★★ | ★★★☆☆ | ★★★★★ | ~10% boundary misses |
| B: Fuzzy SQL (ST_DWithin) | ★★★☆☆ | ★★★★★ | ★★☆☆☆ | Slow imports |
| **C: Hybrid — keep 11m grid + neighbor check** | **★★★★☆** | **★★★★★** | **★★★★☆** | **Best balance** |

### Scope

**In Scope:**
- Add `_find_canonical_edge()` neighbor-cell lookup to merge across grid boundaries
- Track `existing_keys` set in `_update_heat_edges()` for fast in-memory lookups
- One-time migration script to merge existing near-duplicate edges
- Preserve elevation/slope data (keep values from highest-pass edge when merging)
- Update tests

**Out of Scope:**
- Changing `_snap()` grid resolution (keep `round(lat, 4)` = ~11m — good for single tracks)
- OSM road matching (Phase 2 if needed)
- Changing heatmap display/rendering logic
- Modifying the routing graph builder (reads from heat_edges, auto-adapts)

## Context for Development

### Codebase Patterns

**Snap function** (`ingest.py:97-99`) — UNCHANGED:
```python
def _snap(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, 4), round(lon, 4)  # ~11m grid — keep as-is
```

**Edge key** (`ingest.py:102-105`): direction-agnostic, sorted endpoints:
```python
def _edge_key(sport, p1, p2):
    a, b = sorted([p1, p2])
    return f"{sport}/{a[0]},{a[1]}/{b[0]},{b[1]}"
```

**UPSERT** (`ingest.py:189-209`): `INSERT ... ON CONFLICT (edge_key) DO UPDATE SET pass_count = pass_count + 1, forward_count += :fwd, backward_count += :bwd`

**Contributor dedup** (`ingest.py:215-222`): `INSERT ... ON CONFLICT DO NOTHING` into heat_edge_contributors

**Geometry** (`ingest.py:193`): `ST_MakeLine(ST_MakePoint(:a_lon, :a_lat), ST_MakePoint(:b_lon, :b_lat))` — PostGIS line from endpoints

**Test pattern** (`test_ingest_direction.py`): uses isolated sport `"test_dir"`, `_geojson()` helper to build LineString, direct calls to `_update_heat_edges()`, verifies pass/forward/backward counts

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `backend/app/services/ingest.py:97-246` | `_snap()`, `_edge_key()`, `_update_heat_edges()` — core change |
| `backend/app/db/models.py:341-368` | HeatEdge, HeatEdgeContributor models |
| `backend/tests/test_ingest_direction.py` | Existing tests for edge direction tracking |
| `backend/app/services/routing.py:1620+` | Reads heat_edges for routing graph (no changes needed) |
| `backend/app/api/heatmap.py:175+` | Serves heatmap trails (no changes needed) |
| `frontend/lib/client-graph.ts` | Frontend routing graph (SPATIAL_GRID_SIZE = 0.001 ~110m) — no change needed |

### Technical Decisions

1. **Keep 11m grid, add neighbor check only** — Preserves single-track resolution. The grid boundary problem is solved by checking 8 adjacent cells, not by coarsening the grid.
2. **Effective merge radius = ~22m** (1 grid cell + 1 neighbor). Safe for switchbacks (>22m at turns), catches GPS jitter (5-15m).
3. **In-memory neighbor lookup** — `_find_canonical_edge()` checks 8 neighboring grid cells against a set of `existing_keys` already tracked in `_update_heat_edges()`. No DB round-trip.
4. **One-time migration** — existing 180k+ edges need neighbor-merge pass. Find pairs within 1 grid cell of each other, merge counts, redirect contributors, delete duplicates.
5. **Near-real-time** — the neighbor check applies during ingestion, so new activities immediately merge into existing edges when GPS jitter lands in an adjacent cell.

### Why ~22m effective radius? (5 Whys validation)

| Effective radius | Merge probability (same road, ±10m jitter) | Risk for single tracks | Verdict |
|---|---|---|---|
| 11m (current, no neighbor) | ~9% | None | Too fine — most traces don't merge |
| **~22m (11m grid + neighbor)** | **~80%** | **None (switchbacks >22m)** | **Right for single tracks** |
| ~44m (22m grid + neighbor) | >95% | Could merge tight switchbacks | Too wide for VTT |
| ~60m (30m grid + neighbor) | >98% | Merges parallel trails | Way too coarse |

### First Principles Validation

| Assumption | Valid? | Action |
|---|---|---|
| Close endpoints = same road | Yes for our terrain (rural, few bridges/tunnels) | Keep neighbor-based approach |
| Direction matters for merge | No (already direction-agnostic keys) | No change |
| Must merge at ingestion | Yes (simpler than display-time aggregation) | Keep ingestion-time merge |
| Edges = endpoint pairs | Pragmatic 80/20 vs OSM map-matching | Keep. OSM matching = Phase 2 if needed |
| Fixed merge radius | Actually adaptive — only merges when neighbor EXISTS | No change needed |

**Future upgrade path (Phase 2, if needed):** OSM map-matching (Hidden Markov Model) to snap GPS traces to actual road segments. Only justified if Phase 1 shows wrong merges in complex urban areas.

## Implementation Plan

### Tasks

- [x] Task 1: Add helper functions to `ingest.py`
  - File: `backend/app/services/ingest.py`
  - Action: Add `_bearing()`, `_bearing_diff()`, `_find_canonical_edge()` functions after `_edge_key()` (line 105)
  - Notes: `_GRID_STEP = 0.0001`. `_find_canonical_edge` takes `(sport, p1, p2, existing_keys, existing_endpoints)`, returns canonical edge_key. Skips zero-length edges. Sorts candidates for determinism. Bearing check ±30°.

- [x] Task 2: Add bbox-scoped key loading function
  - File: `backend/app/services/ingest.py`
  - Action: Add `_load_existing_keys(sport, bbox)` that queries heat_edges within bbox + 0.0003° buffer (3x grid step = ~33m). Returns `(existing_keys: set[str], existing_endpoints: dict[str, tuple[tuple[float,float], tuple[float,float]]])`. Endpoints stored as `((lat1, lon1), (lat2, lon2))` matching `_snap()` format.
  - Notes: Use raw SQL: `SELECT edge_key, ST_Y(ST_StartPoint(geometry)) as lat1, ST_X(ST_StartPoint(geometry)) as lon1, ST_Y(ST_EndPoint(geometry)) as lat2, ST_X(ST_EndPoint(geometry)) as lon2 FROM heat_edges WHERE sport = :sport AND geometry && ST_MakeEnvelope(:xmin, :ymin, :xmax, :ymax, 4326)`. Build endpoints dict as `{key: ((lat1, lon1), (lat2, lon2))}`. Note: ST_Y=lat, ST_X=lon.

- [x] Task 3: Modify `_update_heat_edges()` to use neighbor lookup
  - File: `backend/app/services/ingest.py`
  - Action: In `_update_heat_edges()`:
    1. Compute activity bbox from snapped points + 0.001° buffer
    2. Call `_load_existing_keys(sport, bbox)` to get existing_keys + existing_endpoints
    3. In the edge loop (line 142), replace `key = _edge_key(sport, p1, p2)` with `key = _find_canonical_edge(sport, p1, p2, existing_keys, existing_endpoints)`
    4. After each UPSERT, update `existing_keys.add(key)` and `existing_endpoints[key] = (a_canonical, b_canonical)`
    5. When using a neighbor key, use the NEIGHBOR's endpoints for the UPSERT (so the geometry stays consistent with the edge_key)
  - Notes: The UPSERT SQL stays the same — `ON CONFLICT (edge_key) DO UPDATE` naturally handles the merge. The key difference is which `edge_key` is used.

- [x] Task 4: Update existing tests
  - File: `backend/tests/test_ingest_direction.py`
  - Action: Add tests for neighbor merging:
    1. Two traces on same road with 10m offset → same edge_key, pass_count=2
    2. Two traces with INDEPENDENT jitter (p1 shifts north, p2 shifts east) → still merge
    3. Two traces on different roads (>30° bearing diff) → separate edges
    4. Zero-length edge → no neighbor check, no crash
    5. Within-activity dedup still works with neighbor keys
    6. Switchback test: two edges at specific coords where turn separation >22m → separate edges (use coords: `[3.87, 43.61] → [3.8701, 43.6102]` and `[3.87, 43.6104] → [3.8701, 43.6102]` — ~44m separation at p1)
    7. Merge logging: verify `logger.info` includes merge count
  - Notes: Use sport `"test_cluster"` to isolate from existing tests

- [x] Task 5: Create migration script
  - File: `backend/app/cli/migrate_edge_clustering.py` (NEW)
  - Action: Create CLI script with `--dry-run` and `--batch-size` flags:
    1. Load all edges per sport
    2. For each edge, find neighbors (same 8-cell + bearing logic)
    3. Build merge groups with union-find, cap at 10
    4. For each group: pick canonical (highest pass_count), sum counts, redirect contributors with `ON CONFLICT DO NOTHING`, delete old edges, recompute user_count
    5. Batch commits every N groups
  - Notes: Log progress, total merged, skipped large groups. Idempotent — safe to re-run.

- [ ] Task 6: Run migration on local DB and validate
  - Action: `docker compose exec backend python -m app.cli.migrate_edge_clustering --dry-run` then without `--dry-run`
  - Notes: Verify edge count reduction (~30-40% expected on busy roads). Check heatmap visually for wrong merges. Verify pass_count and user_count are correct on merged edges.

- [ ] Task 7: Run migration on production DB
  - Action: Deploy new code, then run migration via Cloud Run exec or connect to prod DB
  - Notes: Run `--dry-run` first. Backup DB before running. Monitor edge count and heatmap.

### Acceptance Criteria

- [ ] AC 1: Given two GPS traces on the same road offset by 10m, when both are ingested, then they produce a single edge with pass_count=2 and user_count=1 (or 2 if different users).

- [ ] AC 2: Given two GPS traces on different roads that are 15m apart but have >30° bearing difference, when both are ingested, then they produce two separate edges.

- [ ] AC 3: Given a GPS trace with a switchback (same road, >22m separation at the turn), when ingested, then the two directions produce separate edges.

- [ ] AC 4: Given an existing edge in the DB and a new trace that lands in an adjacent grid cell on the same road, when ingested, then the new trace merges into the existing edge (pass_count incremented).

- [ ] AC 5: Given the migration script run with `--dry-run`, when executed on the current 180k-edge DB, then it reports merge groups without modifying data and logs expected reduction.

- [ ] AC 6: Given the migration script run without `--dry-run`, when executed, then total edge count decreases by 20-40% and no edge has pass_count=0 or user_count=0.

- [ ] AC 7: Given a merge group of >10 edges during migration, when encountered, then the group is skipped and logged as a warning.

- [ ] AC 8: Given the heatmap popup on a busy road after migration, when clicked, then it shows correct aggregated "N contributeurs · M passages" (not "1 passage" per parallel trace).

## Additional Context

### Dependencies

- No new libraries needed (all Python stdlib + existing SQLAlchemy/PostGIS)
- Depends on PostGIS `geometry` column on heat_edges (already exists)
- Spatial index on geometry recommended for bbox queries (verify with `\di` in psql)

### Testing Strategy

**Unit tests** (Task 4):
- Neighbor merge: 10m offset → same edge
- Bearing filter: different roads → separate edges
- Zero-length: no crash
- Determinism: same input always produces same edge_key

**Integration test** (existing E2E):
- Existing `test_ingest_direction.py` tests still pass (no regression)
- Heatmap trails endpoint returns correct data

**Manual validation** (Tasks 6-7):
- Visual: heatmap on Le Lez (Montpellier) shows merged edges instead of parallel spaghetti
- Popup: correct passage counts on busy roads
- Routing: no degradation on client-graph A*

### Risk Mitigations

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | Wrong merge (different roads) | HIGH | Bearing check ±30° with cos(lat) correction (F2, F6 fixed) |
| 2 | Slow import (loading all keys) | MEDIUM | Bbox-scoped key loading (activity extent + 0.0003° buffer — 3x grid step) |
| 3 | Migration crash (partial) | HIGH | Batched (1000/commit) + idempotent + edge_merge_log for rollback (F5 fixed) |
| 4 | Routing degradation | LOW | None — routing grid (110m) already coarser. Validate post-migration (F14) |
| 5 | Wrong user_count | MEDIUM | `ON CONFLICT DO NOTHING` in migration, recompute |
| 6 | Non-deterministic multi-neighbor | MEDIUM | Sort candidates by key (F10: acceptable — frozen position is still within 11m of road) |
| 7 | `existing_keys` stale within activity | HIGH | Update set + endpoints dict after each edge insertion |
| 8 | Zero-length edge crash | LOW | Skip neighbor check for p1==p2 edges |
| 9 | Bbox boundary miss | MEDIUM | 0.0003° buffer (3x grid step = ~33m) around activity bbox (F9 fixed) |
| 10 | Chain-reaction in migration | MEDIUM | No cap — process all groups. Log groups >20 as WARNING (F4 fixed) |
| 11 | Concurrent imports create duplicates | MEDIUM | Accept: rare with single-instance Cloud Run (min=0, max=1). ON CONFLICT UPSERT handles count correctness. Neighbor check on next import will merge. (F3 acknowledged) |
| 12 | No production observability | MEDIUM | Log merge count per import: `logger.info("Edges: %d new, %d merged", new, merged)` (F12 fixed) |
| 13 | Elevation merge undefined | MEDIUM | Keep ele_delta_m + slope_grade from highest pass_count edge (F8 fixed) |

### Notes

- **Expected edge reduction:** ~30-40% on busy roads (Le Lez, Pic Saint-Loup loops). 0% on isolated single tracks.
- **Performance impact:** the bbox query + 8 set lookups per edge adds ~200ms per activity import. Negligible vs the current Strava API rate limit (~1s/activity).
- **Rollback:** if wrong merges are detected, restore from DB backup. The migration is destructive (deletes old edges). Always run `--dry-run` first.
- **Phase 2 (future):** OSM map-matching for urban areas with complex road networks. Only if Phase 1 validation shows issues.

### Algorithm Detail

**COORDINATE ORDER CONVENTION:** All tuples are `(lat, lon)` matching `_snap()` return format. `existing_endpoints` stores `((lat1, lon1), (lat2, lon2))` in canonical sorted order. When passing to PostGIS `ST_MakePoint`, swap to `(lon, lat)`.

```python
import math

_GRID_STEP = 0.0001  # ~11m (same as round(val, 4))

def _bearing(p1, p2):
    """Bearing in degrees (0-360) from p1 to p2. Tuples are (lat, lon).
    Applies cos(lat) correction for longitude at mid-latitude."""
    dlat = p2[0] - p1[0]
    cos_lat = math.cos(math.radians((p1[0] + p2[0]) / 2))
    dlon = (p2[1] - p1[1]) * cos_lat  # correct for latitude
    return math.degrees(math.atan2(dlon, dlat)) % 360

def _bearing_diff(a1, a2, b1, b2):
    """Minimum angle between two edges (0-180°). Handles reversed direction."""
    ba = _bearing(a1, a2)
    bb = _bearing(b1, b2)
    diff = abs(ba - bb) % 360
    return min(diff, 360 - diff, abs(diff - 180))  # also handles reverse direction

def _find_canonical_edge(sport, p1, p2, existing_keys, existing_endpoints):
    """Check if a nearby edge already exists via INDEPENDENT endpoint jitter.

    Searches the Cartesian product of 3x3 neighborhoods for EACH endpoint
    independently (up to 9*9=81 combinations), not the same offset for both.

    Effective merge radius: ~22m per endpoint (1 cell + 1 neighbor).
    Only merges if bearing difference ≤ 30°.
    """
    # Skip for zero-length edges
    if p1 == p2:
        return _edge_key(sport, p1, p2)

    key = _edge_key(sport, p1, p2)
    if key in existing_keys:
        return key

    # Independent per-endpoint neighbor search (Cartesian product)
    offsets = (-_GRID_STEP, 0, _GRID_STEP)
    candidates = []
    for d1lat in offsets:
        for d1lon in offsets:
            np1 = _snap(p1[0] + d1lat, p1[1] + d1lon)
            for d2lat in offsets:
                for d2lon in offsets:
                    if d1lat == 0 and d1lon == 0 and d2lat == 0 and d2lon == 0:
                        continue  # skip exact match (already checked)
                    np2 = _snap(p2[0] + d2lat, p2[1] + d2lon)
                    nkey = _edge_key(sport, np1, np2)
                    if nkey in existing_keys:
                        ep1, ep2 = existing_endpoints[nkey]
                        if _bearing_diff(p1, p2, ep1, ep2) <= 30:
                            candidates.append(nkey)

    if candidates:
        # Pick candidate with highest pass_count (best representative, consistent with migration).
        # existing_pass_counts dict is loaded alongside existing_keys.
        best = max(candidates, key=lambda k: existing_pass_counts.get(k, 0))
        return best

    return key  # genuinely new edge

# IMPORTANT: after each UPSERT in _update_heat_edges():
# existing_keys.add(final_key)
# existing_endpoints[final_key] = (a_canonical, b_canonical)  # (lat,lon) tuples
```

**Performance note on 81 combinations:** Each iteration is a set lookup (O(1)). 81 lookups per edge × 1000 edges/activity = 81k set lookups — takes <1ms total. No performance concern.

### Migration Strategy

One-time Python script (`backend/app/cli/migrate_edge_clustering.py`), batched and idempotent:

```
python -m app.cli.migrate_edge_clustering [--dry-run] [--batch-size=1000]
```

**Algorithm:**
1. Create `edge_merge_log` table: `(old_key TEXT, canonical_key TEXT, merged_at TIMESTAMPTZ DEFAULT NOW())`
2. Load all edges (edge_key, sport, endpoints, pass_count, ele_delta_m, slope_grade)
3. For each edge, compute neighbor keys (independent-endpoint search + bearing ±30°)
4. Build merge groups using union-find. Groups >20 get a WARNING log but are still processed.
5. **Diameter check:** after building each group, verify all members are within 1 grid step (~11m) of the canonical edge (not just any group member). Evict members that are >1 step from canonical — they were chained transitively and might be on a different road.
6. For each group, pick canonical edge (highest pass_count — same as ingestion-time selection)
6. In batches of 1000:
   a. Sum pass_count, forward_count, backward_count into canonical
   b. Elevation: keep ele_delta_m and slope_grade from the edge with highest pass_count (most representative GPS data)
   c. Redirect contributors: `INSERT INTO heat_edge_contributors SELECT :canonical, user_id_hash FROM heat_edge_contributors WHERE edge_key = :old ON CONFLICT DO NOTHING`
   d. Delete old contributors: `DELETE FROM heat_edge_contributors WHERE edge_key = :old`
   e. Log merge: `INSERT INTO edge_merge_log (old_key, canonical_key) VALUES (:old, :canonical)`
   f. Delete non-canonical edges: `DELETE FROM heat_edges WHERE edge_key = :old`
   g. Recompute user_count on canonical from contributors
   h. `db.commit()` + log progress
7. `--dry-run` mode: report merge groups, largest group sizes, estimated reduction — without modifying DB

**Idempotent:** before merging, check if `old_key` exists in heat_edges. If not (already merged in prior run), skip.

**Rollback:** take a `pg_dump` of `heat_edges` and `heat_edge_contributors` before running. The `edge_merge_log` is for audit/debugging (which edges got merged) but does NOT support programmatic undo (merged counts can't be split back). Full rollback = restore from dump.

**Observability:** log and print: total edges before/after, edges merged, groups processed, largest group size, groups >20 (with details).

**Expected result:** ~180k edges → estimated ~120-140k after merge (30-40% reduction on busy roads, 0% on isolated trails).
