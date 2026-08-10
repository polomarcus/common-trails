---
stepsCompleted: ['step-01-init', 'step-02-context', 'step-03-starter', 'step-04-decisions', 'step-05-patterns', 'step-06-structure', 'step-07-validation', 'step-08-complete']
status: 'complete'
completedAt: '2026-03-14'
inputDocuments:
  - '_bmad-output/planning-artifacts/prd.md'
  - '_bmad-output/project-context.md'
  - '_bmad-output/brainstorming/brainstorming-session-2026-03-14-1400.md'
  - '_bmad-output/implementation-artifacts/tech-spec-fix-routing-quality-proposals-e2e.md'
  - '_bmad-output/implementation-artifacts/tech-spec-optimize-test-speed.md'
  - 'docs/vision.md'
  - 'docs/UI.md'
  - 'docs/ROUTING.md'
  - 'docs/routing-architecture.md'
  - 'docs/routing-cost-model.md'
  - 'docs/dataset.md'
  - 'docs/privacy.md'
  - 'docs/development.md'
workflowType: 'architecture'
project_name: 'common-trails'
user_name: 'Paulleclercq'
date: '2026-03-14'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements (31 FRs across 6 areas):**
- Route Planning (FR1-FR8): waypoint CRUD, heatmap+trail routing, signal stacking, per-segment recalc
- Sport & Heatmap (FR9-FR12): sport selection, heatmap filtering, grouped sport↔heatmap UI
- Map & Layers (FR13-FR16): layer toggles, simplified panel, elevation profile, color blindness
- Data Import/Export (FR17-FR21): Strava OAuth, GPX upload/export, URL sharing
- Routing Engine (FR22-FR26): cascade architecture, z14 tiles, tile sync, gradient filtering, <1s client
- Admin & Monitoring (FR27-FR31): Sentry monitoring, fallback rate, import jobs, E2E test suite, config tuning

**Non-Functional Requirements (18 NFRs):**
- Performance: <1s client routing (p95), <3s backend fallback, <2s viewport load, <2MB bundle, 100ms UI feedback
- Security: HTTPS, JWT 7d expiry, K≥2 anonymity, private data isolation, server-side OAuth tokens
- Accessibility: color blindness (deuteranopia/protanopia), keyboard navigation, 44×44px touch targets
- Integration: Strava OAuth <5s, graceful degradation on external failures, no single-dependency failure

**Scale & Complexity:**
- Primary domain: full-stack geospatial web application
- Complexity level: medium (spatial data + multi-source fusion + client-side graph algorithms)
- Target: 15 users / 3 months — architecture optimizes for quality, not scale
- Existing architectural components: ~25 (13 API routers, 5 services, 12 frontend pages/libs)

### Technical Constraints & Dependencies

**Brownfield constraints (already built):**
- PostgreSQL 17 + PostGIS 3.6 — spatial queries, pgRouting available as future upgrade path if Python Dijkstra becomes a bottleneck at scale
- FastAPI with 13 routers — stable API surface
- Next.js 16 static export — no SSR, CDN-served
- MapLibre GL 4.x — map rendering, layer lifecycle
- Client-side Dijkstra — pure TypeScript, z14 tile-based

**Code-level constraints:**
- Routing profiles duplicated in Python + TypeScript — must stay synchronized
- Backend startup loads data in background (~6GB) — server accepts requests before data is ready
- Graph cache keyed by `_edge_version` — auto-invalidated on ingestion mutations
- 5 independent LRU caches with different TTLs and eviction strategies
- `TEST_MODE` + `ROUTING_EXTERNAL` flags control test isolation
- Trace integrity (Crouzet): never alter imported GPX without user consent

**External dependencies:**
- Strava API (OAuth + activity import) — rate-limited, can go down
- Overpass API (OSM enrichment) — rate-limited, cached
- OpenFreeMap (basemap tiles) — free, no API key

### Cross-Cutting Concerns

1. **Routing quality** — spans client graph (TypeScript), backend routing (Python), cost model (both), data imports (DFCI/trails/heatmap), and E2E tests
2. **K-anonymity** — affects ingestion pipeline, heatmap endpoints, graph tile generation, and privacy boundaries
3. **Sport-specific behavior** — routing profiles, heatmap filtering, cost model weights, UI sport selector
4. **Trace integrity** — import pipeline, editor state management, `skipRecalcRef` pattern
5. **Performance** — client-side routing <1s, optimistic rendering, tile caching, LRU strategies
6. **Profile synchronization** — Python and TypeScript cost models must produce equivalent routing decisions

### Architecture Decision Records (Existing Decisions Audit)

**ADR-1: Client-side Dijkstra over z14 tiles**
- Decision: Route computation in browser, not server. Trade-off: complexity (tile loading, race conditions, duplicated cost model) vs. instant UX (<200ms). Correct call — <1s target impossible with server round-trips on every drag.

**ADR-2: 3-level cascade (community graph → personal traces → straight line)**
- Decision: client graph → personal traces → straight line. No BRouter/OSRM in MVP. The graph only contains heatmap + DFCI + trail edges (no full OSM road network). Straight line fallback is intentional: we prefer honesty over unverified routes. More trace contributions = fewer gaps.

