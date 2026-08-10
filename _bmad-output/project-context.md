---
project_name: 'common-trails'
user_name: 'Paulleclercq'
date: '2026-03-14'
sections_completed: ['technology_stack', 'language_rules', 'framework_rules', 'testing_rules', 'code_quality', 'workflow_rules', 'critical_rules']
status: 'complete'
rule_count: 45
optimized_for_llm: true
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

---

## Technology Stack & Versions

### Frontend
- Next.js 16.1.6 — `output: 'export'` (static only, no SSR, no `next start`)
- React 18.3.1 + TypeScript 5.5.2 (strict mode)
- MapLibre GL 4.3.2
- @sentry/react ^8.0.0
- @playwright/test 1.51.1 (peerOptional of next@16.1.6)

### Backend
- FastAPI >=0.111.0 + Uvicorn
- Python 3.13-slim
- SQLAlchemy 2.0+ + GeoAlchemy2 + Alembic
- bcrypt direct (no passlib — incompatible with Python 3.13)
- python-jose[cryptography] for JWT
- httpx, shapely, gpxpy, pyproj
- sentry-sdk[fastapi]
- ruff for linting

### Database
- PostgreSQL 17 + PostGIS 17-3.6-alpine
- `platform: linux/amd64` required on arm64 Mac hosts

### Version Constraints
- bcrypt direct — passlib broken on Python 3.13
- @playwright/test 1.51.1 — required by next@16.1.6
- `attributionControl: true` invalid in maplibre-gl 4.x — use `{}` or omit
- `@ts-expect-error` errors if type error no longer exists — use `@ts-ignore` instead
- Next.js 16 auto-updates tsconfig.json (jsx: preserve→react-jsx) and generates next-env.d.ts
- `as const` makes layers readonly → not assignable to `LayerSpecification[]` in maplibre-gl 4.x

### Dev Ports (custom, to avoid conflicts)
- Backend API: **8787**
- Frontend: **3787**
- PostgreSQL (host-exposed): **5487** (internal Docker stays 5432)

## Critical Implementation Rules

### Language-Specific Rules

**TypeScript:**
- Strict mode enabled — no implicit any
- Path aliases: `@/*` → project root. Always use `import { x } from '@/lib/foo'`, never relative `../../`
- `'use client'` directive required on client components (Next.js App Router)
- Coords are `[number, number]` (lon, lat) or `number[][]` for 3D `[lon, lat, elevation]`
- Prefer `@ts-ignore` over `@ts-expect-error` — the latter errors if the type error is fixed
- No `as any` — use proper typing or `@ts-ignore` for maplibre type mismatches

**Python:**
- Target Python 3.13 — modern syntax OK (match/case, type aliases)
- ruff: line-length 120, select `["E", "F", "W", "I", "UP", "B", "SIM"]`
- Ignored rules: `E501` (long lines), `E402` (deferred router imports), `B008` (`Depends()` defaults)
- isort: `known-first-party = ["app"]`
- GeoJSON coord loops: use `point[0]/point[1]`, NEVER `for lon, lat in coords` (3D coords break tuple unpacking)
- Pydantic v2 models for all request/response validation
- Dependency injection via `Depends()` — never instantiate DB sessions manually

**Error Handling:**
- Backend: `HTTPException` with specific status codes
- Frontend: try/catch + `console.warn` for non-critical, silent fallback in routing cascade
- FastAPI `RedirectResponse` returns 307 (not 302) — E2E tests must account for this

### Framework-Specific Rules

**Next.js / React:**
- Static export only (`output: 'export'`) — no API routes, no SSR, no `getServerSideProps`, no `next start`
- Serve `out/` with `python3 serve.py` (SPA-aware HTTP server)
- All pages are client components (`'use client'`)
- GCS deployment uses env-driven `basePath: '/common-trails-frontend'`
- `useAuth()` hook for token/userId/logout — centralized in `@/lib/use-auth`
- `apiFetch()` in `@/lib/api-client` — auto-injects JWT, handles base URL

