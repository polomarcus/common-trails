# Ingestion Pipeline — Architecture & Operations

> **New to the codebase?** Start with [`docs/ingestion-pipeline-explained.md`](ingestion-pipeline-explained.md) — it covers
> the *why* (problem statement, design choices, Crouzet trace integrity, K-anonymity) and the "where would I change X"
> map. This doc is the line-by-line architecture & ops reference; treat it as the second read.

## Overview

The ingestion pipeline transforms raw GPS data (GPX files, Strava activities) into a community heatmap and routing graph. Four stages:

1. **Data Ingestion** — parse GPS traces, store activities
2. **Heat Edge Computation** — snap GPS points to grid/OSM, aggregate edges with K-anonymity
3. **Graph Serving** — serialize edges to JSON/Protobuf for client-side routing
4. **CDN Publish** — write pre-computed files to Cloud Storage for static serving

```
[GPX/FIT Upload / Strava OAuth / strava-export bulk script]
        ↓
[Parse → GeoJSON geometry]
        ↓
[Store Activity row (skip_heat_computation=True) → 202 to user]
        ↓
[Cloud Tasks enqueue → POST /internal/ingest/heat (OIDC)]
        ↓
[Densify coords (15m gaps, > 500m → track break)
   → OSM map-match (25m road/running, 15m gravel/mtb)
       → project GPS onto OSM segment (May 2026)
       → relaxed run-detector (any-OSM-match)
   → Grid-snap fallback (5dp, ~1.1m, drop edges > 60m)]
        ↓
[Batch UPSERT heat_edges + contributors  (deadlock-retry on parallel imports)]
        ↓
[Debounced REFRESH MATERIALIZED VIEW heat_edges_display
   (skipped during bulk import; refresh once at the end via SKIP_MATVIEW_REFRESH)]
        ↓
[CDN publish: PMTiles (z6-z14) for heatmap display + .fgraph for routing]
        ↓
[Frontend: PMTiles glow z6-z10 + lines z11+ + area.pb / .fgraph for routing]
```

---

## Entry Points

### GPX File Upload (single file — fast path)
- **Endpoint**: `POST /gpx/upload`
- Accepts a single `.gpx` (max 10 MB)
- **Two-phase flow** (production):
  1. **Sync (~200 ms)**: parse → `ingest_activity(skip_heat_computation=True)` → 202 with `activity_id` and `status: "processing"`.
  2. **Async**: `cloud_tasks.enqueue_heat_compute(activity_id, user_id)` pushes a task to the `heat-compute` Cloud Tasks queue. The task target is `POST /internal/ingest/heat` on the same API service, signed with an OIDC token by the `tasks_invoker` SA. The handler runs `_update_heat_edges` off the user's HTTP request thread. (The `_update_heat_cells` write was removed in PR #214 — cells are now aggregated from `heat_edges` at query time, see "Heat Cells" below.)
- **Why two phases?** Cloud Run charges for HTTP-connection time. The legacy synchronous path held the connection for 5–30 s per upload while heat-edge UPSERTs ran. Decoupling drops user-perceived latency and the per-upload bill.
- **TEST_MODE / dev**: Cloud Tasks is bypassed; `ingest_activity()` runs heat compute inline so unit tests have deterministic post-upload state.
- **Idempotency**: `provider_activity_id` / `file_hash` dedup on the activity row, plus `ON CONFLICT (edge_key, sport)` on `heat_edges` and PK on `heat_edge_contributors` mean re-running the task is a no-op.
- **Failure handling**: Cloud Tasks retries 5× with 30 s → 5 min backoff. The activity row is already stored; if heat compute keeps failing past the retry budget the row remains queryable, the heat just lags until a manual rebuild.

### File / ZIP Import (batch path)
- **Endpoint**: `POST /imports/files`
- Accepts `.gpx`, `.fit`, or `.zip` (max 50 MB)
- Calls `ingest_activity(skip_heat_computation=True)` per file, then enqueues **one Cloud Task per newly-created activity** so retries are scoped tight and Cloud Tasks parallelises naturally.

