# Routing Architecture

*How Chemins Communs routes cyclists through community knowledge rather than generic road networks.*

---

## The Core Idea

Most routing engines begin from the full road network and overlay preferences on top. We do the opposite: we begin from **what the community actually rides** and fill gaps only when necessary. This is a deliberate design choice. A road that appears on OpenStreetMap but has never been ridden by a cyclist may be perfectly legal, but it tells you nothing about whether the surface is rideable, whether the gate is locked, or whether the traffic makes it terrifying. Community traces are evidence. The road network is theory.

The consequence is honest: when no community data exists between two points, we draw a straight line rather than pretend we know a good route. The quality of routing improves with every trace shared — a virtuous cycle that rewards community participation.

## Routing Cascade

When the user places two waypoints, the system attempts routing in this order:

| Priority | Engine | Latency | When it fires |
|----------|--------|---------|---------------|
| 1a | **Client CH** (Web Worker, bidirectional) | ~15ms | Long route (> 25km), CH shortcuts loaded |
| 1b | **Client A*** (Web Worker) | < 50ms | Short/medium routes, graph loaded |
| 2 | **Server routing** (pgRouting + heatmap) | 200-800ms | Client graph fails or returns null |
| 3 | **Straight line** | instant | Off-heatmap endpoints (user explicitly placed outside known network) |

For long-distance routes (> 25km), the client first attempts **Contraction Hierarchies (CH)** — a bidirectional search on precomputed shortcut edges that finds paths in ~15ms regardless of distance. If CH is not available (non-CH sport, stale shortcuts, tiles not loaded), it falls back to standard A*. If A* also fails (iteration limit), CH is retried as a last resort before falling through to server routing.

The key insight: **client failure always falls through to server**, not to straight line. The straight line only appears when the client *positively identifies* that one or both endpoints are off the known graph (extended snap at 3km found nothing). This distinction matters — a failed Dijkstra (iteration limit, disconnected subgraph) is not the same as an off-graph waypoint.

### Contraction Hierarchies (CH)

CH is precomputed server-side for **gravel, mtb, and offroad** profiles (3 hierarchies). Shortcuts are served as tile data so the client Web Worker can run bidirectional CH search directly — no server round-trip.

**Two-tier loading:**
- **Regional shortcuts** (level >= 3, spanning >10km): loaded as a single file per sport (~50-100KB gzip) on route mode entry via `GET /routing/ch/{sport}/regional.pb`
- **Local shortcuts** (level < 3): served as z14 tiles via `GET /routing/ch/{sport}/14/{x}/{y}.pb`, loaded alongside regular edge tiles

**Version negotiation:** All CH tile endpoints return `X-CH-Version` and `X-Edge-Version` headers. If `ch_version != edge_version`, all CH data is discarded and A* is used (transparent fallback).

**Proposals:** Proposal #1 uses `bidirectionalCH()` (no penalties, ~15ms). Proposals #2-3 use `dijkstraWithPenalties()` with corridor from #1 (~30ms each). Drag-and-drop edits always use standard A* (short segments).

---

## Part I: The Client Graph

### Data Sources

The client graph contains exclusively **verified data** — segments where we have evidence of actual use or official designation:

| Source | Table | What it provides | Trust level |
|--------|-------|-----------------|-------------|
| Community heatmap | `heat_edges` | Segments ridden by ≥K users (K-anonymity) | High — multiple independent riders |
| DFCI tracks | `dfci_edges` | Fire-prevention forestry tracks (OSM `ref:FR:DFCI`) | High — official designation |
| Marked trails | `trail_edges` | GR, GRP, GT, PR, EuroVelo | High — maintained waymarked routes |
| OSM road network | `osm_edges` | Roads, paths, cycleways from OpenStreetMap | Medium — exists on map, unridden |