**FastAPI Backend:**
- Router pattern: `router = APIRouter(prefix="/xxx", tags=["xxx"])` per module, registered in `main.py`
- Auth deps: `get_current_user` (required) and `get_current_user_optional` (optional) via `Depends()`
- DB: `db: Session = Depends(get_db)` — never create `SessionLocal()` directly
- Lifespan seeds default user (admin@admin / admin) and demo data on startup

**MapLibre GL:**
- Map instance in `useRef`, never in state
- Layers managed in `useEffect` cleanup — strict lifecycle
- `attributionControl` must be `{}` not `true` in v4.x
- `as const` arrays need spread or cast for `LayerSpecification[]`

**Routing Engine Architecture (frontend):**
- Cascade: client graph (< 100ms) → personal traces → straight line
- Full graph preloaded on route mode entry: `GET /routing/graph/{sport}/full.json` (100-500KB gzip)
- No BRouter/OSRM in MVP — graph only contains heatmap + DFCI + trail edges (no full OSM road network)
- Dijkstra in Web Worker with sport-specific cost model (slope × surface × heatmap × trail × direction)
- 3 proposals via successive Dijkstra with corridor penalties (×5.0 on-path, decay with distance)
- VIP snap bias (4.0): DFCI/trail edges appear 5× closer during snap-to-road

**State Management:**
- `useRef` for mutable state not triggering re-renders (map, waypoints, routing in-flight counter)
- `useState` for UI-driven state (routeLine, routeMethod, panels)
- `waypointsRef.current` = source of truth; `setWaypoints` updates React state for rendering
- `routeSegmentsRef.current` stores per-segment coords for splicing on waypoint changes

### Testing Rules

**Organization:**
- Backend: `backend/tests/test_*.py` — pytest, `asyncio_mode = "auto"`
- E2E: `e2e/tests/*.spec.ts` — Playwright 1.51.1
- Frontend unit: `frontend/lib/__tests__/*.test.ts` — run with `npx tsx`
- Fixtures: `backend/fixtures/` (GPX), `e2e/fixtures/`

**Isolation (critical):**
- `TEST_MODE=true` — stubs all external calls (Strava, OSRM, BRouter, DEM)
- `ROUTING_EXTERNAL=false` — no outbound HTTP in tests
- Backend tests: separate DB (`common_trails_test`) to avoid E2E conflicts
- E2E: uses main Docker Compose stack (shared DB `common_trails`)
- DFCI cache must be cleared between API tests to prevent stale hits
- `NEXT_PUBLIC_MAP_OFFLINE=true` — no tile fetching in Playwright

**Playwright Config:**
- 2 projects: `default` + `heatmap-fixture` (depends on default)
- `parallel: false`, `workers: 2`, timeout 30s
- Retries: 2 in CI, 0 locally

**Boundaries:**
- pytest = integration tests (real DB via TestClient + lifespan)
- `__tests__/*.test.ts` = pure unit tests (no DOM, no network)
- Playwright = full-stack E2E (frontend → backend → DB)

**Known Test Gaps:**
- No test for proposal route preservation (selecting proposal recalculates everything)

### Code Quality & Style Rules

**Linting:**
- Backend: `ruff check` + `ruff format` (via `make lint` / `make format`)
- Frontend: Next.js built-in lint (`npm run lint`) — no custom eslint, no Prettier

**File & Folder Structure:**
- Backend: `app/api/` (routes), `app/services/` (business logic), `app/db/` (models + session), `app/cli/` (CLI tools)
- Frontend: `app/` (pages), `components/` (reusable UI), `lib/` (utilities + hooks)
- Tests mirror source: `backend/tests/`, `e2e/tests/`, `frontend/lib/__tests__/`

