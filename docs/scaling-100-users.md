# Scaling to 100 Users — Tech Spec

## Current State (1 user)
- ~1,400 activities, ~500k heat_edges (GPS-point edges), ~1 contributor
- `full.json` graph: ~4-5MB gzip
- Ingest: ~1.5s/activity, Cloud SQL f1-micro (25 max connections)
- Monthly cost: ~$10

## Projected State (100 users)
- ~140k activities, **~5-10M unique heat_edges** (deduplication via UPSERT — users riding overlapping areas share edges), ~100 contributors
- `full.json` graph: ~50-100MB uncompressed (too large for full download)
- Concurrent imports: 5-10 simultaneous Strava syncs
- Monthly cost target: <$50

---

## Priority 1: Database (blocks everything else)

### 1a. SQLAlchemy Connection Pool Tuning
**Problem**: Cloud SQL f1-micro allows 25 connections. Each `_update_heat_edges` call creates a new `SessionLocal()` — no pooling.

**Solution**: Configure SQLAlchemy engine pool:
```python
engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=5, pool_pre_ping=True)
```
- 5 persistent connections + 5 overflow = 10 max per Cloud Run instance
- `pool_pre_ping=True` handles Cloud SQL connection drops after idle
- Cloud SQL Auth Proxy already handles multiplexing — PgBouncer not needed yet

**Effort**: 1 hour. **Impact**: Eliminates connection churn.

### 1b. Upgrade Cloud SQL
**Problem**: f1-micro (0.6GB RAM, shared vCPU) can't handle 10M rows with spatial indexes.

**Solution**: Upgrade to `db-g1-small` (1.7GB RAM, 1 vCPU) at ~$25/mo. Or `db-custom-1-3840` for $35/mo.

**When**: When query latency on `_load_existing_keys` exceeds 100ms. Monitor via Cloud SQL Insights.

**Effort**: 1 Terraform change. **Impact**: 3x more memory for indexes.

### 1c. PgBouncer Sidecar (50+ users)
**Problem**: At 50+ users, concurrent imports + API traffic exceed 25 connections even with pooling.

**Solution**: Add PgBouncer sidecar on Cloud Run.
```
Cloud Run API → PgBouncer (transaction mode) → Cloud SQL
```
- PgBouncer pool: 100 client connections → 15 server connections
- Cloud Run sidecar container (same pod, no network hop)
- Terraform: `google_cloud_run_v2_service.template.containers[1]`

**Effort**: 1 day. **Impact**: Unblocks concurrent users.

---

## Priority 2: Ingest Pipeline (blocks import speed)

### 2a. Cache `existing_keys` Across Activities in Same Area
**Problem**: `_load_existing_keys` does a PostGIS bbox query per activity. At 10M edges, a 10km ride returns 10-50k rows. Activities in the same area repeat this query with overlapping results.

**Solution**: Cache `existing_keys` by area (z12 tile or bbox bucket), similar to how we cache the OSM spatial grid by tile set. Activities covering the same bbox reuse the cache.
```python
_existing_keys_cache: dict[tuple, tuple[set, dict, dict]] = {}
```
Invalidate when new edges are inserted (track via `new_keys`).

**Effort**: 1 day. **Impact**: O(1) cache hit for overlapping activities instead of O(bbox_size) query.

### 2b. Async Ingest via Cloud Tasks
**Problem**: Strava import currently blocks the Cloud Run Job. With 100 users doing monthly resyncs, jobs queue up.

**Solution**:
1. `POST /integrations/strava/start-import` → push message to Cloud Tasks queue
2. Cloud Tasks calls `POST /internal/ingest-activity` per activity (auto-retry, rate limiting)
3. Remove `_update_heat_edges` from import job — defer to Cloud Tasks workers

**Architecture**:
```
User clicks "Import" → API creates ImportJob → Cloud Tasks queue
    ↓ (per activity, rate-limited)
Cloud Run API /internal/ingest-activity → _update_heat_edges
```