**ADR-3: Duplicated cost models (Python + TypeScript)**
- Decision: Same cost formula in `routing.py` and `client-graph.ts`. Necessary evil for client-side routing. **Gap: no automated cross-language parity test.**

**ADR-4: Static export (no SSR)**
- Decision: Next.js `output: 'export'`, CDN-served. Fine for map app (behind login). SEO landing page can be static HTML at `/` outside Next.js build if needed.

**ADR-5: K-anonymity at read time**
- Decision: `heat_edges` stores all contributions; endpoints filter `user_count >= K`. Simpler and more flexible than write-time filtering. **Gap: no middleware or test enforcing K filter on all heatmap endpoints.**

**ADR-6: In-memory LRU caching (5 independent caches)**
- Decision: Zero infrastructure (no Redis). Right call at 15 users. Cache invalidation is ad-hoc — `_edge_version` for graph cache, TTL-only for tiles and heatmap. A Strava import won't show in tiles for up to 5 minutes.

**ADR-7: Background data loading (~6GB on startup)**
- Decision: Server accepts requests immediately, loads DFCI/trails/activities in background. **Gap: no readiness probe — `/healthz` checks server is up, not that data is ready. Cloud Run cold starts serve routes without DFCI/trail data.**

**ADR-8: PostGIS without pgRouting (active use)**
- Decision: Backend Dijkstra in Python, not pgRouting. Cost model too custom for pgRouting's weight functions. pgRouting available as upgrade path if Python Dijkstra becomes bottleneck at scale.

### Architectural Gaps Identified

| Gap | Severity | Recommendation |
|-----|----------|----------------|
| No cross-language cost model parity test | High | Automated test comparing Python vs TS routing on same inputs |
| Silent bad routing (client graph produces route but ignores heatmap) | High | Quality gate: if 0% heatmap edges in area with coverage, trigger server refinement |
| Tile race condition (routing before tiles load) | High | Fix tile preloading — this is both the #1 correctness AND performance bug |
| No readiness probe for background data loading | Medium | Add `/readyz` returning 503 until DFCI/trails loaded |
| Fallback chain lacks observability | Medium | Log/metric which cascade level fired per request |
| K-anonymity filter is endpoint-level only | Medium | Test that enumerates all `/heatmap/*` endpoints and verifies K filter applied |
| New endpoints can skip K filter | Medium | Automated test prevents future regressions |
| Tile/heatmap caches use TTL-only | Low | Acceptable at 15 users, document trade-off |
| pgRouting is vestigial | Low | Remove from deps/migrations when convenient |

### Failure Mode Analysis

**Top 3 failure modes to address in MVP:**

1. **Tile race condition** (high likelihood, high impact) — routing fires before tiles load, route ignores heatmap. Already in tech spec, must fix.
2. **Silent bad routing** (high likelihood, high impact) — client graph produces a route but a bad one (ignoring heatmap). Cascade fallback only fires on NO route, not on low-quality route. Needs quality gate.
3. **Cold start data gap** (high likelihood on Cloud Run) — routes computed during background loading window miss DFCI/trail data. Needs readiness probe + Cloud Scheduler warm-keeping.

**Component failure severity matrix:**
- High likelihood + high impact: tile race condition, silent bad routing, cold start gap
- Low likelihood + high impact: cost model drift (Python/TS diverge), K-filter missing on new endpoint
- High likelihood + low impact: stale tile cache after import
- Low likelihood + low impact: memory pressure, LRU eviction, A* iteration limit

### Performance Analysis

**Routing hot path budget (waypoint drag → route visible):**

| Phase | Time | Bottleneck? |
|-------|------|-------------|
| React re-render | 16ms | No |
| Tile cache lookup | 1ms | No |
| **Tile fetch (cache miss)** | **50-200ms** | **YES — dominant cost** |
| Dijkstra computation | 5-15ms | No |
| Route line render | 5ms | No |
| Server refinement | 1-3s | No (background) |

**Key insight:** If tiles are preloaded, routing takes ~37ms. The entire performance problem is tile loading. Fix tile preloading = fix both correctness AND speed.

**Infrastructure performance:**
- Cloud Run: `min_instances = 0` with Cloud Scheduler pinging `/healthz` every 55 min during active hours (7h-22h). Scale to zero at night. Cost: ~$2-3/month.
- Readiness probe `/readyz` returns 503 until background data load completes.
- Graph tile queries: 3 separate spatial queries (heat_edges, dfci_edges, trail_edges). Verify GiST spatial indexes exist. Consider unified `all_routable_edges` materialized view at scale.

**Top 3 performance actions:**

| Action | Impact | Effort | Priority |
|--------|--------|--------|----------|
| Fix tile preloading (viewport-based, aggressive) | Eliminates 200ms+ from hot path | Medium (tied to tile race fix) | 1 — MVP |
| Cloud Run min-instances=0 + Cloud Scheduler warm-keep | Eliminates cold start during active hours | Trivial (Terraform + scheduler) | 2 — Deploy |
| Readiness probe `/readyz` | Prevents serving incomplete data on cold start | Small (one endpoint) | 3 — Deploy |

### Architectural Principles (First Principles)

