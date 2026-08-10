---
title: 'Route Management UX Overhaul'
slug: 'route-management-ux-overhaul'
created: '2026-03-17'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
adversarial_review: 'completed — 28 findings triaged in cross-functional war room'
tech_stack:
  - 'Next.js 16.1.6 (static export, output: export)'
  - 'React 18.3.1 + TypeScript 5.5.2 (strict mode)'
  - 'MapLibre GL 4.3.2'
  - 'FastAPI + SQLAlchemy 2.0 + GeoAlchemy2 + Alembic'
  - 'PostgreSQL 17 + PostGIS'
  - 'Pydantic v2 for request/response validation'
  - 'pytest + Playwright for tests'
files_to_modify:
  - 'backend/app/db/models.py — Add deleted_at, last_accessed_at to Route; new RouteCollection + RouteCollectionItem models'
  - 'backend/app/api/routes.py — Soft delete, PATCH endpoint, fork_count in response, last_accessed tracking, /me/routes endpoint'
  - 'backend/app/api/schemas.py — RouteOut, MyRouteOut, RouteUpdate Pydantic schemas (or inline in routes.py)'
  - 'backend/app/api/collections.py — NEW: collections API'
  - 'backend/alembic/versions/xxx_add_route_ux_overhaul.py — NEW: Alembic migration'
  - 'frontend/app/me/routes/page.tsx — NEW: personal trail library page'
  - 'frontend/app/map/page.tsx — Detail panel flat layout, parent dimmed variant view, add-to-collection at save, client-side smart naming'
  - 'frontend/app/discover/page.tsx — Fork count badge, improved cards'
  - 'frontend/components/route/RouteTagsSection.tsx — REMOVE'
  - 'frontend/components/route/RoutePRsSection.tsx — REMOVE'
  - 'frontend/components/RouteCard.tsx — NEW: shared card component for /me/routes and /discover'
  - 'backend/tests/test_routes.py — Tests for soft delete, collections, fork count'
  - 'backend/app/main.py — Register collection router'
  - 'docs/UI.md — Update with new pages and patterns'
code_patterns:
  - 'Router pattern: APIRouter(prefix="/xxx", tags=["xxx"]) registered in main.py'
  - 'Auth: get_current_user (required) and get_current_user_optional (optional) via Depends()'
  - 'DB: db: Session = Depends(get_db) — never create SessionLocal() directly'
  - 'Frontend pages: use client directive, TopNav + ProfileSelector pattern for /me/* pages'
  - 'Route detail: floating card bottom-left in map/page.tsx (search: viewedRoute &&)'
  - 'Save flow: handleSaveRoute in map/page.tsx → POST /routes with geometry_geojson, waypoints_json, sport, visibility'
  - 'Draft auto-save: PUT /me/drafts/{id} upsert pattern, localStorage cc_draft'
  - 'Reverse geocode: GET /geocode/reverse?lat=&lon= with 1req/s rate limit + in-memory cache'
  - 'Fork: POST /routes/{id}/fork → creates private copy with name "{parent} (variante)"'
  - 'Batch routes: GET /routes/batch?ids=a,b,c — max 4 routes'
  - 'Pydantic v2 schemas: class MyModel(BaseModel) with model_config, used for request/response validation'
test_patterns:
  - 'Backend: pytest with client.post/get/put/delete, auth_headers fixture'
  - 'E2E: Playwright with data-testid selectors'
  - 'Route tests in backend/tests/test_routes.py: TestRouteCRUD, TestFork, TestUpstreamPR, TestTags, TestDrafts, TestBatchRoutes'
  - 'E2E route tests in e2e/tests/core.spec.ts: tests 4a-4e (routes/forks/versions/PRs), 5a-5b (tags/GPX)'
---

# Tech-Spec: Route Management UX Overhaul

**Created:** 2026-03-17

## Overview

### Problem Statement

Route discovery and browsing is painful — there's no dedicated place for personal routes (they're mixed with activities in the sidebar), and the GitHub-like features (tags/PRs/forks) feel over-engineered for actual cyclist use cases. The PR accept/reject workflow adds complexity without clear user value. Tags exist but aren't visual. Route metadata (rename, description, delete) can't be edited after creation.

### Solution

Add a `/me/routes` personal trail library, simplify the variant model (keep fork + read-only visual comparison with parent, drop the PR workflow), introduce route collections that serve both organization AND multi-route map comparison (replacing the separate version/tag system — "727" collection containing "727 2023", "727 2024", "727 2025" with a "compare on map" toggle), and clean up the route detail panel and Discover page for better browsing.

### Scope

