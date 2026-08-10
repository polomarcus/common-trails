# Heatmap Creation Pipeline

## Overview

The heatmap shows community cycling activity as heat-weighted trails on the map. The pipeline transforms raw GPS traces into styled vector tiles:

```
GPS Trace → Map-match (HMM) → heat_edges → by-way aggregation → MVT / PMTiles → MapLibre
```

(The `heat_edges_display` materialized view was removed June 2026 — the by-way aggregation now runs from one shared SQL builder; see Stage 3.)

## Stage 1: GPS Trace Ingestion

**Entry point:** `backend/app/services/ingest.py` → `ingest_activity()`

When a user uploads a GPX or syncs from Strava:

1. **Parse coordinates** from GPX/GeoJSON.
2. **Densify (two-tier)** — `_densify_coords()`: gaps ≤ 15 m get interpolated points so the
   matcher has a dense chain; gaps in **[500 m, 2500 m]** are *bridged* (densified across) so a
   downsampled-but-continuous ride stays continuous; gaps **> 2500 m** become a hard `None` break
   (real GPS dropout / car transfer → no phantom straight-line edge). *Why two-tier:* a single
   threshold either fragmented downsampled exports or painted fake heat across genuine dropouts.
3. **Map-matching** — `_match_to_osm()` turns the noisy GPS chain into "which OSM way did they
   ride" with an **in-process Viterbi HMM** (`_viterbi_point_matches`, `SPATIAL_HMM_ENABLED`,
   default on) that models the WHOLE trajectory: *emission* = Gaussian on perpendicular distance
   to a candidate way (σ ≈ 6 m), *transition* = topological plausibility (same way ≈ 0 /
   junction-connected small / disconnected forbidden). Viterbi picks the globally most-likely way
   sequence, so it stays committed to the ridden way (rejecting the parallel cycleway) and
   tolerates brief off-radius excursions (no run-break at curve apexes). Setting
   `SPATIAL_HMM_ENABLED=false` falls back to the legacy per-point nearest-snap (nearest way within
   15 m). Both use the same spatial grid over `osm_road_edges` (55 m cells, 3×3-cell candidate
   lookup). See `docs/heatmap-map-matching-plan.md`.
4. **Snap coordinates** for cross-rider edge dedup (so two riders on one road share an `edge_key`):
   - **OSM-matched:** project each point onto its matched OSM segment (`_project_onto_segment`),
     then bucket along the segment's **arc-length at 1 m** (`_snap_along_segment`) — keeps the point
     exactly ON the OSM line (0 m perpendicular) while collapsing nearby riders to one coord.
     *(The old 5dp `_snap_fine` was removed May 2026 — it pushed points off the line and broke the
     routing-graph vertex sharing.)*
   - **Grid fallback** (no OSM match): 4 decimal places (~11 m) via `_snap()`.
