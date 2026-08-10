# Spec: OSM Road Network as Routing Base Layer

## Problem

Currently, the routing graph only contains edges from:
- `heat_edges` (community traces — only where users have ridden)
- `dfci_edges` (fire tracks — south France only)
- `trail_edges` (GR, GT, PR, EuroVelo — marked trails only)

Areas with no heatmap data have no routing coverage → straight lines between waypoints. With 1 user, most of France/Spain/Italy has zero heatmap data.

## Goal

Serve `osm_road_edges` as a **base routing layer** in `area.pb`. The frontend gets full road coverage for any viewport. Heatmap edges provide a routing bonus on top.

## Architecture

```
Current:   area.pb = heat_edges + dfci + trails  (~50-200k edges/viewport)
Proposed:  area.pb = osm_road_edges + heat_edges + dfci + trails  (~200-600k edges/viewport)
```

## Relationship to Existing Overpass Code

`graph_tiles.py` has Overpass-based OSM loading (`_fetch_osm_edges`, `_get_osm_edges`, `_osm_tile_cache`) used by the z14 tile endpoint. This spec **replaces** the Overpass approach for `area.pb` with direct DB queries from `osm_road_edges`.

**Migration plan**:
- `area.pb` endpoint → use `osm_road_edges` table (this spec)
- z14 tile endpoint → keep Overpass fallback for tiles not covered by PBF
- Long term: deprecate Overpass once PBF coverage is complete

The `_build_bbox_graph()` currently uses `ST_Intersects` on heat_edges. For OSM edges we use `tile_key = ANY(:tiles)` because the B-tree index on `tile_key` is faster than a GiST spatial scan on 80M rows.

## Critical Design Decision: Vertex Connectivity

OSM edges use OSM node coordinates. Heat edges use 5dp grid-snap GPS coordinates. **They don't share vertices.**

**Solution**: Snap OSM coordinates to 5dp during query via `ROUND(..., 5)`.

**Urban density risk**: Parallel streets 3-8m apart can snap to same vertex pair. In one-way city grids (Paris, Barcelona), this merges both directions into one bidirectional edge — **wrong topology**. This is a known v1 limitation. Mitigation: the routing is advisory ("itinéraire indicatif"), not turn-by-turn navigation.

**v2 fix (future)**: Store `oneway` tag during PBF import. Use directional edges for one-way streets. Increase snap precision to 6dp in dense urban tiles.

## Backend Changes

### 1. Prerequisites — PBF Import Changes

Before implementing, update `import_osm_roads.py`:

**a) Add `service` to `ROUTABLE_HIGHWAYS`** (currently missing — spec references it but importer skips it):
```python
ROUTABLE_HIGHWAYS = frozenset({
    "residential", "tertiary", "tertiary_link", "secondary", "secondary_link",
    "primary", "primary_link", "unclassified", "track", "path", "cycleway",
    "footway", "bridleway", "living_street", "pedestrian",
    "service",  # NEW — park paths, campus roads, cemeteries
})
```

**b) Add `service` to `HIGHWAY_IDX`** in `graph_builder.py`:
```python
HIGHWAY_IDX = {
    "residential": 0, "tertiary": 1, "secondary": 2, "primary": 3,
    "unclassified": 4, "track": 5, "path": 6, "cycleway": 7,
    "steps": 8, "service": 9, "unknown": 10,
}
```
Note: this changes the index for `unknown` from 9 to 10. Frontend `client-graph.ts` must be updated to match. **This is a breaking change** — must be deployed together.

**c) Store access tags** (optional v2, not blocking):
```python
# During PBF import, also extract:
access = way.tags.get("access", "")
bicycle = way.tags.get("bicycle", "")
# Store in new columns: access TEXT, bicycle TEXT
```

**d) Re-import affected regions** after changing ROUTABLE_HIGHWAYS (adds `service` ways to existing PBF data).

### 2. Surface Normalization

`osm_road_edges.surface` stores raw OSM values (`compacted`, `fine_gravel`, `paving_stones`, etc.). `SURFACE_IDX` only has 5 keys. Must use `_normalize_surface()` (already exists in `graph_tiles.py`) to map raw values:

```python
# In the OSM query loop:
raw_surface = row[4] or "unknown"
normalized = _SURFACE_NORMALIZE.get(raw_surface, raw_surface)
surface_idx = SURFACE_IDX.get(normalized, SURFACE_IDX["unknown"])
```

### 3. Extend `_build_bbox_graph()` in `graph_tiles.py`