**Principle 1: One reliable graph > 5 fallback levels.**
The cascade complexity exists because no single layer is reliable enough. Fixing the client graph reduces architectural complexity — levels 2-5 become emergency-only.

**Principle 2: Heatmap + DFCI = the essential data pair.**
Data source hierarchy: heatmap (empirical, always trust) > DFCI (official, always rideable) > GR/PR/GT (conditional on gradient) > OSM (fallback only). Signal stacking bonus strongest when heatmap + DFCI converge.

**Principle 3: Draft-then-refine, not cascade fallback.**
Client graph produces a fast draft immediately displayed. Server sends quality-checked refinement. This is intentional progressive enhancement, not patching failures.

**Principle 4: Concentric circles of capability.**
- **Core (MVP):** Auth → Import → Heatmap → Route → Export → Share
- **Built but dormant:** Trips, forks, PRs, suggestions, tags, photos, route versioning
- **Planned:** Hover stats, proposed routes, trail cards, suggested loops

Feature flags (`FEATURES_MVP_ONLY=true`) hide dormant features in UI. No code deletion.

**Principle 5: Cost model = technical moat.**
The cost model (`cost = L × S × U × A × H × T × D`) is the most architecturally significant code. It must be: documented, versioned, parity-tested across Python and TypeScript, and tunable via config.

### Solo Dev Survival Rules

1. **One file at a time** — never have two parallel refactors touching `page.tsx`
2. **Feature flags over deletion** — hide dormant features, keep code intact
3. **Test the fix, not the refactor** — E2E tests validate routing quality, not code structure
4. **Ship routing quality first** — everything else follows
5. **Don't refactor page.tsx into hooks for MVP** — delays routing fixes, plan extraction post-MVP

## Starter Template Evaluation

### Primary Technology Domain

Full-stack geospatial web application — brownfield project with all technology choices already made and implemented.

### Existing Stack (No Starter Needed)

This is a brownfield project. All technology decisions are locked:

- **Frontend:** Next.js 16.1.6 (static export) + TypeScript 5.5.2 (strict) + MapLibre GL 4.3.2
- **Backend:** FastAPI + Python 3.13-slim + SQLAlchemy 2.0 + GeoAlchemy2
- **Database:** PostgreSQL 17 + PostGIS 3.6
- **Auth:** bcrypt + JWT (python-jose)
- **Testing:** pytest + Playwright 1.51.1 + tsx unit tests
- **Monitoring:** Sentry (React + FastAPI SDKs)
- **Infrastructure:** Docker Compose (dev) + GCP Terraform (prod)
- **CI/CD:** GitHub Actions + release-please

No technology evaluation or starter template selection needed. Architecture decisions in subsequent steps build on this established foundation.

## Core Architectural Decisions

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Feature flags | Env var `NEXT_PUBLIC_FEATURES_MVP_ONLY=true` | Zero deps, conditional render, flip to false when ready |
| Routing observability | Sentry custom tags (`routing.level`, `routing.method`) | Uses existing Sentry, gives dashboard + alerts for free |
| Cost model parity | Golden test file (JSON with edge inputs + expected costs) | Both Python and TS test against same file. Fast, precise, catches drift |
| K-anonymity safety | pytest test enumerating all `/heatmap/*` routes | Breaks CI if new endpoint skips K filter. Prevention over runtime defense |
| Readiness probe | `/readyz` endpoint, 503 until data loaded | Cloud Run startup probe. Clean separation from `/healthz` liveness |

**Deferred Decisions (Post-MVP):**

| Decision | Why Deferred |
|----------|-------------|
| Unified `all_routable_edges` materialized view | Not needed at 15 users, revisit at geographic expansion |
| Redis/external cache | In-memory LRU sufficient at current scale |
| Rate limiting | 15 users, no abuse risk |
| API versioning | Single consumer (frontend), no third-party API users |
| Offline/PWA | Explicitly out of V1 scope |
| pgRouting migration | Python Dijkstra adequate, available as upgrade path |

### Data Architecture

- **Database:** PostgreSQL 17 + PostGIS 3.6 (locked)
- **ORM:** SQLAlchemy 2.0 + GeoAlchemy2 + Alembic migrations (locked)
- **Caching:** 5 independent in-memory LRU caches, no external cache (Redis deferred)
- **Cache invalidation:** `_edge_version` counter for graph cache, TTL-only for tile/heatmap caches
- **Data source hierarchy:** heatmap (empirical) > DFCI (official) > GR/PR/GT (conditional) > OSM (fallback)

### Authentication & Security

- **Auth:** JWT (HS256, 7d expiry) + bcrypt password hashing (locked)
- **OAuth:** Strava + Garmin via server-side token storage (locked)
- **Privacy:** K-anonymity at read time (K≥2 prod, K=1 dev) + pytest endpoint enumeration test
- **Data isolation:** Private (activities, activity_cells) vs Common/ODbL (heat_cells, heat_edges, routes)

### API & Communication Patterns

- **API style:** REST (FastAPI), 13 routers (locked)
- **Error handling:** HTTPException with specific status codes, frontend try/catch + console.warn
- **Observability:** Sentry custom tags on routing requests (routing.level, routing.method, routing.sport)
- **External calls:** Retry with backoff for Strava API; routing is fully client-side (no external routing APIs)