**Benefits**:
- Auto-retry on failure (no deadlock handling needed)
- Rate limiting prevents DB overload
- Import job finishes in seconds (just discovery + queue)
- Activities processed in parallel across multiple Cloud Run instances

**Effort**: 3 days. **Impact**: 10x faster imports, no blocking.

### ~~2c. Skip Canonical Edge Merge on First Ingest~~ DONE
Implemented: skip `_find_canonical_edge` (441 neighbor checks per edge) when `existing_keys` is empty for the bbox. ~20% faster bulk ingest on clean DB.

---

## Priority 3: Graph Serving (blocks frontend)

### 3a. Kill full.json, Default to area.pb
**Problem**: `full.json` serves ALL edges. At 10M edges = ~100MB. Client can't download this.

**Solution**:
1. `full.json` → return 413 if estimated size > 10MB
2. Frontend defaults to `area.pb` (viewport-based, already implemented)
3. `area.pb` with 50km radius typically covers a ride's area in <2MB

**Effort**: 1 hour. **Impact**: Frontend stays fast at any scale.

### 3b. Tile-Based Graph with HTTP Caching
**Problem**: Even `area.pb` reloads on every viewport change.

**Solution**:
1. Pre-compute z12 graph tiles (each ~100KB protobuf)
2. Serve via Cloud CDN with `Cache-Control: public, max-age=3600`
3. Frontend loads tiles as needed (same pattern as map tiles)
4. Invalidate on edge mutation (version-based ETag)

**Effort**: 3 days. **Impact**: Near-instant graph loading after first visit.

---

## Priority 4: Heatmap Read Path (blocks map display)

### 4a. Materialized View for K-Anonymity Filtered Edges
**Problem**: Every `/heatmap/trails` request JOINs `heat_edges` with `heat_edge_contributors` and filters `user_count >= K`. At 10M edges, this is slow.

**Solution**:
```sql
CREATE MATERIALIZED VIEW heat_edges_public AS
SELECT edge_key, sport, pass_count, forward_count, backward_count,
       ele_delta_m, slope_grade, surface_type, highway_type, geometry
FROM heat_edges
WHERE user_count >= 2;

CREATE INDEX ON heat_edges_public USING GIST (geometry);
REFRESH MATERIALIZED VIEW CONCURRENTLY heat_edges_public;
```

Refresh after each ingest batch (not per-activity — too expensive). Or on a 5-minute schedule.

**Effort**: 1 day. **Impact**: 10x faster heatmap reads.

### 4b. Vector Tile Pre-rendering
**Problem**: Rendering 10M edges as GeoJSON for MapLibre is slow.

**Solution**: Pre-render MVT (Mapbox Vector Tiles) via pg_tileserv or martin:
```
martin (Rust MVT server) → Cloud CDN → MapLibre
```

Martin reads directly from PostGIS, serves .pbf tiles. Zero backend code needed.

**Effort**: 2 days (deploy martin + CDN config). **Impact**: Map renders in <100ms at any zoom.

---

## Priority 5: Monitoring & Cost

### 5a. Cloud SQL Insights
Enable query performance insights to catch slow queries early:
```hcl
resource "google_sql_database_instance" "main" {
  settings {
    insights_config {
      query_insights_enabled  = true
      query_plans_per_minute  = 5
    }
  }
}
```

### 5b. Budget Alerts
```hcl
resource "google_billing_budget" "monthly" {
  amount { specified_amount { units = "50" currency_code = "EUR" } }
  threshold_rules { threshold_percent = 0.8 }
}
```

### 5c. Auto-scaling Policy
- Cloud Run API: min=0, max=3 (currently max=1)
- Cloud Run Jobs: keep max=1 (sequential, advisory lock)
- Cloud SQL: stay on small until P95 query latency > 200ms