```python
# Feature flag
_OSM_ROUTING = os.environ.get("OSM_ROUTING_ENABLED", "true").lower() == "true"

# After heat_edges + dfci + trails queries...
if _OSM_ROUTING:
    z14_tiles = list(_bbox_to_z14_tiles(min_lon, min_lat, max_lon, max_lat))

    # Dedup against ALL existing edges (heat + dfci + trails), not just heat
    existing_keys = set()
    for e in edges:
        fwd = (e[0], e[1], e[2], e[3])
        rev = (e[2], e[3], e[0], e[1])
        existing_keys.add(fwd)
        existing_keys.add(rev)

    osm_highways = _sport_highways(sport)
    TILE_BATCH = 200

    for batch_start in range(0, len(z14_tiles), TILE_BATCH):
        tile_batch = z14_tiles[batch_start:batch_start + TILE_BATCH]
        osm_rows = db.execute(sa_text("""
            SELECT ROUND(ST_X(ST_StartPoint(geometry))::numeric, 5),
                   ROUND(ST_Y(ST_StartPoint(geometry))::numeric, 5),
                   ROUND(ST_X(ST_EndPoint(geometry))::numeric, 5),
                   ROUND(ST_Y(ST_EndPoint(geometry))::numeric, 5),
                   surface, highway
            FROM osm_road_edges
            WHERE tile_key = ANY(:tiles)
              AND highway = ANY(:highways)
        """), {"tiles": tile_batch, "highways": list(osm_highways)}).fetchall()

        for row in osm_rows:
            lon1, lat1 = float(row[0]), float(row[1])
            lon2, lat2 = float(row[2]), float(row[3])
            key = (lon1, lat1, lon2, lat2)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            existing_keys.add((lon2, lat2, lon1, lat1))  # reverse

            raw_surface = row[4] or "unknown"
            normalized = _SURFACE_NORMALIZE.get(raw_surface, raw_surface)

            edges.append([
                lon1, lat1, lon2, lat2,
                0,  # user_count = 0
                0,  # slope_grade = 0
                SURFACE_IDX.get(normalized, SURFACE_IDX["unknown"]),
                HIGHWAY_IDX.get(row[5] or "unknown", HIGHWAY_IDX["unknown"]),
                0,  # trail_idx = none
            ])
```

### 4. Sport-Specific Highway Filtering

```python
_SPORT_HIGHWAYS: dict[str, set[str]] = {
    "road": {"residential", "tertiary", "secondary", "primary", "unclassified",
             "cycleway", "living_street", "service"},
    "gravel": {"residential", "tertiary", "secondary", "unclassified", "track",
               "cycleway", "path", "living_street", "service"},
    "mtb": {"track", "path", "cycleway", "bridleway", "unclassified",
            "residential", "tertiary", "service"},
    "offroad": {"track", "path", "bridleway", "cycleway", "unclassified", "service"},
    "running": {"residential", "tertiary", "unclassified", "track", "path",
                "cycleway", "footway", "pedestrian", "living_street", "bridleway", "service"},
}
```

These filters only work if `service` is in `ROUTABLE_HIGHWAYS` AND in the DB (prerequisite 1a).

### 5. `_bbox_to_z14_tiles()` helper

```python
def _bbox_to_z14_tiles(min_lon: float, min_lat: float,
                        max_lon: float, max_lat: float) -> set[str]:
    """Convert a bbox to z14 tile keys.

    Valid range: lon [-180, 180], lat [-85.05, 85.05] (Web Mercator).
    50km radius at lat 44°N ≈ 1360 tiles → 7 batches at 200/query.
    """
    import math
    z = 14
    n = 2 ** z
    # Clamp to valid Web Mercator range
    min_lon = max(-180.0, min(180.0, min_lon))
    max_lon = max(-180.0, min(180.0, max_lon))
    min_lat = max(-85.05, min(85.05, min_lat))
    max_lat = max(-85.05, min(85.05, max_lat))

    x_min = max(0, int((min_lon + 180) / 360 * n))
    x_max = min(n - 1, int((max_lon + 180) / 360 * n))
    y_min = max(0, int((1 - math.log(math.tan(math.radians(max_lat)) + 1/math.cos(math.radians(max_lat))) / math.pi) / 2 * n))
    y_max = min(n - 1, int((1 - math.log(math.tan(math.radians(min_lat)) + 1/math.cos(math.radians(min_lat))) / math.pi) / 2 * n))
    tiles = set()
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            tiles.add(f"{z}/{x}/{y}")
    return tiles
```

### 6. Performance

**Must validate with `EXPLAIN ANALYZE`** on actual 80M-row table before merging. The `tile_key` is a Text column — string comparisons are slower than integer.

**If slow (>200ms per batch)**:
- Option A: Add composite index `CREATE INDEX idx_osm_tile_highway ON osm_road_edges (tile_key, highway)`
- Option B: Convert `tile_key` to bigint (`z * 10_000_000 + x * 10_000 + y`) — **breaking schema change**, requires migration + PBF re-import. Only if Option A insufficient.

**Response size**: With dedup, 3-8 MB per area.pb. Acceptable — fetched once on route mode entry.

### 7. Caching

Reduce `_AREA_CACHE_MAX` from 20 to 5 (code change needed in `graph_tiles.py`).

**Cache invalidation after PBF import**: The `_area_cache` uses 300s TTL — stale data expires naturally within 5 min. The `@lru_cache` on `_cached_db_edges` has no TTL — it persists until process restart. Since PBF import runs as a CLI process (separate from web server), **in-process cache clearing is impossible from the CLI**.

**Solutions**:
- Accept 5-min staleness from `_area_cache` TTL (good enough)
- For `@lru_cache`: restart Cloud Run service after PBF import (`gcloud run services update ... --no-traffic` then re-enable) — or set `_edge_version += 1` in the DB and check it in the cache
- Simplest: PBF import is rare (quarterly). Restart the service after import. Document this in ops runbook.