### Frontend Architecture

- **Rendering:** Static export, all client-side (locked)
- **State:** `useRef` for mutable non-rendering state (map, waypoints, routing counter), `useState` for UI state
- **Routing engine:** Client-side Dijkstra over full preloaded graph, 3-level cascade (graph → personal → straight line)
- **Feature flags:** `NEXT_PUBLIC_FEATURES_MVP_ONLY=true` hides trips/forks/PRs/tags
- **Map page:** Single large component for MVP, hook extraction planned post-MVP

### Infrastructure & Deployment

- **Dev:** Docker Compose (PostgreSQL + FastAPI + Frontend), ports 5487/8787/3787
- **Prod:** Cloud Run (backend) + Cloud SQL + Cloud Storage + Cloud CDN (frontend)
- **Cold start mitigation:** Cloud Scheduler pings `/healthz` every 55 min (7h-22h), scale to zero at night
- **Readiness:** `/readyz` endpoint returns 503 until background data loading complete
- **CI/CD:** GitHub Actions (ruff → docker build → pytest → npm build → Playwright)

### Decision Impact Analysis

**Implementation sequence:**
1. `/readyz` endpoint (deploy prerequisite)
2. Feature flags env var (UX simplification)
3. Sentry routing tags (observability before fixing routing)
4. Golden test file for cost model parity (safety net before cost model changes)
5. K-anonymity pytest enumeration test (safety net before any new endpoints)

**Cross-component dependencies:**
- Golden test file → needed before any cost model tuning (FR31)
- Sentry routing tags → needed before measuring fallback rate (FR28)
- `/readyz` → needed before Cloud Run deployment
- Feature flags → independent, can ship anytime

## Implementation Patterns & Consistency Rules

### Established Patterns (from codebase)

**Naming Conventions:**
- DB tables: `snake_case` (`heat_edges`, `trail_edges`, `activity_cells`)
- DB models: `PascalCase` class, `snake_case` `__tablename__`
- Python files: `snake_case.py`
- TypeScript components: `PascalCase.tsx`
- TypeScript utils/hooks: `kebab-case.ts`
- API endpoints: `/snake-case` paths with FastAPI router prefixes
- Constants: `UPPER_CASE` in both languages

**Project Structure:**
- Backend: `app/api/` (routes), `app/services/` (logic), `app/db/` (models), `app/cli/` (CLI tools)
- Frontend: `app/` (pages), `components/` (UI), `lib/` (utils + hooks)
- Tests: `backend/tests/test_*.py`, `e2e/tests/*.spec.ts`, `frontend/lib/__tests__/*.test.ts`

**API Format:**
- Responses: direct Pydantic models, no wrapper
- Errors: `HTTPException(status_code=xxx, detail="message")`
- JSON fields: `snake_case`
- Dates: ISO strings
- GeoJSON: standard RFC 7946

**State Management:**
- `useRef` for mutable non-rendering state (map, waypoints, routing counter)
- `useState` for UI-driven state (routeLine, routeMethod, panels)
- `waypointsRef.current` = source of truth, `setWaypoints` for rendering
- `routeSegmentsRef.current` for per-segment coord storage

### Critical Consistency Rules

**1. Coordinate Order Convention**
- ALWAYS `[lon, lat]` (GeoJSON standard), never `[lat, lon]`
- 3D coords: `[lon, lat, elevation]`
- Backend loops: `point[0]/point[1]`, NEVER `for lon, lat in coords` (3D breaks tuple unpacking)

**2. Routing Call Site Rule**
- ALWAYS call `fetchSmartSegmentWithClientRouting()` from UI code
- NEVER call `fetchSmartSegment()` directly (server-only, internal)
- NEVER recalculate entire route when only one segment changed — splice `routeSegmentsRef.current`

**3. Trace Integrity Rule (Crouzet)**
- NEVER alter imported GPX without explicit user consent
- `skipRecalcRef` prevents auto-rerouting when loading existing traces
- Smart routing only on explicit user action (sport change, new waypoint)

**4. Test Isolation Rule**
- `TEST_MODE=true` stubs ALL external calls
- `ROUTING_EXTERNAL=false` — no outbound HTTP in tests
- Backend tests: separate DB (`common_trails_test`)
- E2E: uses main Docker Compose stack
- DFCI cache is read-only, persists across tests (no clearing needed)

**5. MapLibre Layer Lifecycle**
- Map instance in `useRef`, never in state
- Layers managed in `useEffect` cleanup — strict lifecycle
- `addSource`/`addLayer` must clean up in `useEffect` return

**6. Import Path Convention**
- TypeScript: `@/*` path alias, NEVER relative `../../`
- Python: `from app.xxx import yyy`

**7. Error Handling Split**
- Backend: `HTTPException` with specific codes
- Frontend non-critical: try/catch + `console.warn`
- Frontend routing: silent fallback in cascade

**8. Cost Model Synchronization Rule**
- Any change to cost formula in `routing.py` MUST be mirrored in `client-graph.ts`
- Golden test file validates parity
- Cost model versioned in config

### Enforcement Guidelines

