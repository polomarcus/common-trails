---
title: 'Connected High-Precision Edge Graph'
slug: 'connected-hires-edge-graph'
created: '2026-03-24'
status: 'implementation-complete'
stepsCompleted: [1, 2, 3, 4]
tech_stack: [python, typescript, postgis, maplibre]
files_to_modify: [backend/app/services/ingest.py, backend/app/services/osm_enrich.py, frontend/lib/client-graph.ts, frontend/public/routing-worker.js, frontend/lib/__tests__/client-graph.test.ts, backend/app/cli/group_edges_osm.py, backend/app/cli/migrate_resegment.py, backend/tests/test_ingest_direction.py]
code_patterns: [grid-snap, vertex-key-matching, spatial-grid, direction-agnostic-keying, cardinal-neighbor-clustering]
test_patterns: [pytest, playwright, assert-based-frontend-tests]
---

# Tech-Spec: Connected High-Precision Edge Graph

**Created:** 2026-03-24

## Overview

### Problem Statement

Grid-snap edges at 4 decimal places (~11m resolution) create fragmented, disconnected heatmap segments. The frontend routing graph requires exact vertex key matching (`toFixed(4)`), so edges that are physically close (<5m apart) but round to different 4dp values become disconnected. This results in:
- 59,000 dead-end vertices in the routing graph
- 5.3% of edges fully isolated (both endpoints unconnected)
- Routing failures and ugly straight-line bridge detours (250m bridge radius)
- Visually fragmented heatmap with parallel short segments instead of continuous lines
- Heatmap accuracy of ~11m instead of the target 5-10m

### Solution

Increase edge precision from 4dp (~11m) to 5dp (~1.1m) across the full pipeline — backend snap, edge keys, frontend vertex keys. Tighten densification gap from 20m to 5m so every grid cell along a trace gets a point, ensuring edge connectivity. The backend already serves coordinates at 5dp in `graph_tiles.py`, so the core change is aligning `_snap()`, `_edge_key()`, and `vertexKey()` to 5dp, then re-ingesting all activities.

### Scope

**In Scope:**
- Change `_snap()` precision from 4dp to 5dp
- Change `_edge_key()` to use 5dp coordinates
- Change `_DENSIFY_MAX_GAP_M` from 20m to 5m
- Change frontend `vertexKey()` from `toFixed(4)` to `toFixed(5)`
- Update `group_edges_osm.py` snap precision to match
- Update neighbor clustering grid step and radius (±5 steps at 5dp = ~5.5m)
- Re-ingest all activities to rebuild edge graph at new precision
- Update existing tests for new coordinate precision

**Out of Scope:**
- HMM-based map matching (future improvement)
- Full OSM road network as routing base layer
- Changing the heatmap tile display layer (MVT)
- Changing the heatmap GeoJSON display endpoint
- Surface classification or elevation changes

## Context for Development

### Codebase Patterns

- **Dual-precision pipeline:** Backend stores geometry at full PostGIS precision, but `_snap()` rounds to 4dp for edge keying. `graph_tiles.py` serves coordinates at 5dp. Frontend `vertexKey()` rounds to 4dp — the bottleneck.
- **Direction-agnostic keying:** `_edge_key()` sorts endpoints so A→B and B→A share the same key.
- **Neighbor clustering:** `_find_canonical_edge()` searches cardinal neighbors (N/S/E/W, no diagonals) at ±1 grid step to merge near-duplicate edges within ~11m and ≤30° bearing difference. At 5dp, widen to ±5 steps (~5.5m) for same-device jitter merge.
- **Densification:** `_densify_coords()` interpolates points to ensure max gap. At 5dp with 5m gap, consecutive points after snap share endpoints → continuous edge chains.
- **Web worker:** `routing-worker.js` is a separate hand-maintained JS file (not compiled from TS). Must be updated independently.

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `backend/app/services/ingest.py:134-136` | `_snap()` — 4dp rounding → 5dp |
| `backend/app/services/ingest.py:139` | `_DENSIFY_MAX_GAP_M = 20.0` → 5.0 |
| `backend/app/services/ingest.py:569` | `_GRID_STEP = 0.0001` → 0.00001 |
| `backend/app/services/ingest.py:563-566` | `_edge_key()` — inherits snap precision |
| `backend/app/services/ingest.py:590-635` | `_find_canonical_edge()` — widen to ±5 steps |
| `backend/app/services/ingest.py:142-176` | `_densify_coords()` — docstring update only |
| `frontend/lib/client-graph.ts:199-201` | `vertexKey()` — `toFixed(4)` → `toFixed(5)` |
| `frontend/public/routing-worker.js:122-123` | `vertexKey()` JS copy — `toFixed(4)` → `toFixed(5)` |
| `backend/app/api/graph_tiles.py:130-138` | Already serves 5dp — no change needed |
| `backend/app/cli/group_edges_osm.py:22-28` | `_snap()` and `_edge_key()` — match 5dp |
| `backend/app/cli/migrate_resegment.py` | Re-ingestion pattern — reuse/update for this migration |
| `backend/app/cli/migrate_edge_clustering.py` | Imports `_GRID_STEP`, `_snap()` from ingest.py — auto-inherits changes, no modification needed |
| `backend/tests/test_ingest_direction.py` | Test coords must use Iceland (outside OSM coverage), 1 grid step apart |
| `frontend/lib/__tests__/client-graph.test.ts:53-55` | `vertexKey` assertions — update expected values |

