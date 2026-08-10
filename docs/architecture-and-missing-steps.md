# Architecture & Missing Steps — Chemins Communs

> Last updated: 2026-05-09. Snapshot of what exists, what's deployed, and what's missing
> for closed-beta + scale. Read this first when picking the project back up.
>
> **Citations are file:line accurate as of the date above. Re-verify before relying.**

## TL;DR — Where we are

| Pillar | Local | Prod | Quality bar |
|---|---|---|---|
| API (FastAPI) | ✅ Running on `:8787` | ✅ Cloud Run, `min=0/max=1`, `cpu-throttling` | A |
| DB (PostGIS+pgRouting) | ✅ Local Docker `:5487` | ✅ Cloud SQL `db-f1-micro` | B (cost vs perf) |
| Frontend (Next.js static export) | ✅ Built into backend image | ✅ served from FastAPI `/static-frontend` | A |
| Heatmap PMTiles | ✅ Bind-mounted, auto-rebuilt | ✅ in CDN bucket | A |
| Routing graphs (`.fgraph` shards) | ✅ Per-sport regional shards in `frontend/public/` | ✅ Same | A |
| WASM router (`routing-worker-wasm.js`) | ✅ Hand-maintained 1297-line glue | ✅ Same | A |
| Cron-free artefact rebuild (matview, PMTiles, fgraph) | ✅ `threading.Timer` (dev) | ✅ Cloud Tasks const-name dedup | A |
| Backend `GET /routing` cascade fallback | ⚠️ **Broken at scale** — 87 s + OOM kill on 4.2 M edges | ⚠️ Same code, untested at scale | C |
| **Map-matcher (Valhalla)** | ⚠️ Tiles built but Toulouse-area still missing (rebuild from france-latest in progress) | ❌ **Not deployed** | C |
| OSM road edges (continuous-line invariant) | ⚠️ ~44 % road heat_edges have NULL `osm_way_id` | ✅ South-France imported | B |
| Heat-edges dedup | ⚠️ **Open bug** — local audit shows 4.2 M edges with `avg pass_count = 2`; same-user repeat traces don't merge | Same | C |
| Closed-beta gate | ❌ Open registration | ❌ Open registration | C |
| Strava OAuth integration | ✅ TEST_MODE stubs | ⚠️ Callback URL not yet updated for prod | B |

Letter grade: A = production-ready, B = ships but has known gaps, C = blocker for beta.

## System architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         USER (browser)                                   │
│           https://chemins-communs.fr                                     │
└──────────────────┬────────────────────────────────────┬──────────────────┘
                   │ HTTPS                              │ HTTPS (PMTiles)
                   ▼                                    ▼
