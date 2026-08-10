---
title: 'Local OSM Map-Matching for Heatmap Edges'
slug: 'osm-map-matching-heatmap'
created: '2026-03-23'
status: 'implementation-complete'
stepsCompleted: [1, 2, 3, 4]
tech_stack: [python, postgresql, postgis, fastapi, sqlalchemy, overpass, alembic]
files_to_modify: [backend/app/services/ingest.py, backend/app/db/models.py, backend/alembic/versions/0025_add_osm_road_edges.py, backend/tests/test_ingest_direction.py]
code_patterns: [spatial-grid-index, bbox-prefilter, overpass-tile-fetch, point-to-segment-distance, upsert-on-conflict, sync-with-timeout-fallback]
test_patterns: [pytest, isolated-sport-name, geojson-fixture-helpers]
---

# Tech-Spec: Local OSM Map-Matching for Heatmap Edges

**Created:** 2026-03-23

## Overview

### Problem Statement

GPS trace heatmap still shows spaghetti despite densification + grid-snap clustering. The fundamental issue is that snapping to an arbitrary lat/lon grid (round to 4dp) doesn't follow actual road geometry — GPS jitter creates diagonal edges that no grid-based approach can fix. Two traces on the same road 5m apart produce edges with different diagonal angles that never merge.

### Solution

**Snap GPS traces to actual OSM road segments stored locally in PostGIS.** Edges = OSM road segments (identified by `osm_way_id/segment_idx`), not arbitrary grid cells. This eliminates spaghetti by construction — all traces on the same road snap to the same OSM way.

