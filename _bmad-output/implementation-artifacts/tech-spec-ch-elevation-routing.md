---
title: 'Contraction Hierarchies + Elevation-Aware Cost Model'
slug: 'ch-elevation-routing'
created: '2026-03-31'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['FastAPI', 'PostGIS', 'TypeScript', 'MapLibre', 'Web Worker']
files_to_modify:
  - 'backend/app/services/routing.py'
  - 'backend/app/services/routing_profiles.py'
  - 'backend/app/api/routing.py'
  - 'backend/app/services/ingest.py'
  - 'backend/app/services/graph_builder.py'
  - 'backend/app/api/graph_tiles.py'
  - 'backend/app/services/ch.py (new)'
  - 'frontend/lib/client-graph.ts'
  - 'frontend/lib/graph-binary.ts'
  - 'frontend/lib/routing-worker-client.ts'
  - 'frontend/app/map/page.tsx'
code_patterns: ['multiplicative cost model', 'adjacency map graph', 'z14 tile loading', 'sport profiles']
test_patterns: ['pytest backend', 'playwright E2E']
---

# Tech-Spec: Contraction Hierarchies + Elevation-Aware Cost Model

**Created:** 2026-03-31

## Overview

### Problem Statement

Long-distance routes (> 50km, e.g., Montpellier → Tornac at 44km) hit the client-side Dijkstra iteration limit (500k) or take too long to compute through sparse graphs. The current A* explores the full graph on every query — O(n) per search. Additionally, the cost model treats slope as a per-edge factor but ignores cumulative elevation: a route with gentle 3% grades over 2km *feels* the same as one with a brutal 12% wall over 500m, even though rider fatigue is radically different.

### Solution

Two complementary improvements:

1. **Contraction Hierarchies (CH):** Precompute shortcut edges server-side for gravel, MTB, and offroad profiles (3 hierarchies). Serve CH shortcuts as tile data so the **client Web Worker** can run bidirectional CH search directly — no server round-trip. Short routes use standard A* (as today), long routes (> 30km or A* iteration limit hit) switch to bidirectional CH in the Worker. Server routing is legacy fallback only.

2. **Elevation-aware cost model:** Extend the RawEdge wire format to include endpoint elevations (+4 bytes). Add a per-edge elevation multiplier that penalizes steep grades exponentially rather than linearly — making 12% grades dramatically more expensive than 3% grades for the same altitude gain.

### Scope

**In Scope:**
- CH precomputation for gravel, mtb, and offroad profiles (3 hierarchies, server-side build)
- Classic CH algorithm (eager full precomputation — chosen via Algorithm Olympics evaluation)
- CH shortcuts served as tile data to the client (same z14 tile system, separate endpoint or layer)
- Bidirectional CH query in the **client Web Worker** (primary routing path)
- Automatic switch: A* for short segments, CH for long segments (> 30km or iteration limit hit)
- RawEdge format extension: 20 → 24 bytes (add ele1, ele2 as int16)
- Exponential slope factor replacing piecewise linear
- Backend + frontend cost model parity

**Out of Scope:**
- Client-side CH (too complex for incremental tile loading)
- CH for road/running profiles (can be added later with the same pattern)
- Customizable CH (CCH) — topology/metric separation not worth the complexity since our graph changes infrequently
- Cumulative fatigue model across full route (later — requires Dijkstra state extension)
- DEM data sourcing pipeline (use existing slopeGrade + ele_delta_m)
- Turn penalties (separate spec)

## Context for Development

### Codebase Patterns

- **Cost model parity:** Frontend (`client-graph.ts:162-197 edgeCost()`) and backend (`routing_profiles.py:413-440 edge_cost()`) implement identical formulas: `dist × S × U × H × T × V`. Any cost change must be applied to both.
- **Sport profiles:** Defined in `routing_profiles.py:39-125`, 5 profiles (road, gravel, mtb, offroad, running). Each has slope_penalty, surface_factors, heat/trail max bonus, detour_limit.
- **Edge format:** RawEdge = `[lon1, lat1, lon2, lat2, uc, slope, surfIdx, hwIdx, trailIdx]` — 9 values. Binary CTGB format: 8-byte header + 20 bytes/edge (`graph-binary.ts:26-28`).
- **Graph building:** Frontend `mergeEdges()` (`client-graph.ts:285-352`) + backend `_build_graph()` (`routing.py:616-761`). Both use adjacency maps with pre-computed costs.
- **Vertex snapping:** Backend uses `_snap4(lon, lat)` (4 decimals, `routing.py:692`). Frontend uses `vertexKey()` (5 decimals, `client-graph.ts:311`). CH must use 4 decimals for backend consistency.
- **Heatmap import flow:** `ingest.py` → `heat_edges` update → `_edge_version += 1` (5 locations: lines 1205, 1406, 1442, 1558, 1594) → caches auto-miss.
- **Routing cascade:** `compute_route()` (`routing.py:2169-2320`) → `_smart_route_in_memory()` → `_hybrid_route()` → BRouter → OSRM → straight line.
- **Server Dijkstra:** `_dijkstra_path()` (`routing.py:839-878`) — standard min-heap, 500k iteration limit, returns vertex path or None.

### Files to Reference