**In Scope:**
- New `/me/routes` page — personal trail library with mini-map (center-point dots, not full geometries), spatial filters (client-side bounds check on center coords), distance filters, search, rename, soft delete (30-day recovery + "Corbeille"), visibility toggle, fork count display, sorted by "last opened" by default, limit=200 default (no pagination for V1)
- Simplified variant model — keep "Créer une variante" + read-only visual comparison: parent shown as dimmed gray line (opacity 0.3), variant shown bright on top. Stats delta between variant and parent ("+2.3 km, +120 m D+"). No divergence highlighting algorithm.
- Route collections with "Comparer sur la carte" (max 4 routes, checkbox selection) — replaces separate version/tag system. Legend with toggle on/off + hover highlight. Collections serve both organization ("Sorties club") AND temporal comparison ("727" with yearly routes as distinct colored lines)
- "Add to collection" prompt at save time: "Ajouter à un groupe ? [727] [Sorties club] [+ Nouveau]"
- Auto-detect related routes by name similarity (exact case-insensitive substring match) when saving
- Drafts merged into `/me/routes` with "Brouillon" badge (no separate drafts concept)
- Remove RouteTagsSection.tsx and RoutePRsSection.tsx frontend code — collections handle the same need more naturally. Backend tag/PR/version endpoints get deprecation comments but remain for API compat.
- Route metadata editing via PATCH /routes/{id} — rename, description, visibility, sport (all optional fields). Soft delete with 30-day recovery (from detail panel and /me/routes). Extra warning on delete for forked routes (fork_count > 0)
- Route detail panel cleanup — flat layout (no accordions), prominent delete action
- Discover page improvements — better browsing, filtering, card layout, fork count as social proof
- Client-side smart route naming — default to "{Sport} — départ {start_point}" via existing reverse geocode endpoint. Pre-filled + selected so typing immediately replaces. Fallback: "Itinéraire {date}"