┌───────────────────────────────────┐   ┌──────────────────────────────────┐
│  Cloud Run: common-trails-api-prod│   │  Cloud Storage + CDN             │
│  ──────────────────────────────── │   │  ───────────────────────────────  │
│  • FastAPI                        │   │  • static-frontend assets         │
│  • Static frontend served at /    │   │  • heatmap-display.pmtiles        │
│  • API endpoints                  │   │  • Valhalla tiles tarball        │
│  • PMTiles served via /pmtiles    │   │    (planned, not yet uploaded)   │
│    (Range support)                │   └──────────────────────────────────┘
│                                   │
│  min=0, max=1, cpu-throttling on  │
│  → cold start ~5-10s on first hit │
│  → /readyz returns 'warming' if   │
│     heat_edges = 0 (PR #227 fix)  │
└─────┬────────────┬────────────────┘
      │            │
      │ SQL (TLS)  │ HTTP (currently 0 instances — Valhalla not deployed)
      ▼            ▼
┌──────────────────────┐  ┌─────────────────────────────────┐
│ Cloud SQL            │  │ Valhalla (NOT YET DEPLOYED)     │
│ common-trails-prod   │  │ • Map-matching for ingest       │
│ ──────────────────── │  │ • Will be Cloud Run on internal │
│ • PostGIS+pgRouting  │  │   VPC                           │
│ • db-f1-micro        │  │ • Tiles loaded from GCS at boot │
└──────────────────────┘  └─────────────────────────────────┘

ASYNC FLOWS (event-driven, no cron)
  Ingest → enqueue_matview_refresh()  → /internal/matview/refresh   (OIDC)
       → enqueue_artefact_rebuild() → /internal/artefacts/rebuild (OIDC)
  Cloud Tasks queues use const task name (matview-refresh, artefact-rebuild)
  Debounce: matview 10 s, artefact 300 s
```

## Data flow — happy path

### Upload GPX (ingest)

```
POST /gpx/upload  → ingest_activity()  (skip_heat_computation=True in prod)
                    ├─ INSERT activities
                    └─ INSERT activity_cells
                  → enqueue_heat_compute()    [prod: Cloud Tasks]
                                              [dev: runs inline]
                  ↓
POST /internal/ingest/heat (OIDC)  → process_heat_compute()
                                    → _update_heat_edges()
                                       ├─ _match_to_osm()
                                       │   ├─ Valhalla path (5dp snap, osm_way_id from match)
                                       │   ├─ Spatial path (5dp snap, osm_way_id from osm_road_edges)
                                       │   └─ Grid fallback (4dp snap, osm_way_id = NULL)
                                       │
                                       ├─ INSERT heat_edges ON CONFLICT DO UPDATE
                                       │   pass_count = pass_count + 1
                                       │
                                       ├─ INSERT heat_edge_contributors ON CONFLICT DO UPDATE
                                       │   RETURNING new contributor edge_keys
                                       │
                                       └─ UPDATE heat_edges
                                          SET user_count = COUNT(distinct contributors)
                                          WHERE edge_key IN new_contributor_keys
                                  → enqueue_matview_refresh()    (10s debounce)
                                  → enqueue_artefact_rebuild()   (300s debounce)
```

### Render map tile

1. User pans → frontend requests MVT tile from API at `/heatmap/{sport}/{z}/{x}/{y}`
2. Cache hit → CDN returns tile in <50ms
3. Cache miss → API generates from heat_edges + osm_road_edges (`GROUP BY osm_way_id` + LATERAL JOIN — *partially broken because ~44 % rows have NULL `osm_way_id`*)

### Compute route — the motto

1. User enters route mode → `routing-worker-client.ts` sends `loadWasm` to the Worker
2. Worker fetches `/partitions.json`, picks closest partition to viewport center, fetches `{sport}-{partition}.fgraph` via IndexedDB-backed cache
3. `WasmRouter.fromSerialized()` deserializes — pre-built CH + Dijkstra graph included, no rebuild
4. User drops A and B → Rust CH route in WASM, returns polyline
   - Target: < 5 ms for the motto e2e test (50 km Montpellier→Anduze)
5. Drag handle → `snapAndRoute` combined message (snap + 2 routes in one Worker round-trip)
6. If WASM has no shard for the viewport → fall back to viewport-tile loading (`mergeEdges` Garmin-style)
7. If still nothing → backend `GET /routing` cascade (smart_in_memory → hybrid → BRouter → OSRM → straight line). **Backend cascade is broken at scale; treat as "should rarely happen."**

## Missing steps for closed beta

### 🔴 Critical (blocks invite)

#### 1. Closed-beta signup gate
Registration is currently open at `chemins-communs.fr/register`. Anyone can sign up.

**Options**:
- ENV flag `REGISTRATION_OPEN=false` + manual user creation script
- Whitelist email domains
- Single shared signup code (env var)

**Recommended**: env flag + pre-create accounts for the 3 friends.

**Effort**: 1-2 h backend + 30 min frontend.

#### 2. Strava OAuth callback URL (Paul-only)
Callback configured for localhost; will fail on prod. Update Strava developer dashboard at strava.com/developers → set callback to `https://chemins-communs.fr/strava/callback`.

**Effort**: 5 min.

#### 3. Backend cascade timeout / OOM kill
`GET /routing` runs each cascade step with a 30 s timeout, but the cumulative cascade can run 87 s (smart_in_memory 52 s + hybrid 35 s) and triggers OOM-kill on a 4.2 M-edge local DB. This is the cascade fallback when WASM has no shard for a viewport.

**Recommended fix**: cap total cascade at 10 s, return clean 408 with `warnings: ["coverage_gap"]` for the UI to surface honestly. Audit cross-tagging memory footprint (`_tag_trail_segments` enriches 11 k+ edges per call).

**Effort**: 2-4 h.

#### 4. Heat-edges dedup investigation
Local audit (2026-05-09): 4.2 M heat_edges with `avg pass_count ≈ 2`. Same-user repeat traces aren't merging because GPS jitter at 5dp creates near-but-not-identical edge_keys. Symptoms: low pass_count distribution, unexpected edge volume per activity (~2260 vs expected ~1000 for 50 km activity).

**Fix candidates**:
- A) UPSERT on `(osm_way_id, sport)` for OSM-matched edges (cleanest — needs migration + index)
- B) Coarsen 5dp snap to 4dp (loses sub-road precision)
- C) Move dedup to matview/render layer (accept linear edge growth, aggregate at query time)