### Internal Heat Compute Handler
- **Endpoint**: `POST /internal/ingest/heat` (audience-restricted)
- Body: `{"activity_id": "...", "user_id": "..."}`
- Loads the activity row, calls `_update_heat_edges`, returns `{"status": "ok" | "missing" | "noop", "edges_indexed": N}`. (`cells_indexed` removed alongside `_update_heat_cells` in PR #214.)
- **Auth (production)**: validates `Authorization: Bearer <oidc_jwt>` against Google's public keys; `aud` must equal `INTERNAL_HEAT_HANDLER_URL` and `email` must equal `CLOUD_TASKS_INVOKER_SA`. Anything else → 401.
- **Auth (TEST_MODE)**: skipped — endpoint reachable directly so dev / unit tests don't need to mint OIDC tokens.
- **Status codes**: 200 on success or "missing activity" (don't retry); 401 on bad auth (don't retry); 5xx on transient failure (Cloud Tasks does retry).

### Strava OAuth Import
- **Endpoint**: `POST /integrations/strava/start-import`
- Creates `ImportJob` → triggers Cloud Run Job (`import_strava.py`)
- **Phase 1**: Discover all activities via Strava API (paginated, 200/page)
- **Phase 2**: Bulk ingest summary polylines (fast, no heat computation)
- **Phase 3**: GPS upgrade — fetch high-res streams, compute heat edges

### Monthly Resync
- **Job**: `resync_strava.py` (Cloud Scheduler)
- Fetches new activities since `last_synced_at`
- PostgreSQL advisory lock prevents concurrent runs

### Async heat-compute env vars (production)
| Var | Set by | Notes |
|---|---|---|
| `CLOUD_TASKS_QUEUE` | Terraform (`google_cloud_tasks_queue.heat_compute.name`) | Bare name; combined with `GOOGLE_CLOUD_PROJECT` + `CLOUD_TASKS_LOCATION` to build the queue path |
| `INTERNAL_HEAT_HANDLER_URL` | `var.api_public_url` (operator sets after first apply) | Must be the absolute Cloud Run URL — Cloud Tasks signs OIDC tokens against it |
| `CLOUD_TASKS_INVOKER_SA` | Terraform (`tasks_invoker.email`) | Cloud Tasks signs OIDC tokens as this SA; `/internal/*` rejects any other signer |
| `GOOGLE_CLOUD_PROJECT`, `CLOUD_TASKS_LOCATION` | Terraform | Used to assemble the queue path when `CLOUD_TASKS_QUEUE` is a bare id |
| (none of the above set) | n/a | Falls back to inline execution → no behavioural change vs the legacy sync path |

---

## Heat Edge Computation (`_update_heat_edges`)

A walk through the actual code path, with current numerical thresholds. Each step links to the source file/function and calls out the constants you would tune.

```
GPX/FIT bytes
  └─ gpx.parse_gpx() / fit_parser.parse_fit()           # gpx.py:11 / fit_parser.py:15
       └─ activity_data dict (geometry_geojson, file_hash, …)
            └─ ingest_activity()                        # ingest.py
                 ├─ idempotency: (provider_id) | (file_hash) | (date±5min, dist±10%)
                 ├─ INSERT activities row (private)
                 └─ _update_heat_edges()
                      ├─ Step 0  sport normalization    # offroad now real partition (migration 0035)
                      ├─ Step 1  densification          # 15m gap, > 500m → None sentinel (track break)
                      ├─ Step 2  OSM map-match          # radius 25m road/running, 15m gravel/mtb
                      │     ├─ project each GPS pt → its OSM segment   (_project_onto_segment)
                      │     └─ relaxed run-detector (any-OSM-match, not same osm_way_id)
                      ├─ Step 2b unmatched fallback     # 5dp grid-snap (~1.1m), skip > 60m
                      ├─ Step 3  edge length sanity     # belt-and-braces 60m cap on grid edges
                      ├─ Step 4  canonical merge        # ±5-grid neighbour clustering (skippable)
                      └─ Step 5  batch UPSERT
                           ├─ heat_edges                (ON CONFLICT bumps pass/fwd/bwd counts)
                           ├─ heat_edge_contributors    (sha256(user_id) for K-anon)
                           └─ recompute user_count from contributors
                 └─ debounced heat_edges_display refresh (skippable on bulk imports)
```

### Step 0: Sport normalization (`_normalize_heat_edge_sport`)
`heat_edges` is LIST-partitioned by sport with explicit partitions for `road`, `gravel`, `mtb`, `offroad` (added in migration 0035, May 2026), and `running`. `test_*` / `cache_test_*` are rewritten to `gravel` so the default partition stays empty. Pre-May-2026 the normalizer rewrote `offroad → gravel`; that erased rider intent and is fixed.

`RELATED_SPORTS["offroad"] = ["mtb", "offroad", "gravel"]` — query-time, offroad routes still pull from the gravel + mtb partitions to widen coverage.

### Step 1: Densification + track-break (`_densify_coords`)
- `_DENSIFY_MAX_GAP_M = 15.0` — fill any gap > 15 m with linearly-interpolated points (preserves elevation).
- **Gap > 500 m → insert `None` sentinel between p1 and p2** (May 2026 fix, replacing the old "keep raw, ride straight line" behavior). Downstream consumers — `_match_to_osm`, the grid-snap loop, `get_personal_edges`, `_coords_to_z14_tiles` — skip pairs where either side is `None`. Without this, GPS dropouts on tunnels / forest cover used to produce one mega-edge spanning the dropout (the "spaghetti" you'd see crossing fields).
- Output: a coordinate list where consecutive points are either ≤ 15 m apart, or separated by `None` (track break).

### Step 2 prep: OSM tile pre-warm — **PR #213, May 2026**
Before per-point matching, `_prewarm_osm_segments(coords, db)` issues ONE bulk `WHERE tile_key = ANY(:tile_keys)` query for every tile the activity bbox touches that isn't already in `_osm_segment_cache`. Replaces the per-tile lazy fetch loop. Cuts cold-cache DB IO by ~40 % on bulk imports across new geographic areas (the audit measured 763 cold-tile loads across 1404 ingests = 54 % cache miss rate at the old 50-tile cache; bulk-pre-warming + `OSM_GRID_CACHE_MAX=200` brings it close to zero on warm runs).

### Step 2: OSM map-match (`_match_to_osm`) — **rewritten May 2026**
- For each densified point, find the nearest segment in `osm_road_edges` using a 0.0005°-cell spatial grid (~55 m cells).
- **Sport-aware match radius** (`_OSM_MATCH_RADIUS_BY_SPORT`):
  - road / running: **25 m** (urban GPS multipath is 20–40 m)
  - gravel / mtb / offroad: 15 m (single-track precision matters more)
  Pre-May-2026 it was a flat 15 m for everything; that's why road match rate was 1.6 % and running 6.6 %.
- **Run-detector accepts any-OSM-match as consecutive** (not "same `osm_way_id`"). A long real-world trail in OSM is split into many small ways at intersections / name changes; the strict same-way check broke runs whenever GPS noise made consecutive points land on adjacent ways. Per-edge tagging still uses `point_matches[k]` (correct surface / highway / osm_way_id per edge).
- **Edges follow the OSM line, not raw GPS** (the projection fix that killed the "feathery spaghetti"). For each consecutive matched pair, project the GPS coords onto each point's matched OSM segment via `_project_onto_segment` (orthogonal projection clamped to segment endpoints, equivalent to `ST_ClosestPoint`). Edges store the *projected* (lon, lat) snapped to 5 dp. Multiple riders' offset GPS samples on the same trail collapse to identical positions — `edge_key` matches → edges merge by primary key → `user_count` actually accumulates.
- Quantitative result on the audit (50 activities, Montpellier bbox): edge-to-OSM-way distance dropped from 2.26 m avg to **0.07 m avg** (just 5dp rounding noise); `avg_user_count` rose from 1.31 to 2.17 (+65 %).
- Grid cache: the built spatial index is keyed by tile-set, so two activities covering the same area reuse it. Cache size raised 50 → 200 (`OSM_GRID_CACHE_MAX` env var) to reduce thrash on bulk imports across France. **Caveat for memory-constrained workers** (e.g. the 512 Mi Strava import Cloud Run Job): the cache holds rebuilt grids that reference segments from the per-tile cache, but because long segments are inserted into every grid cell they cover, a 50 k-segment grid can hold ~200 k list slots — caching a handful of these blows past 500 MB and signal-9's the container (observed 2026-05-27 on a 1449-activity re-import). The import job now sets `OSM_GRID_CACHE_MAX=0` + `OSM_TILE_CACHE_MAX=10` to skip grid caching entirely; the rebuild from already-loaded tile segments is CPU-only and sub-second. There is also a defensive per-entry cap `OSM_GRID_CACHE_MAX_SEGS_PER_ENTRY=10000` (default) so even on the API service an oversized grid silently bypasses the cache instead of pushing toward OOM.

### Step 2b: Grid-snap fallback
Coordinates not matched to OSM (and not `None` sentinels) → snap each `(lat, lon)` to **5 decimal places** (~1.1 m grid via `_snap`). Consecutive duplicates after snapping are dropped.

### Step 3: Edge length sanity cap (May 2026 belt-and-braces)
`_MAX_GRID_EDGE_M = 60.0`. After snapping, drop any grid-fallback edge longer than 60 m. The densifier sentinel from Step 1 should already prevent these, but this cap catches data that was either ingested before the sentinel landed or has weird GPS patterns the densifier didn't break (e.g. a < 500 m gap that still produces an unusable 80 m straight line).

### Step 4: Canonical edge merge (`_find_canonical_edge` — ingest.py:849)
Snap is exact at 5dp, but two different riders drifting by ±5 grid cells (~5 m) shouldn't create separate edges. We look up endpoints in a **spatial endpoint index** (built once per activity from existing edges in the activity bbox) and merge to the canonical edge if both endpoints are within ±5 grid cells. Without the index this was a 441-combo brute force per edge — replacing it cut ingest from ~120 s to ~5 s for a 4000-pt trace.

`SKIP_CANONICAL_MERGE=true` skips the bbox query + index entirely (used by full-rebuild jobs where merging is provably unnecessary). 10× speedup, but only safe when you start from an empty heat_edges table.

### Step 5: Batch UPSERT (`_upsert_edges_batch` — ingest.py:971)
- Multi-row `INSERT ... VALUES (…), (…), …` (500 rows/batch).
- `ON CONFLICT (edge_key, sport) DO UPDATE` increments `pass_count`, `forward_count`, `backward_count` based on direction (canonical vs reversed).
- Separate batch into `heat_edge_contributors (edge_key, user_id_hash)` — primary key prevents double-count if a single user's trace crosses an edge twice.
- One trailing query recomputes `heat_edges.user_count` from the contributors table for any edge that got a new contributor.
- Bumps `_edge_version` (in-memory counter used to invalidate routing-graph caches).

### Step 6: Heat cells — REMOVED in May 2026 (PR #214)
The legacy `_update_heat_cells` write path was removed and the cell pipeline is no longer maintained on every ingest. Cells are now **aggregated from `heat_edges` at query time** via `ingest.get_heat_cells_aggregated(sport, bbox, k)` — one SQL grouping by z14 tile derived from each edge's start point. This saves ~30 % of per-activity ingest time.

`/heatmap/export`, `/heatmap/stats`, `/me/unexplored` all use the aggregator now. The `heat_cells` + `heat_cell_contributors` tables still physically exist (a follow-up migration `0037_drop_heat_cells_tables` is staged but `pass`-bodied — it activates after one prod soak cycle).

### Step 7: Materialized view refresh — `heat_edges_display`
After heat_edges write, a debounced 10 s timer fires `REFRESH MATERIALIZED VIEW CONCURRENTLY heat_edges_display`. This is the matview the live `/heatmap/tiles/{sport}/{z}/{x}/{y}.mvt` endpoint reads from — it pre-resolves each heat_edge's OSM way geometry (full multi-point road curve) at write time so per-tile generation is just a `WHERE geometry && envelope` (15–50 ms) instead of a per-tile LATERAL join against 11 GB of OSM data (50–200 ms, plus pile-ups).

Bulk imports set `SKIP_MATVIEW_REFRESH=true` and call `refresh_display_matview()` once at the end. `CONCURRENTLY` requires the matview to be populated at least once first — `alembic upgrade head` only creates an empty placeholder, so dev environments need an initial `python -m app.jobs.refresh_matview` (5–15 min) before the live MVT path becomes fast. See `docs/heatmap-pipeline.md` § Stage 3 and `docs/migration-runbook.md` § matview for the full runbook.

### K-Anonymity
- **Write time**: store everything, no filter.
- **Read time**: endpoints filter `user_count >= K`. Prod K=2 (`HEATMAP_K_ANONYMITY=2`); dev K=1 for visibility.
- User ID → SHA-256 hash. Python's built-in `hash()` is salted per-process and would inflate `user_count` across cold starts.

---

## Tunable parameters — the dials in one place

### Numerical thresholds (in `ingest.py`)

| Constant | Current | What it controls |
|---|---|---|
| `_DENSIFY_MAX_GAP_M` | 15 m | Max gap before linear interpolation kicks in |
| `_DENSIFY_GPS_BREAK_M` | 500 m | Gap > this → insert `None` sentinel (track break, no edge drawn across) |
| `_OSM_MATCH_RADIUS_BY_SPORT` | road / running: 25 m, gravel / mtb / offroad: 15 m | Sport-aware OSM nearest-segment match radius. Road / running is wider because urban GPS multipath is 20–40 m |
| `_OSM_GRID_SIZE` | 0.0005° (~55 m) | Spatial grid cell size for OSM segment lookup |
| `_OSM_MIN_CONSECUTIVE` | 2 | Min consecutive matched points to count as a run |
| `_MAX_GRID_EDGE_M` | 60 m | Drop grid-fallback edges longer than this (4× densifier target) |
| `_GRID_STEP` | 0.0001 (~11 m) | Canonical-merge neighbour radius (Step 4) |
| `_DENSIFY_MAX_GAP_M` for matched edges | n/a | OSM-matched edges aren't length-capped (real roads can be long) |

### Env-var switches

| Var | Default | When to flip it |
|---|---|---|
| `HEATMAP_K_ANONYMITY` | prod 2, dev 1 | Read-time filter for community heatmap |
| `OSM_TILE_CACHE_MAX` | 50 | Per-tile segment cache size. Lowered to 10 on the 512 Mi import job |
| `OSM_GRID_CACHE_MAX` | 200 | Built-grid cache (keyed by tile-set). Bulk imports across France easily exceed 200 distinct sets. **Set to 0 on the 512 Mi import job** — see "Caveat for memory-constrained workers" above |
| `OSM_GRID_CACHE_MAX_SEGS_PER_ENTRY` | 10000 | Defensive per-entry cap. Grids with more segments skip the cache entirely (rebuilt fresh each call). Prevents one oversized region from pushing the API service toward OOM |
| `SKIP_OSM_FETCH` | false | Skip Overpass fallback (use PBF data only) — prod default |
| `SKIP_CANONICAL_MERGE` | false | Skip the per-activity bbox query + ±5-grid merge (Step 4). Bulk-import scripts set it to `true` automatically |
| `SKIP_MATVIEW_REFRESH` | false | Skip the debounced `heat_edges_display` refresh (each one is 5–35 s on the live matview). Bulk-import scripts set it and call `refresh_display_matview()` once at the end |
| `TRUNCATE_FIRST` | false | (rebuild_heatmap) wipe heatmap tables before rebuild |
| `HEATMAP_WORKERS` | 4 | (rebuild_heatmap) parallel worker threads |
| `import_strava_export.py --workers` | 4 | Bulk-import parallelism |
| Grid-fallback length cap (PMTiles export) | 60 m | Same filter applied at PMTiles build time (`build_pmtiles.py`) |

---

## Database Schema

### Private (never public)
- `activities` — user activity records. Has both `geometry_geojson` (TEXT, legacy) **and** `geometry` (PostGIS `LINESTRING(4326)`, binary, ~3× smaller, queryable). Migration 0036 added the binary column; readers should prefer it. The TEXT column will be dropped in a follow-up PR once all readers migrate.
- `activity_cells` — z14 tile coverage per activity

### Common (ODbL license)
- `heat_edges` — snapped trace segments, **partitioned by sport** (road, gravel, mtb, offroad, running + default — `offroad` partition added in 0035): `edge_key, sport, pass_count, forward_count, backward_count, ele_delta_m, slope_grade, surface_type, highway_type, osm_way_id, geometry`. Unique constraint: `(edge_key, sport)`
- `heat_edge_contributors` — `(edge_key, user_id_hash)` for K-anonymity dedup
- `heat_cells` / `heat_cell_contributors` — **legacy tables, no longer written** (PR #214 removed `_update_heat_cells`). Reads aggregate from `heat_edges` at query time. Tables stay until migration 0037 is activated post-soak.

### Trail layers
- `dfci_edges` — DFCI fire-prevention tracks (Overpass, `ref:FR:DFCI`)
- `trail_edges` — GR, GRP, GT, PR, EuroVelo (Overpass relations)
- `osm_road_edges` — OSM road network cache (PBF import + Overpass fallback)

---

## Graph Serving

### Heatmap Display — MVT Vector Tiles
`GET /heatmap/tiles/{sport}/{z}/{x}/{y}.mvt` — Mapbox Vector Tiles (PostGIS `ST_AsMVT`)

- **z6-z10**: Returns clustered centroid points (`heat_points` layer) for Strava-style heatmap glow
  - Snap-to-grid aggregation: nearby edges merged into weighted points
  - Grid size adapts per zoom (0.1° at z6 → 0.005° at z10)
- **z11+**: Returns line geometries (`trails` layer) for detailed trail rendering
- **CDN pre-cache**: z8-z12 tiles pre-generated during publish (static files, <50ms)
- **Cache-bust**: Frontend appends `?v={manifest_version}` for freshness after import

### Routing Graph — Binary Protobuf (CTGB)
`GET /routing/graph/{sport}/area.pb` — viewport-based binary graph (~50km radius)

- **CTGB format**: 8-byte header + 20 bytes/edge (~65% smaller than JSON)
  ```
  Header: "CTGB" (magic) + version(1) + zoom(0) + reserved(2)
  Edge:   int32 lon1×100000, int32 lat1×100000, int32 lon2×100000, int32 lat2×100000,
          uint8 userCount, int8 slopeGrade×2, uint8 highway|surface, uint8 trailIdx
  ```
- Encoder: `graph_builder.encode_edges_binary()` (single source of truth)
- Frontend decoder: `graph-binary.ts`
- Loaded on route mode entry, re-fetched on pan (50km radius around viewport center)

### Z14 Tiles
`GET /routing/graph/{sport}/{z}/{x}/{y}.json` — per-tile edges (z=14 only)

### Index Enums (shared: `graph_builder.py` → `graph_tiles.py` → `client-graph.ts`)
```
SURFACE:  asphalt=0, gravel=1, dirt=2, rock=3, unknown=4
HIGHWAY:  residential=0, tertiary=1, secondary=2, primary=3, unclassified=4,
          track=5, path=6, cycleway=7, steps=8, unknown=9
TRAIL:    none=0, DFCI=1, GR=2, GRP=3, GT=4, PR=5, EV=6
```

---

## CDN Write-Through Cache

After import, pre-computed files are written to Cloud Storage for static serving. The frontend loads from CDN first (protobuf preferred), falls back to API.

### Published Files
| File | Path | Format | Size |
|------|------|--------|------|
| Manifest | `/api-cache/manifest.json` | JSON | <1KB |
| Summary | `/api-cache/v{N}/heatmap/summary.json` | JSON | <1KB |
| Trails (per sport) | `/api-cache/v{N}/heatmap/trails/{sport}.json.gz` | gzip JSON | 100KB-5MB |
| DFCI tracks | `/api-cache/v{N}/heatmap/dfci.json.gz` | gzip JSON | 200KB |
| Graph JSON | `/api-cache/v{N}/routing/graph/{sport}/full.json.gz` | gzip JSON | 1-5MB |
| **Graph Protobuf** | `/api-cache/v{N}/routing/graph/{sport}/full.pb` | CTGB binary | 500KB-3MB |

### Flow
1. Import completes → `publish_heatmap_cache()` writes versioned files + manifest
2. Frontend fetches `manifest.json` (60s cache) → discovers current version
3. Frontend loads `full.pb` from CDN (protobuf, 44% smaller) → JSON fallback → API fallback
4. Old versions cleaned up automatically (keep 2 for CDN edge consistency)

### Implementation
- `backend/app/services/graph_builder.py` — computation (single source of truth)
- `backend/app/services/cache_writer.py` — GCS write + versioning + rollback
- `frontend/lib/cdn-cache.ts` — manifest fetch + protobuf/JSON/API cascade
- Opt-in via `PUBLISH_CACHE=true` env var

See `docs/spec-cdn-write-through.md` for full spec.

---

## Frontend Client-Side Routing

### Web Worker Architecture
- `routing-worker.ts` — owns the `ClientGraph`, runs off main thread
- `routing-worker-client.ts` — main thread proxy with heartbeat monitoring
- `client-graph.ts` — TypeScript routing engine (Dijkstra)

### ClientGraph Structure
- `adj: Map<string, EdgeEntry[]>` — adjacency list
- `spatialGrid: Map<string, string[]>` — 0.001° cells (~110m) for O(1) snap
- Cost model: exact port of backend `routing_profiles.py` (slope, surface, heat score)

### Routing Cascade
1. Client graph (instant, offline — from CDN protobuf or API)
2. Backend smart routing (Dijkstra + pgRouting)
3. BRouter / OSRM (external fallback)
4. Straight line (final fallback)

---

## Performance Characteristics

| Operation | Typical Time | Notes |
|-----------|-------------|-------|
| Single activity (~4k pts) | < 2s | Batch UPSERT, OSM cache hit |
| 5 activities (~18k pts) | < 8s | Tile cache reuse across activities |
| Heat cells (~100 cells) | < 500ms | 3 batch queries total |
| Edge throughput | > 500 edges/s | With OSM matching enabled |
| Duplicate ingest | ≤ 2x first | UPDATE path vs INSERT |

### Optimization History
1. **Flat-earth distance**: Replace haversine with inlined arithmetic — 2.5x faster per-point matching
2. **Batch UPSERT**: Multi-row VALUES instead of per-edge INSERT — ~100x fewer DB round-trips
3. **Deterministic hash**: SHA-256 instead of Python `hash()` — fixes contributor count inflation across cold starts
4. **GPS-point edges**: Use GPS coordinates instead of OSM node coordinates — eliminates micro-gaps
5. **Parallel rebuild**: `rebuild_heatmap_parallel()` with ThreadPoolExecutor (4 workers, deadlock retry)
6. **Cache prewarm**: Load all OSM tiles upfront before parallel workers start
7. **Grid cache**: Spatial grids cached by tile set — reused across overlapping activities
8. **Skip canonical merge**: `SKIP_CANONICAL_MERGE=true` skips bbox query during rebuild (~10x faster)
9. **Version-based caching**: In-memory caches invalidate on edge_version change, not TTL
10. **24h Cache-Control**: Browser/CDN caches stable endpoints for 24h

---

## Heatmap Rebuild (Production)

### Recommended: Cloud Run Job (same-region, no proxy latency)

```bash
# Update job image to latest deploy
IMAGE=$(gcloud run services describe common-trails-api-prod --region europe-west1 \
  --format="value(spec.template.spec.containers[0].image)")
gcloud run jobs update common-trails-rebuild-heatmap-prod \
  --region europe-west1 --image="$IMAGE" \
  --set-env-vars="HEATMAP_WORKERS=4,SKIP_OSM_FETCH=true,TRUNCATE_FIRST=true,SKIP_CANONICAL_MERGE=true" \
  --quiet

# Execute
gcloud run jobs execute common-trails-rebuild-heatmap-prod --region europe-west1

# Monitor progress
gcloud logging read "resource.type=cloud_run_job \
  AND labels.\"run.googleapis.com/execution_name\"=<execution_name> \
  AND textPayload:Progress" --limit=10 --format="value(textPayload)" --order=desc
```

**Environment variables:**
| Var | Default | Description |
|-----|---------|-------------|
| `HEATMAP_WORKERS` | `4` | Parallel worker threads |
| `TRUNCATE_FIRST` | `false` | Wipe heatmap tables before rebuild (explicit opt-in) |
| `SKIP_OSM_FETCH` | `false` | Skip Overpass API calls (use PBF data only) |
| `SKIP_CANONICAL_MERGE` | `false` | Skip bbox query + neighbor merge (faster rebuild) |
| `SKIP_MATVIEW_REFRESH` | `false` | Skip the debounced `heat_edges_display` refresh during the loop. Bulk-import scripts call `refresh_display_matview()` once at the end. Saves 10–40 % of total ingest time. |
| `OSM_GRID_CACHE_MAX` | `200` | Max distinct tile-set spatial grids cached in memory. Bulk imports across France easily blow past 50 (the previous default). **Set to 0 on memory-constrained Cloud Run Jobs.** |
| `OSM_GRID_CACHE_MAX_SEGS_PER_ENTRY` | `10000` | Defensive per-entry cap; oversized grids bypass caching instead of risking OOM. |
| `PUBLISH_CACHE` | `false` | Write CDN cache to Cloud Storage after rebuild |

### Performance audit (May 2026)

`scripts/audit_ingest_perf.py` benchmarks the pipeline end-to-end and produces a fragmentation report so "spaghetti vs continuum" is a number, not a vibe. Run with `--sample N` for a quick benchmark or `--full` for everything:

```bash
docker compose exec backend python -m scripts.audit_ingest_perf /strava-export \
    --full --workers 4 --with-heat --fragmentation-bbox '3.85,43.55,3.95,43.65'
```

**Pre-fix baseline** (50-file sample, sequential, 15 m radius, raw GPS coords, matview-refresh during loop, strict same-`osm_way_id` run-detector):

| metric | value |
|---|---|
| 50 files | **752 s** (0.07 files/s) |
| ingest mean | 14.8 s |
| ingest p95 | 56.8 s |
| OSM match rate | 79.4 % |
| edge-to-OSM-way distance | avg 2.26 m, p95 7.71 m, max 14.73 m |
| Montpellier `avg_user_count` | 1.31 |

**Post-fix** (50-file sample, sequential — same code path, just the fixes):

| metric | value | delta |
|---|---|---|
| 50 files | **305 s** (0.16 files/s) | **2.5× faster** |
| ingest mean | 5.76 s | 2.6× |
| ingest p95 | 18.1 s | 3.1× |
| OSM match rate | 81.8 % | + 2.4 pt |
| edge-to-OSM-way distance | avg 0.07 m, p95 0.30 m, max 0.55 m | **30× closer** |
| Montpellier `avg_user_count` | 2.17 | + 65 % |

The 30× collapse in edge-to-OSM-way distance is the spaghetti fix as a number — the perpendicular fan-out collapses to 7 cm of 5dp-grid rounding noise. The 65 % bump in `avg_user_count` is the run-detector relaxation: more activities share their edges instead of each rider's GPS noise producing distinct edge_keys.

**Full re-ingest** (1404 files, 4 workers, end-to-end):

| metric | value |
|---|---|
| total time | **41.6 min** (vs ~2.7 h sequential = 4× speedup from parallelism) |
| imported | 1375 |
| failed | 29 (all `psycopg2.errors.DeadlockDetected`; `retry_on_deadlock` fixes future runs) |
| read+gunzip total | 9 s (negligible) |
| parse total | 582 s (mean 0.42 s, p95 0.51 s, FIT outlier 28 s on one file) |
| ingest+heat total | 9292 s (mean 6.76 s, p50 2.27 s, p95 33 s, max 56 s) |
| OSM match rate | **84.0 %** (3.47 M matched / 4.14 M total raw edge events) |
| unique edges in DB | 167 k (4.1 M raw events deduped via `edge_key`) |
| **avg `user_count` per edge** | **2.23** |

Read + parse are essentially free; ingest+heat dominates at 99 %. Within ingest, OSM tile loading on cold tiles is the biggest single cost (763 cold-tile loads across 1404 ingests = 54 % cache miss rate even with `OSM_GRID_CACHE_MAX=200`). Pre-warming tiles around the activity bbox in a single query is the next big lever — see "Improvement opportunities" → B.

### Alternative: Strava Re-import

If you need to re-fetch GPS streams from Strava (e.g., after upgrading polyline → high-res):

```bash
# 1. Truncate heatmap tables via Cloud SQL proxy
PGPASSWORD="<password>" psql "host=127.0.0.1 port=9470 dbname=common_trails user=api" \
  -c "TRUNCATE heat_edge_contributors, heat_cell_contributors, heat_edges, heat_cells CASCADE;"

# 2. Create import job + trigger
PGPASSWORD="<password>" psql ... \
  -c "INSERT INTO import_jobs (id, user_id, provider, status)
      VALUES (gen_random_uuid(), '<user_id>', 'strava', 'PENDING') RETURNING id;"

gcloud run jobs execute common-trails-import-strava-prod \
  --region europe-west1 \
  --update-env-vars="IMPORT_JOB_ID=<job_id>"
```

---

## Improvement opportunities

Audit performed end-of-May 2026 against the user's 1406-activity Strava export. Eight items landed in this PR; eight more are still open with priority + effort estimates.

### Landed in May 2026

#### 1. OSM map-match radius is sport-aware
Live counts (`user_count >= 1`) when the audit started:

| sport | total edges | OSM-matched | % matched |
|---|---:|---:|---:|
| road | 3.23 M | 53 k | **1.6 %** |
| running | 1.96 M | 129 k | **6.6 %** |
| gravel | 845 k | 380 k | 45.0 % |
| mtb | 378 k | 173 k | 45.9 % |

Road / running were catastrophic — GPS multipath in cities easily produces 20–40 m drift, and a flat 15 m radius rejected most of it. **Per-sport radius via `_OSM_MATCH_RADIUS_BY_SPORT`**: road / running 25 m, gravel / mtb / offroad 15 m. Effect after re-ingest of the audit sample: 81.8 % match rate (up from ~10–15 % depending on sport mix).

#### 2. Densifier breaks polylines on GPS gaps > 500 m
Replaced "skip interpolation, keep `p2`" with "insert `None` sentinel". All downstream consumers (`_match_to_osm`, `_update_heat_edges` grid loop, `get_personal_edges`, `_coords_to_z14_tiles`, bbox computation) skip pairs where either side is `None`. Eliminates the multi-km mega-edges that crossed fields.

#### 3. OSM-matched edges follow the OSM line, not raw GPS (the projection fix)
The high-zoom "feathery spaghetti" had a real signal in the data. Each rider's GPS jitter snapped to slightly different 5dp cells perpendicular to the trail; even with successful OSM match, the *edge geometry* used raw GPS, so co-trail edges from different riders never shared an `edge_key`. Result: 4 contributors → 4 separate `user_count=1` edges fanning around the actual trail.

`_project_onto_segment` projects each GPS coord onto its matched OSM segment (orthogonal projection clamped to endpoints, equivalent to PostGIS `ST_ClosestPoint`). Multiple riders' projections collapse to identical positions → edges merge by primary key.

| metric | pre-fix | post-fix |
|---|---|---|
| edge-to-OSM-way distance, avg | 2.26 m | **0.07 m** |
| edge-to-OSM-way distance, max | 14.73 m | 0.55 m |
| Montpellier `avg_user_count` | 1.31 | **2.17** |

Regression test: `tests/test_pending_bug_fixes.py::TestOsmMatchProjectsToOsmGeometry` (3 cases — projection collapse, endpoint clamp, end-to-end on `_match_to_osm`).

#### 4. Run-detector accepts any-OSM-match (was: same `osm_way_id`)
A long real-world trail in OSM is split into many small ways at intersections. GPS noise easily made consecutive points land on adjacent ways of the *same trail*; the strict same-way check then failed `_OSM_MIN_CONSECUTIVE` and the run fell through to grid-snap. Per-edge tagging (surface / highway / osm_way_id) still uses `point_matches[k]` so each edge gets the right metadata; we only loosened the *run-detection* boundary.

#### 5. `offroad` is now a real partition (migration 0035)
Until May 2026 `_normalize_heat_edge_sport` rewrote `offroad → gravel` because `heat_edges` had no offroad partition. Migration 0035 adds `heat_edges_offroad`; the normalizer keeps `offroad` as-is. Pre-existing offroad rides remain in `heat_edges_gravel` (no backfill — the heat_edges → activity link doesn't carry the original sport tag). Forward-looking only.

#### 6. `SKIP_MATVIEW_REFRESH` env flag for bulk imports
Each `REFRESH MATERIALIZED VIEW CONCURRENTLY heat_edges_display` takes 5–35 s, and the 10 s debounce only collapses concurrent calls. Sequential imports triggered one refresh every ~10 s, eating 10–40 % of total ingest time. Bulk-import scripts (`audit_ingest_perf.py`, `import_strava_export.py`) set the flag and call `refresh_display_matview()` once at the end. The helper is defensive against orphan-refresh pile-ups (one killed audit run left the audit hanging 56 min on a queue of 18 stuck refreshes).

#### 7. `OSM_GRID_CACHE_MAX` 50 → 200 — *and back to 0 on the import job (2026-05-27 postmortem)*
A bulk import scattered across France produces 200+ distinct tile-sets; the old default 50 caused 5–15 s of segment loading + grid rebuild on every miss. Env-tunable via `OSM_GRID_CACHE_MAX`.

**Memory regression discovered 2026-05-27**: on a 512 Mi Cloud Run Job (`common-trails-import-strava-prod`), caching ~50 large grids (each ~50 k segments × ~200 k list refs after long-segment cell duplication) overflows the budget and gets SIGKILL'd mid-Phase-3. The import job was changed to `OSM_GRID_CACHE_MAX=0` (rebuild every call — CPU-only on already-loaded tiles, sub-second) and `OSM_TILE_CACHE_MAX=10` (smaller tile LRU). The API service keeps the 200 cap because (a) its instance has 4 GiB and (b) route overlap between users means the cache hit-rate is high there. A defensive `OSM_GRID_CACHE_MAX_SEGS_PER_ENTRY=10000` was also added so single oversized grids never enter the cache anywhere.

#### 8. `import_strava_export.py --workers 4`
Per-activity ingest is single-threaded; the script now uses `ThreadPoolExecutor`. Threading-safe because `ingest_activity` opens its own `SessionLocal` and `ON CONFLICT` handles concurrent UPSERTs. Brings the 1406-activity import from ~5 h sequential to ~1 h with 4 workers.

### Still open

#### A. Densification target (15 m) > 5dp snap grid (~1.1 m) creates dedup misses on grid-fallback edges
Affects only the ~18 % of edges that fall through to grid-snap (after the May 2026 fixes). The projection fix collapses OSM-matched edges already; this is the residual fan-out for off-OSM trails. **Cheap fix**: drop `_DENSIFY_MAX_GAP_M` to 5 m for grid-fallback only. Doubles dedup hit rate at the cost of ~2× more interpolated points (still cheap relative to OSM matching).

#### B. OSM segment loading is slow on cold tiles
"OSM match: 74 711 segments loaded (41 tiles), matching may be slow" shows up regularly during bulk imports for new geographic areas. Each cache miss is one DB query + spatial-grid build. **Fix**: pre-warm tiles around the activity bbox in a single query (instead of per-tile lazy load). On a 50-activity audit, ~30 % of ingest time was OSM tile loads.

#### C. `heat_edges_display` matview is rebuilt entirely on every refresh
PG doesn't support incremental matview refresh natively. Could approximate with a "dirty bbox" approach: track which areas got new edges, only `REFRESH ... CONCURRENTLY` for those. Or skip the matview entirely on the read side — the SQL it materializes (filter by `user_count >= K` + drop spaghetti) is now also enforced in the live MVT path, so the matview adds little beyond pre-aggregation.

#### D. `_load_existing_keys` bbox query is per-activity
The canonical-merge spatial-endpoint index is rebuilt for every activity from a fresh bbox query on `heat_edges`. Bulk-import scripts skip this entirely now (`SKIP_CANONICAL_MERGE=true`), but the per-activity GPX-upload path still pays it. **Fix**: cache the index across activities in the same area within a session.

#### E. Strava resync is monolithic
`resync_strava.py` holds an advisory lock for the whole run and rebuilds heat in-line. Single failure restarts the whole job. **Fix**: chunk per ~50 activities, commit + release lock between chunks, resume from `last_synced_at`.

#### F. Each ingest schedules its own matview-refresh timer
`_schedule_matview_refresh` cancels and recreates a `threading.Timer` per call. Not expensive in itself, but the timer thread holds a separate DB connection from the pool when it fires. Could be replaced with a single dedicated background thread that polls a "dirty" flag.

#### G. Verified-correct (don't change)
- **`pass_count` derivability**: contributors PK is `(edge_key, user_id_hash)`, no per-pass log exists. Can't be derived. Drift detection would need a new `heat_edge_passes` table — not justified by data (>50× anomalies are 0.01–0.6% per sport and consistent with legit repeat riders).
- **`_update_heat_cells`**: feeds `/heatmap/export` (the ODbL download in stats page). Real dependency — not dead weight.

#### H. Existing scaling items still relevant
1. ~~Async ingest via Cloud Tasks~~ — **landed**: see "GPX File Upload" / "Internal Heat Compute Handler" above. `/gpx/upload` and `/imports/files` defer heat-edge work to a Cloud Tasks queue → user-facing 202 in ~200 ms instead of 5–30 s.
2. Materialized views for K-anonymity — pre-compute filtered edges per K threshold.
3. AlloyDB Omni — PostgreSQL-compatible, runs on Cloud Run, eliminates Cloud SQL cost.

See `docs/scaling-100-users.md` for the full roadmap.