### 8. Feature Flag

```python
_OSM_ROUTING = os.environ.get("OSM_ROUTING_ENABLED", "true").lower() == "true"
```

Kill switch: set `OSM_ROUTING_ENABLED=false` on Cloud Run to revert to heat-only routing without code deploy.

## Known Limitations (v1 — Ship Anyway)

### 1. No slope data for OSM edges
`slope_grade = 0` → router treats all OSM roads as flat. In mountains, may prefer short steep over long flat. Improves as heatmap grows. Future: DEM enrichment during PBF import.

### 2. No one-way enforcement
Bidirectional edges only. Combined with 5dp snapping in dense urban grids, one-way pairs can merge into wrong topology. **Urban routing quality will be poor in v1.** This is acceptable for a cycling-focused app (most users route in rural/suburban areas). Disclaimer: "Itinéraire indicatif — respectez la signalisation routière."

### 3. No access=private filtering
Private tracks may appear. Rare in practice (importer filters to routable highways). Fix with access tag storage in next PBF import cycle.

### 4. OSM data freshness
Static PBF data. Re-import quarterly. Restart Cloud Run service after import to clear caches.

### 5. `userCount` capped at uint8 (255)
CTGB format uses 1 byte for userCount. Popular routes with >255 contributors will cap at 255. Not a problem at current scale (1 user). Document for future.

## Frontend Changes

### 1. Cost Model — No Change Needed
OSM edges have `userCount=0` → base cost (no heat discount). Router naturally prefers heat edges where available.

### 2. `HIGHWAY_IDX` Update
Must match backend change: `service=9, unknown=10` (was `unknown=9`). Update in `client-graph.ts`.

### 3. Route Disclaimer
When route uses edges with `userCount === 0`: "Itinéraire indicatif — respectez la signalisation routière."

### 4. Lite Mode (Deferred to v2)
~~Mobile `?lite=1` param~~ — **deferred**. Adds scope without clear benefit until we measure actual mobile performance. If 8 MB responses cause issues on mobile, implement then. Removes 30-50 lines of cross-file scope from v1.

## Implementation Steps

1. **PBF import changes**: add `service` to `ROUTABLE_HIGHWAYS` + `HIGHWAY_IDX` — update frontend to match
2. **Add `_bbox_to_z14_tiles()`** with bounds clamping — 20 lines
3. **Add `_sport_highways()` filter** — 15 lines
4. **Add surface normalization** in OSM edge loop (reuse `_SURFACE_NORMALIZE`) — 3 lines
5. **Extend `_build_bbox_graph()`** with feature-flagged OSM query + full dedup — 50 lines
6. **Reduce `_AREA_CACHE_MAX`** from 20 to 5 — 1 line
7. **Run `EXPLAIN ANALYZE`** on tile_key query with 200 values on prod DB
8. **Test**:
   - Route in area WITH heatmap → prefers heat edges
   - Route in area WITHOUT heatmap → uses OSM edges (no straight lines)
   - Route in area with NO PBF data → falls back to heat-only (no regression)
   - **Failure case**: tile_key query returns 0 rows for a tile that should have data
   - **Dedup**: verify no duplicate edges between heat/dfci/trail/osm
   - **Surface**: verify `compacted` → `gravel`, `paving_stones` → `asphalt` (normalization)
9. **Deploy behind `OSM_ROUTING_ENABLED=true`**
10. **Restart Cloud Run after deploy** to clear stale caches

**Estimated effort: 2 days** (1 day implementation, 1 day benchmarking + testing).

## Cost Impact

- Cloud SQL: no change (existing indexes, may need composite index if slow)
- Cloud Run: ~2x response size → ~$0.02/mo extra egress
- Frontend: Web Worker handles 400k edges in <100ms

## When to Implement

**Now — behind feature flag.** Biggest UX win possible: routing everywhere, not just where you've ridden.

## Review Notes

- Round 1 (11 findings): vertex snapping, tile count, sport filter, slope/one-way documented
- Round 2 (13 findings): Overpass migration, feature flag, access tags, cache invalidation, effort revised
- Round 3 (12 findings):
  - F1: `service` must be added to `ROUTABLE_HIGHWAYS` + `HIGHWAY_IDX` (prerequisite, not just spec dict)
  - F2: CLI can't clear web process memory → accept TTL-based expiry + service restart
  - F3: `_AREA_CACHE_MAX` reduction is a code change, listed in implementation steps
  - F4+F5: Urban one-way pair collision documented as known v1 limitation
  - F6: Integer tile_key is Option B only if perf insufficient — not casual
  - F7: Bounds clamping added to `_bbox_to_z14_tiles`
  - F8: Surface normalization via `_SURFACE_NORMALIZE` added to query loop
  - F9: Dedup now covers ALL sources (heat + dfci + trail + osm), not just heat vs osm
  - F10: `userCount` uint8 cap documented
  - F11: Test plan expanded with failure cases
  - F12: Lite mode deferred to v2 (reduces scope)