**Decision needed before fix.**

**Effort**: 4-8 h after design decision.

### 🟡 Important (ships without, but loses quality)

#### 5. Valhalla in production
Code is wired and tested locally (`MAP_MATCHER_ENABLED=true` + `VALHALLA_URL`). Tiles exist locally but had Toulouse coverage gap from initial multi-PBF merge. Rebuild from `france-latest.osm.pbf` (clipped lon=-2..8, lat=42..47) in progress.

**Plan** (3 phases):
- a) Build clean tiles — *in progress as of this doc.*
- b) Stage tiles in GCS — `valhalla_tiles/` tarball → `gs://common-trails-heatmap-artefacts/valhalla/`
- c) Cloud Run service `common-trails-valhalla-prod` — image `ghcr.io/gis-ops/docker-valhalla/valhalla` with init script that downloads tiles from GCS at boot. Internal VPC connector. 4 GB memory, 1 vCPU, min=0/max=1 (cold start ~30 s acceptable for ingest async path).

**Effort**: 4-6 h infra (terraform + Cloud Run + secret + smoke).

#### 6. Heatmap density visual parity
Continuous-line invariant in code, but no automated visual regression vs Komoot. Take screenshots of 3 known regions (Cévennes, Vaucluse, Lyon hills), compare against Komoot screenshots side-by-side.

**Caveat**: ~44 % NULL `osm_way_id` rows in road heatmap → spaghetti for those zones. Fix-or-accept decision.

**Effort**: 1 h manual.

### 🟡 Important — found 2026-05-10 user testing

#### 6.5 Trail data (GR/GRP/GT/PR/EV) — fixed by switching to PBF parsing

**Status: fixed in this PR.** Previously the lifespan task hit Overpass
API to import GR/GRP/GT/PR/EV trail route relations. When Overpass was
rate-limited or down, the import failed and `trail_edges` ended up empty
(local audit on 2026-05-10: 0 rows). Cost weights for trails (GR=0.8,
PR=0.5, etc.) were correct in code, just had no edges to weight on.

We already parse PBFs for `osm_road_edges` (9.8 M segments) and
`dfci_edges` (51 k). Trails are also in the same PBF — they're
`type=route, route=hiking|bicycle|mtb` relations referenced by ways.
New CLI `app.cli.import_trails_pbf` does a two-pass osmium parse:
relations first (to identify wanted ways), then ways with locations
(to materialize geometries).