**All AI Agents MUST:**
1. Read `project-context.md` before implementing any code
2. Use `[lon, lat]` coordinate order — never `[lat, lon]`
3. Call `fetchSmartSegmentWithClientRouting()` — never `fetchSmartSegment()` directly
4. Never alter imported GPX traces without user consent
5. Mirror cost model changes in both Python and TypeScript
6. Use `@/*` imports in TypeScript — never relative paths
7. Clean up MapLibre sources/layers in useEffect return

### Anti-Patterns

- `for lon, lat in coords` → crashes on 3D coords, use `point[0]/point[1]`
- `fetchSmartSegment()` from UI → bypasses client-side routing
- `as any` → use proper typing or `@ts-ignore`
- `@ts-expect-error` → use `@ts-ignore` (former errors if type error fixed)
- `as const` arrays → not assignable to `LayerSpecification[]` in maplibre-gl 4.x
- `attributionControl: true` → use `{}` in maplibre-gl 4.x
- `next start` → fails with `output: 'export'`, serve `out/` instead

## Project Structure & Boundaries

### Complete Project Directory Structure

```
common-trails/
├── docker-compose.yml                    # Dev stack: PostgreSQL + Backend + Frontend
├── Makefile                              # Build/test/deploy shortcuts
├── CHANGELOG.md                          # release-please auto-generated
├── .github/
│   └── workflows/
│       ├── ci.yml                        # ruff → docker build → pytest → npm build → Playwright
│       └── deploy-gcp.yml               # Cloud Run + Cloud Storage + CDN invalidation
├── infra/
│   └── terraform/                        # GCP infrastructure (Cloud Run, Cloud SQL, Storage, CDN)
│       ├── main.tf
│       ├── variables.tf
│       └── outputs.tf
├── scripts/
│   ├── seed_heatmap.py                   # Populate heat_edges from activities
│   ├── seed_routes.py                    # Seed demo routes
│   ├── osm_dfci_import.py               # OSM DFCI tagging tool
│   └── extract_dfci_ign.py              # IGN DFCI data extraction
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/                     # 16 migrations (0001–0016)
│   ├── fixtures/                         # GPX test files
│   ├── app/
│   │   ├── main.py                       # FastAPI app, lifespan (seed + background loaders)
│   │   ├── config.py                     # Env vars, feature flags
│   │   ├── logging_config.py
│   │   ├── seed_activities.py            # Demo activity seeding
│   │   ├── seed_demo_data.py             # Demo data orchestrator
│   │   ├── api/                          # 13 routers
│   │   │   ├── auth.py                   # JWT login, register, get_current_user
│   │   │   ├── routing.py                # Smart routing endpoint (/routing)
│   │   │   ├── graph_tiles.py            # z14 tile endpoint (/routing/graph/{sport}/{z}/{x}/{y}.json)
│   │   │   ├── heatmap.py                # Heatmap cells + edges (/heatmap/*)
│   │   │   ├── me_activities.py          # User activities CRUD (/me/activities)
│   │   │   ├── me_photos.py              # Activity photos (/me/photos)
│   │   │   ├── me_stats.py              # User statistics (/me/stats)
│   │   │   ├── routes.py                 # Routes CRUD + surface stats (/routes/*)
│   │   │   ├── gpx_upload.py             # GPX/ZIP file upload
│   │   │   ├── imports.py                # Import jobs management
│   │   │   ├── integrations_strava.py    # Strava OAuth + import
│   │   │   ├── integrations_garmin.py    # Garmin OAuth + import
│   │   │   ├── trips.py                  # Trips/stages CRUD
│   │   │   ├── tags_export.py            # Tags export
│   │   │   ├── geocode.py                # Geocoding proxy
│   │   │   ├── health.py                 # /healthz + future /readyz
│   │   │   └── schemas.py                # Shared Pydantic models
│   │   ├── services/                     # Business logic
│   │   │   ├── routing.py                # Backend Dijkstra + cost model (Python)
│   │   │   ├── routing_profiles.py       # Sport-specific routing profiles
│   │   │   ├── routing_types.py          # Routing type definitions
│   │   │   ├── ingest.py                 # Activity ingestion + heat_edges + personal edges
│   │   │   ├── gpx.py                    # GPX parsing (verbatim coord storage)
│   │   │   ├── geo.py                    # Geometry utilities
│   │   │   ├── dem.py                    # DEM elevation service
│   │   │   ├── osm_enrich.py             # OSM surface/tag enrichment
│   │   │   ├── overpass.py               # Overpass API client
│   │   │   ├── surface_classification.py # Crouzet surface classification
│   │   │   └── strava_client.py          # Strava API client
│   │   ├── db/
│   │   │   ├── models.py                 # SQLAlchemy + GeoAlchemy2 models (30+ tables)
│   │   │   ├── session.py                # DB session factory
│   │   │   └── seed_minigraph.py         # Minigraph seeding
│   │   ├── cli/                          # CLI tools
│   │   │   ├── import_dfci.py            # DFCI OSM import
│   │   │   ├── import_dfci_herault.py    # DFCI Hérault specific
│   │   │   ├── import_dfci_ign.py        # DFCI IGN data
│   │   │   ├── import_trails.py          # GR/PR/GT trail import
│   │   │   └── surfaces_audit.py         # Surface data audit
│   │   └── jobs/
│   │       └── import_strava.py          # Strava background import job
│   └── tests/                            # pytest integration tests
│       ├── conftest.py                   # Fixtures, separate DB (common_trails_test)
│       ├── test_auth.py
│       ├── test_routing.py
│       ├── test_routing_integration.py
│       ├── test_graph_tiles.py
│       ├── test_health.py
│       ├── test_me_activities.py
│       ├── test_routes.py
│       ├── test_imports.py
│       ├── test_strava.py
│       ├── test_dfci.py
│       ├── test_dfci_herault.py
│       ├── test_osm_dfci_import.py
│       ├── test_osm_enrich.py
│       ├── test_overpass.py
│       ├── test_dem.py
│       ├── test_ingest_direction.py
│       ├── test_isolation.py
│       ├── test_performance.py
│       ├── test_surface_classification.py
│       └── test_trips.py
│
├── frontend/
│   ├── package.json
│   ├── next.config.ts
│   ├── tsconfig.json
│   ├── serve.py                          # SPA-aware static server for out/
│   ├── app/
│   │   ├── layout.tsx                    # Root layout + SentryProvider
│   │   ├── page.tsx                      # Landing / redirect
│   │   ├── map/page.tsx                  # Main map page (route editor, layers, sidebar)
│   │   ├── activities/page.tsx           # Activity list
│   │   ├── discover/page.tsx             # Public discovery
│   │   ├── heatmap/page.tsx              # Heatmap explorer
│   │   ├── routes/page.tsx               # Route viewer
│   │   ├── trips/page.tsx                # Trip planner
│   │   ├── stats/page.tsx                # Global stats
│   │   ├── me/stats/page.tsx             # Personal stats
│   │   ├── strava/page.tsx               # Strava OAuth callback
│   │   └── methode/page.tsx              # Methodology page
│   ├── components/
│   │   ├── Map.tsx                       # MapLibre wrapper
│   │   ├── TopNav.tsx                    # Navigation bar
│   │   ├── ElevationProfile.tsx          # SVG elevation with slope colors
│   │   ├── ElevationProfilePro.tsx       # Enhanced elevation profile
│   │   ├── ElevationChart.tsx            # Chart-based elevation
│   │   ├── ProfileSelector.tsx           # Sport profile selector
│   │   ├── PlaceSearch.tsx               # Geocoding search
│   │   ├── TraceMap.tsx                  # Activity trace renderer
│   │   ├── TripMap.tsx                   # Trip map renderer
│   │   ├── ErrorBoundary.tsx             # React error boundary
│   │   ├── SentryProvider.tsx            # Sentry initialization
│   │   ├── ServerWakeupBanner.tsx        # Cold start banner
│   │   └── route/
│   │       ├── RoutePRsSection.tsx       # Route PRs (dormant)
│   │       └── RouteTagsSection.tsx      # Route tags (dormant)
│   ├── lib/
│   │   ├── client-graph.ts              # Client-side Dijkstra + cost model (TypeScript)
│   │   ├── tile-cache.ts                # z14 tile cache management
│   │   ├── routing-state.ts             # Route segment state management
│   │   ├── routing-metrics.ts           # Routing performance metrics
│   │   ├── routing-style.ts             # Route line styling
│   │   ├── snap-to-road.ts              # Road snapping utilities
│   │   ├── api-client.ts                # apiFetch() — JWT auto-inject
│   │   ├── use-auth.ts                  # useAuth() hook
│   │   ├── auth.ts                      # Auth utilities
│   │   ├── constants.ts                 # App constants
│   │   ├── geo.ts                       # Geometry utilities
│   │   ├── elevation.ts                 # Elevation utilities
│   │   ├── elevation-profile.ts         # Elevation profile data processing
│   │   ├── format.ts                    # Display formatting
│   │   ├── explain.ts                   # Route explanation
│   │   └── profile.ts                   # Sport profile definitions
│   ├── lib/__tests__/                   # Frontend unit tests (run with npx tsx)
│   │   ├── client-graph.test.ts
│   │   ├── routing-metrics.test.ts
│   │   ├── routing-state.test.ts
│   │   └── tile-cache.test.ts
│   └── types/
│       └── css.d.ts
│
├── e2e/
│   ├── playwright.config.ts             # 2 projects: default + heatmap-fixture
│   ├── fixtures/                        # E2E test data
│   └── tests/
│       ├── helpers.ts                   # Shared test utilities
│       ├── core.spec.ts                 # Auth, navigation, basic flows
│       ├── client-routing.spec.ts       # Client-side routing validation
│       ├── routing-coherence.spec.ts    # Cross-method routing coherence
│       ├── routing-heatmap-fixture.spec.ts  # Heatmap routing with fixtures
│       ├── routing-quality-montpellier.spec.ts  # Real-world routing quality
│       └── trips.spec.ts               # Trip management
│
└── docs/
    ├── ROUTING.md                       # Routing philosophy
    ├── routing-architecture.md          # Cascade architecture
    ├── routing-cost-model.md            # Cost model formula
    ├── UI.md                            # Map-first interface principles
    ├── dataset.md                       # Data sources (OSM, IGN, heatmap)
    ├── privacy.md                       # K-anonymity, data boundaries
    ├── development.md                   # Development principles
    └── vision.md                        # Product vision
```