### Technical Decisions

1. **5dp precision (~1.1m):** Well within the 5-10m accuracy target. 10x improvement over current 4dp.
2. **Densification fixed at 5m:** Distinguishes single tracks and parallel paths. Produces ~1M edges (6x current). Accept the edge count — if payload exceeds 3MB gzip, increase densify to 7m and re-measure.
3. **Neighbor clustering: ±5 grid steps at 5dp (~5.5m radius).** Same device's GPS jitter (2-5m) merges. Different devices create parallel edges — acceptable, represents real data. Cardinal offsets: 21 per endpoint (±5 on one axis, 0 on the other, + origin). 21×21−1 = 440 combos (21×21−1, excluding origin×origin already checked) per edge pair (was 24). Offsets tuple must be a module-level constant to avoid per-call allocation.
4. **Re-ingestion via `migrate_resegment.py` pattern:** pg_dump → truncate heat_edges + contributors → re-ingest 3,258 activities.
5. **`graph_tiles.py` already serves at 5dp** — no API change needed.
6. **`SPATIAL_GRID_SIZE` stays at 0.001** — spatial indexing for snap/bridge, not vertex precision.
7. **GPS gap guard stays at 500m** — signal losses should not be densified.
8. **Heatmap display and routing graph share the same `heat_edges` table.** The 5dp precision change improves both simultaneously. `/heatmap/trails` (display, K≥2) and `/routing/graph/{sport}/full.json` (routing, K≥1) read from the same edges. `group_edges_osm.py` post-processing improves both visual quality and routing connectivity.
9. **OSM grouping must re-run after migration** at 5dp precision.
10. **Success gates (revert to pg_dump if fail):** dead-ends < 30k, payload < 3MB gzip, Dijkstra p95 < 500ms.
11. **`routing.py` `_snap4()` stays at 4dp.** Only frontend client-side routing is used. Server-side routing in `routing.py` is legacy/fallback — no change needed.
12. **`osm_enrich.py` cache key** uses `round(lon, 4)` — update to 5dp to avoid cache collisions between 5dp-distinct points.
13. **OSM-grouped edge geometry:** `graph_tiles.py` reads raw PostGIS geometry via `ST_X(ST_StartPoint(...))`. After `group_edges_osm.py` re-keys edges to OSM geometry, the stored LINESTRING endpoints must be snapped to 5dp to match edge_key. The grouping script already writes geometry via `ST_MakeLine(ST_MakePoint(...))` with snapped coords — verify this uses 5dp.
14. **DFCI/trail edges:** Served by `graph_tiles.py` alongside heat_edges. Their endpoints come from separate import scripts with full-precision OSM coords. Frontend `vertexKey(toFixed(5))` will produce different keys than heat_edges for the same junction. The bridge logic (250m) handles this — DFCI/trail edges are already bridged today.
15. **Cache-bust strategy:** Add `?v={timestamp}` query param to `/routing/graph/{sport}/full.json` URL, or use build-time hash on `routing-worker.js` filename.

## Implementation Plan

### Phase 1: Fix Routing Connectivity (quick win)

_One constant change + re-ingest. Fixes fragmentation NOW. No frontend changes._

- [x] P1-Task 1: Tighten densification gap
  - File: `backend/app/services/ingest.py:139`
  - Action: Change `_DENSIFY_MAX_GAP_M` from `20.0` to `10.0`. Update docstring.
  - Notes: At 4dp (~11m grid), 10m densification ensures every grid cell along a trace gets a point. Consecutive snapped points are 0-1 cells apart → continuous chains. Edge count ~2x current (~340k). No precision change, no frontend change.