5. **Create edges** between consecutive snapped points; tag each with its **per-point** `osm_way_id`
   (the way that point matched — NOT a run-level id; the run-level bug collapsed rides onto ~2 ways
   and broke the by-way merge, fixed #420).
6. **UPSERT** into `heat_edges` (increment `user_count` only for a new distinct contributor,
   `pass_count` for every traversal; contributor PK = `(edge_key, user, activity)` so a re-upload
   doesn't double-count).

**Key tables:**
- `heat_edges` — partitioned by sport. Each row = one directed edge between two snapped points, carrying `user_count` / `pass_count` / `osm_way_id` / surface / highway. Row count scales with ingested activities (local single-user ~2.0 M; prod scales with users).
- `heat_edge_contributors` — which user contributed to which edge (drives K-anonymity + the `?days=` time filter).
- **`osm_road_edges` — the imported OpenStreetMap road/path network.** The reference map the matcher snaps GPS onto. Built by `app/cli/import_osm_roads.py` from a Geofabrik **PBF** (e.g. `france-south.osm.pbf`), parsed with `pyosmium`. **One row per *sub-segment* of an OSM way** (a way = a road/path, split into consecutive 2-point segments), so one road becomes many rows. Each row carries `osm_way_id` + `segment_idx` (which way + position — lets the display re-assemble the whole way), `geometry` (the 2-point sub-segment the spatial grid indexes for fast nearest-lookup), **`way_geometry`** (the *full* multi-point curve of the whole way — what Stage 4's aggregation uses to render smooth roads instead of 2-point hops), and `surface`/`highway`/`bridge_yes`/`tunnel_yes` OSM tags. **Row count depends entirely on the imported region** (it's the whole OSM road graph there, not user data): local `occitanie` ≈ **7.2 M**, `france-south` ≈ **9.8 M**, `france` ≈ 50 M (too big for a laptop). Biggest table on disk (~11 GB for france-south); **read-only reference data**, rebuilt only on a PBF re-import.

## Stage 2: Heat Edge Storage

**Schema (`heat_edges`):**
```
edge_key TEXT UNIQUE    -- "sport/lat1,lon1/lat2,lon2" (canonical direction)
sport TEXT              -- road, gravel, mtb, offroad, running
user_count INT          -- distinct users (K-anonymity threshold)
pass_count INT          -- total traversals
geometry LINESTRING     -- 2-point segment (snapped endpoints)
bucket INT              -- popularity bucket (1-5, computed from user_count)
surface_type TEXT       -- asphalt, gravel, dirt, rock, unknown
highway_type TEXT       -- from OSM tags
```

**Popularity buckets:**
| Bucket | user_count | heat_score | Meaning |
|--------|-----------|------------|---------|
| 1 | 1-2 | 0.0-0.32 | Rarely used |
| 2 | 3-5 | 0.32-0.52 | Occasional |
| 3 | 6-15 | 0.52-0.80 | Popular |
| 4 | 16-50 | 0.80-1.0 | Very popular |
| 5 | 51+ | 1.0 | Highway |

## Stage 3: ~~Materialized View (`heat_edges_display`)~~ — REMOVED (June 2026)

**The `heat_edges_display` materialized view was dropped.** It only
accelerated the LIVE `/heatmap/tiles` MVT endpoint — which the frontend uses
*only as a fallback* behind the primary static PMTiles (`frontend/lib/cdn-cache.ts`,
Stage 5). For a rarely-hit fallback it cost a refresh-on-every-ingest, a 5–15 min
build, a `WHERE false` placeholder gotcha, and a 27-min pre-warm hazard.

The by-OSM-way aggregation it pre-computed (GROUP BY `(osm_way_id, sport)` +
LATERAL-join `osm_road_edges` for the smooth `way_geometry` + K-anon +
grid-fallback filtering) now lives in ONE shared builder,
`app/services/heat_aggregation.py::build_heat_aggregation_sql`, called by BOTH
the static PMTiles export (Stage 5) and the live MVT endpoint (Stage 4, z11+).
The live endpoint runs that aggregation against `heat_edges` **directly at
request time** (~50–200 ms for the rarely-hit fallback) — it still resolves the
smooth OSM way geometry, so it never regresses to 2-point hops. There is no
matview to build, refresh, or pre-warm anymore.

Migration `0055_drop_heat_edges_display_matview.py` drops the view + its
indexes.

## Stage 4: MVT Tile Generation

### Why MVT tiles at all
The browser can't load millions of heat_edges as GeoJSON — hundreds of MB would freeze MapLibre. **Vector tiles (MVT)** chop the world into 256-px tiles per zoom; the browser fetches only the few tiles in view, each a tiny pre-projected binary MapLibre renders on the GPU. That's what makes "millions of community edges" pannable. Two ways we produce them:
- **Static PMTiles** (Stage 5, the DEFAULT served path) — the *whole* tileset pre-rendered once into one `heatmap-display.pmtiles` file, served from disk/CDN with **zero DB hit per tile**. The "free heatmap at scale" mechanism (`app/jobs/build_pmtiles.py`).
- **Live MVT endpoint** (below) — generates a tile on demand from `heat_edges`. The frontend uses it only as a **fallback** behind PMTiles (`frontend/lib/cdn-cache.ts`: PMTiles → CDN → this endpoint), and for **dynamic** queries the static file can't answer (e.g. `?days=N` time-filtered heat).

### Why zoom-dependent rendering
A z6 tile spans ~150 km — drawing every edge there is pointless and huge, so low zooms **cluster** points to a coarse grid (`heat_points`). High zooms show actual trail geometry (`trails`), simplified at z11-13 and full-detail at z14+. This caps each tile's byte size so panning stays fast at every scale.

### Why the by-way aggregation
Both paths resolve each heat_edge to its smooth OSM `way_geometry` via the shared `app/services/heat_aggregation.py::build_heat_aggregation_sql` (GROUP BY `(osm_way_id, sport)` + LATERAL-join `osm_road_edges`). PMTiles bakes it ahead of time; the live endpoint runs it at request time (~50-200 ms — acceptable for a rarely-hit fallback). Without the by-way grouping, N traces on one road render as N overlapping 2-point segments (z16+ micro-zigzags).

**Endpoint:** `GET /heatmap/tiles/{sport}/{z}/{x}/{y}.mvt`

**Zoom-dependent rendering:**

| Zoom | Source | Processing | Layer |
|------|--------|-----------|-------|
| z6-10 | `heat_edges` | Clustered points (snap to degree grid) | `heat_points` |
| z11-13 | `heat_edges` (shared by-way aggregation) | SimplifyPreserveTopology | `trails` |
| z14+ | `heat_edges` (shared by-way aggregation) | Direct geometry | `trails` |

**z11+ path (~50-200ms, shared `build_heat_aggregation_sql` CTE chain):**
```sql
WITH heat AS (... K-anon + bbox + grid-fallback filters ...),
     osm_grouped AS (... GROUP BY osm_way_id, sport ...),
     osm_matched AS (... LATERAL join osm_road_edges for way_geometry ...),
     grid_fallback AS (...),
     combined AS (SELECT * FROM osm_matched UNION ALL SELECT * FROM grid_fallback)
SELECT ST_AsMVT(edges, 'trails', 4096, 'mvt_geom') FROM (
  SELECT ST_AsMVTGeom(ST_Transform(geometry, 3857), ST_TileEnvelope(...)), ...
  FROM combined ORDER BY user_count DESC LIMIT :row_limit
) edges
```

The SAME CTE chain (minus the bbox predicate, whole-world) feeds the static
PMTiles build, so the two display paths cannot drift.

**Caching:**
- In-memory LRU (500 tiles, version-tagged) — reduced from 2000 to limit memory
- Concurrent tile generation capped by `TILE_GEN_CONCURRENCY` semaphore (default 4) — each MVT tile uses 100-500MB peak
- HTTP `Cache-Control: public, max-age=86400`
- Gzip level 1 (fast CPU, adequate compression for MVT)
- Version-based invalidation (edge_version counter increments on ingest)

**Time filtering:** Optional `?days=N` parameter JOINs `heat_edge_contributors` to filter by `activity_date`.

**Sport expansion:** `offroad` expands to `['gravel', 'mtb']`.

## Stage 5: Frontend Rendering

**MapLibre layers (3 layers from same MVT source):**

1. **`community-trails-glow`** (z10+) — Wide blurred line, soft purple bloom. Width + blur driven by `heat_score`.
2. **`community-trails-line`** (z10+) — Crisp core line. Color ramp: dark purple → lavender → near-white for popular. Width 0.6-8px driven by `heat_score` + zoom.
3. **`community-trails-hit`** (z11+) — Invisible 16px click target for explore mode popups.

Plus **`community-trails-points`** (z6-10) — Purple circles for overview zoom.

**Paint properties:** All use `['interpolate', ['linear'], ['get', 'heat_score'], ...]` for data-driven styling. Popular edges render on top via `line-sort-key: ['get', 'user_count']`.

**Tile prefetching:**
- Visible tiles prefetched on sport/time filter change
- Predictive prefetch on pan (50% viewport ahead in pan direction)
- Service Worker cache (stale-while-revalidate)

## Operations

### Rebuild heatmap (re-process all activities)
```bash
docker compose exec backend python -m app.jobs.rebuild_heatmap
# Bbox filter:
docker compose exec backend python -m app.jobs.rebuild_heatmap --bbox "3.5,43.4,4.1,43.8"
# Incremental (last 24h):
docker compose exec backend python -m app.jobs.rebuild_heatmap --since 24h
```

### Rebuild the display PMTiles (the browser-facing artefact)
```bash
docker compose exec backend python -m app.jobs.build_pmtiles --output-dir /tmp --min-uc 2
# or: make pmtiles
```
(The `heat_edges_display` materialized view was dropped June 2026 — the live
MVT endpoint aggregates `heat_edges` directly, so there is no matview to
refresh. The PMTiles binary is the primary display artefact.)

### Import OSM roads from PBF
```bash
docker compose exec backend python -m app.cli.import_osm_roads --region occitanie
```

### Build .fgraph files (pre-built routing graphs)
```bash
./wasm-router/build.sh --prebuild
```

### Edge enrichment (CLI, NOT at startup)

Enrichment tasks are **never run at startup** — they are baked into the
import scripts (DEM slopes) and exposed as CLI commands for ad-hoc runs.
Startup should be O(1), not O(N). Previously, enrichment ran on every
container restart and wasted 3+ minutes for 0 new rows on a populated DB.

```bash
# Run all enrichment tasks
docker compose exec backend python -m app.cli.enrich_edges all

# Individual tasks
docker compose exec backend python -m app.cli.enrich_edges heat-edges          # OSM tag enrichment via Overpass
docker compose exec backend python -m app.cli.enrich_edges surfaces            # DFCI/trail → heat_edges surface spatial join
docker compose exec backend python -m app.cli.enrich_edges slopes              # DEM-derived slopes for DFCI + trail edges
docker compose exec backend python -m app.cli.enrich_edges dangerous-highways  # tag motorways/trunks
```

**When to run:**
- After `rebuild_heatmap` → run `heat-edges` + `surfaces` + `dangerous-highways`
- After a fresh DFCI/trail import → `slopes` (usually automatic — baked into import scripts)
- After new OSM PBF import → `surfaces` (re-joins against new DFCI/trail data)

**DEM slopes at import time:** `import_dfci_ign`, `import_dfci_herault`,
and `import_trails` now call `enrich_dfci_trail_slopes(source=...)` inline
after inserting new edges. Subsequent startup sees `slope_grade != 0` and
skips. No external API calls at boot.

## PMTiles: Primary Heatmap Tile System

The heatmap uses **PMTiles** — a single static file containing all vector tiles for z6-z14.
Served from Cloud Storage + CDN via HTTP range requests. Zero backend computation at runtime.

### How it works

```
heat_edges (PostGIS) → GeoJSON export → tippecanoe → heatmap-display.pmtiles → CDN
                                                                                  ↓
                                                            MapLibre (pmtiles:// protocol)
```

1. **Build**: `python -m app.jobs.build_pmtiles` (default `--min-uc 1` for dev/beta, use `--min-uc 2` for production K-anonymity)
2. **Upload**: single file to GCS bucket or `/frontend/public/` for dev
3. **Serve**: MapLibre fetches tiles via HTTP range requests — 5-15ms per tile from CDN
4. **Sport filter**: client-side `["==", "sport", "offroad"]` — all sports in one file
5. **Rebuild**: after heatmap data changes, re-run build_pmtiles + upload

### File size by min_uc filter (measured April 2026, 6.4M edges)
| min_uc | Features | File size | Build time |
|--------|---------|-----------|------------|
| 1 (dev/beta) | 6.4M | 168MB | 2 min 19s |
| 2 (production K-anon) | 280K | 9.4MB | ~1 min |
| 5 (high-traffic only) | ~50K | ~3MB | ~30s |

### File size estimate
- 6M edges → ~100-130MB PMTiles file
- Includes z6-z14 with automatic feature thinning at low zooms

### vs Individual MVT tiles (old approach)
| | PMTiles | Individual tiles |
|---|---|---|
| Build time | ~5 min | 8+ hours (z8-12 France) |
| Files | 1 file (~100MB) | 150K+ files |
| Deploy | Upload 1 file | Sync 150K files |
| CDN cost | Range requests (cheap) | Per-file requests (expensive) |
| Latency | 5-15ms (CDN) | 5-10ms (disk) or 50-800ms (PostGIS) |

### MVT fallback
The API still serves `GET /heatmap/tiles/{sport}/{z}/{x}/{y}.mvt` as a fallback for:
- Time filtering (`?days=30`) — PMTiles doesn't support dynamic filters
- Development (before PMTiles is built)

### Local PMTiles rebuild workflow

The PMTiles file is **bundled into the frontend Docker image at build time** (Next.js `output: 'export'` copies `frontend/public/*` into `/app/out/`). When you change the data on disk in `frontend/public/heatmap-display.pmtiles`, the running container *won't* see it unless you either rebuild the image or override the file at runtime.

`docker-compose.yml` solves this with a bind mount for the frontend service:

```yaml
volumes:
  - ./frontend/public/heatmap-display.pmtiles:/app/out/heatmap-display.pmtiles:ro
```

So the host file always wins, and the workflow is:

```bash
# 1. Build a fresh PMTiles inside the backend container (it has access
#    to PostGIS + tippecanoe). Output goes straight into the host's
#    frontend/public/ via the backend volume mount on /app.
docker compose exec backend python -m app.jobs.build_pmtiles \
    --output-dir /tmp
docker compose cp backend:/tmp/heatmap-display.pmtiles \
    frontend/public/heatmap-display.pmtiles

# 2. Hard-reload the browser. No frontend restart needed — the bind
#    mount means MapLibre fetches the new file on the next range
#    request.
```

**Gotcha** — the bind mount requires the host file to exist *before*
`docker compose up`. On a fresh clone there is no PMTiles file (it's in
`.gitignore`), so the up will fail. Two options:

- Comment out the `volumes:` section under `frontend:` in
  `docker-compose.yml`, bring the stack up (image-baked PMTiles works),
  build a fresh PMTiles via the workflow above, then re-enable the mount.
- Or `touch frontend/public/heatmap-display.pmtiles` to create an empty
  file, bring up, then build for real (the empty file gets overwritten).

**Why the bind mount matters**: the May 2026 spaghetti-cleanup work
(60m grid-fallback filter, sport-aware OSM match radius, densifier
track breaks) only takes effect on the *map* once the PMTiles file is
rebuilt. Without the mount, you'd need a full `docker compose build
frontend` cycle to see your fix. With the mount, it's
`build_pmtiles` + browser hard-reload.

## Heatmap Export — public download (PRD #391, Phase 1 + 2 + 2.5 + 3)

Once the PMTiles binary is built, it is also exposed for **third-party
consumption** under ODbL-1.0. The heatmap is reusable in MapLibre,
GPX Studio, OruxMaps, Locus Map, Gaia GPS, Alpine Quest, QGIS, and any
other mapping app that consumes PMTiles, MBTiles (vector or raster) or
GeoJSON.

### Endpoints

- `GET /export/heatmap`
    Discovery JSON: license, attribution, `k_anonymity`, `generated_at`,
    and a `formats` list. Phase 2.5 advertises four formats:
    `pmtiles` (static URL), `mbtiles` (vector) + `mbtiles-raster` +
    `geojson` (on-demand endpoints with a relative `endpoint` field
    instead of `url`).
- `GET /export/heatmap.pmtiles`
    302 redirect to the canonical URL of the latest PMTiles binary.
    Backend never proxies the bytes. **Phase 1.**
- `GET /export/heatmap.mbtiles?bbox=&sport=&min_uc=&days=`
    Vector MBTiles built on demand via tippecanoe. Bbox is **required**
    and capped at 50 km × 50 km. Synchronous build with a 60 s budget;
    bboxes that exceed the budget return 400 ("reduce bbox or use
    PMTiles"). When `HEATMAP_GCS_BUCKET` is set, the binary is cached
    in `mbtiles/{slug}.mbtiles` and subsequent requests for the same
    `(bbox, sport, min_uc, days)` tuple 302-redirect to the cached
    object. **Phase 2.**
- `GET /export/heatmap-raster.mbtiles?bbox=&sport=&min_uc=&days=`
    Raster MBTiles (PNG tiles z10..z13) for Alpine Quest, OruxMaps
    raster mode, Locus raster mode. Same bbox cap + 60 s budget as the
    vector path. Cache slug carries an `-r` suffix
    (`mbtiles/{slug}-r.mbtiles`) so raster and vector blobs never
    collide in the same GCS prefix. **Phase 2.5.**
- `GET /export/heatmap.geojson?bbox=&sport=&min_uc=&days=`
    Bbox-clipped GeoJSON FeatureCollection of LineStrings. Bbox
    required, same 50 km cap (413 if larger). Gzip-encoded when the
    client sends `Accept-Encoding: gzip`. **Phase 2.**
- `GET /export/heatmap.kml?bbox=&sport=&min_uc=&days=&compressed=`
    KML LineStrings with five color-coded popularity buckets matching
    the in-app heat ramp. Bbox required, same 50 km sync cap.
    `?compressed=true` → KMZ (zipped, Google-Earth canonical). Stdlib
    `xml.etree.ElementTree` + `zipfile`, no external deps. **Phase 3.**
- `POST /export/heatmap/request`
    Async pipeline for bboxes too large for the sync endpoints. Body:
    `{format: 'geojson'|'mbtiles'|'kml'|'kmz', bbox: [w,s,e,n], sport, min_uc, days}`.
    Response 202 + `{request_id, status: 'queued', poll_url}`. Bbox
    capped at **250 km × 250 km** (413 above). The build is dispatched
    via Cloud Tasks → `/internal/export/build/{id}` (OIDC-authenticated);
    in TEST_MODE / unconfigured dev, runs inline. **Phase 3.**
- `GET /export/heatmap/{request_id}`
    Poll an async build. Responses: 200 `{status, progress, ...}` for
    queued/running/failed; 302 → public GCS URL for ready; 404 unknown;
    410 Gone past the 24 h TTL. **Phase 3.**

### Async pipeline sequence (Phase 3)

```
Client → POST /export/heatmap/request
       ← 202 {request_id, poll_url}
       │   (server: INSERT export_requests, enqueue Cloud Task)
       │
       │   Cloud Tasks → POST /internal/export/build/{id} (OIDC)
       │             │   1. SELECT row, flip status='running'
       │             │   2. Query heat edges via get_heat_edges_public
       │             │   3. Serialize (geojson/mbtiles/kml/kmz)
       │             │   4. Upload to gs://bucket/archive/{id}/{fmt}.{ext}
       │             │   5. UPDATE status='ready', gcs_uri
       │             └ (or status='failed', error on exception)
       │
Client → GET /export/heatmap/{request_id}  (every 2 s)
       ← 200 {status: 'running', progress: 0.5}
       ← 302 Location: https://storage.googleapis.com/.../archive/{id}/fmt.ext
              (when status='ready')
       ← 410 (when past expires_at)
```

The `export_requests` row carries a 24 h TTL (`expires_at`). The
[`cleanup_export_requests`](../backend/app/jobs/cleanup_export_requests.py)
job runs alongside the matview refresh + deletes expired rows. The
binaries themselves are GC'd by the GCS lifecycle rule on the
`archive/` prefix (terraform `google_storage_bucket.heatmap` declares
`age=90, matches_prefix=['archive/']`).

### PMTiles versioning convention (Phase 3)

`build_pmtiles` now writes THREE objects per build:

| Object | Mutability | Cache | Purpose |
|---|---|---|---|
| `heatmap-display.pmtiles` | mutable alias | `public, max-age=86400` | Phase 1 backwards compat. In-app heatmap + "Latest" UI choice. |
| `heatmap-display-v{H}-{sha8}.pmtiles` | **immutable** per-build | `public, max-age=31536000, immutable` | Snapshot pinning. `H` is the epoch-hour integer + an 8-char sha256 prefix of the bytes; identical bytes within the same hour collapse onto one blob (idempotent re-upload), distinct bytes always get distinct names so the `immutable` cache contract holds across intra-day rebuilds. |
| `heatmap-display.json` | pointer | `public, max-age=300` | `{latest, latest_url, mutable_url, size_bytes, captured_at, k_anonymity, edge_count, license, attribution}`. Atomic write (`.tmp → rewrite()`). Written LAST so it always references a snapshot that already exists. |

Discovery payload (`GET /export/heatmap`) reads the pointer (via a
60 s in-process cache so a hot endpoint doesn't hammer GCS) and
exposes the pinned snapshot to clients:

```
formats:
  - format: pmtiles
    url: https://.../heatmap-display.pmtiles  # mutable
    pinned_url: https://.../heatmap-display-v494544-deadbeef.pmtiles  # immutable
    version: v494544-deadbeef
    captured_at: 2026-06-02T00:00:00+00:00
    edge_count: 1700000
    pointer_url: https://.../heatmap-display.json
```

Retention: terraform adds a second lifecycle rule
(`age=365, matches_prefix=['heatmap-display-v']`) to cap the pinned
archive at one year (~3.6 GB at 10 MB/snapshot). The mutable file +
the pointer JSON are explicitly NOT touched by any lifecycle rule.

### Format comparison

| Format         | Best for                                    | Build time              | Bbox cap   | Typical size           | Third-party app compat (PRD §2)           |
|----------------|---------------------------------------------|-------------------------|------------|-------------------------|-------------------------------------------|
| PMTiles        | Devs, MapLibre / pmtiles.js embeds, OSS projects | 0 (static, hourly rebuild) | None  | 5–170 MB (full France) | MapLibre Android 11.7+ / pmtiles.js       |
| MBTiles vector | OruxMaps, Locus Map, Gaia GPS (vector support varies by app version) | On demand ≤ 60 s | 50 km × 50 km | 1–5 MB | OruxMaps, Locus Map, Gaia GPS (caveat: some Gaia versions drop vector-PBF) |
| MBTiles raster | **Alpine Quest**, OruxMaps raster, Locus raster, Gaia raster | On demand ≤ 60 s | 50 km × 50 km | 5–50 MB | Alpine Quest, OruxMaps, Locus Map, Gaia GPS |
| GeoJSON        | GPX Studio, QGIS, MapLibre web demos, GIS tools | On demand, query-time only | 50 km × 50 km | 0.1–30 MB | GPX Studio, QGIS, Gaia (web), MapLibre |

### Raster rendering pipeline (Phase 2.5)

The raster MBTiles renderer is hand-rolled in pure Python — no
`mapbox-gl-native` (~500 MB), no `gdal+mapnik`. The lightest path that
works:

```
heat_edges (bbox + sport + min_uc filter)
  └─→ ingest.get_heat_edges_public() returns GeoJSON LineStrings
        └─→ for each (z, x, y) tile in bbox at zoom 10..13:
              ├─ project the lines into 256 × 256 pixel space (Web Mercator)
              ├─ AABB-reject edges that don't touch the tile
              ├─ render with Pillow ImageDraw, colored by user_count bucket
              │  (green → yellow → orange → red → magenta), wider for popular
              └─ write the PNG into the MBTiles SQLite (XYZ → TMS Y-flip)
        └─→ output: SQLite file with metadata (format=png, bounds,
            minzoom, maxzoom, attribution, license, …) + tiles
```

**Per-tile budget:** empirically 50–500 ms per non-empty 256 × 256 tile
in dev. A 50 km × 50 km bbox covers ≤ 64 tiles per zoom; at 4 zoom
levels (z10..z13) that's < 256 tiles ≈ well under the 60 s
synchronous wall-clock. If a future bbox / heat_edges combination
exceeds the budget mid-render, `build_raster_mbtiles` raises
`TimeoutError` and the endpoint returns 400 (same "reduce bbox" hint
as the vector path).

**Why z10..z13** (not z6..z14 like the in-app heatmap PMTiles)?
Rasterising 9 zoom levels with Pillow blows the 60 s budget at
non-trivial bboxes. The PRD §2.2 accepts the trade-off: cycling
navigation works at z10..z13 (overview → neighbourhood). If Persona B/C
users request wider zoom coverage, Phase 3 can add an async build
queue with a longer budget.

The implementation is at
[`backend/app/services/heatmap_raster.py`](../backend/app/services/heatmap_raster.py),
exercised end-to-end by
`backend/tests/test_export.py::test_build_raster_mbtiles_integration_tiny_fixture`.

### Filters

All four formats accept (via query string for `.mbtiles` /
`.mbtiles-raster` / `.geojson`):

| Param   | Default | Notes                                                                            |
|---------|---------|----------------------------------------------------------------------------------|
| `bbox`  | required | `min_lon,min_lat,max_lon,max_lat`. Capped at 50 km × 50 km for MBTiles (vector + raster) + GeoJSON. |
| `sport` | `all`   | One of `road`, `gravel`, `mtb`, `offroad`, `running`, `all`.                     |
| `min_uc` | `HEATMAP_K_ANONYMITY` | Min user_count per edge. Cannot go below K (400 if attempted).      |
| `days`  | all-time | Optional time-window filter, 7–3650 days.                                        |

ODbL attribution is enforced two ways:

- `X-License: ODbL-1.0` + `X-Attribution` headers on every response.
- License metadata embedded in the binary (PMTiles `metadata`, MBTiles
  SQLite `metadata` table — vector and raster, GeoJSON FeatureCollection
  `metadata` field).

### Storage

- **PMTiles**: public GCS bucket `common-trails-heatmap-${env}`
  (terraform resource `google_storage_bucket.heatmap`). The
  `build_pmtiles` job uploads `heatmap-display.pmtiles` with
  `Cache-Control: public, max-age=86400` once `HEATMAP_GCS_BUCKET` is
  set on the job's env.
- **MBTiles vector** (Phase 2): cached under the `mbtiles/` prefix of
  the same bucket. Cache key = `heatmap-{sport}-{bbox-rounded-to-3dp}-uc{N}[-d{days}].mbtiles`.
  Mutable filename for Phase 2 (versioning is Phase 3).
- **MBTiles raster** (Phase 2.5): same `mbtiles/` prefix, slug with
  trailing `-r`: `heatmap-{sport}-{bbox-rounded-to-3dp}-uc{N}[-d{days}]-r.mbtiles`.
  The suffix means raster + vector blobs never collide.
- **GeoJSON**: not cached on disk; the underlying
  `ingest.get_heat_edges_public` already has its own version-keyed
  gzip cache in `/heatmap/trails`.
- **Local dev**: `HEATMAP_GCS_BUCKET` unset → the discovery + PMTiles
  redirect fall back to the relative path `/heatmap-display.pmtiles`,
  which is served from the frontend container's static export. MBTiles
  (vector + raster) builds run in `/tmp` and stream the binary directly
  (no GCS).

### Frontend

- A "⬇️ Fond" button in the map toolbar opens `ExportHeatmapModal`
  ([`frontend/components/ExportHeatmapModal.tsx`](../frontend/components/ExportHeatmapModal.tsx)).
- Phase 2.5 adds a fourth format radio (PMTiles / MBTiles vector /
  MBTiles raster / GeoJSON), a bbox-editor seeded from the current map
  viewport (numeric lon/lat inputs — Phase 3 will add a draw-rectangle
  interaction), a sport dropdown, and a `min_uc` slider. The
  bbox-editor surfaces the approximate width/height in km and disables
  the Download button when the bbox exceeds 50 km × 50 km. The bbox
  cap UI is shared with vector MBTiles + GeoJSON.
- The modal forces an ODbL acknowledgement checkbox; the Download
  link stays disabled until the checkbox is ticked.
- `NEXT_PUBLIC_HEATMAP_URL` overrides the in-app PMTiles source URL —
  use it to point the map at the public GCS bucket directly in prod.

### Phase 2/2.5 follow-ups

- **Phase 2.5 (Alpine Quest) — DONE** in `feat/heatmap-export-phase-2-5`:
  Pillow-based raster MBTiles renderer (z10..z13) at
  `app/services/heatmap_raster.py`; exposed at
  `/export/heatmap-raster.mbtiles`. Trade-off: zoom capped at 10..13
  (PRD asked for 6..14) to stay inside the 60 s sync budget.
- **Phase 3 (async)**: Cloud Tasks dispatcher + `request_id` polling
  for bboxes > 50 km² and full-region MBTiles builds. Required for
  Personas A + C planning multi-département gravel tours, and to
  extend raster zoom range back to z6..z14.
- **Phase 3 (KML/KMZ)**: For users routed via Google Earth / Garmin
  BaseCamp.

## Routing Graph Tiles (Separate CTGB System — by design)

Routing intentionally uses a separate CTGB binary tile system, NOT PMTiles.

**Why NOT merge them:**
1. **tippecanoe simplifies geometry** (`--simplification=5`) — drops vertices to make tiles smaller. Display can tolerate this; routing accuracy can't.
2. **tippecanoe drops "densest" features** at low zooms — would lose routing edges in dense areas.
3. **Wrong abstraction**: tippecanoe is a rendering tool. Routing needs exact coordinates for snapping + cost model.
4. **MVT → CTGB conversion in JS would cost 50-200ms** per tile load — wasteful.

**Architecture:**
- **Display (PMTiles)**: simplified, dropped, optimized for fast rendering
- **Routing (CTGB)**: exact geometry, full cost model, no simplification
- Two purposes, two formats — no merge planned.

## Production Deploy Checklist

### First deploy
```bash
# 1. Run migrations
docker compose exec backend alembic upgrade head

# 2. Build PMTiles heatmap (~5 min)
docker compose run --rm backend python -m app.jobs.build_pmtiles \
  --output /app/frontend-public/heatmap.pmtiles

# Or in GCP: build + upload to Cloud Storage
gcloud run jobs execute build-pmtiles
gsutil cp /tmp/heatmap.pmtiles gs://common-trails-cdn/heatmap.pmtiles

# 3. Build .fgraph routing graphs (~5 min, <5MB per partition)
./wasm-router/build.sh --prebuild

# 4. Verify
curl -s -o /dev/null -w "PMTiles: %{time_total}s\n" -H "Range: bytes=0-126" \
  https://cdn.chemins-communs.fr/heatmap.pmtiles
# Expected: <50ms
```

### After new activities are imported
```bash
# 1. Incremental heatmap rebuild
docker compose run --rm backend python -m app.jobs.rebuild_heatmap --since 24h

# 2. Rebuild PMTiles (~5 min)
docker compose run --rm backend python -m app.jobs.build_pmtiles

# 3. Upload to CDN (overwrite + cache purge)
gsutil cp /tmp/heatmap.pmtiles gs://common-trails-cdn/heatmap.pmtiles
gcloud cdn invalidate --path "/heatmap.pmtiles"
```

### Routing graph loading (route mode)
When user enters route mode:
1. **Try `.fgraph`** — pre-built partition file (<5MB, served from CDN). Instant WASM routing.
2. **If `.fgraph` too large (>5MB) or missing** → load z14 routing tiles per viewport
3. **On pan** → `ensureTilesLoaded()` fetches additional z14 tiles incrementally

### Heatmap tile serving architecture
```
MapLibre request
  ↓ pmtiles:// protocol
  ↓ HTTP range request to CDN (5-15ms)
  ↓ CDN edge cache hit → instant
  ↓ CDN miss → Cloud Storage (single file, range request)
  ↓ response (MVT tile bytes from PMTiles archive)
```

No FastAPI, no PostGIS, no backend involved. Pure static file serving.

### MVT API fallback (days filter only)
```
GET /heatmap/tiles/{sport}/{z}/{x}/{y}.mvt?days=30
  → FastAPI → PostGIS query → gzip → response
  → Only used when time filter is active (PMTiles is static, can't filter by date)
```

**Safety limits:**
- Row LIMIT 50,000 per tile (ORDER BY user_count DESC — keeps most popular edges)
- Concurrent tile generation capped by `TILE_GEN_CONCURRENCY` semaphore (default 4)
- LRU cache: 500 tiles, version-tagged (cleared when edge_version bumps on ingest)
- Cold z13: ~630ms, warm: ~2ms

### DFCI fire-prevention tracks
```
GET /heatmap/dfci
  → FastAPI → PostGIS query → gzip JSON → response (1-hour cache in memory)
  → Safety limit: max 100K features
```
With the standard IGN + Hérault imports (~52K edges), response is ~380KB gzipped.
The endpoint can handle up to 100K edges safely. For larger datasets (full PBF),
consider building a separate DFCI PMTiles file.

### Build pipeline — local Mac recommended
```bash
# 1. Enrich DFCI/trail cache files with SRTM DEM slopes (one-time, 0.6s)
python scripts/enrich_cache_dem.py

# 2. Build PMTiles heatmap (needs tippecanoe in Docker or local)
docker compose exec backend python -m app.jobs.build_pmtiles --min-uc 1

# 3. Upload to CDN
gsutil cp frontend/public/heatmap-display.pmtiles gs://common-trails-cdn/
```

Cloud Run has limited RAM (512MB–2GB). Your local Mac (24GB) is better for builds.
Cloud Run should only serve pre-built assets.

## Performance: Initial Page Load (Measured April 2026)

### Frontend heatmap ready (browser-side, Playwright)

```
   0ms ─ page.goto('/map')
 ~80ms ─ DOMContentLoaded
~500ms ─ MapLibre + basemap style loaded
~510ms ─ PMTiles source added (pmtiles:// protocol pre-registered)
~520ms ─ PMTiles header parsed (16KB range request)
~750ms ─ First MVT tile decoded from PMTiles
~800ms ─ First frame painted with heatmap visible
```

**Total: ~800ms** (warm cache, localhost). Production with CDN should be similar.

### Frontend static server (serve.py)

- HTTP/1.1 with Range request support (required for PMTiles)
- Range `bytes=0-127` returns 128 bytes in 7ms (previously returned full 168MB)
- ThreadingHTTPServer for concurrent range fetches

### Backend startup (docker compose restart)

```
0.0s  ─ READY (healthz responding)
2.5s  ─ Activities loaded (3834)
8.3s  ─ DFCI + trails checked (already in DB → skip)
9.6s  ─ Background load DONE
```

No enrichment at startup. DEM slopes baked at import time. Surface join
available as CLI. Backend is fully functional within 0.0s; background data
load completes within ~10s.

### Lifecycle logs (opt-in via `?trace=1` or `localStorage.cc_perf_trace=1`)

The frontend emits `[init]` and `[heatmap]` timing logs at key lifecycle
points. Visible in browser DevTools and captured by the Playwright
`heatmap-ready-time.spec.ts` benchmark test.

Source files:
- `frontend/lib/perf-trace.ts` — trace/traceWith helpers (no-op without opt-in)
- `frontend/app/map/page.tsx` → `[init] map-ready`, `[init] init-map-layers done`
- `frontend/lib/init-map-layers.ts` → `[heatmap] source-add`
- `frontend/hooks/useHeatmapControl.ts` → `[heatmap] source-loaded / first-tile / first-paint`

## Performance: Tile Serving (Measured April 2026)

### Compared to Garmin Connect
| Flow | Garmin | Common Trails (warm) |
|------|--------|---------------------|
| Full heatmap z9 wide view | ~10s | ~200ms (pre-built) |
| Zoom in z13 | ~0.5s | 17ms |
| Pan at same zoom | 1-2s | 8ms (4 tiles parallel) |
| z14 detail | instant | 3ms |

### Our latencies by cache source
| Source | Latency | When |
|--------|---------|------|
| Memory LRU | 1-5ms | After first request |
| Disk cache | 5-10ms | After `prebuild_tiles` job |
| PostGIS (warm buffers) | 50-800ms | First-ever request, no cache |
| PostGIS (cold buffers) | 1-4s | After restart, no cache |

## K-Anonymity

Only edges with `user_count >= K` are included in public tiles. K=2 in production, K=1 in dev. This ensures no single user's trace is identifiable.

Private tables: `activities`, `activity_cells`
Public tables (ODbL): `heat_edges` (source of truth, partitioned by sport). The by-OSM-way display aggregation runs on read via the shared `app/services/heat_aggregation.py` builder (no separate matview since June 2026).
`heat_cells` and `heat_cell_contributors` are legacy: no longer written (PR #214) and aggregated from `heat_edges` at query time via `get_heat_cells_aggregated`. Migration 0037 will drop them after a prod soak cycle.