OSM edges serve as connective tissue — they provide the road network skeleton that links community-verified segments. But they carry no heatmap bonus and receive an `unvalidated` cost penalty (×1.15) to naturally prefer verified alternatives when available.

### Tile Loading

The graph loads incrementally as z14 slippy map tiles:

```
GET /routing/graph/{sport}/14/{x}/{y}.pb
```

Each tile covers ~4.8 km × 4.8 km. The binary format (CTGB — Chemins Communs Tile Graph Binary) encodes each edge in exactly 20 bytes:

```
┌─────────┬─────────┬─────────┬─────────┬────┬───────┬────────┬───────┐
│ lon1 i32│ lat1 i32│ lon2 i32│ lat2 i32│ uc │ slope │hw|surf │ trail │
│ ×100000 │ ×100000 │ ×100000 │ ×100000 │ u8 │ i8×2  │4b|4b   │  u8   │
└─────────┴─────────┴─────────┴─────────┴────┴───────┴────────┴───────┘
  4 bytes   4 bytes   4 bytes   4 bytes  1b    1b      1b       1b = 20 bytes/edge
```

This is 44% smaller than the JSON equivalent after gzip, and parses roughly 5× faster because there is no string tokenization — just typed reads from an `ArrayBuffer`.

**Tile loading strategy:**

1. On route mode entry, compute z14 tiles covering the viewport
2. Check IndexedDB cache first (survives page reloads)
3. Fetch uncached tiles in parallel (HTTP), cap at 50 per batch
4. Merge into the in-memory graph via `mergeEdges()`
5. On `moveend`, load new viewport tiles (300ms debounce)

The 50-tile cap prevents pathological cases (e.g., zoom level 6 would request thousands of tiles). When truncated, tiles nearest to the viewport center are prioritized.

### Graph Structure

The in-memory graph is an adjacency map:

```typescript
adj: Map<string, EdgeEntry[]>    // "3.87000,43.62000" → [edges...]
spatialGrid: Map<string, string[]>  // "3870,43620" → [vertex keys...]
```

Vertex keys are stringified coordinates at 5 decimal places (~1.1m precision). The spatial grid (cell size 0.001° ≈ 110m) enables O(1) nearest-vertex lookups for snap-to-road.

Every edge exists in both directions. The reverse direction negates the slope grade (uphill becomes downhill), which matters for the cost model.

### The Web Worker

All graph operations run in a dedicated Web Worker. This is not optional — Dijkstra on a 200k-vertex graph can take 50ms, which would freeze the UI during panning. The Worker owns the graph exclusively; the main thread communicates via structured messages.

**Health monitoring:** A heartbeat ping every 30 seconds detects Worker crashes. After 2 consecutive failures, the system falls back to main-thread routing (identical algorithm, degraded responsiveness). Recovery is attempted every 30 seconds, up to 3 times.

---

## Part II: The A* Algorithm

### Why A* and not Dijkstra

Strictly speaking, the implementation *is* Dijkstra, augmented with a haversine heuristic that makes it A*. The heuristic dramatically reduces the search space for point-to-point queries — typically exploring 10-30% of the vertices that plain Dijkstra would touch.

```
f(n) = g(n) + h(n)
```

where `g(n)` is the accumulated cost from start, and `h(n)` is the estimated remaining cost to the goal.

### Admissibility

The heuristic must never overestimate, or A* may miss the optimal path. Our cost model can reduce edge costs below raw distance (via heatmap and trail bonuses), so the heuristic must account for the maximum possible discount:

```
h(n) = haversine(n, goal) × TRAIL_FLOOR
```

`TRAIL_FLOOR = 0.55` is the product of the minimum possible cost factors — a maximally popular segment on a perfectly graded DFCI track with ideal surface. Any real path will cost at least this fraction of its geometric distance, so the heuristic remains admissible.

### Iteration Budget

The search terminates after **500,000 iterations** without finding a path. This is a safety valve for disconnected subgraphs. At the typical search rate (~2M iterations/second on modern hardware), this budget allows ~250ms of computation — enough for routes up to 100km through well-connected graphs.