- [x] P1-Task 2: Add `--limit N` flag to migrate_resegment.py
  - File: `backend/app/cli/migrate_resegment.py`
  - Action: Add `--limit N` argument that appends `LIMIT :limit` to the activity query. Useful for dry-run measurements.

- [ ] P1-Task 3: Re-ingest all activities
  - Action: Run sequence:
    1. `pg_dump` backup of heat_edges + heat_edge_contributors
    2. `--limit 100` dry-run to verify edge count increase is ~2x (not higher)
    3. Full run: truncate + re-ingest 3,258 activities
    4. Run `group_edges_osm.py` post-processing
  - Notes: `migrate_resegment.py` calls `_update_heat_edges()` which inherits the new constant automatically. No code change needed beyond Task 2.

- [ ] P1-Task 4: Validate Phase 1 success
  - Action: Restart backend to clear `_trails_gz_cache` (300s TTL), or wait 5 minutes. Then measure:
    1. Dead-end vertex count. Target: <40k (was 59k). Run:
       ```sql
       WITH endpoints AS (
         SELECT round(ST_X(ST_StartPoint(geometry))::numeric, 4) as x,
                round(ST_Y(ST_StartPoint(geometry))::numeric, 4) as y FROM heat_edges
         UNION ALL
         SELECT round(ST_X(ST_EndPoint(geometry))::numeric, 4),
                round(ST_Y(ST_EndPoint(geometry))::numeric, 4) FROM heat_edges
       )
       SELECT COUNT(*) FROM (SELECT x, y, COUNT(*) as n FROM endpoints GROUP BY x, y HAVING COUNT(*) = 1) dead;
       ```
    2. Full-graph payload size: `curl -s http://localhost:8787/routing/graph/gravel/full.json -H 'Accept-Encoding: gzip' | wc -c`. Should stay well under 3MB.
    3. Test routing in frontend: drag waypoints on well-traveled roads, verify smooth routes without straight-line jumps.
    4. Count bridge ratio on a 10km test route — should be lower than before.
  - Note: Phase 2 targets <30k dead-ends — Phase 1 is a partial fix for connectivity only.

### Phase 2: 5dp Precision for Heatmap Accuracy

_Full precision upgrade. Requires frontend changes, test updates, and second re-ingest._

- [x] P2-Task 1: Update backend precision constants
  - File: `backend/app/services/ingest.py`
  - Action: Change `_snap()` from `round(lat, 4)` to `round(lat, 5)`. Update docstring.
  - Action: Change `_DENSIFY_MAX_GAP_M` from `10.0` to `5.0`. Update docstring ("~5x grid cell").
  - Action: Change `_GRID_STEP` from `0.0001` to `0.00001`.
  - Action: **Keep `_load_existing_keys` buffer at `0.0003` (line ~651) unchanged.** It's a bbox expansion for the SQL query that loads candidate edges into a `set`. The actual matching in `_find_canonical_edge()` uses exact key lookups — overbroad loading is safe (more memory, not incorrect). The buffer covers the new ±5 step radius (0.00005) with margin. Do NOT scale it down.
  - Action: Update stale "4dp" inline comments to "5dp". Find them with: `grep -n "4dp\|4 decimal" backend/app/services/ingest.py`
  - Action: **Verification:** After all changes, grep codebase for `round(lat, 4)`, `round(lon, 4)`, `toFixed(4)` — no remaining references except `routing.py` (intentionally unchanged, only frontend routing is used).
  - Notes: `_edge_key()` inherits precision from `_snap()` output — no change needed to that function itself. OSM-matched edges (Phase 2a, lines ~832-847) bypass `_find_canonical_edge` — this is correct because OSM segment endpoints are deterministic. No change needed there. Rounding is how traces from different devices merge onto the same edge — 5dp (~1.1m) is the sweet spot between merge quality and positional accuracy.