| File | Purpose | Key Lines |
| ---- | ------- | --------- |
| `backend/app/services/routing.py` | Server Dijkstra, graph building | `_build_graph()` L616, `_dijkstra_path()` L839, `compute_route()` L2169 |
| `backend/app/services/routing_profiles.py` | Sport profiles, `edge_cost()` | Profiles L39-125, `edge_cost()` L413, `slope_factor()` L380 |
| `backend/app/api/routing.py` | API endpoints | `GET /routing` L38, `POST /routing/proposals` L326 |
| `backend/app/services/ingest.py` | Import pipeline, edge versioning | `_edge_version` L90, `get_heat_edges_public()` L1220 |
| `backend/app/db/models.py` | HeatEdge model | `HeatEdge` L348 — has `ele_delta_m`, `slope_grade` columns |
| `frontend/lib/client-graph.ts` | Client A*, cost model | `edgeCost()` L162, `slopeFactor()` L123, `mergeEdges()` L285, `dijkstra()` L706 |
| `frontend/lib/graph-binary.ts` | CTGB binary decoder | `decodeBinaryEdges()` L30, `EDGE_SIZE=20` L28 |
| `frontend/lib/routing-worker-client.ts` | Worker, tile loading | `ensureTilesLoaded()` L437, 50-tile cap L451 |
| `frontend/app/map/page.tsx` | Client→server cascade | `fetchSmartSegmentWithClientRouting()` L733, server fallback L800 |
| `frontend/lib/graph-binary.ts` | CTGB binary decoder | `decodeBinaryEdges()` L30, `EDGE_SIZE=20` L28 (→ 24) |
| `backend/app/services/graph_builder.py` | CTGB binary serializer | `struct.pack` at L145, 20 bytes/edge (→ 24) |
| `backend/app/api/graph_tiles.py` | Tile endpoints, CTGB header | `area.pb` L515, tile `.pb` L631 |
| `backend/app/services/ch.py` (new) | CH construction + query | Node ordering, contraction, bidirectional search |

### Technical Anchor Points (from Deep Investigation)

**CH insertion points:**

*Server (precomputation + tile serving):*
1. **New table `ch_shortcuts`** — `(source_vertex, target_vertex, cost, via_vertex, ch_level, sport, profile_hash)` with spatial + sport indexes
2. **New module `backend/app/services/ch.py`** — CH construction (node ordering, contraction, shortcut generation)
3. **New tile endpoint** `GET /routing/ch/{sport}/14/{x}/{y}.pb` — serves CH shortcuts as CTGB-compatible binary tiles (same z14 grid, loaded alongside regular edge tiles)
4. **Lazy rebuild trigger** — when `_get_edge_version()` != `ch_version`, schedule subprocess rebuild

*Client (querying):*
5. **New `bidirectionalCH()` in `client-graph.ts`** — bidirectional Dijkstra that only relaxes upward edges (by CH level). No penalty support — used for first proposal only.
6. **Two-tier CH loading** in `routing-worker-client.ts`:
   - On route mode entry: fetch `GET /routing/ch/{sport}/regional.pb` (~50-100KB, hierarchy top) — loads once, covers entire region
   - On viewport move: fetch `GET /routing/ch/{sport}/14/{x}/{y}.pb` (local shortcuts) — loaded alongside regular tiles via `ensureTilesLoaded()`
   - Merge both into graph with `isShortcut` flag + `chLevel` + `via` vertex
7. **Modified `clientRouteProposals()`** (`client-graph.ts`):
   - Proposal #1: `bidirectionalCH()` (~15ms, no penalties)
   - Proposal #2: `dijkstraWithPenalties()` (~30ms, corridor from #1)
   - Proposal #3: `dijkstraWithPenalties()` (~30ms, corridors from #1+#2)
   - Drag-and-drop re-routing: always standard A* (short segments)
8. **Auto-switch in `clientRoute()`** (`client-graph.ts:895`) — if `directM > 30_000` or standard A* returns null, retry with `bidirectionalCH()`. Transparent to user.
9. **Path unpacking** in Worker — recursively replace shortcut edges with original edges via `via` vertex. Short shortcuts (< 5 edges) have inline coords; longer ones trigger on-demand tile fetch.

**Elevation insertion points:**
1. **Backend:** `ele_delta_m` and `slope_grade` already exist on `HeatEdge` (models.py L348). No migration needed for backend.
2. **Binary format:** `graph-binary.ts:28` `EDGE_SIZE = 20` → 24. Add `ele1` (int16 LE, offset 20) and `ele2` (int16 LE, offset 22). Version byte in header distinguishes 20 vs 24 byte edges.
3. **Cost model:** Replace `slopeFactor()` at `client-graph.ts:123` and `routing_profiles.py:380` with exponential variant. Keep fallback to piecewise linear when elevation = 0.
4. **RawEdge extension:** 9 → 11 values: `[..., ele1, ele2]`. Update both `graph-binary.ts` decoder and backend tile serializer.

**Graph cache already handles invalidation:**
- Cache key includes `edge_version` (`routing.py:75`) — CH version mismatch auto-misses
- Graph cache max: 20 entries, 0.05° discretized bbox (`routing.py:49-50`)

### Technical Decisions

1. **CH precomputed server-side, queried client-side.** The server builds CH hierarchies (needs full graph). CH shortcuts are then served as tile data to the client Web Worker, which runs bidirectional CH search directly — no server round-trip. Standard A* stays for short segments. Server routing is legacy fallback (< 1% of queries).