- **No heat_edges schema change (clarified by Red Team):** Edge key stays `sport/lat4dp,lon4dp/lat4dp,lon4dp`. OSM matching just chooses BETTER 4dp cells (from OSM nodes instead of GPS jitter). OSM-matched and grid-fallback edges coexist seamlessly. ONE new support table: `osm_road_edges` (with `tile_key` + `fetched_at` for tile tracking — no separate tile status table). All existing code reading heat_edges (heatmap API, routing graph, frontend) needs ZERO changes.
- **Fallback:** When no OSM way is within 15m (off-road, unmapped trails), fall back to current grid-snap system.
- **Sync tile fetch with timeout (simplified via Occam's Razor):** Tile fetch is synchronous during ingestion, 5s timeout per tile. If timeout/error → grid-snap fallback for that tile's GPS points. Most tiles already cached → zero Overpass calls. New area = 1-2s per tile, typically 0-3 new tiles per activity. No async queue complexity.
- **Zero runtime API calls** for already-covered areas. Overpass only called once per z14 tile, then cached forever in DB.
- **Match against `osm_road_edges` only (corrected by Red Team):** DFCI/trail edges have wrong segment lengths (up to 500m) for map-matching. They stay as separate routing layers. OSM road edges have proper 20-50m segments.
- **Bbox prefilter + Python matching (corrected by Red Team):** One bbox query loads candidate OSM segments, then Python walks GPS points to find nearest segment per point. NOT `ST_DWithin` on complex polyline (too slow) or per-point DB queries (too many queries).

### Scope

**In Scope (simplified via Occam's Razor — 3 files instead of 7):**
- New `osm_road_edges` PostGIS table with `tile_key` + `fetched_at` columns (no separate tile tracking table)
- `_fetch_and_store_osm_tile()` in ingest.py — copy+adapt Overpass query from graph_tiles.py (~40 LOC)
- `_match_to_osm()` in ingest.py — bbox prefilter + spatial grid + 2-point consecutive filter (~60 LOC)
- Modified `_update_heat_edges()`: sync OSM tile fetch (5s timeout) → match → fallback to grid-snap
- Same edge_key format: `sport/lat4dp,lon4dp/lat4dp,lon4dp` with coordinates from OSM nodes
- Migration: reuse existing `migrate_resegment.py` (re-run after deploy, OSM matching activates automatically)
- Tests in existing `test_ingest_direction.py`

**Out of Scope:**
- Changing stored GPS traces (Crouzet methodology — trace integrity preserved)
- Hidden Markov Model map-matching (overkill — simple nearest-segment is sufficient)
- Real-time Overpass calls during user request (only background/pre-fetch)
- Changing the frontend rendering logic
- Modifying DFCI or trail edge systems

## Context for Development

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `backend/app/api/graph_tiles.py:207-279` | `_fetch_osm_edges()` — Overpass query + way parsing. Reuse for DB import. Query: `way["highway"~"^(filter)$"](bbox); out geom;` |
| `backend/app/api/graph_tiles.py:51-55` | `_ROUTABLE_HIGHWAYS` — frozenset of 14 highway types to include |
| `backend/app/api/graph_tiles.py:86-93` | `tile_bbox(z, x, y)` → `(min_lon, min_lat, max_lon, max_lat)` |
| `backend/app/api/graph_tiles.py:70-73` | `_OVERPASS_SERVERS` list + `_overpass_semaphore = Semaphore(2)` — reuse for throttling |
| `backend/app/services/osm_enrich.py:86-102` | **`_point_to_segment_dist_m()`** — point-to-line-segment distance. Reuse directly for spatial grid matching. |
| `backend/app/services/ingest.py:264-457` | `_update_heat_edges()` — hook point after coords parse (line 277), before snap loop (line 281) |
| `backend/app/services/ingest.py:386-406` | UPSERT SQL — same for OSM-matched edges (same heat_edges schema) |
| `backend/app/services/ingest.py:98-100` | `_snap(lat, lon)` → `round(lat, 4), round(lon, 4)` — used for 4dp edge_key from OSM nodes |
| `backend/app/services/ingest.py:151-154` | `_edge_key(sport, p1, p2)` — canonical direction-agnostic key. Same format for OSM edges. |
| `backend/app/db/models.py:343-362` | HeatEdge model — unchanged. `surface_type`, `highway_type` columns get populated from OSM tags. |
| `backend/app/db/models.py:365-371` | HeatEdgeContributor — unchanged. |
| `backend/alembic/versions/0024_*` | Latest migration. Next = `0025_add_osm_road_edges.py` |
| `backend/app/services/geo.py:5-12` | `haversine_m(lon1, lat1, lon2, lat2)` — reuse for distance calcs |

### Existing Code to Reuse (copy+adapt, don't refactor — Occam's Razor)

- **`_fetch_osm_edges()`** from graph_tiles.py → copy the 10-line Overpass query + 20-line way parser into ingest.py, adapt to write to DB instead of returning arrays. graph_tiles.py unchanged.
- **`_point_to_segment_dist_m()`** from osm_enrich.py → copy into ingest.py (~15 LOC). **NOTE (from adversarial review F6):** this function uses `haversine_m(lon, lat, lon, lat)` from geo.py — lon-first. ingest.py already imports it as `from app.services.geo import haversine_m as _haversine_m`. Use `_haversine_m` in the copied function.
- **`_snap()` + `_edge_key()`** already in ingest.py → snap OSM node coordinates to 4dp, same key format
- **`_ROUTABLE_HIGHWAYS`** + `_normalize_surface()` + `_normalize_highway()` from graph_tiles.py → copy into ingest.py for tag parsing
- **Semaphore:** Create NEW `_osm_fetch_lock = threading.Semaphore(2)` in ingest.py. Do NOT import/reuse `_overpass_semaphore` from graph_tiles.py — that's `asyncio.Semaphore` (async context) which won't work in sync code. The two semaphores are independent: if both graph_tiles and ingest run simultaneously, total concurrent Overpass = 4 (acceptable — Overpass allows ~6).

### Algorithm Comparison (from elicitation)

| Approach | Merge quality | Parallel road safe | Simplicity | Performance | Direction | Verdict |
|---|---|---|---|---|---|---|
| A: Nearest segment | ★★★★☆ | ★★☆☆☆ | ★★★★★ | ★★★☆☆ | ★★★★★ | Jumps between parallel roads |
| B: +Continuity filter | ★★★★★ | ★★★★☆ | ★★★★☆ | ★★★☆☆ | ★★★★★ | Good but needs way adjacency |
| C: HMM/Viterbi | ★★★★★ | ★★★★★ | ★☆☆☆☆ | ★★★★☆ | ★★★★★ | Gold standard but 10x complexity |
| D: Bulk buffer match | ★★★☆☆ | ★★☆☆☆ | ★★★★★ | ★★★★★ | ★☆☆☆☆ | Loses direction, false matches |
| **E: Hybrid (nearest + 3-point verify)** | **★★★★★** | **★★★★☆** | **★★★★☆** | **★★★☆☆** | **★★★★★** | **Winner — ~50 LOC, accurate** |

**Chosen: E (Hybrid per-point nearest + consecutive-point verification)**
- Per-point: find nearest `osm_road_edge` within 15m via `ST_DWithin`
- Verification: only count an OSM segment as "traversed" if ≥2 consecutive GPS points matched it (filters intersection noise, catches short connectors ≥40m)
- Direction: traversal order preserved → forward/backward counting works
- Fallback: points with no OSM match within 15m → grid-snap (current system)
- HMM rejected: 10x complexity for marginal heatmap improvement, worth considering as v2 if E shows wrong matches

### Technical Decisions

1. **No data model change (from first principles)** — Edge key stays `sport/round(lat,4),round(lon,4)/round(lat,4),round(lon,4)`. OSM matching just changes WHERE the 4dp coordinates come from: OSM road nodes instead of GPS points. Compatible with `client-graph.ts:vertexKey()`. OSM-matched and grid-fallback edges coexist in the same `heat_edges` table. No schema migration needed.
2. **Fallback to grid-snap** for GPS points >15m from any OSM way (off-road, unmapped trails). These still use the current key format with densification + cardinal neighbor merge.
3. **Sync tile fetch with 5s timeout (simplified via Occam's Razor):**
   - Activity arrives → compute z14 tiles from trace bbox
   - For each tile: `SELECT 1 FROM osm_road_edges WHERE tile_key = :key LIMIT 1`
   - Missing tile? → fetch from Overpass (5s timeout, semaphore=2) → store in `osm_road_edges`
   - Timeout/error? → grid-snap fallback for GPS points in that tile
   - No async, no background queue, no piggyback on graph_tiles.py. One code path.
   - 90-day refresh: `WHERE tile_key = :key AND fetched_at > NOW() - INTERVAL '90 days'`
4. **z14 tile granularity** — matches the existing tile system in `graph_tiles.py`. Each tile is ~2.5km × 2.5km. A typical 50km activity covers ~20-40 tiles.
5. **2-consecutive-point verification (revised from 3 by first principles)** — eliminates false matches at intersections while still catching short connectors (~40m). With 20m densification, 2 consecutive points = 40m minimum road segment. 3 points (60m) missed bridges and short connectors.
6. **15m match radius (from first principles)** — Reduced from 20m. At 15m, a trace on Le Lez east bank (with ±10m jitter) rarely reaches the west bank's OSM way (12m gap + 10m jitter = 22m > 15m). The 3-point consecutive filter is a safety net, not the primary filter.
7. **Bulk matching flow (hardened by Red Team):**
   1. Compute trace bbox → `SELECT DISTINCT ON (ST_AsText(geometry)) * FROM osm_road_edges WHERE geometry && ST_Expand(bbox, 0.0002) LIMIT 5000` → load candidate segments into Python list (~500-2000 segments). `DISTINCT ON` deduplicates segments that appear in multiple tiles at boundaries (from adversarial review F4). LIMIT 5000 caps dense urban areas (from F9).
   2. Build in-memory spatial grid (0.0005° ≈ 55m cells) over candidate segments. For each GPS point, check only segments in same + 8 neighboring cells (~10-50 candidates instead of 2000). Find nearest within 15m. ~3ms per activity (validated: 40x faster than brute force, 5s total for 1,789-activity migration).
   3. Apply 2-consecutive-point filter (same OSM segment for ≥2 consecutive GPS points)
   4. Determine direction from traversal order
   5. Snap matched segment endpoints to 4dp → `_edge_key()` → UPSERT into `heat_edges`
   6. Unmatched points → group into contiguous runs → grid-snap fallback (existing pipeline)
8. **Clean refactor (from first principles):** Extract `_match_to_osm(coords, db)` as a separate function returning `(osm_matched, grid_fallback)`. `_update_heat_edges` orchestrates: match → UPSERT OSM edges → UPSERT grid-fallback edges.
9. **Overpass throttling (from pre-mortem F1):** Max 2 concurrent requests via NEW `threading.Semaphore(2)` in ingest.py (NOT the async semaphore from graph_tiles.py), 1s delay between fetches. For bulk imports: compute all unique z14 tiles for the entire batch first, fetch only missing tiles, THEN ingest activities. If Overpass down/banned → fall back to grid-snap for entire import, re-match later.
10. **Tile refresh (from pre-mortem F5):** Tile freshness checked via `SELECT MIN(fetched_at) FROM osm_road_edges WHERE tile_key = :key`. Tiles older than 90 days: `DELETE FROM osm_road_edges WHERE tile_key = :key` then re-fetch. No separate `osm_tile_status` table (eliminated by Occam's Razor).
11. **Migration reuses `migrate_resegment.py` (simplified via Occam's Razor):** After deploying OSM matching code, re-run the existing migration script. Each activity triggers sync tile fetches as needed — tiles accumulate in DB as activities are processed chronologically. No separate tile pre-fetch step needed (tiles are fetched once and cached for subsequent activities in the same area). For bulk import with many tiles, add `--tile-prefetch` flag that computes all unique z14 tiles upfront and batch-fetches before re-ingesting.
12. **Concurrent tile dedup (from failure mode F6):** Check `SELECT 1 FROM osm_road_edges WHERE tile_key = :key LIMIT 1` before fetch. If concurrent requests both check simultaneously, the worst case is a duplicate fetch — harmless since segments are identical. No unique constraint needed on individual rows.
13. **Match logging (from pre-mortem F4):** `logger.info("Match: %d OSM, %d grid-fallback, %d unmatched")` per activity for monitoring.

### Risk Mitigations (from pre-mortem + failure mode analysis)

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | Overpass ban on bulk import | HIGH | Batch tile dedup + throttle (2 concurrent, 1s delay) + grid fallback |
| 2 | Routing graph gaps (coord precision) | CRITICAL | 4dp edge_key (not 5dp) for `vertexKey` compatibility |
| 3 | Stale OSM tiles (new roads) | MEDIUM | 90-day refresh: DELETE old osm_road_edges rows for tile, re-fetch. Existing heat_edges unaffected (no FK, keys are in heat_edges by value). New activities after refresh will match new OSM geometry. Old activities keep their old keys (acceptable — spaghetti only on new roads added post-original-fetch, re-run migration to fix). |
| 4 | Migration tile pre-fetch takes hours | MEDIUM | Separate tile-fetch phase from re-ingest phase |
| 5 | Missing spatial index | HIGH | GiST index on `osm_road_edges.geometry` in table creation |
| 6 | Concurrent tile fetch dupes | LOW | Check-before-fetch (`SELECT 1 WHERE tile_key = :key LIMIT 1`). Worst case: duplicate fetch, harmless. |
| 7 | Dense urban area too many candidates | LOW | LIMIT 5000 on bbox query + DISTINCT ON dedup |
| 8 | Single tracks not in OSM | LOW | Grid-snap fallback works, low traffic = no spaghetti |
| 9 | Crash mid-migration | MEDIUM | `pg_dump` before, NOT crash-safe, restore and retry |

## Implementation Plan

### Tasks

- [x] Task 1: Create `OsmRoadEdge` model + Alembic migration
  - File: `backend/app/db/models.py`, `backend/alembic/versions/0025_add_osm_road_edges.py`
  - Action: Add `OsmRoadEdge` model with columns: `id` (BigInteger PK), `tile_key` (Text, indexed), `osm_way_id` (BigInteger), `segment_idx` (Integer), `surface` (Text), `highway` (Text), `geometry` (Geometry LINESTRING 4326), `fetched_at` (DateTime). Create Alembic migration with GiST spatial index on geometry and btree index on `tile_key`.
  - Notes: No unique constraint on `(osm_way_id, segment_idx)` — a way can appear in multiple tiles at boundaries. `tile_key` format: `"14/x/y"` matching graph_tiles.py.

- [x] Task 2: Add Overpass tile fetcher to `ingest.py`
  - File: `backend/app/services/ingest.py`
  - Action: Add module-level constants and function:
    - Copy `_ROUTABLE_HIGHWAYS`, `_normalize_surface()`, `_normalize_highway()` from graph_tiles.py
    - Add `_OSM_FETCH_TIMEOUT = 5.0` and `_osm_fetch_lock = threading.Semaphore(2)`
    - **CRITICAL (from adversarial review F1):** `_update_heat_edges` runs in a sync context (called from thread pool). Use synchronous `httpx.Client` (NOT async `httpx.AsyncClient`) with `threading.Semaphore`. The tile fetch runs in the same sync thread as the rest of `_update_heat_edges` — no event loop involvement.
    - Add `_latlon_to_z14_tiles(coords)` → returns set of `(z, x, y)` tile keys covering the trace bbox
    - Add `_fetch_and_store_osm_tile(tile_key: str, db) -> int` — checks if tile exists in DB (with 90-day freshness), fetches from Overpass if missing/stale, parses ways into segments, bulk inserts into `osm_road_edges`, returns segment count. Uses `httpx` with timeout + semaphore. On error: logs warning, returns 0 (caller falls back to grid-snap).
  - Notes: Copy the Overpass query pattern from `graph_tiles.py:215-220`. Use `out geom;` for full node geometry. Parse ways into 2-point segments same as `graph_tiles.py:242-277` but store as DB rows instead of arrays. **Long segment guard (from adversarial review F7):** After parsing, split any segment longer than 100m into sub-segments by densifying at ~50m intervals (reuse `_densify_coords` on the 2-point segment). Rural OSM ways can have nodes 200-500m apart — without splitting, these produce giant diagonal edges and fail the spatial grid lookup.

- [x] Task 3: Add `_point_to_segment_dist_m()` + spatial grid to `ingest.py`
  - File: `backend/app/services/ingest.py`
  - Action: Copy `_point_to_segment_dist_m()` from `osm_enrich.py:86-102`. Add spatial grid builder:
    ```python
    _OSM_GRID_SIZE = 0.0005  # ~55m cells

    def _build_segment_grid(segments):
        """Index segments into grid cells. Long segments are added to ALL cells they cover
        (from adversarial review F2 — midpoint-only misses segments >165m)."""
        grid = defaultdict(list)
        for seg in segments:
            # Compute all grid cells from endpoint to endpoint
            cx1, cy1 = int(seg.lon1 / _OSM_GRID_SIZE), int(seg.lat1 / _OSM_GRID_SIZE)
            cx2, cy2 = int(seg.lon2 / _OSM_GRID_SIZE), int(seg.lat2 / _OSM_GRID_SIZE)
            for cx in range(min(cx1, cx2), max(cx1, cx2) + 1):
                for cy in range(min(cy1, cy2), max(cy1, cy2) + 1):
                    grid[(cx, cy)].append(seg)
        return grid

    def _nearest_osm_segment(lon, lat, grid, max_dist_m=15.0):
        cx, cy = int(lon / _OSM_GRID_SIZE), int(lat / _OSM_GRID_SIZE)
        best_dist, best_seg = float('inf'), None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for seg in grid.get((cx+dx, cy+dy), []):
                    d = _point_to_segment_dist_m(lon, lat, seg.lon1, seg.lat1, seg.lon2, seg.lat2)
                    if d < best_dist:
                        best_dist, best_seg = d, seg
        return (best_seg, best_dist) if best_dist <= max_dist_m else (None, None)
    ```
  - Notes: ~3ms per activity with grid. `_point_to_segment_dist_m` signature: `(px, py, ax, ay, bx, by) -> float` in meters.

- [x] Task 4: Add `_match_to_osm()` function
  - File: `backend/app/services/ingest.py`
  - Action: Add function that orchestrates the full matching flow:
    1. Compute trace bbox, load all `osm_road_edges` within expanded bbox from DB
    2. Build spatial grid over loaded segments
    3. For each GPS point, find nearest OSM segment via grid lookup
    4. Apply 2-consecutive-point filter: group consecutive points matching same segment
    5. For each matched segment group: compute direction (forward/backward based on traversal order vs segment direction), compute elevation from GPS points
    6. Return `(matched_segments: list[dict], unmatched_coords: list[list])` where each matched dict has `{edge_key, sport, is_canonical, ele_delta_m, slope_grade, a_lat, a_lon, b_lat, b_lon, surface, highway}`
    7. Unmatched coords are contiguous runs of GPS points >15m from any OSM way
  - Notes: The `edge_key` uses `_snap()` on the OSM segment endpoints → `_edge_key(sport, _snap(lat1, lon1), _snap(lat2, lon2))`. **CRITICAL COORD ORDER (from adversarial review F5):** OSM data is `(lon, lat)` but `_snap(lat, lon)` takes lat first. When calling `_snap` from OSM coords, swap: `_snap(seg.lat, seg.lon)` not `_snap(seg.lon, seg.lat)`. Same trap as the `_haversine_m` import — `geo.py:haversine_m(lon1, lat1, ...)` takes lon first, `_snap(lat, lon)` takes lat first. Document clearly in code comments.

- [x] Task 5: Modify `_update_heat_edges()` to use OSM matching
  - File: `backend/app/services/ingest.py`
  - Action: Restructure the function into two phases:
    1. **Phase 1 (NEW): OSM tile ensure + match.** Before the snap loop, call `_ensure_osm_tiles(coords, db)` to fetch missing tiles. Then call `_match_to_osm(coords, sport, db)` → get `(osm_edges, fallback_coords)`.
    2. **Phase 2a (NEW): UPSERT OSM-matched edges.** For each matched segment, UPSERT using the existing SQL (same `heat_edges` table, same `ON CONFLICT (edge_key) DO UPDATE` logic). Set `surface_type` and `highway_type` from OSM tags on INSERT.
    3. **Phase 2b (EXISTING): Grid-snap fallback.** Run the existing densify → snap → neighbor-cluster → UPSERT pipeline on `fallback_coords` only.
    4. Combine contributor tracking from both phases.
    5. Add logging: `logger.info("Match: %d OSM, %d grid-fallback, %d unmatched", ...)`
  - Notes: The UPSERT SQL needs a **modified version for OSM edges (from adversarial review F10):** same `ON CONFLICT (edge_key) DO UPDATE` pattern, but the INSERT clause adds `surface_type = :surface, highway_type = :highway` from OSM tags. The UPDATE clause does NOT overwrite surface/highway (keeps existing enriched values). Create a second UPSERT string `_UPSERT_OSM_EDGE_SQL` alongside the existing `_UPSERT_EDGE_SQL`. The grid-snap path continues using the original UPSERT unchanged.

- [x] Task 6: Add tests
  - File: `backend/tests/test_ingest_direction.py`
  - Action: Add `TestOsmMatching` class with sport `"test_osm"`. Tests:
    1. **`_point_to_segment_dist_m` accuracy**: known point + segment → expected distance (±0.5m)
    2. **Spatial grid returns nearest**: 3 segments in grid, query point → correct nearest
    3. **2-consecutive-point filter**: single point on road A between road B points → road A filtered out
    4. **OSM-matched edges use OSM node 4dp key**: mock OSM segment → edge_key matches `_snap()` of OSM nodes
    5. **Fallback to grid-snap**: GPS points >15m from any OSM segment → grid-snapped edges created
    6. **Mixed match**: trace with 100m on OSM road + 50m off-road → both OSM and grid edges created
    7. **Overpass timeout fallback**: mock timeout → grid-snap fallback, no crash
    8. **Direction tracking**: trace forward on OSM segment → forward_count=1, backward → backward_count=1
    9. **Surface/highway from OSM tags**: OSM segment with surface=asphalt → heat_edge.surface_type="asphalt"
  - Notes: Mock Overpass responses for tests (don't call real API). Use `unittest.mock.patch` on the httpx call.

- [x] Task 7: Run migration and validate
  - Action: Run `docker compose exec backend python -m app.cli.migrate_resegment` (existing script, now uses OSM matching)
  - Notes: First run will fetch many Overpass tiles (~50k tiles for south France coverage). Expect ~2-4 hours for tile fetch phase. After tiles cached, re-ingest is fast (~10 min). Validate: Le Lez at zoom 17+ shows single clean lines per road. Check `SELECT sport, COUNT(*), AVG(pass_count) FROM heat_edges WHERE geometry && bbox GROUP BY sport` to compare with pre-migration stats.

### Acceptance Criteria

- [x] AC 1: Given two GPS traces on the same road with different GPS jitter, when both are ingested with OSM matching, then they produce the SAME edge_key (from OSM node 4dp) and pass_count=2.

- [x] AC 2: Given a GPS trace on a road with OSM data available, when ingested, then the heat_edge has correct `surface_type` and `highway_type` from OSM tags (not "unknown").

- [x] AC 3: Given a GPS trace 20m from the nearest OSM road (off-road single track), when ingested, then it falls back to grid-snap and produces grid-based edges (not OSM-matched).

- [x] AC 4: Given a GPS trace crossing a z14 tile with no OSM data in the DB, when ingested, then the tile is fetched from Overpass and stored in `osm_road_edges` before matching.

- [x] AC 5: Given a previously fetched z14 tile, when a new activity is ingested in the same area, then NO Overpass call is made (tile is cached).

- [x] AC 6: Given an Overpass timeout (5s) during tile fetch, when ingesting, then the activity falls back to grid-snap for that tile's GPS points — no crash, no stall.

- [x] AC 7: Given the Le Lez area after migration, when viewing the heatmap at zoom 17+, then parallel spaghetti edges are replaced by single clean lines per road with aggregated pass_count.

- [x] AC 8: Given a GPS trace that is partially on-road and partially off-road, when ingested, then the on-road portion produces OSM-matched edges and the off-road portion produces grid-snap edges — both coexist in heat_edges.

- [x] AC 9: Given a z14 tile older than 90 days in `osm_road_edges`, when an activity is ingested in that area, then the tile is re-fetched from Overpass to pick up new/changed roads.

## Additional Context

### Dependencies

- **httpx** — already in dependencies, used for Overpass HTTP calls
- **Overpass API** — external, rate-limited. Two fallback servers already configured in graph_tiles.py
- **PostGIS** — `ST_MakeEnvelope`, `ST_MakeLine`, `ST_MakePoint`, GiST index. Already in use.
- **No new Python libraries needed**

### Testing Strategy

**Unit tests (Task 6):**
- `_point_to_segment_dist_m()`: geometric accuracy
- Spatial grid: nearest-segment lookup
- 2-point consecutive filter: intersection noise rejection
- Edge key from OSM nodes: 4dp snap consistency
- Overpass timeout: graceful fallback

**Integration tests:**
- Existing `test_ingest_direction.py` tests must still pass (grid-snap path unchanged)
- Mixed OSM + grid-snap coexistence

**Manual validation (Task 7):**
- Visual: Le Lez at zoom 17+ — single lines per road, not spaghetti
- Popup: aggregated "N contributeurs · M passages" on busy roads
- Routing: no graph gaps (4dp keys compatible with vertexKey)
- `full.json` size: should decrease (fewer unique edges from OSM dedup)

### Notes

- **Expected improvement:** Le Lez area currently has ~150 road edges (avg pass_count 7.8). After OSM matching, expect ~30-50 road edges (avg pass_count 30+). The spaghetti is eliminated by construction — all traces on the same OSM way produce identical edge_keys.
- **Tile fetch time:** First migration pre-fetches ~500-2000 z14 tiles for current activity coverage. At 2 concurrent requests with 1s delay: ~4-17 minutes. After that, tiles are cached forever (90-day refresh).
- **Rollback:** `pg_dump` before migration. Restore from dump if results are wrong.
- **Future (v2):** If parallel road false-matches persist (despite 15m radius + 2-point filter), consider HMM/Viterbi map-matching. Only justified if v1 validation shows significant wrong matches.