- [x] P2-Task 2: Widen neighbor clustering radius
  - File: `backend/app/services/ingest.py:590-635`
  - Action: In `_find_canonical_edge()`, replace inline `_CARDINAL_OFFSETS` tuple with a **module-level constant**. Generate as:
    ```python
    _s = _GRID_STEP  # 0.00001
    _CARDINAL_OFFSETS = tuple(
        {(0, 0)}
        | {(i * _s, 0) for i in range(-5, 6) if i != 0}  # 10 lat-axis
        | {(0, i * _s) for i in range(-5, 6) if i != 0}  # 10 lon-axis
    )  # = 21 offsets per endpoint → 21×21−1 = 440 combos per edge pair
    ```
  - Notes: Module-level to avoid per-call allocation. Bearing check at 30° rejects most combos quickly. Current code has 5 offsets (±1 step) → 24 combos. New code: 21 offsets (±5 steps, axis-aligned) → 440 combos.

- [x] P2-Task 3: Update `group_edges_osm.py` precision
  - File: `backend/app/cli/group_edges_osm.py`
  - Action: Change `_snap()` from `round(lat, 4)` to `round(lat, 5)`. Same for `_edge_key()` if it has its own copy. Verify that the geometry written via `ST_MakeLine(ST_MakePoint(...))` uses 5dp-snapped coordinates so endpoints match edge_key.
  - Notes: The `ST_DWithin` radius (0.00035 = ~35m) stays the same — it's for candidate search, not snap precision. The UPDATE path (line ~117-128) writes raw OSM geometry — acceptable because `graph_tiles.py` re-rounds on read. The INSERT path (line ~155+) uses snapped coords — verify 5dp.

- [x] P2-Task 3b: Update `osm_enrich.py` cache precision
  - File: `backend/app/services/osm_enrich.py:112`
  - Action: Change `cache_key = (round(lon, 4), round(lat, 4))` to `round(lon, 5), round(lat, 5)`.
  - Notes: Prevents cache collisions between points that are distinct at 5dp.

- [x] P2-Task 4: Update frontend vertex key precision
  - File: `frontend/lib/client-graph.ts:199-201`
  - Action: Change `vertexKey()` from `toFixed(4)` to `toFixed(5)`. Update comment from "4 decimal places ≈ 11m" to "5 decimal places ≈ 1.1m".
  - File: `frontend/public/routing-worker.js:122-123`
  - Action: Same change — `toFixed(4)` → `toFixed(5)`.
  - Notes: `parseKey()` uses `parseFloat()` which handles any precision — no change needed.

- [x] P2-Task 5: Update backend tests
  - File: `backend/tests/test_ingest_direction.py`
  - Action: Move test coordinates to Iceland (lon: -18.x, lat: 65.x) to avoid interference from OSM road data in local dev DB. Use coordinates 1 grid step apart (0.00001° ≈ 1.1m) for single-edge tests. Only change `TestUpdateHeatEdgesDirection`, `TestGetHeatEdgesPublicDirectionFields`, `TestNeighborCellClustering`, `TestTimeFilteredHeatmap` classes. Leave `TestResegmentation`, `TestOsmMatch*` classes untouched.
  - Notes: Tests were already fragile due to OSM data in dev DB — this fixes a pre-existing issue.

- [x] P2-Task 6: Update frontend tests
  - File: `frontend/lib/__tests__/client-graph.test.ts`
  - Action: Update `vertexKey` assertions from 4dp expectations to 5dp. E.g., `'3.8712,43.6199'` → `'3.87123,43.61988'` (verify exact rounding).
  - Notes: Other test coordinates used in `mergeEdges`/`bridgeNearbyVertices` tests should still work since `parseKey` handles any precision.

- [ ] P2-Task 7: Dry-run measurement (GO/NO-GO gate)
  - Action: Run `migrate_resegment.py --limit 100` to re-ingest ~100 activities at 5dp. Measure:
    1. Edge count per activity (extrapolate to 3,258 activities)
    2. Serialize to JSON, gzip, measure payload size
    3. If extrapolated payload >3MB gzip → increase `_DENSIFY_MAX_GAP_M` to 7m and re-measure. Only consider z14 tile loading as a Phase 3 if 7m still exceeds 3MB.
    4. If extrapolated payload <3MB → proceed to P2-Task 8
  - Notes: This catches the edge count explosion risk before committing to full migration.

- [ ] P2-Task 8: Full re-ingest at 5dp
  - Action: `migrate_resegment.py` calls `_update_heat_edges()` which inherits new constants. Run:
    1. `pg_dump` backup of heat_edges + heat_edge_contributors
    2. Full run: truncate + re-ingest 3,258 activities
    3. Run `group_edges_osm.py` post-processing at 5dp
  - Notes: NOT crash-safe. If interrupted, restore from pg_dump.