### Architectural Boundaries

**API Boundaries:**
- All API endpoints behind `/` prefix, registered in `main.py` via `include_router()`
- Auth boundary: `get_current_user` (required) vs `get_current_user_optional` (optional)
- Privacy boundary: heatmap endpoints enforce K-anonymity filter (`user_count >= K`)
- Private endpoints: `/me/*` (activities, stats, photos) — require auth
- Public endpoints: `/routes?visibility=public`, `/heatmap/*` — no auth required
- Internal-only: `fetchSmartSegment()` — never called from UI, only from `fetchSmartSegmentWithClientRouting()`

**Component Boundaries:**
- `map/page.tsx` is the single orchestration point — all routing, layers, sidebar, editor live here
- Components are presentation-only (no direct API calls) except `Map.tsx` (tile loading)
- `lib/` modules are pure logic — no React dependencies except `use-auth.ts`
- Dormant components (`route/RoutePRsSection.tsx`, `RouteTagsSection.tsx`) hidden by feature flag

**Data Boundaries:**
- Private data: `activities`, `activity_cells`, `activity_photos`, `integration_accounts`, `import_jobs`
- Common data (ODbL): `heat_cells`, `heat_edges`, `routes`, `route_versions`, `route_forks`
- Read-only imported data: `dfci_edges`, `trail_edges` (loaded from OSM/IGN at startup)
- Graph tiles: generated on-demand from `heat_edges` + `dfci_edges` + `trail_edges`, cached in LRU

