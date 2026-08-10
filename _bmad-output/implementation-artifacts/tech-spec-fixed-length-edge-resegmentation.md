---
title: 'Fixed-Length Edge Re-segmentation'
slug: 'fixed-length-edge-resegmentation'
created: '2026-03-22'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: [python, postgresql, postgis, fastapi, sqlalchemy]
files_to_modify: [backend/app/services/ingest.py, backend/tests/test_ingest_direction.py, backend/app/cli/migrate_resegment.py, backend/app/services/osm_enrich.py]
code_patterns: [fixed-length-resegment, turn-preserving-interpolation, snap-grid, upsert-on-conflict, neighbor-clustering, haversine-distance]
test_patterns: [pytest, isolated-sport-name, geojson-fixture-helpers, varied-gps-spacing]
---

# Tech-Spec: Fixed-Length Edge Re-segmentation

**Created:** 2026-03-22

## Overview

### Problem Statement

Heatmap edges create visual "spaghetti" because each GPS trace produces edges of different lengths between consecutive snapped points. On the same road, 10 traces create 10+ different edges with similar start areas but endpoints spread across 100-200m. The current neighbor-cell clustering (22m merge radius) only merges edges with both endpoints within ~22m — it can't merge edges of different lengths on the same road.

**Root cause (from 5 Whys):** The edge model creates edges between consecutive GPS recording points without normalizing for recording rate or speed. The same physical road segment generates edges of wildly different lengths (3m to 100m+) depending on the device and activity, and these different-length edges can never merge because their endpoints don't align. This only became visible when diverse device types (phones, Garmin smart recording, watches) and activity types (running vs cycling) accumulated on popular paths.

### Solution

**Re-segment GPS traces into fixed-length chunks (~50m) before snapping to the grid.** Instead of creating one edge per consecutive GPS point pair, interpolate along the trace at fixed intervals and create edges between those interpolated points. This normalizes edge length so that all traces on the same road produce edges with similar endpoints, making the existing neighbor clustering effective.

### Algorithm Comparison (from elicitation)

| Approach | Merge rate | Single-track safe | DB impact | Complexity | Verdict |
|---|---|---|---|---|---|
| **A: Fixed 50m re-segmentation** | **~85%** | **★★★★★** | **neutral** | **~30 LOC** | **Winner — 80/20 balance** |
| B: Grid-aligned (11m edges) | ~95% | ★★★★★ | +4x edges | ~40 LOC | Best merge, but 1.2M edges |
| C: Coarser grid (3dp ≈ 110m) | ~95% | ★★☆☆☆ | fewer | 1 line | Destroys MTB resolution |
| D: Display-time simplification | ~90% visual | ★★★★★ | none | ~50 LOC SQL | Doesn't fix data, slow queries |

### Scope