- [ ] P2-Task 9: Validate Phase 2 success gates
  - Action: Restart backend to clear caches. Then measure and verify:
    1. Dead-end vertex count <30k. Run:
       ```sql
       WITH endpoints AS (
         SELECT round(ST_X(ST_StartPoint(geometry))::numeric, 5) as x,
                round(ST_Y(ST_StartPoint(geometry))::numeric, 5) as y FROM heat_edges
         UNION ALL
         SELECT round(ST_X(ST_EndPoint(geometry))::numeric, 5),
                round(ST_Y(ST_EndPoint(geometry))::numeric, 5) FROM heat_edges
       )
       SELECT COUNT(*) FROM (SELECT x, y, COUNT(*) as n FROM endpoints GROUP BY x, y HAVING COUNT(*) = 1) dead;
       ```
    2. Full-graph gzip payload <3MB: `curl -s http://localhost:8787/routing/graph/gravel/full.json -H 'Accept-Encoding: gzip' | wc -c`. If fails → increase densify to 7m and re-measure.
    3. Dijkstra p95 latency <500ms on web worker (check browser console timing)
    4. Heatmap accuracy: hover tooltip shows edges at ~1m precision, not 11m grid artifacts
  - Notes: Also benchmark re-ingestion time from dry-run. If >30min for 3,258 activities, consider optimizing `_find_canonical_edge` (e.g. frozenset for offsets).

### Acceptance Criteria

- [ ] AC 1: Given 100 re-ingested activities at 5dp, when querying consecutive edge pairs within each trace, then >95% share a vertex (endpoint B of edge N = endpoint A of edge N+1 at 5dp).
- [ ] AC 2: Given two traces from the same user on the same road with 3m GPS jitter, when ingested, then the neighbor clustering (±5 steps) merges them into the same edge with pass_count=2.
- [ ] AC 3: Given two traces from different users on the same road with 8m offset, when ingested, then they create parallel edges (not merged) — both visible in the routing graph.
- [ ] AC 4: Given the full re-ingested edge graph, when counting dead-end vertices (endpoints appearing only once), then the count is <30,000 (down from 59,000).
- [ ] AC 5: Given the full routing graph served via `/routing/graph/gravel/full.json`, when gzip-compressed, then the payload is <3MB.
- [ ] AC 6: Given a route request between two points 10km apart on a well-traveled road, when computed via Dijkstra on the web worker, then it completes in <500ms with bridge ratio <10%.
- [ ] AC 7: Given the frontend loads the routing graph, when `vertexKey()` is called with coordinates from the backend, then vertex keys match at 5dp precision and edges connect into continuous chains.
- [ ] AC 8: Given existing backend tests with Iceland coordinates, when `pytest tests/test_ingest_direction.py` runs, then all tests pass.
- [ ] AC 9: Given the heatmap display endpoint `/heatmap/trails`, when fetched, then edges still render as continuous lines (post-processing via `group_edges_osm.py` handles visual grouping).

## Additional Context

### Dependencies

- No new external dependencies
- Requires Docker Compose running (PostGIS + backend) for migration
- `group_edges_osm.py` must run after re-ingestion

### Testing Strategy

- **Unit tests (backend):** Update `test_ingest_direction.py` with Iceland coords at 5dp spacing. Verify snap, edge_key, densification, neighbor clustering all work at new precision.
- **Unit tests (frontend):** Update `client-graph.test.ts` vertexKey assertions to 5dp.
- **Integration:** Run full re-ingestion on dev DB, verify edge count and connectivity via SQL queries.
- **Manual:** Open map at http://localhost:3787/map, enter route mode, drag waypoints — verify smooth routes without straight-line jumps. Hover heatmap edges — verify tooltip shows correct data.
- **Regression:** Existing Playwright E2E tests must still pass.

### Notes

- **Pre-mortem risks identified:**
  - Payload size may exceed 3MB → fallback: increase densify to 7m, z14 tiles only as Phase 3 last resort
  - Neighbor clustering at ±5 steps creates 441 combos (was 25) → acceptable, bearing check rejects most quickly
  - `routing-worker.js` browser cache → cache-bust via filename hash
  - Pass count corruption on re-ingest → pg_dump + count verification
- **Parallel edges are a feature, not a bug:** Different devices on the same road create parallel edges. This preserves real spatial data and distinguishes single tracks from wide roads.
- **Future improvement:** HMM-based map matching would further improve accuracy by considering the full trace context, not just individual point-to-grid-cell snapping.