Local result on `france-south.osm.pbf` (1.2 GB merged):
```
Pass 1/2 — scanning relations: 508,682 in 3 s, 798 kept, 72,773 member ways
Pass 2/2 — materializing way geometries: 36,477,790 ways in 132 s, 65,258 matched
Stored 65,258 trail edges
  GR=27,258, GT=14,099, EV=12,514, PR=10,049, GRP=1,338
```

Make targets:
- `make import-trails-pbf` — local
- `make prod-import-trails-south` — prod via cloud-sql-proxy

Plus `OVERPASS_ENABLED=false` is now set on the Cloud Run service in
`.github/workflows/deploy-gcp.yml` to also kill the **runtime** Overpass
calls in `graph_tiles.py` and `ingest.py` (those were the slow culprits
in prod ingest + map tiles).

#### 6.6 Heatmap PMTiles `pass_count` was SUMmed → ~400k absurd numbers

Fixed in this PR. Was: `SUM(pass_count)` per OSM way → 331,981 RUNNING
displayed in popup. Now: `MAX(pass_count)` → ~286 (max plausible from
the underlying heat_edges table).

#### 6.7 Proposals modal re-opening on every long-distance extension

Fixed in this PR. Now only triggers on the FIRST click pair (or
explicit Alt+click). Subsequent extensions add direct segments —
matches user mental model and avoids the "0 km reset / cards
unclickable" rabbit hole.

#### 6.8 Proposals diversity weak (all 3 quasi-identical)

User reported the 3 proposals on Montpellier→Anduze look almost the
same on the map. Likely the cost-perturbation strategies (popularity /
direct / explorer) yield nearly the same path when the heatmap has
strong dominance on a single corridor. Not blocking — cosmetic.

### 🟢 Nice-to-have (won't block beta)

#### 7. Cold start mitigation
`min=0/max=1` chosen for cost ($10/mo target). Cold start ~5-10 s. ServerWakeupBanner already handles it, dismisses on `/readyz`.

#### 8. Mobile UX — view-only mode on phones (status: shipped 2026-05-10)

Confirmed on 2026-05-10 via Playwright at `viewport: 375x667`:

- Page used to overflow horizontally → fixed with `overflow-x: hidden` on `html, body` in `globals.css`
- Route-editor toggle + panel + proposal sheet hidden via `@media (max-width: 640px) { display: none !important }` — phones get **view-only** mode (browse the heatmap, switch basemap/layers, but cannot create / edit routes)
- Decision per user 2026-05-10: friends will use phones to *view* the heatmap on rides; route creation is laptop-first for the closed beta. Fixing the editor for mobile is a follow-up if/when we want phone-native route creation.

Tests under `e2e/tests/mobile-viewport-sanity.spec.ts`: 3 active
(load + no h-scroll + editor-toggle-hidden), 1 `@mobile-broken`
skipped (the "editor toggle should be visible+clickable" test —
intentionally still failing because we deliberately hid it).

#### 9. CDN cache warming
Currently CDN warms on first request. For known-popular tiles (Cévennes, Marseille, Lyon hills), pre-warm post-deploy.

#### 10. Cost-model dual maintenance
Routing cost weights live in two places — `frontend/public/routing-worker-wasm.js:20-72` AND `backend/app/services/routing_profiles.py`. Drift risk. Either generate one from the other or add a CI test that diffs them.

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Cloud SQL `db-f1-micro` chokes at 5+ concurrent users | M | H | scaling-100-users.md plan ready (pool tuning) |
| OSM data drift (PBF gets stale) | M | M | quarterly re-import (`make prod-import-osm-south`) |
| Valhalla tile coverage gap (Toulouse) | H | L | rebuild from france-latest.osm.pbf in progress |
| Strava rate limit hits during multi-user import | L | M | TEST_MODE stubs; production limits ~600/15 min |
| ADC drift (gcloud auth ↔ ADC) | H | H | `make gcp-precheck` enforces both before destructive ops |
| Backend `/routing` cascade exceeds 30 s + OOMs | H | H | needs cap fix (see missing step 3) |
| WASM Worker bundle (`routing-worker-wasm.js`) accidentally lost on a refactor | M | H | hand-maintained — always run motto test after diff |

