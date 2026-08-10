# Ingestion Pipeline — Explained for First-Time Contributors

This doc is the **"why does this exist, how does it fit together, where would I change X"** companion to the existing
[`docs/ingestion-pipeline.md`](ingestion-pipeline.md), which is the line-by-line **architecture & operations reference**.
Read this one first if you have never opened `backend/app/services/ingest.py`. By the end you should be able to scope
a PR that touches any layer — GPX parse, heat-edge upsert, K-anonymity, DEM elevation enrichment, PBF import, map-matching,
matview refresh, PMTiles publish, fgraph rebuild — with confidence about what you can change and what you must not.

Audience assumption: senior backend dev, comfortable with FastAPI / SQL / Python — but new to geospatial work and the
Chemins Communs codebase specifically.

---

## 1. What problem is the pipeline solving?

A user uploads a GPX trace (a list of `(lon, lat, ele)` points sampled every 1–5 seconds by their bike computer or phone).
We need to do **two things** with that data — and they are subtly different:

1. **Preserve it verbatim as a personal artefact.** The user owns the trace. They want to see it on their stats page, share it
   as a route, replay it on the map. The bytes we store in `activities.geometry` are the bytes they uploaded. We never
   re-route, never simplify, never snap. This is the [Crouzet trace integrity invariant](#8-trace-integrity-also-crouzet)
   and it is non-negotiable.

2. **Aggregate it into a community heatmap and routing graph.** Every other rider's experience improves when one rider's GPS
   is folded into the shared map: popular roads glow, single-tracks get tagged with the right surface, the routing engine
   learns that cyclists actually use that gravel link road. This is the "common data" half — under ODbL — and it requires
   *deduplicating across riders*, *resisting de-anonymisation*, *staying cheap on a `db-f1-micro`*, and *publishing artefacts
   the frontend can serve from a CDN with no API round-trip*.

Why not just dump the GPX into S3 and call it a day? Because (2) needs structure:

- **Snap riders' overlapping GPS noise onto a common grid** so the same road traversed by 5 riders becomes one popular edge
  (`heat_edges` row), not 5 individual edges with `user_count=1` each.
- **Tag each edge with OSM metadata** — way ID, surface, highway class — so the surface-overlay UI and the routing
  cost model can reason about it.
- **Enforce K-anonymity** so a unique trace near someone's home doesn't reveal who they are.
- **Publish the result as PMTiles + binary fgraph regional shards** so panning the map and dragging waypoints feels instant,
  with zero per-request API hits.

That structural transformation — GPS jitter to canonical OSM-aware edges to dedup-anonymised public artefact — is what
the ingestion pipeline does. It is the difference between "we have your trace on disk" and "we have a heatmap that improves
with every contribution and you can build a route on it offline in 50 ms".

---

## 2. Why OSM PBF imports?

To map-match each GPS point to a real road segment, we need OSM way geometry + tags **already loaded** into our database.
That's what `osm_road_edges` is: a precomputed, indexed copy of the OSM road network filtered to cycle-relevant ways.

**What's a PBF?** OpenStreetMap's binary distribution format — `OSM Protocol Buffers Binary Format`. Compared to the XML
dump, a PBF is ~7× smaller (binary varint encoding) and orders of magnitude faster to parse. France's PBF from Geofabrik
is around 4 GB; the whole planet is ~75 GB. We use `pyosmium` (the official streaming parser) to walk the file twice:
once for nodes, once for ways. See [`backend/app/cli/import_osm_roads.py`](../backend/app/cli/import_osm_roads.py).

**Why regional shards, not the whole planet?**

- **Geofabrik** publishes a daily-updated PBF per country and per French region (~22 of them, see the table in the agent
  brief). We grab `geofabrik.de/europe/france/<region>-latest.osm.pbf` and parse only the cycle-relevant tags.
- **The whole-France PBF doesn't fit our memory budget.** It OOMs at 8 GiB on a Cloud Run Job; we've never managed an
  end-to-end successful planet import. Regional PBFs sit comfortably in 4 GiB.
- **Per-region imports parallelise on Cloud SQL CPU**, not memory. Three concurrent imports give roughly 1.5× speedup
  before saturating the DB CPU.

**Streaming, not accumulating.** PR #248 rewrote `import_osm_roads.py` around a `StreamingRoadCollector` (`import_osm_roads.py:152`)
that flushes every N segments to a temporary staging table, then transactionally swaps it into `osm_road_edges`. Prior to that,
parsing held everything in Python lists and OOMed on anything larger than Île-de-France.

**COPY, not INSERT.** PR #251 switched the flush from `INSERT … VALUES` to `COPY FROM STDIN` via the staging table. Over
Cloud SQL proxy on a tiny tier, that's a 10–100× win. Whole-Occitanie import went from a 17 h ETA to ~1–2 h.

**Why not just call Overpass at runtime?** We tried. Overpass is rate-limited, latency-sensitive, often-flaky for bulk
lookups, and a single dependency-of-record on every ingest is a foot-gun. `OVERPASS_ENABLED=false` is the prod default
since 2026-05-10. PBF imports happen offline; runtime ingest hits only our own DB.

**Where it lives.** PBF imports run as **Cloud Run Jobs** (`common-trails-import-osm-prod`), not from the API. The API
process must stay scale-to-zero-friendly; PBF parsing is a one-shot 30–180 min batch that wants 8 GiB / 4 vCPU and dies
when it's done. Cloud Run Jobs is the right shape for that.

See [`docs/ops-osm-pbf-import.md`](ops-osm-pbf-import.md) for the operator runbook.

---

## 3. Why Copernicus GLO-90 DEM, baked into the image?

GPX `<ele>` tags from a barometric altimeter are surprisingly noisy — ±2 m at 1 Hz on a flat ride accumulates 200–400 m of
**phantom climb** over 50 km. For the routing cost model (slope penalty) and for surface inference (a steep `track` is almost
always singletrack; a flat one is almost always asphalted farm access), we need an **independent** elevation source.

**What's a DEM?** Digital Elevation Model — a raster of ground heights. The pixel value at `(row, col)` is the elevation in
metres for that grid cell. Resolution = the cell side length on the ground (90 m for Copernicus GLO-90, ~30 m for SRTM3,
1 m for IGN RGE Alti in France).

**Why GLO-90 specifically?**

- **90 m resolution is enough for ingest.** We use it to compute *per-edge* slope grades over typically 10–55 m segments —
  reading the DEM at the endpoints gives a perfectly usable mean grade. We're not building topographic singletracks here;
  we're tagging routing edges.
- **Global, free, public-domain-equivalent.** ESA publishes Copernicus GLO-90 under a permissive licence; the
  [viewfinderpanoramas.org](http://www.viewfinderpanoramas.org/) consolidated tiles fold in void-fill from SRTM3, USGS, and
  others to a uniform HGT file per 1° tile (1201×1201 int16 big-endian — ~2.8 MB per tile).
- **No auth, no rate limits, no flaky upstreams at runtime.**

**Why bake the HGT tiles into the Cloud Run Job container image?**

This is the architectural choice that matters. At ingest time, we resolve `(lat, lon) → elevation` *hundreds of thousands
of times per PBF*. Three options were considered:

| Approach | Latency per lookup | Failure modes | Runtime cost |
|---|---|---|---|
| Hit Open-Meteo at runtime (legacy) | 50–500 ms (over HTTP) | rate limits, flaky upstream, no circuit breaker | egress bandwidth + provider stability |
| Cache HGT in GCS, fetch on demand | ~10 ms + S3 download | tile cache misses → cold-start latency | GCS egress |
| **Bake the HGTs into the container image** | ~1 µs (numpy slice on RAM-cached tile) | no external dep at runtime | image size grows |

We picked option 3. The HGT tiles live under `${DEM_DIR}` inside the image; `backend/Dockerfile` bakes them at build time
via `python -m app.cli.download_dem --region <name>`. Coverage today is `france`, `cataluna`, `italia-nord-ovest` (see the
agent brief for the exact bbox envelope).

**The numpy reader.** [`backend/app/services/local_dem.py`](../backend/app/services/local_dem.py) is a pure-numpy HGT reader.
It bilinearly interpolates the 4 surrounding cells; voids (`-32768`) return `None`. The process-wide cache (max 64 tiles ≈
185 MB) means hot paths cost a single numpy index per lookup.

**Crouzet invariant preserved.** This enriches *derived metrics* (slope, D+ on routing edges, surface inference) on the OSM
graph rows — it does **not** touch stored user GPX coordinates. A 2D-GPX trace from an old Polar device stays 2D in
`activities.geometry`; its heat_edges pick up elevation from the matched OSM way's pre-baked DEM data.

For background, see the surface + elevation audit section of [`.claude/agents/ingest-pipeline.md`](../.claude/agents/ingest-pipeline.md).

---

## 4. The end-to-end flow

```
                    ┌──────────────────────────────┐
                    │ Friend's browser             │
                    │   POST /gpx/upload           │
                    └──────────┬───────────────────┘
                               │
                               ▼
        ┌────────────────────────────────────────────────┐
        │ FastAPI (Cloud Run "common-trails-api-prod")   │
        │   gpx_upload.py:49  (handler)                  │
        │     ↓ parse_gpx()                              │
        │     ↓ ingest_activity(skip_heat_computation=T) │
        │     ↓ INSERT activities (returns 202)          │
        └──────────┬─────────────────────────────────────┘
                   │
                   ▼
        ┌──────────────────────────────┐
        │ Cloud Tasks "heat-compute"   │   (PROD only; DEV runs inline)
        │   enqueue_heat_compute()     │
        └──────────┬───────────────────┘
                   │ (OIDC-signed token)
                   ▼
        ┌────────────────────────────────────────────────┐
        │ POST /internal/ingest/heat                     │
        │   _verify_oidc_token()                         │
        │   process_heat_compute(activity_id, user_id)   │
        │     ↓ _update_heat_edges()                     │
        │         ↓ _match_to_osm()                      │
        │             ├─ Valhalla  (if MAP_MATCHER       │
        │             │   _ENABLED + confidence ≥ 0.7)   │
        │             └─ spatial per-point fallback      │
        │         ↓ Grid-fallback for unmatched coords   │
        │         ↓ _upsert_edges_batch()                │
        │             → heat_edges (ON CONFLICT)         │
        │             → heat_edge_contributors           │
        │             → UPDATE user_count                │
        └──────────┬─────────────────────────────────────┘
                   │
                   ▼
        ┌──────────────────────────────────────┐
        │ Debounced (PROD: Cloud Tasks)        │
        │   enqueue_matview_refresh()  (10 s)  │
        │   enqueue_artefact_rebuild() (5 min) │
        └──────────┬───────────────────────────┘
                   │
                   ▼
        ┌─────────────────────────────────────────────────┐
        │ POST /internal/artefacts/rebuild                │
        │   REFRESH MATERIALIZED VIEW heat_edges_display  │
        │   build_pmtiles → gs://…/heatmap-display.pmtiles│
        │   build_fgraph  → gs://…/<sport>-<region>.fgraph│
        └──────────┬──────────────────────────────────────┘
                   │
                   ▼
        ┌──────────────────────────────────────┐
        │ Cloud CDN (5 min edge cache)         │
        │   → Friend's browser                 │
        │     heatmap PMTiles layer            │
        │     routing-worker-wasm + fgraph     │
        └──────────────────────────────────────┘
```

For ground-truth line-by-line, see the diagram at the top of [`docs/ingestion-pipeline.md`](ingestion-pipeline.md) and
the prod-focused single-trace flow in [`docs/prod-ingestion-flow.md`](prod-ingestion-flow.md).

---

## 5. K-anonymity — what, why, how

### Threat model

A single user with a unique commute (their home street, an unusual loop in their village) is **trivially re-identifiable**
from the heatmap. If their street appears as a glowing line that *only they* could have ridden, anyone with the heatmap
plus a guess at their home neighbourhood can pinpoint them. We have an ethical and legal obligation (GDPR) to make that
impossible.

### The K-anonymity rule

For any edge to appear on the **community** heatmap, **at least K distinct users must have contributed to it.** Below
that threshold, the edge stays in `heat_edges` (so it can be aggregated as that user's *personal* heatmap, which only
they see) but is filtered out of every public read path.

| Environment | `HEATMAP_K_ANONYMITY` | Why |
|---|---|---|
| Production | `2` | Minimum viable anonymisation. Pair of riders means neither can claim a unique trace. |
| Dev | `1` | Visibility while you're the only rider on a brand-new DB. Without this you'd see an empty heatmap. |

### Where it's enforced

- `services/ingest.py:36` reads `HEATMAP_K_ANONYMITY` from env (default `2`).
- **Writes are unfiltered.** Every heat_edge insert / update writes through regardless of `user_count`. We need the data
  to accumulate; the filter only applies on read.
- **Public reads filter `WHERE user_count >= K`.** This is in the MVT tile endpoint, the PMTiles build job, the `/heatmap/*`
  routes, and the routing-cost-model heat lookup.
- **Personal reads bypass K.** `get_personal_edges()` (`ingest.py:2519`) filters by `user_id_hash` instead.

### Operational consequence: the "two-rider rule"

A fresh segment that only you have ridden **will not appear on the community heatmap until a second rider crosses it**.
This is by design and contributors must internalise it before debugging "why isn't my upload showing up":

1. Yes the upload succeeded — check `activities.created_at`.
2. Yes `heat_edges` got new rows — check `heat_edges.user_count` for the bbox.
3. The MVT layer is empty in that area because `user_count < 2`. Not a bug.
4. To verify visually, temporarily flip `HEATMAP_K_ANONYMITY=1` in your dev env.

See also the personal heatmap path in [`docs/heatmap-pipeline.md`](heatmap-pipeline.md) § Stage 5.

---

## 6. Map-matching: why two paths (Valhalla + spatial)

A GPX trace records where the user's GPS *thought* they were — typically 5–30 m off the road they were actually on, more
in urban canyons and forests. Storing those raw coordinates in `heat_edges` means every rider's noise creates its own
`edge_key`; the heatmap never accumulates. Map-matching projects each GPS point onto a real OSM way so multiple riders'
edges collapse to the same key.

We have **two** map-matching implementations because they serve different operating regimes:

### Path A — Valhalla (when `MAP_MATCHER_ENABLED=true`)

[Valhalla](https://valhalla.github.io/valhalla/) is a routing engine that includes a Hidden Markov Model trace-matcher.
You hand it a list of GPS points + a sport-specific costing model; it returns a polyline of OSM ways with a per-point
confidence. We run it as its own Cloud Run service (`valhalla-prod`, scale-to-zero, ~1 GB RAM).

Sport-to-costing mapping ([`services/map_matcher.py:69-75`](../backend/app/services/map_matcher.py)):

| Sport | Valhalla costing |
|---|---|
| `road` | `bicycle` (Road) |
| `gravel` | `bicycle` (Cross) |
| `mtb` | `bicycle` (Mountain) |
| `offroad` | `bicycle` (Mountain + `use_roads=0`) |
| `running` | `pedestrian` (different shape, but same call) |

**Confidence tiers** (`map_matcher.py:51-52`, env-overridable):
- `mean_confidence ≥ 0.7` (`MAP_MATCHER_CONFIDENCE_HIGH`) — trust the match, snap edges to the Valhalla polyline at 5 dp
  (~1.1 m), tag `osm_way_id` from the match, set `match_source='valhalla'`.
- `0.3 ≤ confidence < 0.7` — too uncertain. Raise `MatchUnavailable` and fall through to path B.
- `< 0.3` (`MAP_MATCHER_CONFIDENCE_LOW`) — match is noise. Raise `MatchLowConfidence` and skip the segment entirely
  (no grid-fallback emitted — it would be too noisy to be useful).
- Timeout: `MAP_MATCHER_TIMEOUT_S = 5.0` (env-overridable).

### Path B — Spatial per-point matcher (the legacy fallback)

When Valhalla is disabled, times out, or returns a mid-confidence match, `_match_to_osm` (`ingest.py:1147`) does
per-point nearest-segment lookups against `osm_road_edges` using a 0.0005° spatial grid (~55 m cells).

**Sport-aware match radius** (`ingest.py:111-117`):

| Sport | Radius |
|---|---|
| road, running | 25 m (urban GPS multipath is 20–40 m) |
| gravel, mtb, offroad | 15 m (single-track precision matters more) |

The May 2026 audit (see `docs/ingestion-pipeline.md` § "Improvement opportunities") found that a flat 15 m radius left
road and running at 1.6 % and 6.6 % match rates respectively. Sport-aware radii brought aggregate match rate from
~10–15 % to 81.8 %.

After matching, the matched-pair endpoints are projected orthogonally onto the OSM segment (`_project_onto_segment`), then
snapped to 5 dp. That projection is the spaghetti-fix — without it, each rider's offset GPS jitter created its own
`edge_key`. Post-projection, four riders on the same trail produce one `edge_key` with `user_count=4`.

### Path C — Grid-fallback

For coordinates that *neither* path matched (a GPS-only single-track that doesn't exist in OSM yet, or a forest road tagged
as `path` excluded by our cycle filter), we snap each `(lat, lon)` to 4 dp (~11 m) via `_snap` and emit edges with
`osm_way_id = NULL`. There's a 60 m length cap (`_MAX_GRID_EDGE_M`) so a track break doesn't produce one mega-edge.

A nightly `python -m app.cli.group_edges_osm` (since PR #250) catches up unmatched grid-fallback edges against any OSM
data imported *after* the original ingest — recovering ~70 % of them on the Montpellier benchmark.

### Surface attribution flows from match path

OSM-matched edges (paths A & B) inherit `surface_type` / `highway_type` from the matched way. Grid-fallback edges (path C)
stay `surface_type='unknown'` until `group_edges_osm` catches them. See § 7.

---

## 7. Surface attribution & confidence scoring (Crouzet method)

> We don't claim absolute truth on surface. OSM's tagging coverage is uneven — especially on small French paths
> where many `track`s are genuinely unknown. The frontend shows surface as **an estimation with uncertainty**, not a
> verdict. This is a brand value, not just an implementation detail.

### How it works

[`backend/app/services/surface_classification.py`](../backend/app/services/surface_classification.py) is the single source
of truth (consolidated in PR #259 from three previously-duplicated maps). The `classify_surface` function applies three
rules in order, each producing a `(surface_class, confidence)` pair:

| Rule | Input | Confidence range |
|---|---|---|
| A — explicit `surface=*` tag on the OSM way | `surface=asphalt` → `(asphalt, 1.0)`; `surface=fine_gravel` → `(gravel, 0.85)` | 0.85–1.0 |
| B — `tracktype` or `highway` fallback | `highway=track + tracktype=grade1` → `(asphalt, 0.95)`; `highway=path` → `(dirt, 0.30)` | 0.30–0.95 |
| C — `smoothness` downgrade | adjacent rule cap if `smoothness=bad` | caps at 0.6 |

Heat_edges get `surface_type` written at ingest from the matched OSM way (no confidence stored — see "weakpoints" in the
agent brief). The live `/routes/surface_stats` endpoint reclassifies on demand and returns per-segment confidence.

### `data_quality` buckets

The frontend aggregates `surface_stats` over the route and labels the result:

| Bucket | % unknown distance | UI behaviour |
|---|---|---|
| `good` | < 20 % | full surface overlay rendered |
| `partial` | 20–50 % | overlay rendered with a "(partial data)" badge |
| `poor` | > 50 % | overlay **hidden**; base route line stays visible |

That last rule is the brand-value part: rather than render a misleading overlay, **we hide it**. The base GPX trace is
never hidden (Crouzet — see § 8).

### A known systematic mislabel

`_HIGHWAY_DEFAULT` (`surface_classification.py:42-61`) assigns `track → gravel @ 0.40` and `path → dirt @ 0.30`. In rural
France a huge share of `track`s are asphalted farm-access roads. Improving this is in the "still open" weakpoints list
of the agent brief — region-conditioned priors are the planned fix, but until then the confidence score (0.40) is the
honest signal: low-confidence inference, not absolute truth.

---

## 8. Trace integrity (also Crouzet)

**Never auto-reroute, never simplify, never snap an imported GPX.** This is the Crouzet methodology (named after the
project's design inspiration, [tcrouzet.com](https://tcrouzet.com/) — see [`docs/vision.md`](vision.md) and
[`memory/project_crouzet_methodology.md`](../.claude/projects/-Users-paulleclercq-projects-common-trails/memory/project_crouzet_methodology.md)).

### Why this matters to riders

A rider's GPX trace is **the record of where they actually rode**. If we rewrite it — even to "clean it up" — we're erasing
their lived experience and replacing it with our guess at what they meant. That breaks trust irrecoverably:

- Tour de France climbs on real terrain look subtly different from re-snapped routes. Riders notice.
- Off-trail singletrack that doesn't appear in OSM gets erased entirely if we auto-snap. Then their data tells *us* there's
  no trail there, and we never improve.
- "The app changed my route" is the single most common reason users abandon a cycling app. It's worth eating implementation
  complexity to never do it.

### How the code enforces it

- `services/gpx.py:parse_gpx` stores coordinates **verbatim** (`gpx.py:119+`). 3D when every point has `<ele>`, 2D otherwise
  — but never lossy.
- `ingest_activity` writes `geometry_geojson` (and `geometry` since migration 0036) as-is.
- The frontend `skipRecalcRef` flag prevents auto-rerouting when loading an existing trace into the editor.
- Smart routing only fires on **explicit user action** (changing sport, adding a waypoint).

### The heatmap is a separate object

The 5 dp snapping done in `_snap_fine()` (for heat_edges) and 4 dp snapping (`_snap()`, grid-fallback) is **isolated to
edge statistics**. It feeds `heat_edges`, never `activities.geometry`. The same applies to map-matching, projection, and
canonical merging — they all build `heat_edges` rows; they never overwrite a user trace.

When in doubt, the test is: **if I delete this heat_edge, can the user still see their original ride at full fidelity?**
The answer must always be yes.

---

## 9. The two-phase upload (sync 202 → Cloud Tasks)

### What it looks like

```
t=0 ms      Friend clicks Upload, browser sends multipart POST /gpx/upload
t=80 ms     API parses GPX, validates, idempotency check
t=120 ms    INSERT into activities (skip_heat_computation=True)
t=150 ms    Cloud Tasks queue: enqueue_heat_compute(activity_id, user_id)
t=200 ms    API returns 202 {"activity_id": "...", "status": "processing"}
            ← Friend's browser shows a spinner. The trace is durable;
              they can already see it on /me/stats.

t=200 ms    Cloud Tasks picks the task off the queue (in parallel, OOB)
t=250 ms    POST /internal/ingest/heat (signed with OIDC by tasks_invoker SA)
t=280 ms    process_heat_compute() reads the activity row
t=300 ms+   _update_heat_edges() runs map-matching, dedup, UPSERT, contributor update
t=5–30 s    Done. The heatmap will reflect the new edges after the next
            debounced matview refresh + PMTiles publish.
```

### Why decouple?

Two reasons, both about money and user experience:

1. **Cloud Run charges for HTTP-connection time.** The synchronous path held the user's connection open for the full 5–30 s
   of heat-edge computation. At even modest scale that's expensive. Closing the connection at 200 ms drops the per-upload
   bill dramatically.

2. **Heat-edge computation can time out.** K-anonymity dedup retries on deadlock, OSM segment loading on cold tiles can
   take 5–15 s, and a bad GPX with 30 000 points can blow the 60-second Cloud Run request budget. Pushing it to Cloud Tasks
   (which has its own 10 min budget per task + retries with backoff) means a long ingest never fails the user-visible
   upload.

The activity row is durable **immediately**. The heatmap lags by seconds (the matview refresh debounces 10 s; PMTiles
publish debounces 5 min). That's a deliberate trade-off — users see their trace right away, the community heatmap catches
up shortly after.

### Don't reintroduce the synchronous path

PR #214 removed the `_update_heat_cells` write (cells are now aggregated from `heat_edges` at query time). Don't add it
back, and don't add any new "block the user's upload until X is done" code. The shape of `gpx_upload.py:49+` is
*intentional*: parse, store, enqueue, return.

If you need to add a synchronous validation step, do it before the `INSERT` — not after.

---

## 10. fgraph regional builds — why a binary per sport per region

The README promises **"100 km A→B route in 50 ms, then drag-and-drop edits feel instant"**. The way we deliver that is by
running the routing engine **inside the user's browser**, off a binary graph that's loaded once and reused for all queries.

### What's a `.fgraph`?

A compact binary serialisation of the routing graph (nodes + edges + cost-model inputs) for one sport × one region.
Format is documented in [`docs/spec-osm-routing-graph.md`](spec-osm-routing-graph.md) and the encoder is in
`backend/app/services/graph_builder.py`.

Typical sizes:

| Region | Sport | `.fgraph` size |
|---|---|---|
| `gravel-sud-est` | gravel | ~27 MB |
| `mtb-sud-est` | mtb | ~22 MB |
| `road-paris` | road | ~12 MB |
| `running-paris` | running | ~8 MB |

The frontend's `routing-worker-wasm.js` loads the relevant shard on map init, keeps it in worker memory, and runs Dijkstra
against it. No API round-trips; no Cloud SQL touches. Drag-and-drop on a 100 km route stays under the 50 ms target.

### Why regional + per-sport, not one global graph?

- **A global graph of cycle-relevant ways in France is too big to ship to a browser** (~700 MB raw, ~250 MB after the binary
  encoding). Per region it's 5–25 MB — within mobile data budgets, cacheable in `localStorage`.
- **The cost model is sport-specific.** Pre-computing per sport keeps each `.fgraph` smaller and the worker code simpler
  (no profile switching at query time).

### How it gets built

After every ingest cycle that crosses the `enqueue_artefact_rebuild` 5-min debounce, the
`/internal/artefacts/rebuild` handler walks `heat_edges` per region per sport, encodes the graph, gzips it, and uploads
to `gs://YOUR-PROJECT-heatmap-artefacts/<sport>-<region>.fgraph`. Cloud CDN caches with a 5-min TTL.

The frontend's cascade for graph loading is documented in [`docs/routing-architecture.md`](routing-architecture.md).

---

## 11. "Where would I change X" map

Each row of this table was verified against `main` on 2026-05-17. If you find drift, fix it in the same PR as your change.

| I want to… | Touch this | Notes |
|---|---|---|
| Add a new GPX field (e.g. heart-rate) | `services/gpx.py:parse_gpx()` (line 119) | Decide if it belongs in `activities` (private) or stays out of the schema. **Do not** put it in `heat_edges`. |
| Add a new sport to the heatmap | (a) `db/models.py` HeatEdge enum; (b) `_normalize_heat_edge_sport` (`ingest.py:1681`); (c) Alembic migration adding a `heat_edges_<sport>` partition; (d) `routing_profiles.py`; (e) frontend sport selector | The partition is mandatory — `heat_edges` is LIST-partitioned. Missing partition → INSERTs fail silently into `default`. |
| Change the K-anonymity threshold | `HEATMAP_K_ANONYMITY` env var | Defined at `ingest.py:36`. **Do not inline the constant** — multiple read paths reference it. |
| Support a new file format (TCX, FIT) | FIT already exists at `services/fit_parser.py:15`. Mirror it for TCX. Then wire into `api/imports.py`. | The contract is: return a dict with `geometry_geojson`, `file_hash`, `distance_m`, `elevation_gain_m`, `started_at`, `coords` ([lon, lat, ele] triples). |
| Fix dedup (known: 99 % `user_count=1`) | `_upsert_edges_batch()` at `ingest.py:1540` + `heat_edge_contributors` UPSERT logic | See `project_dedup_bug.md` memory. The likely fix is UPSERT on `(osm_way_id, sport)` for OSM-matched edges. |
| Add a new OSM tag for surface | `services/surface_classification.py:_SURFACE_MAP` (the SSOT post-PR #259). Mirror into `osm_road_edges` import (re-run `import_osm_roads`). | Don't add a duplicate map elsewhere — the de-duplication of `SURFACE_NORMALIZE` was PR #259. |
| Change OSM match radius for a sport | `_OSM_MATCH_RADIUS_BY_SPORT` at `ingest.py:111-117` | Re-run the audit on `scripts/audit_ingest_perf.py` before/after. |
| Change Valhalla confidence threshold | `MAP_MATCHER_CONFIDENCE_HIGH` / `MAP_MATCHER_CONFIDENCE_LOW` env vars | Both read in `services/map_matcher.py:51-52`. |
| Add elevation/slope to grid-fallback edges | `services/local_dem.py` + `ingest.py` grid-fallback block (~1825-1900) | The DEM lookup helper exists; you'd just need to call it in the grid loop. Watch the bbox envelope — see § 3. |
| Change the matview refresh debounce | `_MATVIEW_REFRESH_DELAY` (top of `ingest.py`) | Bulk imports bypass via `SKIP_MATVIEW_REFRESH=true`. |
| Change PMTiles output zoom range | `app.jobs.build_pmtiles` `main()` args (z6–z15 today) | Called from `_do_pmtiles_rebuild()` at `ingest.py:328`. |
| Add a new OSM region preset | `REGIONS` dict in `app/cli/import_osm_roads.py:46`; also `app/cli/download_dem.py:65+` for DEM coverage | Then bake DEM into `backend/Dockerfile`. |

---

## 12. First contribution playbook (ingestion-specific)

The README has a generic Quickstart; this is the **ingestion-pipeline** version.

### Setup

```bash
docker compose up -d                          # db + backend + frontend
curl http://localhost:8787/healthz             # API alive?
docker compose exec backend python -m app.jobs.refresh_matview  # 5–15 min, once
```

That `refresh_matview` step is critical for dev: without it the MVT path falls through to the slower live query and you'll
think the heatmap is broken. See [`docs/heatmap-pipeline.md`](heatmap-pipeline.md) § Stage 3 for why.

### Make a change, run the relevant tests

Don't run the full suite for every iteration — it takes ~5 min. Pick the focused set:

```bash
# Anything touching ingest internals
docker compose exec backend pytest -q backend/tests/test_ingest_bulk.py backend/tests/test_internal_ingest.py

# GPX parser changes
docker compose exec backend pytest -q backend/tests/test_gpx_edge_cases.py backend/tests/test_gpx_elevation_smoothing.py backend/tests/test_gpx_sport_classification.py

# Map-matching
docker compose exec backend pytest -q backend/tests/test_map_matcher.py

# K-anonymity
docker compose exec backend pytest -q backend/tests/test_k_anonymity_boundary.py  # 8 cases

# Heat-quality monitor (new feature; bump if you change grid-fallback logic)
docker compose exec backend pytest -q backend/tests/test_heat_quality.py

# OSM PBF import
docker compose exec backend pytest -q backend/tests/test_osm_import_streaming.py
```

### Inspect the dev DB

```bash
# Quick activity check
docker compose exec db psql -U postgres -d common_trails -c "
SELECT id, sport, distance_m, ROUND(elevation_gain_m::numeric) AS ele_m, created_at
FROM activities ORDER BY created_at DESC LIMIT 5;"

# Heat-edge dedup health
docker compose exec db psql -U postgres -d common_trails -c "
SELECT
  COUNT(*) AS edges,
  COUNT(*) FILTER (WHERE user_count = 1) AS uc_1,
  COUNT(*) FILTER (WHERE user_count >= 2) AS uc_2plus,
  MAX(pass_count) AS max_pc,
  ROUND(AVG(pass_count)::numeric, 2) AS avg_pc
FROM heat_edges;"

# OSM tagging coverage (continuous-line invariant)
docker compose exec db psql -U postgres -d common_trails -c "
SELECT sport,
       ROUND(100.0 * COUNT(*) FILTER (WHERE osm_way_id IS NOT NULL) / COUNT(*), 1) AS pct_with_osm
FROM heat_edges GROUP BY sport ORDER BY 2 DESC;"
```

### Verify your change shows up on the heatmap

1. Upload a fresh GPX (e.g. `backend/tests/fixtures/Morning_Gravel_Ride.gpx`):
   ```bash
   curl -F "file=@backend/tests/fixtures/Morning_Gravel_Ride.gpx" \
        -F "sport=gravel" \
        http://localhost:8787/gpx/upload
   ```
2. Wait for the Cloud Tasks fallback (dev runs inline; ~5–30 s).
3. Re-check `heat_edges` for new rows in that area.
4. Refresh the matview manually if you bypassed the debounce:
   ```bash
   docker compose exec backend python -m app.jobs.refresh_matview
   ```
5. Open `http://localhost:3787` and pan to the activity bbox. If `HEATMAP_K_ANONYMITY=1` in dev, you should see the
   trace on the heatmap immediately.

### Fixtures available

```
backend/tests/fixtures/
├── Afternoon_Mountain_Bike_Ride.gpx
├── Mistral_Mountain_Bike_Ride.gpx
├── Morning_Gravel_Ride.gpx
└── Morning_Mountain_Bike_Ride.gpx
```

Real-rider GPX from the project owner, anonymised. Use these instead of hand-crafted XML for any pipeline test that needs
realistic GPS jitter.

---

## 13. Known traps

A non-exhaustive list of bugs that have bitten the project. A newcomer would re-step on these without context.

1. **`for lon, lat in coords` breaks on 3D points.** GPX with `<ele>` produces `[lon, lat, ele]` triples. Always index with
   `point[0]/point[1]`, never destructure. See § "3D coords" in the agent brief.

2. **Destructive Makefile targets must be single-shell.** PR #249 fix: a `@if … exit 0; fi` split across two `@`-prefixed
   recipe lines does **not** short-circuit, because each `@` line spawns its own shell. The prod incident 2026-05-14 wiped
   prod heat_edges through exactly this mistake. CONFIRM check and action must be in the same shell — see
   `feedback_makefile_dry_run_must_be_single_shell` memory.

3. **`--min-uc 2` is never the right answer to spaghetti in prod beta.** Filtering out `user_count=1` edges hides the
   problem rather than fixing it. See `feedback_min_uc_1_for_beta` memory.

4. **Matview refresh must be debounced.** Bulk imports set `SKIP_MATVIEW_REFRESH=1` and call `refresh_display_matview()`
   **once at the end**. Each refresh is 5–35 s; a 1400-activity Strava export with refresh-on-every-write spends 30 min
   refreshing.

5. **`INSERT VALUES` vs `COPY FROM STDIN` matters at scale.** PR #251: OSM PBF import flush over Cloud SQL proxy is 10–100×
   faster via COPY. Don't add an INSERT-loop in any bulk import path.

6. **The streaming OSM collector flushes per-batch** (PR #248). Don't accumulate into Python lists during PBF parse —
   that's how `import_osm_roads.py` used to OOM at 16–33 GB on whole-France PBFs.

7. **Cloud Run Job `--task-timeout` default is 1 h.** Anything that processes >100 k rows in a tight loop (e.g.
   `group_edges_osm` on 1.7 M heat_edges) will be silently killed at the wall. Bump to `--task-timeout=14400s` (4 h) or
   `--task-timeout=86400s` (24 h max) at job creation.

8. **`heat_edge_contributors` has no FK to `heat_edges`.** Deleted activities leave orphan contributor rows. Test
   isolation must wipe both tables (and `osm_road_edges` if you scope by way_id). See the test isolation note in the
   `group_edges_osm` section of the agent brief.

9. **Module-level state breaks at `max_instances ≥ 2`.** `_edge_version`, `_osm_segment_cache`, `_osm_grid_cache` are all
   instance-local. Today we run `min=0/max=1`; if you ever scale up, these need to move to Redis or a DB-backed cache.

---

## 14. Debugging recipes

Short cookbook entries for the most common ingestion-pipeline mysteries. Each one starts with what the user reports and
walks to root cause.

### "My GPX uploaded but isn't on the heatmap"

```
1. activities.created_at recent?       SELECT created_at FROM activities WHERE id = '...';
2. Cloud Tasks task ran?                gcloud logging read "resource.type=cloud_run_revision \
                                          AND textPayload:process_heat_compute" --limit=5
3. heat_edges populated in the bbox?    SELECT count(*) FROM heat_edges
                                          WHERE ST_Within(geometry, ST_MakeEnvelope(<bbox>, 4326));
4. user_count >= K?                     SELECT count(*) FROM heat_edges
                                          WHERE ST_Within(...) AND user_count >= 2;
5. Matview refreshed since the write?   SELECT MAX(refreshed_at) FROM heat_edges_display_meta;
6. PMTiles published since refresh?     gsutil ls -l gs://YOUR-PROJECT-heatmap-artefacts/heatmap-display.pmtiles
```

Step 4 is the most common answer ("two-rider rule" — see § 5). If you're the only user in dev, set
`HEATMAP_K_ANONYMITY=1`.

### "The heatmap is full of straight diagonal lines"

Grid-fallback is firing because OSM data is missing in that bbox. Check:

```sql
-- How much of this region's heat_edges have no OSM way?
SELECT
  ROUND(100.0 * COUNT(*) FILTER (WHERE osm_way_id IS NULL) / COUNT(*), 1) AS pct_grid_fallback
FROM heat_edges
WHERE ST_Within(geometry, ST_MakeEnvelope(<bbox>, 4326));

-- How many OSM ways do we have there?
SELECT COUNT(*) FROM osm_road_edges
WHERE ST_Intersects(way_geometry, ST_MakeEnvelope(<bbox>, 4326));
```

If `osm_road_edges` count is low, you need to import the relevant region's PBF (see § 2). If it's normal, the
`_match_to_osm` confidence is rejecting matches — bump `MAP_MATCHER_CONFIDENCE_LOW` or widen
`_OSM_MATCH_RADIUS_BY_SPORT` for that sport.

After importing more OSM data, run `python -m app.cli.group_edges_osm` (or `make prod-group-edges-osm-job`) to catch up
existing grid-fallback edges against the new OSM data.

### "My import is slow"

The flowchart:

```
Bulk path?  no  →  set SKIP_MATVIEW_REFRESH=true + SKIP_PMTILES_REBUILD=true
            yes →  also set SKIP_CANONICAL_MERGE=true (10× speedup on clean DB)
                              + SKIP_OSM_FETCH=true (use existing osm_road_edges only)
                              + OVERPASS_ENABLED=false
                              + bump OSM_GRID_CACHE_MAX from 200 to 500 if many regions
                              + use COPY-staging via the streaming collector (not VALUES)

Cold-cache OSM tile loads (text: "OSM match: 74711 segments loaded (41 tiles)")
                                  ↑ each of these is a DB round-trip; this is option B
                                    in the agent brief "Improvement opportunities" list

Single-trace ingest > 30 s?  →  count the GPX points. 30 000+ pt traces blow up grid lookup;
                                 consider raising MAP_MATCHER_TIMEOUT_S only after profiling.

Deadlock errors?              →  retry_on_deadlock decorator (already on _upsert_edges_batch).
                                 Concurrent rebuild_heatmap executions cause this — never run
                                 two of them simultaneously; see the rebuild_heatmap tripwires
                                 in the agent brief.
```

### "Tests pass locally but fail in CI"

Fixture isolation. Since PR #242 we switched `test_pipeline_patterns` from sport-based isolation to bbox-based, which
avoids cross-test bleed when sport partitions are shared. If your test wipes only `heat_edges` it leaves orphan
contributors (no FK — see trap #8). Use the wipe pattern from `test_group_edges_osm_tags_way_id.py`:

```python
db.execute(sa_text("DELETE FROM heat_edge_contributors WHERE edge_key LIKE :p"), {"p": f"%{prefix}%"})
db.execute(sa_text("DELETE FROM heat_edges WHERE ST_Within(geometry, ST_MakeEnvelope(<bbox>, 4326))"))
db.execute(sa_text("DELETE FROM osm_road_edges WHERE osm_way_id BETWEEN 999999000 AND 999999099"))
db.commit()
```

Use osm_way_ids in the 999_999_xxx range for test rows; never overlap real data.

---

## Further reading

- [`docs/ingestion-pipeline.md`](ingestion-pipeline.md) — the architecture & operations reference, with current
  line-by-line code citations, tunable parameters, and the optimisation history. Read this **after** you've finished the
  doc you're reading now.
- [`docs/prod-ingestion-flow.md`](prod-ingestion-flow.md) — the prod-focused "single trace, end-to-end with timings"
  walk-through. Best companion when you're debugging a real prod ingest.
- [`docs/heatmap-pipeline.md`](heatmap-pipeline.md) — the heatmap-specific pipeline view, including MVT tile generation
  and the `heat_edges_display` matview.
- [`docs/routing-cost-model.md`](routing-cost-model.md) — how surface, slope, and heat scores combine into the routing
  edge cost. Touch this if you change anything in `heat_edges.surface_type` or `slope_grade`.
- [`docs/routing-architecture.md`](routing-architecture.md) — how the binary `.fgraph` shards get served to the WASM
  routing worker.
- [`docs/spec-osm-routing-graph.md`](spec-osm-routing-graph.md) — the `.fgraph` binary format spec.
- [`docs/ops-osm-pbf-import.md`](ops-osm-pbf-import.md) — operator runbook for PBF imports.
- [`docs/valhalla-ops.md`](valhalla-ops.md) — Valhalla map-matcher operational notes.
- [`docs/migration-runbook.md`](migration-runbook.md) — schema migrations + matview recovery.
- [`docs/prod-rebuild-runbook.md`](prod-rebuild-runbook.md) — end-to-end heatmap rebuild procedure.
- [`docs/privacy.md`](privacy.md) — K-anonymity policy and GDPR considerations from the user perspective.
- [`docs/vision.md`](vision.md) — Crouzet methodology in narrative form.
- [`.claude/agents/ingest-pipeline.md`](../.claude/agents/ingest-pipeline.md) — the agent brief, refreshed with the same
  PR as this doc.