### Requirements to Structure Mapping

**FR1-FR8 (Route Planning):**
- `frontend/app/map/page.tsx` — waypoint CRUD, route editor UI
- `frontend/lib/client-graph.ts` — client-side Dijkstra routing
- `frontend/lib/routing-state.ts` — per-segment state management
- `backend/app/api/routing.py` — server-side smart routing endpoint
- `backend/app/services/routing.py` — backend Dijkstra + cost model

**FR9-FR12 (Sport & Heatmap):**
- `frontend/app/map/page.tsx` — sport selector, heatmap toggle, layer panel
- `frontend/lib/profile.ts` — sport profile definitions
- `backend/app/api/heatmap.py` — heatmap data endpoints
- `backend/app/services/routing_profiles.py` — sport-specific routing weights

**FR13-FR16 (Map & Layers):**
- `frontend/app/map/page.tsx` — layer toggles, simplified panel
- `frontend/components/ElevationProfile.tsx` — elevation display
- `frontend/lib/routing-style.ts` — color-blind safe route styling

**FR17-FR21 (Data Import/Export):**
- `backend/app/api/gpx_upload.py` — GPX/ZIP upload
- `backend/app/api/integrations_strava.py` — Strava OAuth
- `backend/app/api/integrations_garmin.py` — Garmin OAuth
- `backend/app/services/gpx.py` — GPX parsing (verbatim)
- `backend/app/services/ingest.py` — activity ingestion pipeline

**FR22-FR26 (Routing Engine):**
- `frontend/lib/client-graph.ts` — client-side Dijkstra, cost model (TS)
- `frontend/lib/tile-cache.ts` — z14 tile caching
- `backend/app/api/graph_tiles.py` — tile generation endpoint
- `backend/app/services/routing.py` — cost model (Python), backend Dijkstra
- `backend/app/cli/import_dfci.py` — DFCI edge loading
- `backend/app/cli/import_trails.py` — trail edge loading

**FR27-FR31 (Admin & Monitoring):**
- `frontend/components/SentryProvider.tsx` — frontend error tracking
- `backend/app/api/health.py` — `/healthz` + future `/readyz`
- `backend/app/services/routing.py` — Sentry routing tags
- `e2e/tests/*.spec.ts` — E2E test suite

### Cross-Cutting Concerns Mapping

**Cost Model Synchronization:**
- `backend/app/services/routing.py` — Python implementation
- `frontend/lib/client-graph.ts` — TypeScript implementation
- Future: `tests/golden-cost-model.json` — parity test file

**K-Anonymity Enforcement:**
- `backend/app/api/heatmap.py` — endpoint-level filter
- `backend/app/api/graph_tiles.py` — tile-level filter
- `backend/app/services/ingest.py` — `get_personal_edges()` bypasses K for own data
- Future: `backend/tests/test_k_anonymity.py` — endpoint enumeration test

**Trace Integrity:**
- `backend/app/services/gpx.py` — verbatim coordinate storage
- `backend/app/services/ingest.py` — `geometry_geojson` stored as-is
- `frontend/app/map/page.tsx` — `skipRecalcRef` pattern

### Integration Points

**External Services:**
- Strava API → `backend/app/services/strava_client.py` → `backend/app/api/integrations_strava.py`
- Overpass → `backend/app/services/overpass.py` → `backend/app/services/osm_enrich.py`
- OpenFreeMap → `frontend/components/Map.tsx` (basemap tiles)
- Sentry → `frontend/components/SentryProvider.tsx` + `backend/app/main.py`