2. **Classic CH (eager, full precomputation).** Chosen via Algorithm Olympics evaluation over 5 candidates:
   - **Classic CH** (winner): 10-15ms queries, 30s build/sport, well-understood (OSRM/GraphHopper use this)
   - CCH: topology/metric split not worth complexity — our graph changes infrequently
   - Hub Labeling: sub-ms queries but 400MB storage × 3 sports = impractical
   - Lazy CH: no precomputation but 80-200ms queries — too slow for UX
   - ALT (Landmarks): 30-80ms queries, simpler — noted as fallback if CH proves too complex

3. **Three profiles: gravel, mtb, offroad.** Offroad has its own cost profile (asphalt=2.0, slopePenalty=0.5) distinct from both gravel and mtb — it needs its own hierarchy. Road/running can be added later. Total: ~90s build, ~360MB storage.

4. **Lazy rebuild, not per-import.** CH is NOT rebuilt inline during heatmap import. Instead: on first CH query after `edge_version` changes, trigger async rebuild in a subprocess, serve standard Dijkstra fallback until ready. This avoids blocking the import pipeline (a Strava sync importing 200 activities would otherwise queue 200 rebuilds). Rebuild always processes all 3 profiles together — never individually.

5. **Atomic swap + version check.** New CH shortcuts are written to a staging table, then swapped in a single transaction. Every CH query checks `ch_version == edge_version`; on mismatch, falls back to standard Dijkstra. Stale CH is never served.

6. **Shortcut limit for memory safety.** If contracting a vertex would add > 10 shortcuts, skip it (leave uncontracted). This caps memory at ~2-3× original edges. CH construction runs in a subprocess with explicit memory limit, not in the FastAPI process.

7. **Profile hash for drift detection.** Store `hash(profile_params)` alongside CH data. If profile hash changed (someone updated routing_profiles.py), CH is stale even if `edge_version` didn't change.

8. **Exponential slope, not cumulative fatigue.** Per-edge exponential penalty (grade² above threshold) is simpler than tracking cumulative state in Dijkstra, and captures most of the rider perception gap. Cumulative fatigue can be layered on later.

9. **RawEdge 20 → 24 bytes.** Adding ele1/ele2 as int16 (±32,767m range, 1m precision). No backward compatibility needed (no production yet) — just switch to 24 bytes everywhere. Header reserved byte = `0x01` signals elevation present.

10. **Elevation is relative, not absolute.** Stored as `ele1 = 0, ele2 = round(ele_delta_m)` — cost model only needs the delta. When `ele_delta_m == 0` (no elevation data, ~60% of OSM edges), fall back to current piecewise linear slope from `slopeGrade` field. Do NOT apply exponential penalty to unknown-elevation edges — they should not benefit from looking "flat." Absolute DEM enrichment is a future enhancement.

11. **CH shortcuts store `via` vertex + inline geometry.** Each shortcut = `(from, to, cost, via_vertex, coords[])`. For short shortcuts (< 5 original edges), store intermediate coords inline (~40 extra bytes). For longer shortcuts, store only `via` and fetch missing tiles on demand during unpacking. Most shortcuts span 2-3 edges — inline is the common case.

12. **CH is all-or-nothing per query.** Client compares `ch_version` (from CH tile headers) with `edge_version` (from regular tile headers). On mismatch: discard all CH tiles, use A* only. Partial/stale CH is worse than no CH.

13. **Empty tiles for non-CH sports.** CH tile endpoint returns empty tile (200, 0 edges) for road/running — never 404. Client doesn't need to know which sports have CH; empty CH tiles = no shortcuts = A* only.

14. **CH for first proposal only; A* for alternatives.** Shortcut costs are pre-computed and can't accept per-query corridor penalties. So: proposal #1 uses `bidirectionalCH()` (no penalties, ~15ms). Proposals #2 and #3 use standard `dijkstraWithPenalties()` with corridor from #1 (~30ms each). Drag-and-drop edits always use A* (short segments, 5-20km). No user-visible mode switch — transparent.

15. **Two-tier shortcut loading.** Regional shortcuts (hierarchy levels 3+, spanning >10km) are loaded as a **single file per sport** (~50-100KB) on route mode entry. Local shortcuts (levels 0-2, ~2km span) are served as z14 tiles alongside regular edges. This ensures bidirectional CH search can "meet in the middle" even when corridor tiles between A and B aren't loaded yet.

16. **IndexedDB cache includes `ch_version`.** Cache key: `"ch:{sport}:14/{x}/{y}:v{ch_version}"`. Server returns `X-CH-Version` header. Client evicts on mismatch. Prevents stale shortcuts from persisting across sessions.

17. **Bridge detection in node ordering.** Vertices whose removal disconnects graph components get maximum priority (contracted last). Standard edge-difference heuristic alone is insufficient for sparse cycling graphs with isolated clusters connected by single-path corridors.

18. **Subprocess isolation for CH build.** Construction runs in a subprocess (not FastAPI process). Writes to `ch_shortcuts_staging` table, then atomic swap via `ALTER TABLE RENAME`. Orphaned subprocess = harmless stale staging table, overwritten by next rebuild. Track `ch_build_started_at` — if > 10 min with no completion, consider failed.

## Implementation Plan

### Tasks

#### Phase 0: Prerequisites (fix pre-existing bugs that CH would amplify)