**Out of Scope:**
- Route editor / map click-drag interactions (already working well)
- Trips UX (separate spec)
- Mobile-specific redesign (responsive tweaks OK, no dedicated mobile rethink)
- Routing engine / cost model changes
- Pagination on /me/routes (V2 — 50 users won't have 1000+ routes at launch)
- Divergence highlighting algorithm (V2 — parent dimmed is sufficient for launch)
- Permanent delete endpoint (V2 — soft delete with 30-day window is sufficient)

## Context for Development

### Codebase Patterns

- All pages are `'use client'` components (Next.js static export, no SSR)
- Path aliases: `@/*` → project root
- Map instance in `useRef`, layers managed in `useEffect` cleanup
- Auth: `getToken()` from `@/lib/auth`, `useAuth()` hook available
- API: `API_URL` from `@/lib/api-client`, JWT in Authorization header
- Backend routers: `APIRouter(prefix="/xxx")` registered in `main.py`
- Pydantic v2 models for request/response validation
- Existing route API is comprehensive (CRUD, fork, versions, tags, PRs, GPX export)

### Files to Reference

| File | Purpose | Key Anchors |
| ---- | ------- | ----------- |
| `backend/app/db/models.py` | Route model (no deleted_at, no last_accessed_at, no collections) | Search: `class Route(`, `class RouteVersion(`, `class RouteFork(`, `class RouteTag(`, `class UpstreamPR(` |
| `backend/app/api/routes.py` | Routes API — CRUD, fork, versions, tags, PRs, drafts | Search: `def create_route(`, `def list_routes(`, `def delete_route(` (hard delete), `def fork_route(`, `@router.put("/me/drafts` |
| `backend/app/api/geocode.py` | Reverse geocode (Nominatim proxy, 1req/s, in-memory cache) | Search: `def reverse_geocode(` |
| `backend/app/api/me_activities.py` | Personal activities as GeoJSON — pattern for /me/routes | Search: `def get_my_activities(` |
| `backend/app/main.py` | Router registration — add collection router here | Search: `app.include_router(` |
| `backend/tests/test_routes.py` | Route tests: CRUD, fork, PR, tags, drafts, batch | Search: `class TestRouteCRUD`, `class TestFork`, `class TestDrafts` |
| `frontend/app/map/page.tsx` | Route detail panel, save flow, variant display | Search: `viewedRoute &&` (detail panel), `handleSaveRoute` (save flow), `parentCoords` (variant) |
| `frontend/app/discover/page.tsx` | Public route discovery — RouteCard, filtering, compare mode, clone | Search: `function RouteCard(`, `handleClone` |
| `frontend/app/me/stats/page.tsx` | Reference pattern for /me/* pages (TopNav, ProfileSelector, auth-gated) | |
| `frontend/components/route/RouteTagsSection.tsx` | Tags UI — TO REMOVE (replaced by collections) | |
| `frontend/components/route/RoutePRsSection.tsx` | PRs UI — TO REMOVE | |
| `frontend/lib/constants.ts` | SPORT_COLORS, SPORT_ICONS, SPORT_LABELS | |
| `frontend/lib/format.ts` | fmtKm, fmtElev, fmtDateLong formatters | |
| `e2e/tests/core.spec.ts` | E2E route tests: create, fork, version, PR, tag, GPX | Search: `test('4a`, `test('5a` |

### Technical Decisions

- Terminology: keep "Variante" / "Créer une variante", "Historique" for version history
- Collections replace versions/tags — a collection groups related routes with "Comparer sur la carte" toggle for multi-route map overlay
- PR merge/reject workflow is being removed entirely (frontend only — backend endpoints get deprecation comments)
- Variant always shows parent route as dimmed line underneath (opacity 0.3, gray) — read-only comparison, not collaboration. No divergence highlighting algorithm (V2).
- Variant shows "Inspiré de {parent}" as small credit line, not prominent UI
- Drafts appear in `/me/routes` with "Brouillon" badge — no separate drafts management
- Eliminate: RoutePRsSection.tsx, RouteTagsSection.tsx, upstream PR frontend code, accordion nesting in detail panel
- Six fundamental cyclist actions drive the design: Plan, Reuse, Share, Compare, Find, Remember
- `/me/routes` follows existing `/me/stats` page patterns (TopNav, ProfileSelector, auth-gated)
- Soft delete only (no permanent delete endpoint) — 30-day recovery + "Corbeille" section (collapsed by default in /me/routes). Routes past 30 days simply no longer appear in Corbeille (no cron cleanup for V1).
- Extra delete warning when route has fork_count > 0 (no share_count — field doesn't exist)
- `/me/routes` needs client-side spatial filters (bounds check on center coords) + distance range, not just text search
- `/me/routes` limit=200 default, no pagination (V2). All routes loaded in one request.
- Fork count displayed on route cards as social proof ("3 variantes")
- Smart naming is **client-side**: use existing `GET /geocode/reverse?lat=&lon=` and format as `{SPORT_LABEL} — départ {place_label}` in frontend. No dedicated backend endpoint.
- `/me/routes` default sort: "last opened" not "last created"
- `/me/routes` mini-map shows center-point dots colored by sport (NOT full route geometries — performance). Click dot → scroll to card.
- Collection compare: max 4 routes, checkbox selection, legend with toggle + hover highlight
- "Add to collection" prompt appears at route save time (after router.push to saved route, via `?saved=true` URL param)
- Auto-detect name-similar routes when saving → exact case-insensitive substring match → suggest adding to existing collection
- PATCH /routes/{id} with all-optional RouteUpdate schema for inline edits (rename, description, visibility, sport)
- Alembic migration required for new columns and tables
- Pydantic v2 schemas: RouteOut (public), MyRouteOut (extends with last_accessed_at, deleted_at), RouteUpdate (all optional)
- Variant parent data: expand parent fetch to include distance_m and elevation_gain_m for stats delta display

## Implementation Plan

### Tasks

#### Phase 1: Backend Foundation (dependency-first)

- [ ] Task 1: Add soft delete + last_accessed fields to Route model + Alembic migration
  - File: `backend/app/db/models.py`
  - Action: Add `deleted_at = Column(DateTime(timezone=True), nullable=True)` and `last_accessed_at = Column(DateTime(timezone=True), nullable=True)` to Route model (search: `class Route(`). Add index on `deleted_at` for filtered queries.
  - Migration: Create Alembic migration `alembic revision --autogenerate -m "add_route_soft_delete_and_last_accessed"`. Verify it adds the two nullable columns. Run `alembic upgrade head` to apply.
  - Notes: Both columns are nullable — existing routes get NULL (never deleted, never accessed).

- [ ] Task 2: Create RouteCollection + RouteCollectionItem models
  - File: `backend/app/db/models.py`
  - Action: Add new models:
    - `RouteCollection`: id (UUID), owner_id (String), name (String 255), description (Text nullable), created_at, updated_at
    - `RouteCollectionItem`: id (UUID), collection_id (FK), route_id (FK), added_at, unique constraint on (collection_id, route_id)
  - Migration: Include in the same Alembic migration as Task 1 (or generate a second one).
  - Notes: Collection belongs to a user. A route can be in multiple collections.

- [ ] Task 3: Update routes API — soft delete, PATCH, fork_count, /me/routes
  - File: `backend/app/api/routes.py`
  - Action:
    - **Pydantic schemas** (add at top of file or in separate `schemas.py`):
      - `RouteOut(BaseModel)`: id, name, sport, distance_m, elevation_gain_m, visibility, status, created_at, forked_from_id, fork_count (int), geometry_geojson (optional), center (list[float] — [lon, lat])
      - `MyRouteOut(RouteOut)`: extends with last_accessed_at (optional datetime), deleted_at (optional datetime)
      - `RouteUpdate(BaseModel)`: name (optional str), description (optional str), visibility (optional str), sport (optional str) — all fields optional
    - **Soft delete**: Change `DELETE /routes/{id}` (search: `def delete_route(`) to set `deleted_at = now()` instead of `db.delete(route)`. No permanent delete endpoint.
    - **Filter deleted**: Add `Route.deleted_at.is_(None)` filter to all route queries (list, get, batch, forks).
    - **Fork count**: Add `fork_count` computed field to route responses: `db.query(func.count(Route.id)).filter(Route.forked_from_id == route.id, Route.deleted_at.is_(None)).scalar()`
    - **Last accessed**: Update `GET /routes/{id}` to set `last_accessed_at = now()` when the authenticated user is the route owner.
    - **PATCH endpoint**: Add `PATCH /routes/{id}` using `RouteUpdate` schema. Only update fields that are not None. Owner-only. Return updated route.
    - **GET /me/routes**: List user's routes (including drafts) with optional query params: `sport`, `search` (name ilike), `min_distance_m`, `max_distance_m`, `sort` (last_accessed | created_at | distance_m, default: last_accessed), `include_deleted` (bool, for trash view). Default `limit=200`. Include `fork_count` in each response. Response uses `MyRouteOut` schema.
    - **POST /routes/{id}/restore**: Set `deleted_at = NULL` (owner only, must have deleted_at set and within 30 days).
  - Notes: `GET /me/routes` replaces the need for frontend to combine /me/drafts + /routes?owner=me. All route list/get responses include `fork_count`. Use `RouteOut` for public endpoints, `MyRouteOut` for /me/routes.

- [ ] Task 4: Create collections API
  - File: `backend/app/api/collections.py` (NEW)
  - Action: Create router with:
    - `POST /me/collections` — create collection (name, description optional)
    - `GET /me/collections` — list user's collections with route counts
    - `GET /me/collections/{id}` — get collection with route summaries (id, name, sport, distance_m, elevation_gain_m, center)
    - `PUT /me/collections/{id}` — update name/description
    - `DELETE /me/collections/{id}` — delete collection (not routes)
    - `POST /me/collections/{id}/routes` — add route to collection (body: {route_id})
    - `DELETE /me/collections/{id}/routes/{route_id}` — remove route from collection
    - `GET /me/collections/{id}/compare` — return geometries for up to 4 routes in collection (reuse batch pattern)
  - Notes: Register in `main.py` (search: `app.include_router(`). Auth: `get_current_user` on all endpoints. Pydantic v2 schemas for all request/response.

- [ ] Task 5: Backend tests for new features
  - File: `backend/tests/test_routes.py`
  - Action: Add test classes:
    - `TestSoftDelete`: delete route → still in DB with deleted_at set, not in list_routes, GET returns 404, restore works within 30 days, restore fails after 30 days (mock datetime)
    - `TestForkCount`: create parent + 2 forks → parent response includes fork_count=2, delete 1 fork → fork_count=1
    - `TestMyRoutes`: list personal routes with drafts, filter by sport/distance, sort by last_accessed, limit=200 default, include_deleted returns trashed routes
    - `TestPatchRoute`: PATCH with partial fields updates only those fields, PATCH by non-owner returns 403, PATCH with empty body returns unchanged route
    - `TestCollections`: CRUD collection, add/remove routes, compare endpoint returns max 4 geometries, cannot add same route twice, delete collection doesn't delete routes
  - Notes: Follow existing test patterns (auth_headers, client fixture). No TestSmartNaming — naming is client-side.

#### Phase 2: Frontend — Shared Components

- [ ] Task 6: Extract shared RouteCard component
  - File: `frontend/components/RouteCard.tsx` (NEW)
  - Action: Extract RouteCard from `discover/page.tsx` (search: `function RouteCard(`) into shared component. Add props:
    - `route`: Route data (id, name, sport, distance_m, elevation_gain_m, surface_pct, forked_from_id, fork_count, visibility, status, created_at, last_accessed_at, center)
    - `onDelete?`: callback for delete action
    - `onRename?`: callback for inline rename (uses PATCH /routes/{id})
    - `onToggleVisibility?`: callback for visibility toggle (uses PATCH /routes/{id})
    - `showActions?`: 'owner' | 'public' | 'compare' (controls which buttons show)
    - `compareMode?`, `selectedForCompare?`, `onToggleCompare?`: for compare flow
    - Display fork_count badge: "🍴 {n} variantes" if fork_count > 0
    - Display "Brouillon" badge if status === 'draft'
    - Display "Inspiré de {parent}" credit if forked_from_id
  - Notes: Reuse SPORT_COLORS, SPORT_ICONS from constants. Keep existing card styling from discover page.

- [ ] Task 7: Remove RouteTagsSection and RoutePRsSection
  - Files: `frontend/components/route/RouteTagsSection.tsx`, `frontend/components/route/RoutePRsSection.tsx`
  - Action: Delete both files. Remove all imports and usages from `frontend/app/map/page.tsx` (search: `RouteTagsSection`, `RoutePRsSection`). Remove the expandable accordion sections from the route detail panel.
  - Backend: Add deprecation comments to tag/PR/version endpoints in `routes.py` (search: `def create_tag(`, `def create_upstream_pr(`): `# DEPRECATED: frontend no longer uses this endpoint. Keep for API compat.`
  - Notes: Backend endpoints stay for API compat — only frontend code is removed.

#### Phase 3: Frontend — /me/routes Page

- [ ] Task 8: Create /me/routes trail library page
  - File: `frontend/app/me/routes/page.tsx` (NEW)
  - Action: Create personal route management page following `/me/stats` pattern:
    - `'use client'` directive, TopNav, ProfileSelector
    - Auth-gated: redirect to `/` if no token
    - Fetch `GET /me/routes` (limit=200, all routes in one request)
    - **Layout:** Two-column on desktop: route list (left, 60%) + mini-map (right, 40%). Single column on mobile.
    - **Mini-map:** Small MapLibre map showing all personal routes as **center-point dots** colored by sport (using SPORT_COLORS). NOT full route geometries (performance). Click a dot → highlight + scroll to corresponding card. Click empty area → no-op.
    - **Spatial filter:** Client-side bounds check on route `center` coords. When user pans/zooms the mini-map, filter route list to only show routes whose center is within visible bounds. Toggle: "Filtrer par la carte" checkbox (off by default).
    - **Filters:** Sport (ProfileSelector), text search (debounced 300ms), distance range (min/max km inputs), sort dropdown (last opened, newest, distance)
    - **Route list:** Use shared RouteCard component with showActions='owner'. Each card has: inline rename (click name → edit input → blur saves via PATCH), visibility toggle (via PATCH), delete button (soft delete confirmation), "Ouvrir" link to `/map?route={id}`
    - **Drafts:** Shown with "Brouillon" badge, sorted first if sort is "last opened"
    - **Trash ("Corbeille"):** Collapsed section at bottom. Shows routes with deleted_at set (fetched via `include_deleted=true`). Filter client-side to only show routes deleted within 30 days. Each has "Restaurer" button → POST /routes/{id}/restore
    - **Empty state:** "Pas encore d'itinéraires — créez votre premier sur la carte" with link to /map
    - **Collections section:** Below route list. Show user's collections as expandable cards. Each collection shows route count, "Comparer sur la carte" button. Click collection → expand to show member routes with remove (×) button.
    - **Create collection:** "+ Nouveau groupe" button → inline name input
  - Notes: Default sort "last opened" (last_accessed_at DESC, nulls last). Mini-map uses same MapLibre GL with reduced controls (no navigation, no attribution).

#### Phase 4: Frontend — Map Page Updates

- [ ] Task 9: Flatten route detail panel + PATCH editing
  - File: `frontend/app/map/page.tsx`
  - Action: Refactor the route detail card (search: `viewedRoute &&`):
    - Remove accordion/expandable sections — show all content flat
    - Layout: Header (name + badges) → Stats row → Surface bar → Elevation profile → Actions row → Variant section → Collection section
    - Replace RouteTagsSection/RoutePRsSection with: "Ajouter à un groupe" dropdown (user's collections + "+ Nouveau")
    - Add inline edit for route name (click to edit, blur to save via **PATCH /routes/{id}** with `{ name: newName }`)
    - Add delete button with soft delete confirmation dialog. If route has fork_count > 0, show extra warning: "Cet itinéraire a {n} variantes. Supprimer quand même ?"
    - Add visibility toggle button (public ↔ private via **PATCH /routes/{id}** with `{ visibility: newVisibility }`)
    - "Inspiré de {parent}" small credit line for variants (replacing prominent "Variante de" block)
  - Notes: Keep data-testid attributes for E2E tests. Keep existing share/edit/GPX/fork buttons.

- [ ] Task 10: Variant parent dimmed view + stats delta
  - File: `frontend/app/map/page.tsx`
  - Action: When viewing a variant (forked_from_id set) and parentCoords are loaded:
    - Parent route: show as dimmed single-color line (opacity 0.3, gray `#888888`)
    - Variant: show as normal bright route line on top
    - **Expand parent data loading**: when loading parent route for variant display, also fetch `distance_m` and `elevation_gain_m` from parent (search: `parentCoords` — ensure parent data includes stats)
    - Add stats delta in detail panel: compute difference in distance_m and elevation_gain_m between variant and parent. Show as "+2.3 km, +120 m D+" or "-1.1 km, -45 m D+"
    - No divergence highlighting algorithm (V2) — just parent dimmed + variant bright
  - Notes: Parent dimmed behavior may already partially exist. Verify and enhance. Stats delta requires parent stats to be loaded — use parent route GET response.

- [ ] Task 11: Client-side smart naming in save flow
  - File: `frontend/app/map/page.tsx`
  - Action: In handleSaveRoute (search: `handleSaveRoute`):
    - Before showing the save UI, if routeName is empty or default ("Mon itinéraire"):
      - Fetch `GET /geocode/reverse?lat={start_lat}&lon={start_lon}` (existing endpoint)
      - Extract place name from response (city/town/village)
      - Format client-side: `{SPORT_LABEL} — départ {place_label}` using SPORT_LABELS from constants
      - Pre-fill routeName input with the suggestion
      - Auto-select the text in the input so typing immediately replaces
    - Fallback if geocode fails: "Itinéraire {date}" where date is formatted as "17 mars 2026"
    - Sport labels mapping (in constants or inline): road→"Route", gravel→"Gravel", mtb→"VTT", offroad→"Off-road", running→"Running"
  - Notes: Use first waypoint coords for start point. Rate limit already handled by geocode endpoint. No dedicated backend endpoint needed.

- [ ] Task 12: Add-to-collection prompt at save time
  - File: `frontend/app/map/page.tsx`
  - Action: Change save flow to use `router.push('/map?route={id}&saved=true')` instead of `window.location.href` redirect after successful route save. Then:
    - When `?saved=true` URL param is detected on mount:
      - Show success banner: "Itinéraire enregistré !"
      - Fetch user's collections: `GET /me/collections`
      - Check name similarity: exact case-insensitive substring match — find collections where any member route name contains a 3+ character common substring with new route name
      - Show inline prompt below success banner: "Ajouter à un groupe ? [suggested_collection] [+ Nouveau]"
      - If user clicks a collection: `POST /me/collections/{id}/routes` with new route_id
      - If user clicks "+ Nouveau": show inline input for collection name, create collection, add route
      - If user dismisses (click × or timeout 10s): remove banner
    - Clean up `saved=true` from URL after handling (router.replace without param)
  - Notes: Prompt is non-blocking — route is already saved. Using router.push instead of hard redirect preserves React state and enables the collection prompt.

#### Phase 5: Frontend — Discover Page Updates

- [ ] Task 13: Update Discover page with fork count + shared RouteCard
  - File: `frontend/app/discover/page.tsx`
  - Action:
    - Replace inline RouteCard (search: `function RouteCard(`) with shared `<RouteCard>` component from Task 6
    - Pass `showActions='public'` to show: "Ouvrir la carte", "Créer une variante" buttons
    - Fork count badge visible on cards: "🍴 {n} variantes" from route response
    - Keep existing compare mode with shared RouteCard's compare props
  - Notes: Minimal changes — mostly replacing inline component with shared import. Keep all existing filtering/sorting logic.

#### Phase 6: Collection Compare on Map

- [ ] Task 14: Collection compare map overlay
  - File: `frontend/app/map/page.tsx`
  - Action: Add collection compare mode (triggered from `/me/routes` or URL param `?compare_collection={id}`):
    - Fetch `GET /me/collections/{id}/compare` → up to 4 route geometries
    - Add map sources + layers for each route with distinct colors: `['#e74c3c', '#3498db', '#2ecc71', '#f39c12']`
    - Add floating legend panel (bottom-right): route name + color swatch + toggle checkbox + distance/D+
    - Hover legend entry → that route brightens (opacity 1.0), others dim (opacity 0.3)
    - Click legend checkbox → toggle route visibility
    - "Fermer la comparaison" button to exit compare mode and remove layers
  - Notes: Reuse existing compared route infrastructure (search: `ComparedRoute` interface). Max 4 routes enforced by backend.

#### Phase 7: Navigation + Cleanup

- [ ] Task 15: Update TopNav with /me/routes link
  - File: `frontend/components/TopNav.tsx`
  - Action: Add "Mes itinéraires" link pointing to `/me/routes` (visible when authenticated). Place between "Carte" and "Découvrir" links.
  - Notes: Follow existing active link styling pattern.

- [ ] Task 16: Update UI documentation
  - File: `docs/UI.md`
  - Action: Add `/me/routes` page description, update route detail panel description, document collections, remove mentions of tags/PRs UI, add smart naming and soft delete behaviors.

- [ ] Task 17: Update E2E tests
  - File: `e2e/tests/core.spec.ts`
  - Action:
    - Update existing route tests (4a-4e) to work without PR UI
    - Add test: navigate to /me/routes, see personal routes listed
    - Add test: soft delete route → appears in "Corbeille", restore it
    - Add test: create collection, add route, open compare view
    - Remove or skip tests that depend on RoutePRsSection UI
  - Notes: Keep backend PR endpoint tests (they still work). Only remove frontend-facing PR test assertions.

### Acceptance Criteria

#### /me/routes Page
- [ ] AC 1: Given an authenticated user with 5 routes (3 published, 2 drafts), when navigating to `/me/routes`, then all 5 routes are displayed with drafts showing "Brouillon" badge, sorted by last_accessed_at DESC
- [ ] AC 2: Given a user on `/me/routes`, when typing "727" in search, then only routes with "727" in the name are shown (debounced 300ms)
- [ ] AC 3: Given a user on `/me/routes`, when clicking a route dot on the mini-map, then the corresponding card scrolls into view and is highlighted
- [ ] AC 4: Given a user on `/me/routes`, when setting distance filter to 30-60 km, then only routes within that range are shown

#### Soft Delete
- [ ] AC 5: Given a route owner, when clicking delete on a route with 0 forks, then a confirmation dialog appears. On confirm, route disappears from list and appears in "Corbeille"
- [ ] AC 6: Given a route owner, when deleting a route with fork_count > 0, then the confirmation shows "Cet itinéraire a {n} variantes. Supprimer quand même ?"
- [ ] AC 7: Given a deleted route in "Corbeille", when clicking "Restaurer", then route reappears in the main list with all metadata intact
- [ ] AC 8: Given a deleted route older than 30 days, then it is no longer visible in "Corbeille" (client-side filter — no backend cleanup for V1)

#### Collections
- [ ] AC 9: Given a user, when creating a collection named "727" and adding 3 routes, then the collection appears in `/me/routes` with route count "3 itinéraires"
- [ ] AC 10: Given a collection with 4 routes, when clicking "Comparer sur la carte", then all 4 routes are overlaid on the map with distinct colors and a legend panel
- [ ] AC 11: Given a compare legend, when hovering a route name, then that route brightens and others dim. When clicking the checkbox, that route hides/shows
- [ ] AC 12: Given a user saving a route named "727 2025" while collection "727" exists with "727 2024", then a prompt suggests: "Ajouter au groupe '727' ?"

#### Variant Comparison
- [ ] AC 13: Given a variant route with forked_from_id, when viewing it on the map, then the parent route is shown as a dimmed gray line (opacity 0.3) underneath the bright variant line
- [ ] AC 14: Given a variant, when the detail panel is open, then it shows "Inspiré de {parent_name}" as a small credit line and stats delta ("+X km, +Y m D+")

#### Smart Naming
- [ ] AC 15: Given a user saving a new route starting near Montpellier with sport=gravel, when the name input is empty, then it is pre-filled with "Gravel — départ Montpellier" and the text is selected
- [ ] AC 16: Given a geocode failure, when saving a route, then the name defaults to "Itinéraire {date}" (e.g., "Itinéraire 17 mars 2026")

#### Route Detail Panel
- [ ] AC 17: Given a route detail panel, when opened, then all information is shown in a flat layout (no accordions, no expandable sections)
- [ ] AC 18: Given a route owner viewing their route, when clicking the route name, then it becomes an editable input. On blur, the name is saved via PATCH /routes/{id}
- [ ] AC 19: Given a route detail panel, when the route has no RouteTagsSection or RoutePRsSection, then no accordion/expandable sections for tags or PRs are rendered

#### Discover Page
- [ ] AC 20: Given the Discover page, when routes are loaded, then each card displays fork_count badge "🍴 {n} variantes" if fork_count > 0

## Additional Context

### Dependencies

- Existing route API already supports all needed CRUD operations (create, update, fork, versions, GPX export)
- Fork API works — PR endpoints remain in backend for API compat (with deprecation comments), only frontend code removed
- New backend: collections API needed (CRUD collection + add/remove routes + compare endpoint)
- Collections compare endpoint reuses batch route geometry fetch pattern (max 4 routes)
- Reverse geocode endpoint exists — smart naming uses it directly (client-side formatting, no new endpoint)
- No new external dependencies or libraries needed
- Alembic migration required for new columns (deleted_at, last_accessed_at) and new tables (RouteCollection, RouteCollectionItem)

### Testing Strategy

**Backend (pytest):**
- `TestSoftDelete`: soft delete, restore, filtered queries exclude deleted, 30-day expiry (mock), no permanent delete
- `TestForkCount`: fork_count computed correctly, updates on fork delete
- `TestMyRoutes`: list with filters (sport, distance range, search), sort options, includes drafts, limit=200 default, include_deleted param
- `TestPatchRoute`: partial updates, owner-only, empty body no-op
- `TestCollections`: CRUD, add/remove routes, unique constraint, compare endpoint (max 4), delete collection doesn't delete routes

**E2E (Playwright):**
- Navigate to `/me/routes` → routes listed, drafts badged
- Soft delete → "Corbeille" → restore
- Create collection, add route, open compare view
- Save route → smart name pre-filled
- Remove PR-dependent test assertions (backend PR endpoints keep working)

**Manual testing:**
- Parent dimmed view with known variant routes
- Mini-map dot interaction on `/me/routes`
- Collection compare legend hover/toggle
- Smart naming accuracy with various start locations

### War Room Decisions (Adversarial Review)

Summary of key decisions from cross-functional war room (28 findings triaged):

| Finding | Decision |
| ------- | -------- |
| F1: Stale line numbers | Replaced with searchable code anchors |
| F2: Missing Pydantic schemas | Added RouteOut, MyRouteOut, RouteUpdate to Task 3 |
| F3+F20: No PATCH endpoint | Added PATCH /routes/{id} with all-optional RouteUpdate |
| F4+F7: FK violations on permanent delete | Cut permanent delete entirely — soft delete only |
| F5: Alembic migration contradiction | Write the migration (Task 1) |
| F6: share_count doesn't exist | Dropped — warn on fork_count only |
| F8+F10: Mini-map performance | Center-point dots, not full geometries. Client-side bounds filter. |
| F9: Name-similarity under-specified | Exact case-insensitive substring match |
| F11+F26: Divergence algorithm vague | Cut divergence highlighting — parent dimmed only (V2) |
| F12: Parent stats not loaded | Expand parent fetch to include distance_m, elevation_gain_m |
| F14: No pagination | limit=200 default, pagination is V2 |
| F16: Smart naming endpoint redundant | Cut — client-side using existing geocode + SPORT_LABELS |
| F18: Hard redirect blocks collection prompt | router.push + ?saved=true param |

### Notes

- Variant view: parent route dimmed (opacity 0.3, gray) + variant bright overlaid on the map (read-only comparison, not collaboration)
- Collection "compare" mode: distinct colors per route with a legend panel
- Consider route card component extraction (shared between /me/routes and /discover)
- User persona insights (Léa/Nico focus group):
  - Routes lost among activities is the #1 frustration → `/me/routes` is critical
  - PR terminology is incomprehensible to non-dev cyclists → confirmed removal
  - Fork count motivates sharing → add as social proof
  - Bad default names ("Mon itinéraire") cause findability problems → smart naming
  - Spatial/distance filters needed because users name routes poorly
- SCAMPER insights:
  - Variant always shows parent dimmed = diff is default, not an action
  - Drafts merged into /me/routes with "Brouillon" badge
  - Route collections for user-created groups
  - Eliminate RoutePRsSection.tsx + upstream PR frontend code
  - Flat detail panel layout, no accordions
  - Forked route shows "Inspiré de {parent}" as small credit line, not prominent UI element
- First principles insights:
  - Six fundamental cyclist actions: Plan, Reuse, Share, Compare, Find, Remember
  - Collections replace versions/tags — one concept (grouping routes) serves both organization and comparison
  - Variant parent overlay is read-only comparison, not collaboration tool
  - `/me/routes` is a personal trail library, not a file manager
  - RouteTag frontend code eliminated — collections handle temporal comparison more naturally
- Pre-mortem failure preventions:
  - Mini-map in /me/routes for visual spatial search (prevents "can't find my routes")
  - Sort by "last opened" default (prevents scrolling through stale routes)
  - "Add to collection" prompt at save time (prevents empty collections)
  - Auto-detect name-similar routes → suggest collection (prevents manual curation fatigue)
  - Soft delete with 30-day recovery + "Corbeille" (prevents accidental data loss)
  - Extra warning for forked routes on delete (prevents breaking variant chains)
  - Collection compare capped at 4 routes with checkbox selection (prevents visual spaghetti)
  - Legend toggle on/off + hover highlight (prevents confusion in compare view)
  - Smart naming uses start point not centroid, pre-filled + selected (prevents garbage names)
  - Stats diff between variant and parent (prevents "what changed?" confusion)