**Internal Data Flow:**
1. **Import:** Strava/GPX → `gpx.py` → `ingest.py` → DB (activities + heat_edges)
2. **Tile generation:** DB (heat_edges + dfci_edges + trail_edges) → `graph_tiles.py` → JSON tile → LRU cache
3. **Client routing:** Tiles → `tile-cache.ts` → `client-graph.ts` (Dijkstra) → route line
4. **Server refinement:** `/routing` → `routing.py` (Dijkstra) → quality-checked route
5. **Display:** Route coords → `map/page.tsx` → MapLibre layers

## Architecture Validation Results

### Coherence Validation

**Decision Compatibility:** All decisions are internally consistent. Client-side Dijkstra (ADR-1) + z14 tiles + tile-cache.ts form a coherent client routing stack. Static export (ADR-4) + CDN + Cloud Run backend cleanly separates concerns. K-anonymity at read time (ADR-5) + endpoint enumeration test provides defense in depth. No contradictions between any pair of decisions.

**Pattern Consistency:** Naming conventions uniform across all 13 routers and 30+ models. Coordinate convention (`[lon, lat]` / `point[0]/point[1]`) consistently documented. Routing call site rule, import path conventions, and error handling split all consistently applied.

**Structure Alignment:** Project structure matches all pattern definitions exactly. Test organization mirrors source. Integration boundaries align with directory structure.

### Requirements Coverage

| FR Range | Area | Architectural Support | Status |
|----------|------|-----------------------|--------|
| FR1-FR8 | Route Planning | page.tsx + client-graph.ts + routing-state.ts + routing.py | Covered |
| FR9-FR12 | Sport & Heatmap | profile.ts + heatmap.py + routing_profiles.py | Covered |
| FR13-FR16 | Map & Layers | page.tsx + ElevationProfile + routing-style.ts | Covered |
| FR17-FR21 | Data Import/Export | gpx_upload.py + integrations_*.py + gpx.py + ingest.py | Covered |
| FR22-FR26 | Routing Engine | client-graph.ts + tile-cache.ts + graph_tiles.py + routing.py | Covered |
| FR27-FR31 | Admin & Monitoring | SentryProvider + health.py + e2e tests | Covered |

**NFR Coverage:** Performance (<1s client routing via tile preloading), Security (JWT + K-anonymity + data isolation), Accessibility (color blindness in routing-style.ts), Integration (graceful degradation via cascade) — all architecturally addressed.

### Implementation Readiness

- **Decision Completeness:** 8 existing ADRs + 5 new decisions = all critical decisions documented with versions, rationale, and trade-offs
- **Structure Completeness:** Full directory tree with every source file. FR→file mapping explicit for all 31 FRs
- **Pattern Completeness:** 8 consistency rules covering all conflict-prone areas + anti-patterns list

### Gap Analysis

No critical gaps remaining. All gaps from context analysis have corresponding decisions:

| Original Gap | Resolution |
|-------------|-----------|
| Cross-language cost model parity | Golden test file (Decision 3) |
| Silent bad routing | Quality gate in failure modes |
| Tile race condition | Tile preloading fix (Performance Action 1) |
| No readiness probe | `/readyz` endpoint (Decision 5) |
| Fallback chain observability | Sentry routing tags (Decision 2) |
| K-anonymity regression risk | pytest endpoint enumeration (Decision 4) |

### Architecture Completeness Checklist

**Requirements Analysis**
- [x] Project context thoroughly analyzed (brownfield codebase, 40+ tool calls)
- [x] Scale and complexity assessed (15 users / 3 months)
- [x] Technical constraints identified (8 ADRs from existing code)
- [x] Cross-cutting concerns mapped (6 concerns with file-level mapping)

**Architectural Decisions**
- [x] Critical decisions documented with versions (8 existing + 5 new)
- [x] Technology stack fully specified (all versions locked)
- [x] Integration patterns defined (5 external services + internal data flow)
- [x] Performance considerations addressed (tile preloading, Cloud Run warm-keep, readiness probe)

**Implementation Patterns**
- [x] Naming conventions established (Python/TS/DB/API)
- [x] Structure patterns defined (backend/frontend/test organization)
- [x] Communication patterns specified (API/component/data boundaries)
- [x] Process patterns documented (8 consistency rules, anti-patterns)

**Project Structure**
- [x] Complete directory structure defined (every source file listed)
- [x] Component boundaries established (API/component/data)
- [x] Integration points mapped (external services + internal data flow)
- [x] Requirements to structure mapping complete (31 FRs → specific files)

### Architecture Readiness Assessment

**Overall Status:** READY FOR IMPLEMENTATION

**Confidence Level:** High — architecture grounded in actual brownfield code, not theoretical design.

**Key Strengths:**
- Every decision validated against existing working code
- Cost model documented as technical moat with parity testing strategy
- Solo-dev survival rules prevent over-engineering
- Feature flags preserve dormant features without code deletion
- Concentric capability model provides clear MVP boundary

**Implementation Handoff — First Priorities:**
1. `/readyz` endpoint (deploy prerequisite)
2. Feature flags env var (UX simplification)
3. Sentry routing tags (observability before fixing)
4. Golden test file for cost model parity
5. K-anonymity pytest enumeration test
6. Tile preloading fix (routing quality + performance)