## File map (what lives where)

| Concern | Path |
|---|---|
| Backend entrypoint | `backend/app/main.py` |
| Ingest pipeline | `backend/app/services/ingest.py` |
| Map-matcher (Valhalla) | `backend/app/services/map_matcher.py` |
| Routing API (cascade) | `backend/app/api/routing.py`, `backend/app/services/routing.py` |
| Routing cost profiles (Python copy) | `backend/app/services/routing_profiles.py` |
| Heatmap API | `backend/app/api/heatmap.py` |
| Internal handlers | `backend/app/api/internal_artefacts.py`, `backend/app/api/internal_ingest.py` |
| Cloud Tasks helper | `backend/app/services/cloud_tasks.py` |
| Frontend map page | `frontend/app/map/page.tsx` |
| Worker orchestrator (TS) | `frontend/lib/routing-worker-client.ts` |
| TS Worker stub | `frontend/lib/routing-worker.ts` (don't edit — esbuild target only) |
| Hand-maintained WASM Worker | `frontend/public/routing-worker-wasm.js` (1297 lines) |
| WASM binary | `frontend/public/ct_wasm_router_bg.wasm` |
| Rust crate | `wasm-router/` (Cargo.toml, src/, build.sh) |
| `.fgraph` prebuild | `wasm-router/src/bin/prebuild.rs` |
| Partition manifest | `frontend/public/partitions.json` |
| Routing cost profiles (WASM copy) | `frontend/public/routing-worker-wasm.js:20-72` |
| Terraform | `infra/terraform/main.tf` |
| GitHub deploy | `.github/workflows/deploy-gcp.yml` |
| OSM import target | `Makefile` (`make prod-import-osm-south`) |
| Strava import target | `Makefile` (`make prod-import-strava`) |
| Motto e2e test | `e2e/tests/motto-montpellier-anduze.spec.ts` |

## What "done" looks like for beta

- [ ] `make pytest` green (backend full suite)
- [ ] `make e2e` green (Playwright full suite)
- [ ] `npm run build` succeeds (static export)
- [ ] Local motto test passes (50 km route < 5 ms WASM CH)
- [ ] Local Valhalla smoke: 8/8 cities matched (after france-latest rebuild)
- [ ] Backend `/routing` cascade caps cleanly at 10 s (no 30 s+ runaway)
- [ ] Heatmap visual matches Komoot density baseline (Cévennes screenshot test)
- [ ] Closed-beta gate live (registration disabled or whitelisted)
- [ ] 3 pre-created friend accounts ready
- [ ] chemins-communs.fr loads in <3 s on cold start (ServerWakeupBanner OK)
- [ ] Mobile Chrome devtools walkthrough works
- [ ] Strava OAuth callback updated for prod
- [ ] Paul's ~1400 activities ingested to prod
- [ ] Valhalla deployed in prod (or explicit decision to ship without)
- [ ] Dedup investigation has a fix landed OR explicit "ship-as-is" decision

## Pointers to related docs

- `docs/vision.md` — why we exist
- `docs/migration-runbook.md` — release/deploy playbook
- `docs/heatmap-pipeline.md` — heatmap rebuild details
- `docs/heatmap-komoot-parity.md` — visual quality roadmap
- `docs/heatmap-map-matching-plan.md` — Valhalla 8-step plan
- `docs/valhalla-ops.md` — day-to-day Valhalla ops
- `docs/routing-architecture.md` — client routing details (verify dates — pre-WASM-only)
- `docs/routing-cost-model.md` — cost weights per sport
- `docs/scaling-100-users.md` — DB+infra scaling spec
- `docs/prod-ingestion-flow.md` — end-to-end ingest flow
- `docs/ops-osm-pbf-import.md` — OSM PBF re-import procedure
- `docs/privacy.md` — privacy/license boundary