- [ ] Task 0a: Fix frontend HIGHWAYS enum — add missing `service` at index 9
  - File: `frontend/lib/client-graph.ts` (HIGHWAYS array)
  - Action: Add `'service'` at index 9 before `'unknown'` (index 10). Aligns with backend `HIGHWAY_IDX`.
  - Finding: F3

- [ ] Task 0b: Fix `userCount` pack format — signed→unsigned
  - File: `backend/app/services/graph_builder.py` (struct.pack format)
  - Action: Change `bb` → `Bb` (unsigned uint8 for userCount, signed int8 for slope). Currently works by two's-complement accident.
  - Finding: F4

- [ ] Task 0c: Fix merge key `-1` race in Worker
  - File: `frontend/lib/routing-worker-client.ts` (mergeEdges method)
  - Action: Use `this.nextId++` for merge requests instead of hardcoded `-1`. Include id in Worker message + response.
  - Finding: F19

- [ ] Task 0d: Add AbortController to `reinit()` — cancel in-flight fetches on sport switch
  - File: `frontend/lib/routing-worker-client.ts`
  - Action: Add `_abortController: AbortController` field. Abort on `reinit()` and `destroy()`. Pass signal to all fetch calls in `ensureTilesLoaded()` and `loadAreaGraph()`. Add generation counter; discard merge results from stale generations.
  - Finding: F23

- [ ] Task 0e: Fix `_mergeGate` overwrite — chain instead of replace
  - File: `frontend/lib/routing-worker-client.ts`
  - Action: Change `this._mergeGate = mergeP.then(...)` to `this._mergeGate = this._mergeGate.then(() => mergeP).then(...)` everywhere.
  - Finding: F30

- [ ] Task 0f: Add IndexedDB format version to regular tile cache keys
  - File: `frontend/lib/routing-worker-client.ts` (tile caching)
  - Action: Change cache key from `"{sport}:14/{x}/{y}"` to `"{sport}:14/{x}/{y}:f2"` (format version 2 for 24-byte edges). Old 9-element cached arrays auto-evict on key mismatch.
  - Finding: F13

- [ ] Task 0g: Fix stale `H_SCALE` comment
  - File: `frontend/lib/client-graph.ts` (dijkstra, H_SCALE line)
  - Action: Change comment from `// 0.55` to `// 0.65 — minimum possible cost/distance ratio`.
  - Finding: F17

#### Phase 1: Elevation-Aware Cost Model (no CH dependency)

- [ ] Task 1: Extend CTGB binary format to 24 bytes/edge
  - File: `backend/app/services/graph_builder.py`
  - Action: Change `struct.pack` to include `ele1` (int16) and `ele2` (int16) at offsets 20-23. Store absolute elevations: `ele1 = round(start_elevation_m)`, `ele2 = round(end_elevation_m)`. If unknown: both = 0. Update header comment.
  - File: `backend/app/api/graph_tiles.py`
  - Action: Update CTGB header and inline packing to 24 bytes.
  - Finding: F32 — absolute elevation needed for elevation profile rendering + CH accumulation.

- [ ] Task 2: Update client binary decoder to 24 bytes
  - File: `frontend/lib/graph-binary.ts`
  - Action: Change `EDGE_SIZE = 20` → `24`. Add `ele1 = view.getInt16(off + 20, true)` and `ele2 = view.getInt16(off + 22, true)` to `decodeBinaryEdges()`. Return 11-value `RawEdge`. Add coordinate range validation: reject edges with `|lon| > 180` or `|lat| > 90`.
  - Finding: F39 — validate coordinates to prevent malicious tile injection.

- [ ] Task 3: Extend RawEdge type and mergeEdges
  - File: `frontend/lib/client-graph.ts`
  - Action: Extend `RawEdge` from 9 → 11 values `[..., ele1, ele2]`. Update `mergeEdges()` destructuring to extract `ele1, ele2`. Store on `EdgeEntry`.

- [ ] Task 4: Implement exponential slope factor (C0-continuous)
  - File: `frontend/lib/client-graph.ts` (`slopeFactor()`)
  - Action: Replace piecewise linear with continuous exponential splice:
    ```
    if grade < 5%:  factor = 1.0
    if 5% ≤ grade < 8%: factor = 1.0 + (grade - 5) / 5 × 0.2  (piecewise: 1.0→1.12)
    if grade ≥ 8%:  factor = 1.12 × e^(0.08 × (grade - 8))     (continuous at 8%: starts at 1.12)
    cap at 3.0
    downhill: grade × 0.7 applied BEFORE the above (unchanged)
    scaled by sport penalty: final = max(FLOOR, 1.0 + (raw - 1.0) × penalty)
    ```
    When `ele1 == ele2 == 0 && slopeGrade == 0`: return 1.0.
    When `ele1 == ele2 == 0 && slopeGrade > 0`: use current piecewise.
  - Finding: F8, F38 — ensure C0-continuity at 8% splice, specify penalty scaling, fix downhill asymmetry.
  - Notes: Assert `slopeFactor(7.99) ≈ slopeFactor(8.01)` in tests (continuity check).

- [ ] Task 5: Unit tests for exponential slope
  - File: `frontend/lib/__tests__/client-graph.test.ts` (new or extend)
  - Action: Test at key grades: 0%, 5%, 7.99%, 8%, 8.01%, 10%, 12%, 15%, 20%, 25%. Assert continuity at 8%. Test downhill (-12%). Test each sport's penalty scaling. Test unknown elevation fallback.