**Naming Conventions:**
- Backend files: `snake_case.py`
- Frontend components: `PascalCase.tsx`
- Frontend utils/hooks: `kebab-case.ts`
- Pages: `app/{route}/page.tsx`
- Tests: `test_*.py` (pytest), `*.spec.ts` (Playwright), `*.test.ts` (unit)
- Constants: `UPPER_CASE` in both Python and TypeScript
- DB models: PascalCase class, `snake_case` `__tablename__`

**Documentation:**
- No mandatory docstrings — only comment where logic isn't self-evident
- Don't add comments/docstrings to code you didn't change
- `docs/` for long-term project knowledge
- Never create README or `.md` files unless explicitly requested

### Development Workflow Rules

**Branching:**
- `main` — production (protected, NEVER push directly)
- `feat/<desc>`, `fix/<desc>`, `refactor/<desc>` — working branches
- `ci-debug/<run_id>-<slug>` — CI debug branches
- Always open PR via `gh pr create`, never merge directly

**Commit Messages:**
- Format: `type(scope): message` (Conventional Commits)
- Types: `feat`, `fix`, `perf`, `refactor`, `test`, `ci`, `chore`, `docs`
- Lowercase message, concise
- Suffix: `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>` when AI-authored

**Local Validation (mandatory before push):**
1. `docker compose build`
2. `docker compose up -d` + `curl localhost:8787/healthz`
3. `docker compose exec backend pytest -q`
4. `cd frontend && npm install && npm run build` (static export)
5. Serve `out/` + `cd e2e && npx playwright test`

**Deployment:**
- release-please auto-versions (single CHANGELOG.md)
- Push to main → `deploy-gcp.yml` (Cloud Run + Cloud Storage + CDN invalidation)
- GCP secrets via Secret Manager

### Critical Don't-Miss Rules

**Trace Integrity (Crouzet methodology — HIGHEST priority):**
- NEVER alter imported GPX traces without explicit user consent (no auto-reroute, no simplify, no snap)
- `gpx.py` stores coordinates verbatim; `ingest.py` stores `geometry_geojson` as-is
- `skipRecalcRef` prevents auto-rerouting when loading existing traces into editor
- Smart routing only on explicit user action (sport change, new waypoint, engine change)
- Heatmap `_snap()` isolated to edge statistics — never touches stored geometry

**Data Privacy Boundaries:**
- Private: `activities`, `activity_cells`, `activity_photos`, `integration_accounts`, `import_jobs`
- Common (ODbL): `heat_cells`, `edge_popularity`, `routes`, `route_versions`, `route_forks`
- K-anonymity: K=2 production, K=1 dev (`HEATMAP_K_ANONYMITY`)
- Never expose private data in public endpoints

**Routing Anti-Patterns (CRITICAL):**
- NEVER call `fetchSmartSegment()` from UI code — always `fetchSmartSegmentWithClientRouting()`
- NEVER recalculate entire route when only one segment changed — splice `routeSegmentsRef.current`
- Proposal acceptance must preserve existing segments — only route the new segment
- `intentStrength` computed per-segment via `computeIntentStrength()`, never hardcoded

**Security:**
- JWT secret via env `JWT_SECRET` — never hardcode
- Default dev user `admin@admin / admin` seeded in lifespan — never in production
- `TEST_MODE=true` stubs auth — never in production
- Always SQLAlchemy ORM — never raw SQL strings

**Performance:**
- PostGIS upgrade requires `docker compose down -v` (drop old data volume)
- MapLibre `addSource`/`addLayer` must clean up in `useEffect` return
- Client graph tiles: deduplicate via `pendingTiles` Map
- `routingInFlightRef` counter prevents premature spinner dismissal during concurrent routing

---

## Usage Guidelines

**For AI Agents:**
- Read this file before implementing any code
- Follow ALL rules exactly as documented
- When in doubt, prefer the more restrictive option
- Update this file if new patterns emerge

**For Humans:**
- Keep this file lean and focused on agent needs
- Update when technology stack changes
- Review quarterly for outdated rules
- Remove rules that become obvious over time

Last Updated: 2026-03-14