When the budget exhausts, the client returns `null` and the system falls through to server routing.

### Intent Strength

When a user places many precise waypoints, they are communicating "I know exactly where I want to go." The cost model should respect this rather than pulling the route toward popular segments 500m away.

Intent strength ∈ [0, 1] interpolates between the full cost model and pure distance:

```
effective_cost = edge.cost × (1 - intent) + edge.dist × intent
```

At `intent=0`: full heatmap/trail optimization. At `intent=1`: shortest geometric path. The value scales automatically based on waypoint density.

Intent also tightens the VIP snap bias (so waypoints land where the user clicked, not on a popular trail nearby) and the detour cap (so the route doesn't wander).

---

## Part III: The Cost Model

The cost of traversing an edge is a product of independent factors:

```
cost = L × S × U × H × T × V
```

Each factor is designed to be interpretable in isolation: slope makes steep roads expensive, surface makes dirt roads expensive for road bikes, heatmap makes popular segments cheap. The product combines them multiplicatively because the factors are independent — a steep dirt road is penalized by both slope *and* surface.

### Factor: Slope (S)

Piecewise linear, calibrated to cyclist effort perception:

| Grade | Factor | Rationale |
|-------|--------|-----------|
| < 5% | 1.0 | Negligible effort increase |
| 5-10% | 1.0 → 1.2 | Noticeable but sustainable |
| 10-15% | 1.2 → 1.5 | Serious climb, most cyclists slow significantly |
| 15%+ | 1.5 → 2.0 | Walking pace for most; capped to avoid infinite penalty |

Downhill receives a 30% discount on the penalty (`grade × 0.7`), reflecting that descending a 10% grade is not equivalent to climbing one. Each sport profile scales the penalty differently: road cyclists feel gradients more (`slopePenalty=1.0`), mountain bikers much less (`slopePenalty=0.5`).

### Factor: Surface (U)

Per-sport preference matrix:

| Surface | Road | Gravel | MTB | Off-road | Running |
|---------|------|--------|-----|----------|---------|
| Asphalt | 1.0 | 1.1 | 1.6 | 2.0 | 1.0 |
| Gravel | 1.1 | **0.80** | 0.90 | 0.90 | 1.05 |
| Dirt | 1.3 | 1.0 | **0.90** | **0.90** | 1.0 |
| Rock | 1.6 | 1.2 | 1.0 | 1.0 | 1.2 |
| Unknown | 1.15 | 1.1 | 1.05 | 1.0 | 1.15 |

Values below 1.0 are deliberate bonuses — gravel bikes *prefer* gravel surfaces, MTBs *prefer* dirt. This is not a penalty avoidance; it is an active attraction.

### Factor: Heatmap (H)

Community popularity follows a logarithmic saturation curve:

```
heat_score = min(1.0, log₂(1 + user_count) / 4.0)
H = 1.0 - heat_score × heat_max_bonus
```

| Users | heat_score | Road bonus | MTB bonus |
|-------|-----------|------------|-----------|
| 0 | 0.00 | 0% | 0% |
| 1 | 0.25 | 3.75% | 8.75% |
| 3 | 0.50 | 7.5% | 17.5% |
| 7 | 0.75 | 11.25% | 26.25% |
| 15+ | 1.00 | 15% | 35% |

The logarithmic curve is essential: the jump from 0 to 1 user is the most informative signal (someone has verified this road is rideable). The jump from 15 to 150 users tells you it is popular, but adds little routing value. The log curve captures this diminishing information return.

The maximum bonus varies by sport: road cyclists care less about community validation (roads are predictable), while MTB riders benefit enormously from knowing someone else has actually ridden a trail.

### Factor: Trail (T)

Official trail markings provide a strong signal independent of community data:

```
T = 1.0 - trail_score × trail_max_bonus
```

| Trail type | Score | Gravel bonus (max 35%) |
|-----------|-------|----------------------|
| GT (Grande Traversée VTT) | 1.0 | 35% |
| DFCI (fire prevention) | 1.0 | 35% |
| EuroVelo | 0.9 | 31.5% |
| GR (Grande Randonnée) | 0.8 | 28% |
| GRP (GR de Pays) | 0.8 | 28% |
| PR (Promenade) | 0.5 | 17.5% |

Road profiles exclude hiking trail bonuses (a GR through a forest is not useful for a road bike). The exclusion is per-profile, not per-trail — the same DFCI track that benefits a gravel rider is neutral for a road rider.

### Factor: Unvalidated (V)

```
V = 1.15  if user_count == 0 AND trail_type == none
V = 1.0   otherwise
```

A 15% penalty for edges with zero evidence. This gently steers routing toward known segments without making unknown roads impassable. The penalty is small enough that a direct unknown road will still beat a long detour through known segments.

---

## Part IV: Snap-to-Road

When the user clicks the map, we need to find the nearest graph vertex. This sounds simple, but the naive approach (check all vertices) is O(n) per click. With 200k vertices, that is too slow for interactive use.

### Spatial Grid

The graph maintains a hash grid with 0.001° cells (~110m). Snapping queries only examine cells within the search radius:

```
radius_cells = ceil(max_snap_m / 111_000 / 0.001) + 1
```

For the default 800m snap: 8 cells examined, typically containing 10-50 vertices. O(1) amortized.

### VIP Bias

Not all vertices are equal. A vertex on a popular heatmap segment is more likely to be what the user intended than a vertex on an unknown residential street at the same distance. The VIP bias makes heatmap/trail vertices appear "closer":

```
effective_distance = geometric_distance / (1 + VIP_SNAP_BIAS × vip_score)
```

With `VIP_SNAP_BIAS = 4.0`: a heatmap vertex at 100m has effective distance 33m (for `vip_score=0.5`), beating an unknown vertex at 40m. This matches user intent — they clicked near the heatmap line they can see on the map.

### Extended Snap (Off-Heatmap Bridging)

When normal snap (800m) fails, a second attempt at 3,000m catches rural areas where the nearest known road may be several kilometers away. If even this fails, the endpoint is marked as off-heatmap, and the segment renders as a straight line. This is honest: we are saying "we don't know how to get there from the known network."

---

## Part V: Route Proposals

For longer segments or when the user requests alternatives (Alt+click), the system generates three diverse routes:

| Proposal | Strategy | Philosophy |
|----------|----------|------------|
| A | **Communautaire** | Best route according to the full cost model |
| B | **Plus direct** | Shorter path, avoiding A's corridor |
| C | **Explorateur** | Discover alternatives, avoiding both A and B |

### Corridor Penalties

After computing proposal A, we penalize edges near A's path before computing B. The penalty decays with distance:

| Proximity to A | Penalty multiplier |
|---------------|-------------------|
| On the path | ×5.0 |
| < 200m | ×5.0 |
| 200-500m | ×2.6 (5^0.6) |
| 500-1000m | ×1.6 (5^0.3) |
| > 1000m | ×1.0 (no penalty) |

For proposal C, penalties from both A and B accumulate (max of the two). This forces C to find genuinely different corridors — not just a slight variation of A or B.

### Constrained Corridors

In narrow valleys or mountain passes, all three routes may converge. If every pair of proposals overlaps by more than 50% (measured by bidirectional nearest-neighbor sampling at 100m threshold), the result is flagged as `constrainedCorridor`. The UI communicates this honestly: "few alternatives exist in this terrain."

### Detour Caps

Each sport has a maximum acceptable detour ratio (route distance / direct distance):

| Sport | Max detour | Rationale |
|-------|-----------|-----------|
| Road | 1.8× | Roads are dense, detours should be modest |
| Gravel | 2.0× | Moderate — gravel networks are sparser |
| MTB | 2.5× | Trails wind; significant detours are normal |
| Off-road | 3.0× | Off-road paths are unpredictable |
| Running | 2.0× | Similar to gravel |

Routes exceeding these caps are rejected — better to show no route than a 50km detour on a 20km segment.

---

## Part VI: Server-Side Routing

The single-segment backend endpoint (`GET /routing`) was **removed in late May 2026** because it was broken at scale (87s+ on a 4.2M-edge local DB, OOM-killed mid-request). Client-side WASM routing on regional `.fgraph` shards now handles all single-segment requests directly; failures fall through to a straight line drawn on the client (with the `off_heatmap` marker so the UI can show it as an estimation).

Two related endpoints **remain** on the backend because they're how the editor sources its diverse alternatives:

```
POST /routing/multi      — route through N waypoints on a single shared graph
POST /routing/proposals  — return 3 diverse proposals (Populaire / Direct / Surface)
```

Both run the same server-side cost model and gain two capabilities that the client doesn't have:

1. **Personal traces** — authenticated users get routing through their own unpublished activities (no K-anonymity required).
2. **Full Dijkstra on the server graph** — larger memory budget, can chain in BRouter / OSRM for gaps via `_hybrid_route`.

The server graph is cached in-memory with version-based invalidation. Cache keys are discretized to a 0.05° grid (~5.5km) for spatial reuse — two nearby queries hit the same cached graph.

### Post-Processing

Server routes receive additional cleanup:

**Loop removal:** Heatmap traces sometimes create small detour loops where multiple GPS tracks diverge and reconverge. The algorithm detects points that pass within 50m of earlier points, then checks whether the entry and exit headings are similar (< 60° difference = same direction = loop → remove) or different (≥ 60° = switchback → preserve).

**Steep segment detection:** Grades ≥ 15% are flagged with surface type and trail context, enabling the UI to warn about pushing sections.

---

## Part VII: Heatmap Tile Performance

The heatmap is rendered via MapLibre vector tiles (MVT), served by the backend:

```
GET /heatmap/tiles/{sport}/{z}/{x}/{y}.mvt
```

### Two rendering strategies by zoom

| Zoom | MVT layer | Geometry | Visual |
|------|-----------|----------|--------|
| z6-z10 | `heat_points` | Clustered centroids | Blurred glow circles |
| z11-z14 | `trails` | Line geometries | Detailed trail lines |

At low zoom, computing line geometries for tens of thousands of edges would be wasteful — the user cannot distinguish individual trails. Instead, edges are aggregated into a spatial grid (0.005° to 0.1° depending on zoom) and rendered as weighted points with circle blur. The visual result is similar but the tile is 10× smaller.

### Performance optimizations

With 3.1M edges in the database, naive tile generation would be far too slow. The current stack achieves **< 100ms cold, < 2ms warm** per tile:

| Optimization | Impact | Details |
|-------------|--------|---------|
| Pre-computed bbox | -40% planning time | Tile bounds calculated in Python, not PostGIS CTE |
| `ST_StartPoint` over `ST_Centroid` | -30% execution time | First vertex instead of geometric center (same visual at grid scale) |
| Degree-based length filter | Eliminates `::geography` cast | `ST_XMax - ST_XMin` instead of `ST_Length(::geography)` |
| Gzip compression | -60-80% transfer size | 50KB tile → 10KB over the wire |
| `OrderedDict` LRU cache | O(1) eviction | 2,000 tiles cached in-memory |
| `sync def` endpoint | Parallel via threadpool | FastAPI runs sync handlers in threadpool automatically |
| Connection pool (20+10) | Concurrent tile queries | 30 simultaneous DB connections available |
| Version TTL (5 min) | Skip version check | Heatmap imports are rare; 5-minute staleness is acceptable |
| `fadeDuration: 0` | Instant tile appearance | No 300ms MapLibre fade animation on pan |
| Viewport prefetch | Warm server cache | On heatmap enable, prefetch visible tiles in parallel |

---

## Part VIII: What's Next

The routing system is functional but has clear growth paths. Listed roughly by impact-to-effort ratio:

### Near-term (incremental improvements)

**Contraction hierarchies.** The current A* explores the full graph on every query. For long routes (> 50km), precomputing a hierarchy of shortcut edges would reduce search time from O(n) to O(√n) — making 200km routes as fast as 20km routes. The challenge is that our cost model is sport-dependent, so we would need one hierarchy per sport.

**Elevation-aware cost model.** The current slope factor uses a per-edge grade, but does not account for cumulative elevation gain. A route that climbs 200m in gentle 3% grades feels very different from one that climbs 200m in a single 12% wall. Incorporating total ascent into the cost function would better match rider perception.

**Turn penalties.** Sharp turns on road bikes (especially U-turns) are costly in time and momentum. Adding a turn angle penalty at vertices would reduce the zigzag patterns that sometimes appear in dense urban graphs.

**Offline routing.** The IndexedDB tile cache already survives page reloads. Extending it with a Service Worker would enable full offline routing — download the graph tiles for a region, then route without connectivity. Particularly valuable for bikepacking in areas with poor mobile coverage.

### Medium-term (architectural evolution)

**Live surface classification.** The current surface model relies on OSM tags, which are often missing or outdated. Community GPS traces carry implicit surface information: average speed, track noise, and acceleration patterns correlate with surface type. A statistical model trained on labeled traces could infer surface classification from ride characteristics alone.

**Time-filtered heatmap.** Currently all traces have equal weight regardless of when they were recorded. A trail that was popular in 2020 but is now overgrown should not receive the same bonus as one ridden last month. Exponential time decay (half-life ~6 months) would keep the heatmap fresh without discarding historical data entirely.

**Multi-day trip planning.** The current system routes segment by segment. For multi-day trips, the optimizer should consider daily distance targets, accommodation locations, water sources, and cumulative fatigue. This is a fundamentally different problem (constraint satisfaction over a sequence of days) that requires a different solver.

### Long-term (research directions)

**Probabilistic routing.** Instead of a single "best" route, present a *distribution* of plausible routes weighted by confidence. Segments with high heatmap counts have narrow uncertainty; segments with a single trace have wide uncertainty. The user sees not just "go here" but "we are 95% confident this segment is good" vs. "one person rode this once, proceed with caution."

**Collaborative route refinement.** When a user rides a route and then submits their GPS trace, the system can compare planned vs. actual trajectory. Deviations are signal: if 80% of riders deviate from the suggested route at the same point, the cost model is wrong there. This creates a feedback loop where routing quality improves automatically from usage data.

**Federation.** The current architecture assumes a single server. For a truly community-owned system, routing data should be federable — multiple instances sharing heatmap data under ODbL, each maintaining their own regional graph. This aligns with the project's open-source philosophy: no single point of control, no vendor lock-in on community data.

---

## Appendix: Known weak spots, May 2026 audit

A scan of the client-side routing surfaced eleven concerns. Eight are fixed or verified-correct; three are larger-scope architectural items that need a deliberate decision rather than a patch.

### Fixed in May 2026

| # | Concern | Fix |
|---|---|---|
| 2 | CH version mismatch silently dropped the response | `routing-worker-client.ts` now `console.warn`s with both versions on mismatch, distinguishing it from the legitimate "non-CH sport" case (`chVersion === 0`) |
| 3 | Generation counter wasn't propagated to Worker | Routing-style messages carry `gen`. The Worker tracks `latestRequestGen` and short-circuits stale messages with a null-result reply. `setGen` is also pushed eagerly on `reinit` to flush the mailbox |
| 4 | No per-request budget on Worker calls | `_REQUEST_TIMEOUT_MS = 30_000` rejects the Promise if the Worker hangs. Heartbeat handles "is the Worker alive"; this handles "is *this* call going to come back" |
| 5 | Abort signal didn't cascade to Worker | Covered by #3 — `setGen` lets the Worker drop queued stale work; in-flight Dijkstra still runs to completion (single-threaded JS, no preemption) but its result is discarded on the main thread via `gen` mismatch |
| 8 | Heartbeat false-timeout under load | No code change needed — passive proof-of-life + `MAX_FAILURES = 2` already absorb a single 35 s in-flight route. Documented in source so future readers don't lower the threshold by accident. |

### Verified correct on careful re-read (audit was speculative)

| # | Audit claim | Reality |
|---|---|---|
| 6 | "Cost-model drift between TS `edgeCost` and Rust `edge_cost`" | All five sport profiles match exactly. Slope formula (`1.12 * exp(0.08 * (g − 8))`), heat formula (`log2(1+n) / 4`), DFCI <10 % slope exemption, excluded highways/trails — identical. Drift *risk* exists; current state is parity. |
| 7 | "Snap VIP-bias asymmetry TS vs Rust" | Both implementations use `effective = dist / (1 + vip_bias * vip)` with the same `compute_vip_score = max(heat, trail)`. Identical. |
| 11 | "Elevation-delta sign asymmetry" | Both swap `ele1` ↔ `ele2` and negate `slope_grade` on the reverse edge. Both compute `has_elevation = ele1 != 0 || ele2 != 0`. Identical. |

### Deferred — architectural decisions, not patches

**1. TS engine is a fallback, not a peer — but it's a fallback the user *can* hit.** Each routing case in `routing-worker-wasm.js` is structured `if (wasmRouter) { try Rust; if (result) return result; } // JS fallback`. Happy path is Rust-only. The risk isn't "two brains disagree on the same input" — it's drift: a bug fixed in Rust but not in TS produces a divergent answer when WASM is unavailable (slow load on cold cache) or returns null (graph not ready, vertex off-snap). Cost-model parity is currently exact (verified May 2026), so the fallback gives an identical answer; we just can't rely on that staying true without a parity test. **The deferred decision is whether to keep the TS fallback at all** — removing it makes "WASM didn't load" a hard failure (cascade to backend), simplifying the code at the cost of one less safety net.

**5. Memory: TS graph + Rust graph held simultaneously.** Edges live in `ClientGraph.adj` (Map) + `RustGraph.adj` (HashMap) + `FastGraph` internals. Same dependency chain as #1 — if we drop the TS fallback, one of the three copies disappears. Defer with #1.

**9. Hand-maintained Worker bundle.** `frontend/public/routing-worker-wasm.js` is a 1286-line file (after Phase B) that was hand-built (probably from `lib/routing-worker.ts` + WASM glue + extra cases like `snapAndRoute` / `loadWasm` / the IndexedDB cache). The `package.json` `prebuild:worker` script targets a *different* output (`routing-worker.js`) that the app doesn't load. Risk: every fix needs to be applied in both places, and the source of truth is unclear. The proper fix (rewire `prebuild:worker` to bundle TS source + the wasm-pack-generated `pkg/ct_wasm_router.js` into `routing-worker-wasm.js`) is a multi-day refactor that touches the WASM-loading flow — too risky to land in this PR without dedicated verification.

Mitigation that *did* land in May 2026: a vitest drift guard at `frontend/lib/__tests__/routing-worker-bundle.test.ts`. It reads the bundle and asserts (a) every `WorkerRequest` case the main thread can send has a handler, (b) the dead TS routing functions deleted in Phase B don't reappear, (c) the WASM constructors and key methods are still wired. So drift is at least *caught* in CI even while the dual-source remains.

**10. Route-proposal diversity collapses on sparse graphs.** Tracked separately as the open "three proposals too similar" bug.