#### Phase 2: CH Precomputation (backend)

- [ ] Task 6: Create `ch_shortcuts` DB table with spatial index
  - File: `backend/app/db/models.py`
  - Action: Add `CHShortcut` model with `source_lon/lat`, `target_lon/lat`, `via_lon/lat`, `cost`, `ch_level`, `sport`, `profile_hash`, `inline_coords` (JSONB), `cumulative_ascent_m` (Float — for elevation profile). Add PostGIS point geometry column with GIST index, OR composite btree on `(sport, source_lon, source_lat)` + `(sport, target_lon, target_lat)` for tile bbox queries.
    Add `CHBuildStatus` model: `sport, ch_version, edge_version, profile_hash, started_at, completed_at, failed_count, last_failure_at`.
  - File: `backend/alembic/versions/` (new migration)
  - Finding: F25 — spatial index required for tile queries. F46 — `failed_count` for backoff.

- [ ] Task 7: Implement CH construction algorithm
  - File: `backend/app/services/ch.py` (new)
  - Action: Implement:
    - `build_ch(sport, profile, edges) → list[CHShortcut]`
    - Node ordering: edge-difference heuristic + bridge detection via Tarjan's algorithm (run once before ordering; bridge vertices get max priority)
    - Contraction loop with shortcut limit (skip if > 10 shortcuts)
    - Store `via` vertex, inline coords (< 5 original edges), and `cumulative_ascent_m`
    - Vertex keys snapped to 5 decimals (matching frontend `vertexKey()` format)
  - Finding: F6 — vertex precision must match frontend (5 decimals, not 4). F11 — specify Tarjan's for bridge detection. F41 — store cumulative ascent on shortcuts.

- [ ] Task 8: CH rebuild pipeline with backoff
  - File: `backend/app/services/ch.py`
  - Action: Implement:
    - `rebuild_all_ch()` — builds all 3 profiles (gravel, mtb, offroad)
    - Writes via `TRUNCATE + INSERT` in single transaction (not `ALTER TABLE RENAME`)
    - `trigger_lazy_rebuild()` — checks `CHBuildStatus.failed_count`; if > 3 consecutive failures, back off exponentially (5min, 15min, 1h). Returns immediately.
    - Include `profile_hash` in `X-CH-Profile-Hash` response header for client-side staleness detection
  - Finding: F10 — `TRUNCATE + INSERT` avoids `ACCESS EXCLUSIVE` lock. F46 — backoff on persistent failure. F53 — expose profile hash to client.

- [ ] Task 9: CH optimality validation
  - File: `backend/app/services/ch.py`
  - Action: After building CH, validate 100 random pairs: `ch_cost <= dijkstra_cost × 1.001`. If any fail: increment `failed_count`, don't swap, log to Sentry.

- [ ] Task 10: Unit tests for CH construction
  - File: `backend/tests/test_ch.py` (new)
  - Action: Test small graphs (10-50 vertices): shortcut costs correct, bridges contracted last, shortcut limit respected, bidirectional search optimal, path unpacking recovers edges, cumulative ascent correct.

#### Phase 3: CH Tile Serving (backend)

- [ ] Task 11: CH shortcut binary serializer
  - File: `backend/app/services/graph_builder.py`
  - Action: Add `pack_ch_shortcuts()`: CTGB v2 header + 30 bytes/shortcut + inline coords. Include `cumulative_ascent_m` as int16 at offset 28 (expand shortcut to 32 bytes base).

- [ ] Task 12: CH tile endpoints
  - File: `backend/app/api/graph_tiles.py`
  - Action: Two new endpoints:
    - `GET /routing/ch/{sport}/regional.pb` — shortcuts with `ch_level >= 3`. Headers: `X-CH-Version`, `X-Edge-Version`, `X-CH-Profile-Hash`.
    - `GET /routing/ch/{sport}/14/{x}/{y}.pb` — shortcuts with `ch_level < 3` in tile bbox (uses spatial index).
    - Both return empty CTGB v2 for sports without CH or non-CH sports. Never 404.

- [ ] Task 13: Lazy rebuild trigger with backoff
  - File: `backend/app/api/graph_tiles.py`
  - Action: Check `ch_version != edge_version` → call `trigger_lazy_rebuild()` (respects backoff). Serve empty tiles while rebuilding.

#### Phase 4: CH Client Integration (frontend)

- [ ] Task 14: CH shortcut decoder + coordinate validation
  - File: `frontend/lib/graph-binary.ts`
  - Action: Add `decodeCHShortcuts(buffer) → CHShortcutEdge[]`. Check header version == 2. Variable-length parsing. Validate coordinate ranges. Extract `cumulativeAscentM` from shortcut bytes.