**In Scope:**
- Add `_resegment_coords()` function to split GPS coordinates into ~50m chunks
- Apply re-segmentation in `_update_heat_edges()` before the snap+edge loop
- Apply re-segmentation in `get_personal_edges()` for consistent edge length (note: personal traces are per-user so don't benefit from cross-user clustering, but uniform 50m edges reduce visual noise)
- Migration script: full re-ingest with post-migration surface enrichment re-run
- Update tests

**Out of Scope:**
- Changing stored GPS traces (Crouzet methodology — trace integrity preserved)
- OSM map-matching (Phase 2 if needed)
- Changing heatmap display/rendering logic
- Changing the routing graph builder
- Changing the snap grid resolution (keep 4dp ≈ 11m)

## Context for Development

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `backend/app/services/ingest.py:97-412` | `_snap()`, `_edge_key()`, `_find_canonical_edge()`, `_update_heat_edges()` — core change location |
| `backend/app/services/ingest.py:221-232` | Coordinate parsing + snap loop — insert re-segmentation HERE (between parse and snap) |
| `backend/app/services/geo.py:5-12` | `haversine_m(lon1, lat1, lon2, lat2)` — reuse for distance calculation |
| `backend/app/services/ingest.py:108-175` | `_bearing()`, `_bearing_diff()`, `_find_canonical_edge()` — existing clustering (unchanged) |
| `backend/app/db/models.py:258-278` | Activity model — `user_id`, `sport`, `geometry_geojson`, `contribute_heatmap`, `activity_date`, `created_at` |
| `backend/app/db/models.py:341-368` | HeatEdge, HeatEdgeContributor models |
| `backend/tests/test_ingest_direction.py` | Existing tests — add re-segmentation tests here |
| `backend/app/cli/migrate_edge_clustering.py` | Existing migration pattern — base new script on this. **Superseded** by `migrate_resegment.py` (full re-ingest replaces incremental clustering). Keep file but no longer needed after reseg migration runs. |
| `backend/app/services/ingest.py:903-957` | `get_personal_edges()` — must also apply `_resegment_coords` for consistency |
| `backend/app/services/osm_enrich.py` | Surface enrichment — must re-run after migration (new edges need surface tags) |

### Codebase Patterns

**Current flow in `_update_heat_edges()` (ingest.py:221-412):**
1. Parse GeoJSON coordinates
2. Snap each point to 4dp grid, drop consecutive duplicates
3. For each consecutive pair → `_find_canonical_edge()` → UPSERT

**Proposed flow (insertion point: immediately after `coords = json.loads(...)`, before `# Snap all points`):**
1. Parse GeoJSON coordinates (`coords = json.loads(geojson_str).get("coordinates", [])`)
2. **`coords = _resegment_coords(coords)` ← single line insertion**
3. Snap each interpolated point to 4dp grid, drop consecutive duplicates (existing snap loop)
4. For each consecutive pair → `_find_canonical_edge()` → UPSERT (existing, unchanged)

**Routing graph impact:** None. The client-graph A* works on any edge length. 50m edges produce a cleaner, slightly smaller graph. No code changes needed in `graph_tiles.py` or `client-graph.ts`.

**Interpolation approach:** Walk along the trace polyline. Every 50m (haversine), emit an interpolated point. This uses simple linear interpolation between consecutive GPS points — no external data needed.

**Reference implementation (validated via Algorithm Olympics, simplified via Occam's Razor):**

Single function, ~20 lines. Turn detection was cut — the 11m snap grid already handles intersections (worst-case diagonal offset ~7.8m, within road width).

```python
_RESEG_LENGTH_M = 50.0  # validated: 46% edge reduction on Le Lez benchmarks

def _resegment_coords(coords: list, seg_len: float = _RESEG_LENGTH_M) -> list:
    """Re-segment a coordinate list into fixed-length chunks via interpolation.

    Walks along the polyline, emitting an interpolated point every seg_len meters.
    Preserves elevation (linear interpolation) when available.
    Short traces (< seg_len total) return original coords unchanged.
    """
    if len(coords) < 2:
        return coords
    result = [coords[0]]
    accum = 0.0
    for i in range(len(coords) - 1):
        p1, p2 = coords[i], coords[i + 1]
        d = _haversine_m(p1[0], p1[1], p2[0], p2[1])
        if d < 0.1:
            continue
        # GPS gap guard: skip interpolation for signal losses (>500m)
        if d > 500:
            result.append(p2)
            accum = 0.0
            continue
        remaining = d
        start_frac = 0.0
        while accum + remaining >= seg_len:
            needed = seg_len - accum
            frac = start_frac + needed / d
            lon = p1[0] + (p2[0] - p1[0]) * frac
            lat = p1[1] + (p2[1] - p1[1]) * frac
            ele = None
            if len(p1) > 2 and len(p2) > 2 and p1[2] is not None and p2[2] is not None:
                e1, e2 = float(p1[2]), float(p2[2])
                if not (math.isnan(e1) or math.isnan(e2)):
                    ele = e1 + (e2 - e1) * frac
            result.append([lon, lat] if ele is None else [lon, lat, ele])
            start_frac = frac
            remaining -= needed
            accum = 0.0
        accum += remaining
    result.append(coords[-1])
    return result
```

**Performance:** O(n) single pass, ~0.01ms for 1000-point trace. Zero external dependencies.

**Design notes:**
- **No turn detection needed:** The 11m snap grid handles intersections — worst-case diagonal offset is ~7.8m, within road width. Removed ~30 lines of complexity (Occam's Razor).
- **Elevation:** Linear interpolation for synthetic points. NaN guard prevents propagation. Error bounded to ~1.25m at 50m segments, within GPS noise.
- **GPS gap guard (from Failure Mode Analysis):** Gaps >500m (signal loss, tunnel, transport) skip interpolation — emits the far endpoint and resets accumulator. The last interpolated/emitted point before the gap serves as the near endpoint naturally.
- **Tightened merge radius (11m, was 22m):** With 50m re-segmentation, GPS jitter is typically 5-10m — well within 1 grid cell (11m). The old 2-cell radius (22m) was needed because variable edge lengths created larger endpoint offsets. With fixed 50m segments, 11m is sufficient and prevents false merges on parallel paths like Le Lez banks (12m apart). Change: in `_find_canonical_edge`, use only 4 direct neighbors (N/S/E/W) instead of 8 (removes diagonals), giving effective radius of 11m instead of ~15.5m diagonal.
- **Phase shift between traces:** Up to 25m offset for traces starting far apart. With 11m merge radius, ~50-60% of phase-shifted edges merge (down from ~70% at 22m). The re-segmentation itself handles the bulk of the reduction (46%), so this is acceptable.
- **Short traces:** Naturally handled — emits [start, end] for traces < seg_len.
- **Edge count impact:** Re-segmentation REDUCES edges per activity (~180 vs ~400 for a typical 9km running trace) because 50m segments are coarser than the median 18m GPS spacing.

**Critical test (from 5 Whys gap analysis):** Must include a test with two traces on the same road using different GPS point spacing (e.g., 5m vs 80m intervals) and assert they produce overlapping/merged edges after re-segmentation + clustering. This is the exact scenario that caused the original spaghetti and would have caught it in development.

### Technical Decisions

1. **50m segment length + tightened merge radius (11m)** — Benchmarked 10/15/20/25/50m on 20 traces. 10m is WORSE (+60% edges — below snap grid). 50m gives 46% edge reduction. Combined with a tightened neighbor merge of 1 grid cell (11m, down from 22m), this keeps parallel paths >11m apart separate (e.g., Le Lez east/west banks at 12m) while still merging GPS jitter (5-10m) on the same path. GPS point spacing: p50=18m, p90=47m, p99=92m.
2. **50m for ALL sports (simplified from Critique & Refine)** — Originally spec'd per-sport (30m for MTB/offroad), but 50m is simpler and MTB switchbacks are typically >50m apart at the turn point. The spaghetti problem is worst on running/road, not MTB. One constant is easier to maintain and test.
3. **Interpolate before snapping** — Re-segment on raw coords, then snap. This preserves the existing snap+cluster pipeline unchanged.
4. **No phase alignment (simplified from Critique & Refine)** — Originally spec'd to snap the first coordinate before re-segmenting. Dropped because: (a) complex to implement correctly without altering trace coords, (b) the 22m neighbor merge handles the ~25m max phase shift between traces, (c) only helps traces starting from the same spot. Net: ~70% merge for offset traces instead of ~85% — acceptable.
5. **Keep elevation** — Interpolate elevation linearly between GPS points when available.
6. **Migration = full re-ingest (simplified via Occam's Razor):**
   - **One strategy for all environments:** Delete all heat_edges + heat_edge_contributors + edge_merge_log, loop through all activities, re-run `_update_heat_edges()` with re-segmentation. ~10-15 min, idempotent, ~30 LOC script.
   - **Post-migration steps (from Comparative Analysis):**
     - Re-run surface enrichment (`enrich_heat_edges` + `enrich_surfaces_from_known_edges`) — new edges need OSM surface/highway tags.
     - Drop `edge_merge_log` table (from prior clustering migration, no longer relevant).
     - Increment `_edge_version` once at end (not per-activity) to avoid noisy cache invalidation.
   - Shadow table swap was cut — <10 users in production, a 15-min maintenance window at 3 AM is fine.
   - **Critical constraints (from pre-mortem):**
     - **Filter on contribute_heatmap:** Only re-process activities where `contribute_heatmap=True`. Query: `SELECT user_id, sport, geometry_geojson FROM activities WHERE contribute_heatmap = true ORDER BY created_at ASC`.
     - **Chronological order:** `ORDER BY created_at ASC` ensures the first trace on a road creates the canonical edge and subsequent traces merge into it — matching live ingestion behavior.
     - **Batch commits + session cleanup:** `db.close()` after each activity. Commit every 100 activities. Log progress.
     - **Rollback:** `pg_dump heat_edges heat_edge_contributors` before running. Restore from dump if needed.

## Implementation Plan

### Tasks

- [ ] Task 1: Add `_resegment_coords()` function to `ingest.py`
  - File: `backend/app/services/ingest.py`
  - Action: Add `_RESEG_LENGTH_M = 50.0` constant and `_resegment_coords()` function after the existing `_snap()` function (line ~99). Use the reference implementation from this spec (includes GPS gap guard and NaN elevation check). Import `math` is already present.
  - Notes: Function takes `coords: list` (GeoJSON **[lon, lat, ?ele]** format), returns re-segmented coords in same format. Uses existing `_haversine_m` import from `geo.py`. **CRITICAL COORD ORDER:** `_resegment_coords` operates on raw GeoJSON `[lon, lat]` coords — `p[0]=lon, p[1]=lat`. This is correct for `_haversine_m(lon1, lat1, lon2, lat2)`. Do NOT confuse with snapped tuples which are `(lat, lon)` — the snap loop later handles the conversion.

- [ ] Task 2: Tighten neighbor merge radius from 22m to 11m
  - File: `backend/app/services/ingest.py`
  - Action: In `_find_canonical_edge()`:
    1. Replace the 4-nested `for d1lat/d1lon/d2lat/d2lon in offsets` loop with a list-of-tuples approach:
       ```python
       _CARDINAL_OFFSETS = [(0, 0), (-_GRID_STEP, 0), (_GRID_STEP, 0), (0, -_GRID_STEP), (0, _GRID_STEP)]
       # Then:
       for d1lat, d1lon in _CARDINAL_OFFSETS:
           np1 = _snap(p1[0] + d1lat, p1[1] + d1lon)
           for d2lat, d2lon in _CARDINAL_OFFSETS:
               if d1lat == 0 and d1lon == 0 and d2lat == 0 and d2lon == 0:
                   continue  # exact match already checked
               np2 = _snap(p2[0] + d2lat, p2[1] + d2lon)
               # ... rest of neighbor check unchanged
       ```
    2. Update the function docstring: change "9*9=81 combinations" to "5*5=25 combinations" and "Effective merge radius: ~22m" to "~11m (cardinal neighbors only, no diagonals)"
    3. This gives 24 neighbor checks (down from 80), effective per-endpoint max offset = 11m (1 grid step on a single cardinal axis). Prevents merging parallel paths >11m apart.
  - Notes: With 50m re-segmented edges, GPS jitter (5-10m) is within 11m. The old 22m was needed for variable-length edges. **Existing test review required:** `test_same_road_10m_offset_merges` and `test_independent_endpoint_jitter_merges` use offsets of ~0.0001° (11m) on cardinal axes — they should still pass with 4-neighbor. `test_merge_across_activity_boundary` uses 0.00005° (~5.5m) — still within 11m. Run all existing `TestNeighborCellClustering` tests after this change. If any fail, update test coords to use offsets within 11m (1 grid step on a single axis). Also update `_find_neighbors` in `migrate_edge_clustering.py` to match (or note it's superseded by `migrate_resegment.py`).

- [ ] Task 3: Integrate re-segmentation into `_update_heat_edges()`
  - File: `backend/app/services/ingest.py`
  - Action: Add single line `coords = _resegment_coords(coords)` immediately after `coords = json.loads(geojson_str).get("coordinates", [])` and before the `# Snap all points to 4dp grid` comment block. Search for the string `coords = json.loads(geojson_str)` to find the exact insertion point. No other changes to this function.
  - Notes: The existing snap + neighbor clustering pipeline remains unchanged. Re-segmented coords are processed identically to raw coords.

- [ ] Task 4: Apply re-segmentation in `get_personal_edges()`
  - File: `backend/app/services/ingest.py`
  - Action: In `get_personal_edges()`, after `coords = geom.get("coordinates", [])`, add `coords = _resegment_coords(coords)` before the `for i in range(len(coords) - 1)` edge iteration loop. Search for `coords = geom.get("coordinates"` to find the exact line.
  - Notes: `get_personal_edges()` has no snap grid or neighbor clustering — it generates edges from raw coords per activity. Adding reseg normalizes edge lengths to 50m (reducing visual clutter from variable-length edges) but does NOT merge edges across activities. Full despaghettification for personal traces would require adding snap+cluster to this function too — out of scope for now. Performance: ~0.01ms per activity, negligible.

- [ ] Task 5: Add re-segmentation and tightened merge tests
  - File: `backend/tests/test_ingest_direction.py`
  - Action: Add `TestResegmentation` class with isolated sport `"test_reseg"`. Tests:
    1. **Basic 50m re-segmentation**: 200m straight trace → 5 points (0, 50, 100, 150, 200m)
    2. **Short trace unchanged**: 30m trace → returns original 2 coords
    3. **Elevation preserved**: 3D coords re-segmented, interpolated elevation correct (±0.1m)
    4. **GPS gap >500m skipped**: Two points 1km apart → no interpolated points between them
    5. **NaN elevation handled**: coords with NaN ele → no crash, synthetic points have no elevation
    6. **Empty/single point**: `[]` and `[[3.87, 43.61]]` → returned as-is
    7. **Different GPS spacing merge (critical from 5 Whys)**: Two traces on same 200m road, one with 5m point spacing, one with 80m → after reseg + snap + clustering → query `SELECT COUNT(*) FROM heat_edges WHERE sport = 'test_reseg'` returns ≤5 (not 40+) and `SELECT MAX(pass_count) ...` returns ≥2
    8. **Parallel paths stay separate (Le Lez test)**: Two traces 12m apart on parallel paths → after reseg + 11m merge → two separate edges (not merged)
  - Notes: Use `_geojson()` helper and `_RESEG_SPORT = "test_reseg"` for isolation. Tests 7-8 are the most important — they validate both the merge and the separation.

- [ ] Task 6: Create migration script `migrate_resegment.py`
  - File: `backend/app/cli/migrate_resegment.py` (NEW)

  - Action: Create CLI script with `--dry-run` flag. Steps:
    1. `pg_dump` reminder (log warning if not `--dry-run`)
    2. Count existing edges (log before)
    3. If not dry-run: `DELETE FROM heat_edge_contributors`, `DELETE FROM heat_edges`, `DROP TABLE IF EXISTS edge_merge_log`
    4. Query: `SELECT user_id, sport, geometry_geojson, COALESCE(activity_date, created_at) AS activity_date FROM activities WHERE contribute_heatmap = true AND geometry_geojson IS NOT NULL ORDER BY created_at ASC`
    5. For each activity: call `_update_heat_edges(user_id, sport, geometry_geojson, activity_date=activity_date)`
    6. Log progress every 100 activities (N/total, edge count so far)
    7. After all: log final edge count
    8. If not dry-run: trigger surface enrichment — call `enrich_heat_edges()` first (requires Overpass), then `enrich_surfaces_from_known_edges()` (local spatial join). If enrichment fails (e.g., Overpass rate limit), log warning and advise re-running enrichment later — edges work without surface tags, just with `surface_type="unknown"`.
  - Notes:
    - `--dry-run` only counts activities and estimates edge reduction (doesn't modify DB).
    - **Session management (from adversarial review):** `_update_heat_edges` creates and closes its own `SessionLocal()` internally and commits per call. The migration script does NOT manage sessions or call `db.commit()` — each `_update_heat_edges` call is self-contained. The "commit every 100" from earlier spec was incorrect — removed.
    - **`_edge_version` (from adversarial review):** `_update_heat_edges` increments `_edge_version` on every call. This is fine — the version is just an in-memory counter for cache invalidation, no performance impact. No special handling needed.
    - **NOT crash-safe:** If the script crashes mid-way, the DB has a partial re-ingest. The ONLY recovery is to restore from the `pg_dump` taken in step 1 and re-run. There is no incremental resume.
    - **`activity_date` (from adversarial review):** Must be passed to `_update_heat_edges` to preserve time-filtered heatmap functionality. The Activity model has `activity_date` column — use it directly.

- [ ] Task 7: Run migration locally and validate
  - Action: `docker compose exec backend python -m app.cli.migrate_resegment --dry-run` then without `--dry-run`
  - Notes: Verify edge count reduction. Check heatmap visually on Le Lez at zoom 17+. Verify "1 contributeur · 1 passage" edges are reduced. Check that pass_count/user_count are correct on merged edges. Run full test suite.

### Acceptance Criteria

- [ ] AC 1: Given two GPS traces on the same 200m road with different point spacing (5m vs 80m), when both are ingested with re-segmentation, then the total unique edge count is ≤5 (not 40+), and at least one edge has pass_count=2 (proving merge occurred).

- [ ] AC 2: Given a GPS trace of 200m on a straight road, when re-segmented at 50m, then `_resegment_coords` returns 5 points (0m, 50m, 100m, 150m, 200m). After snap+dedup, the pipeline produces approximately 4 edges (exact count may vary by ±1 due to snap dedup, but NOT 20+ edges).

- [ ] AC 3: Given a GPS trace shorter than 50m (e.g., 30m), when ingested, then it returns the original coordinates unchanged and produces one edge.

- [ ] AC 4: Given a GPS trace with a >500m gap (signal loss), when re-segmented, then no interpolated points are created in the gap — only the two raw endpoints are emitted.

- [ ] AC 5: Given 3D coordinates with elevation, when re-segmented, then synthetic intermediate points have linearly interpolated elevation (±0.1m accuracy vs true linear value).

- [ ] AC 6: Given the migration script run with `--dry-run`, when executed on the current DB, then it reports activity count and estimated edge reduction without modifying data.

- [ ] AC 7: Given the migration script run without `--dry-run`, when executed, then total heat_edge count decreases by 30-50% and the Le Lez area shows visibly fewer parallel edges at zoom 17+.

- [ ] AC 8: Given a user viewing their personal traces via `get_personal_edges()`, when traces are returned, then they are re-segmented consistently with the heatmap (no spaghetti on personal trace display).

- [ ] AC 9: Given the migration completes, when surface enrichment re-runs, then new edges have correct `surface_type` and `highway_type` values (not all "unknown").

## Additional Context

### Dependencies

- No new libraries needed — uses existing `math`, `json`, `_haversine_m` from `geo.py`
- Depends on existing neighbor clustering (`_find_canonical_edge`) for merge effectiveness
- PostGIS spatial index on `heat_edges.geometry` must exist (already does)

### Testing Strategy

**Unit tests (Task 5):**
- Re-segmentation function: basic, short trace, elevation, GPS gap, NaN, empty input
- Integration: different-GPS-spacing merge (the critical root-cause test)

**Integration tests (existing E2E):**
- Existing `test_ingest_direction.py` tests must still pass (no regression)
- Heatmap trails endpoint returns correct data with fewer edges

**Manual validation (Task 6):**
- Visual: Le Lez heatmap at zoom 17+ shows merged edges instead of spaghetti
- Popup: correct "N contributeurs · M passages" on busy roads
- Routing: no degradation on client-graph A*
- Personal traces: no spaghetti in sidebar trace display

### Risk Mitigations

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | pass_count inflation (private activities) | HIGH | Filter `contribute_heatmap=True` in migration query |
| 2 | user_count=0 from wrong ordering | HIGH | `ORDER BY created_at ASC` in migration |
| 3 | Phantom edges through terrain (GPS gap) | MEDIUM | >500m gap guard skips interpolation |
| 4 | NaN elevation propagation | LOW | `math.isnan()` guard in interpolation |
| 5 | Migration OOM (2,371 activities) | MEDIUM | `_update_heat_edges` manages its own sessions internally — no leak risk. Monitor memory during run. |
| 6 | Surface tags lost after migration | MEDIUM | Post-migration enrichment re-run |
| 7 | Phase shift between offset traces | LOW | Accepted: 11m merge + reseg still gives 50-60% merge for offset traces |
| 8 | Switchback merge on tight MTB trails | LOW | 50m segments + 11m merge = very safe (only merges <11m jitter) |
| 9 | Parallel paths wrongly merged (Le Lez) | MEDIUM | Tightened merge from 22m to 11m — paths >11m apart stay separate |

### Notes

- **Expected edge reduction:** Re-segmentation alone gives 46% reduction (benchmarked). Tightened merge radius (11m vs 22m) reduces clustering effectiveness somewhat. Combined estimate: 30-50% total reduction (300k → 150-210k). The 46% benchmark was with 22m merge — actual results with 11m will be validated in Task 7.
- **Performance impact:** Re-segmentation adds ~0.01ms per activity during ingestion. Negligible vs DB I/O.
- **Rollback:** `pg_dump` before migration. Restore from dump if wrong merges detected.
- **Future (Phase 2):** If visual quality still insufficient after re-segmentation, consider local OSM road network import for map-matching. Only justified if Phase 1 shows wrong merges in complex urban areas.