---

## Cost Optimization: CDN-First Architecture

The key insight: **heatmap and graph data changes at most once per day** (on import). Setting `Cache-Control: public, max-age=86400, stale-while-revalidate=86400` (24h + 24h stale) on all stable endpoints means:

- **Browsers** cache everything locally for 24h — zero requests to Cloud Run
- **stale-while-revalidate** — after 24h, serve stale while fetching fresh data in background (no loading spinner)
- **Cloud Run** sees near-zero traffic for read-only users (only first visit per 24h window)
- **Cloud CDN** (when added) caches at edge — even first visits are fast

### Endpoints with 24h cache (implemented):
- `/heatmap/summary` — aggregate stats
- `/heatmap/stats` — cell data (K-anonymity filtered)
- `/heatmap/trails` — edge GeoJSON (gzip compressed)
- `/heatmap/dfci` — DFCI tracks
- `/heatmap/tiles/{sport}/{z}/{x}/{y}.mvt` — vector tiles
- `/heatmap/export` — GeoJSON export
- `/routing/graph/{sport}/area.pb` — binary graph bbox
- `/routing/graph/{sport}/{z}/{x}/{y}.json` — tile graph (JSON)
- `/routing/graph/{sport}/{z}/{x}/{y}.pb` — tile graph (protobuf)

### Endpoints NOT cached (dynamic):
- `/routing` — user-specific waypoints
- `/me/*` — private data
- `/imports/*` — file upload

### Optimized cost path (vs expensive path):

| Component | Expensive | Optimized (CDN-first) |
|-----------|-----------|----------------------|
| Cloud SQL | $70 (primary + replica) | $35 (single g1-small) |
| Redis | $25 (Memorystore) | $0 (browser + CDN cache) |
| Cloud Run API | $20 (min=1 warm) | $0-2 (scale-to-zero, CDN absorbs reads) |
| Cloud Run Jobs | $5 | $5 |
| martin MVT | $10 (separate) | $2 (Cloud Run scale-to-zero) |
| CDN | $5 | $5 |
| Storage | $3 | $3 |
| **Total** | **~$138/mo** | **~$50-52/mo** |

---

## Alternative: AlloyDB Omni (100+ users)

At 100+ users, consider AlloyDB Omni (free, runs on Cloud Run):
- PostgreSQL-compatible, columnar engine for analytics
- Built-in connection pooling (no PgBouncer needed)
- Runs as a container — same deployment model as current setup
- PostGIS support via extensions

Would replace Cloud SQL entirely. Worth evaluating when Cloud SQL costs exceed $50/mo.

---

## Implementation Order

| Phase | Items | Trigger | Effort |
|-------|-------|---------|--------|
| ~~**Done**~~ | ~~2c (skip merge on empty DB)~~ | ~~Free win~~ | ~~30 min~~ |
| **10 users** | 1a (pool tuning), 3a (kill full.json), 5a-c (monitoring) | Connection errors | 1 day |
| **30 users** | 1b (upgrade SQL), 2a (existing_keys cache), 4a (materialized view) | Slow queries | 3 days |
| **50 users** | 1c (PgBouncer), 3b (tile CDN) | Connection exhaustion | 2 days |
| **100 users** | 2b (Cloud Tasks), 4b (martin MVT) | Import timeouts | 1 week |

---

## Cost Projection

| Users | Cloud SQL | Cloud Run | Storage | CDN | Total |
|-------|-----------|-----------|---------|-----|-------|
| 1 | $9 (f1-micro) | $0 (scale-to-zero) | $1 | $0 | ~$10 |
| 10 | $9 | $2 | $1 | $0 | ~$12 |
| 30 | $25 (g1-small) | $5 | $2 | $1 | ~$33 |
| 50 | $25 | $8 | $2 | $2 | ~$37 |
| 100 | $35 (custom) | $10 | $3 | $2 | ~$50 |