- [ ] Task 15: Two-tier CH loading with separate tile tracking
  - File: `frontend/lib/routing-worker-client.ts`
  - Action:
    - Add `loadedCHTiles: Set<string>` (separate from `loadedTiles`) with own 50-tile cap
    - Regional: fetch on route mode entry, validate `X-CH-Version == X-Edge-Version` AND `X-CH-Profile-Hash` matches
    - Local: fetch alongside regular tiles, separate cap (don't double the regular cap)
    - Add `vertexChLevel: Map<string, number>` to `ClientGraph` — populated during `mergeCHShortcuts()`
    - On sport switch: clear all CH data via AbortController (Task 0d)
    - IndexedDB key: `"ch:{sport}:14/{x}/{y}:v{ch_version}:h{profile_hash}"`
  - Finding: F20 — vertexChLevel map needed. F40 — separate tile cap. F53 — profile hash in cache key.

- [ ] Task 16: Implement `bidirectionalCH()`
  - File: `frontend/lib/client-graph.ts`
  - Action: New function using `graph.vertexChLevel` for upward constraint (NOT edge.chLevel):
    ```typescript
    export function bidirectionalCH(
      graph: ClientGraph,
      startKey: string,
      endKey: string,
      maxIterations = 500000,
    ): string[] | null
    ```
    - Two min-heaps. Upward constraint: `graph.vertexChLevel.get(neighbor) >= graph.vertexChLevel.get(current)`
    - Termination: `μ ≤ min(d_f[top_f], d_b[top_b])` where `μ` is best meeting cost, `top_f/top_b` are min heap keys
    - On partial tile coverage: if both heaps exhaust without meeting, return null (fall back to A*)
    - Add Sentry breadcrumb: `addRoutingBreadcrumb('CH search', { method: 'client_ch', iterations, meetingLevel })`
  - Finding: F20 — vertex level not edge level. F35 — correct termination for partial graphs. F49 — Sentry observability.

- [ ] Task 17: Path unpacking with cycle guard
  - File: `frontend/lib/client-graph.ts`
  - Action: New function:
    ```typescript
    export function unpackCHPath(graph: ClientGraph, path: string[]): [number, number][]
    ```
    - Recursive unpack via `chVia` vertex. Max recursion depth = 20. Visited-edge set to detect cycles.
    - Short shortcuts: use `chCoords` inline geometry
    - Long shortcuts: use `chCoords` if available, otherwise insert `[viaLon, viaLat]` as approximate point (progressive refinement on zoom)
    - CH shortcuts excluded from `isBridgeEdge()`: add `if (edge.chLevel !== undefined) return false;`
    - `extractRouteStats()`: unpack CH shortcuts first, then compute stats on original edges. Use `cumulativeAscentM` from shortcut for elevation gain when original edges unavailable.
  - Finding: F36 — cycle guard + depth limit. F37 — exclude shortcuts from bridge detection. F41 — elevation gain from shortcuts.

- [ ] Task 18: Auto-switch and proposals integration
  - File: `frontend/lib/client-graph.ts` (`clientRoute()`)
  - Action: After standard A* attempt, if `!path` (null), try `bidirectionalCH()`. Also try CH first when `directM > 25000` (lowered from 30km to cover the 25-35km dead zone).
  - File: `frontend/lib/client-graph.ts` (`clientRouteProposals()`)
  - Action: Proposal #1 uses `bidirectionalCH()` when `directM > 25000`. Proposals #2-3 use `dijkstraWithPenalties()` with increased `maxIterations = 500000` and bbox-bounded search (only explore vertices within 10km of proposal #1's corridor).
  - Finding: F50 — lower threshold to 25km. F26 — increase iteration limit + bbox bound for penalized proposals.

- [ ] Task 19: E2E tests + Sentry integration
  - File: `e2e/tests/ch-routing.spec.ts` (new)
  - Action:
    - Montpellier → Tornac (44km) returns route, not straight line
    - 100km route returns 3 proposals in < 2s
    - Drag-and-drop re-routes segment in < 1s
    - Sport switch reloads CH tiles
    - Non-CH sport (road) still routes via A*
    - CH version mismatch → A* fallback (intercept headers)
  - File: `frontend/lib/client-graph.ts`
  - Action: Add `'client_ch'` to Sentry `methodToLevel()` mapping.
  - Finding: F49 — observability.

#### Phase 5: Documentation & Cleanup

- [ ] Task 20: Update routing documentation
  - File: `docs/routing-architecture.md`
  - Action: Add CH section: two-tier loading, bidirectional search, auto-switch, proposals. Update cascade diagram. Note: backend routing is legacy (< 1% of queries, not updated for CH).
  - File: `docs/routing-cost-model.md`
  - Action: Update slope factor with exponential formula. Add elevation encoding. Document absolute vs relative elevation decision.

### Acceptance Criteria

#### Phase 0: Prerequisites
- [ ] AC0a: Given a tile with `highwayIdx=9`, when decoded by frontend, then highway = `'service'` (not `'unknown'`)
- [ ] AC0b: Given two concurrent `mergeEdges()` calls, when both complete, then both promises resolve (no hanging)
- [ ] AC0c: Given `reinit()` called during in-flight tile fetch, when the old fetch completes, then its edges are discarded (not merged into new sport's graph)

#### Elevation Cost Model
- [ ] AC1: Given an edge with 12% grade, when computing slope factor for gravel (penalty=0.8), then the result is > 1.5× the factor for 8% grade (exponential, not linear)
- [ ] AC2: Given `slopeFactor(7.99)` and `slopeFactor(8.01)`, then results differ by < 0.01 (C0-continuity at splice point)
- [ ] AC3: Given an edge with `ele1 == ele2 == 0` and `slopeGrade == 0`, when computing cost, then slope factor = 1.0
- [ ] AC4: Given a CTGB tile with 24-byte edges, when decoded, then `ele1` and `ele2` are correctly extracted as int16 absolute meters
- [ ] AC5: Given a tile with `lon > 180` in binary data, when decoded, then the edge is rejected (coordinate validation)

#### CH Construction
- [ ] AC6: Given 1.5M gravel edges, when `build_ch()` completes, then shortcut count < 4× original edges
- [ ] AC7: Given a constructed CH, when 100 random pairs validated, then `ch_cost <= dijkstra_cost × 1.001` (optimality)
- [ ] AC8: Given a bridge vertex (Tarjan's), when node ordering runs, then it has maximum priority (contracted last)
- [ ] AC9: Given a vertex where contraction adds > 10 shortcuts, then it is skipped
- [ ] AC10: Given 3 consecutive CH build failures, when `trigger_lazy_rebuild()` is called, then it backs off exponentially (not immediate retry)
- [ ] AC11: Given `TRUNCATE + INSERT` during CH swap, when a concurrent CH tile read is in-flight, then the read completes without blocking (no `ACCESS EXCLUSIVE` lock)

#### CH Client Integration
- [ ] AC12: Given Montpellier → Tornac (44km, gravel), when user clicks both waypoints, then 3 proposals appear in < 2s (no straight line)
- [ ] AC13: Given regional shortcuts loaded + partial local tiles, when `bidirectionalCH()` runs on 100km, then it finds a path via regional shortcuts
- [ ] AC14: Given a CH path with shortcuts, when `unpackCHPath()` runs, then coordinates form a continuous polyline with no gaps > 200m
- [ ] AC15: Given a CH shortcut cycle (A→C via B, A→B via C), when `unpackCHPath()` runs, then it terminates (max depth 20, no stack overflow)
- [ ] AC16: Given user drags waypoint on long route, when segment re-routes, then A* is used (< 500ms)
- [ ] AC17: Given `X-CH-Version != X-Edge-Version`, when client loads CH tiles, then all CH data discarded, A* only
- [ ] AC18: Given sport = "road" (no CH), when CH endpoint called, then returns 200 with empty tile
- [ ] AC19: Given loop route A→B→A, when B→A proposals generated, then they differ from A→B (corridor penalties)
- [ ] AC20: Given CH shortcut edge, when `isBridgeEdge()` called, then returns false (not misclassified)
- [ ] AC21: Given CH route, when `extractRouteStats()` runs, then elevation gain reflects shortcut's `cumulativeAscentM` (not 0)
- [ ] AC22: Given CH search completes, when Sentry breadcrumb checked, then `routing.method = 'client_ch'` is logged

## Additional Context

### Architecture Decision Records

**ADR-1: Binary formats (no backward compat — no production yet)**

Version byte distinguishes format. Version 1 = regular edges (24 bytes). Version 2 = CH shortcuts.

Regular edges (CTGB version=1, 24 bytes/edge):
```
[lon1 i32][lat1 i32][lon2 i32][lat2 i32][uc u8][slope i8×2][hw|surf u8][trail u8][ele1 i16][ele2 i16]
```
Elevation encoding: `ele1 = 0`, `ele2 = round(ele_delta_m)`. Relative, not absolute — the cost model only needs the delta `(ele2 - ele1)`. Absolute DEM enrichment can come later.

CH shortcuts (CTGB version=2, 30 bytes + 8×n_coords per shortcut):
```
[lon1 i32][lat1 i32][lon2 i32][lat2 i32][via_lon i32][via_lat i32][cost f32][level u8][n_coords u8]
[inline coords: [lon i32][lat i32] × n_coords]
```
Inline coords give coarse geometry for instant rendering. As the user zooms in, regular edge tiles load and geometry refines progressively.

**ADR-2: Two-tier CH loading**
- Regional file: `GET /routing/ch/{sport}/regional.pb` — single CTGB v2 file, ~5-20KB gzip, loaded once on route mode entry
- Local tiles: `GET /routing/ch/{sport}/14/{x}/{y}.pb` — z14 tiles, loaded with viewport pan, empty=200 (never 404)

**ADR-3: Version negotiation**
- All tile endpoints return `X-Edge-Version` and `X-CH-Version` headers
- Client discards all CH data if `ch_version != edge_version` — transparent fallback to A*
- IndexedDB cache keys include version: `"ch:{sport}:14/{x}/{y}:v{ch_version}"`

**ADR-4: Client types**
```typescript
// Regular edge (extended):
type RawEdge = [lon1, lat1, lon2, lat2, uc, slope, surfIdx, hwIdx, trailIdx, ele1, ele2]; // 11 values

// CH shortcut:
interface CHShortcutEdge {
  lon1: number; lat1: number; lon2: number; lat2: number;
  viaLon: number; viaLat: number;
  cost: number; level: number;
  coords: [number, number][]; // inline geometry
}

// EdgeEntry extension:
interface EdgeEntry {
  // ... existing fields ...
  chLevel?: number;     // undefined = regular edge
  chVia?: string;       // vertex key for unpacking
  chCoords?: [number, number][]; // inline geometry
}
```

**ADR-5: Proposals integration**
- Proposal #1: `bidirectionalCH()` (no penalties, ~15ms)
- Proposals #2-3: `dijkstraWithPenalties()` (corridor from #1, ~30ms each)
- Drag-and-drop: always standard A* (short segments)

### UX-Derived Requirements (from Reverse Engineering)

| # | Requirement | Source workflow | Budget |
|---|-------------|---------------|--------|
| R1 | CH tiles load in parallel with regular tiles on route mode entry | A→B | — |
| R2 | Regional shortcuts (hierarchy top) loaded as single file per sport | A→B 100km | ~50-100KB, loaded once |
| R3 | Local shortcuts served as z14 tiles | A→B viewport | loaded with pan |
| R4 | 3 proposals for 100km segment in < 500ms | A→B | CH ~15ms + A* ~30ms×2 + unpack ~30ms |
| R5 | Path unpacking works without middle corridor tiles | A→B | regional shortcuts bridge the gap |
| R6 | CH for first proposal only; A* with penalties for #2 and #3 | A→B→A loop | shortcut costs are fixed |
| R7 | B→A proposals different from A→B (corridor penalties) | A→B→A loop | existing penalty system |
| R8 | Drag-and-drop always uses A* (never CH) | Both | segments 5-20km, A* faster |
| R9 | No user-visible mode switch between CH and A* | Both | transparent |
| R10 | Sport switch clears CH tiles and reloads for new sport | Both | 3 separate hierarchies |

### Dependencies

- Existing `slopeGrade` and `ele_delta_m` data in heat_edges table
- DEM service (`dem.py`) for edges missing elevation data
- Server routing endpoint already exists as fallback

### Testing Strategy

- **Unit tests:** CH construction correctness (shortcut costs = sum of contracted path costs)
- **Unit tests:** Exponential slope factor matches expected values at key grades (5%, 10%, 15%, 20%)
- **Unit tests:** Path unpacking recovers original edges (unpacked cost == shortcut cost)
- **CH optimality validation:** For 100 random query pairs, `ch_cost <= dijkstra_cost × 1.001` — runs as part of rebuild pipeline, not just CI
- **Integration tests:** Server CH routing returns valid path for Montpellier → Tornac (44km)
- **Integration tests:** Version mismatch → Dijkstra fallback (not stale CH)
- **Integration tests:** Lazy rebuild triggers on first query after edge_version change
- **E2E tests:** Long-distance route no longer returns straight line
- **E2E tests:** Elevation data missing → falls back to current slope behavior (no regression)
- **Benchmark:** CH query time vs current A* for 50km+ routes
- **Memory test:** CH construction on 1.5M gravel edges stays under 2GB RSS

### Risks (from 4 rounds of adversarial review — 53 findings)

| Component | Risk | Severity | Mitigation | Finding |
|-----------|------|----------|------------|---------|
| **Prerequisites** | Highway enum mismatch (service=9) | High | Task 0a: add `service` to frontend | F3 |
| **Prerequisites** | Concurrent merge race (key -1) | High | Task 0c: use nextId++ | F19 |
| **Prerequisites** | Sport switch corrupts graph | High | Task 0d: AbortController on reinit | F23 |
| **CH Tiles** | Stale shortcuts in IndexedDB | High | `ch_version` + `profile_hash` in cache key | F53 |
| **CH Tiles** | Tile cap doubled (regular + CH) | Medium | Separate `loadedCHTiles` set with own cap | F40 |
| **CH Search** | Vertex level vs edge level confusion | High | `vertexChLevel` map on ClientGraph | F20 |
| **CH Search** | Wrong termination on partial graph | High | Correct μ-based termination + fallback | F35 |
| **CH Search** | Unpacking cycle → stack overflow | Medium | Max depth 20 + visited-edge set | F36 |
| **CH Search** | Shortcuts classified as bridge edges | High | `if (edge.chLevel !== undefined) return false` | F37 |
| **CH Search** | Elevation gain = 0 for CH routes | High | Store `cumulativeAscentM` on shortcuts | F41 |
| **CH Search** | VIP snap bias wrong for CH hubs | Medium | Don't let shortcut userCount affect snap VIP | F43 |
| **CH Build** | Wrong node ordering for sparse graph | High | Tarjan's bridge detection | F11 |
| **CH Build** | Permanent build failure → subprocess loop | High | Backoff: failed_count + exponential delay | F46 |
| **CH Build** | Memory blow-up during construction | High | Shortcut limit (10/vertex) + subprocess | F6 pre-mortem |
| **CH Build** | ALTER TABLE lock blocks reads | Medium | Use TRUNCATE + INSERT instead | F10, F42 |
| **Data Flow** | TOCTOU version race between endpoints | Medium | Embed edge_version in CH tile header | F28 |
| **Data Flow** | Profile hash not exposed to client | Medium | `X-CH-Profile-Hash` header + cache key | F53 |
| **Elevation** | Relative encoding breaks elevation profile | High | Store absolute ele1/ele2 | F32 |
| **Elevation** | Slope discontinuity at 8% splice | Medium | C0-continuous formula (start at 1.12) | F8, F38 |
| **Proposals** | Penalized A* hits iteration limit (25-35km) | Medium | 500k iterations + bbox-bounded search | F26, F50 |
| **Observability** | No Sentry breadcrumb for CH | Medium | Add `client_ch` to methodToLevel | F49 |

### Notes

- Backend routing is legacy (< 1% of queries) — not updated for CH or exponential slope. Divergence is accepted.
- The exponential slope curve is tunable per sport (mtb tolerates steep grades better than gravel)
- CH data stored in separate `ch_shortcuts` table — not bloating `heat_edges`
- Future: DEM enrichment pipeline to backfill elevation for OSM edges (not a blocker)
- Future: multi-waypoint CH support (F45 — `_tryMultiBackend` currently bypasses CH)
