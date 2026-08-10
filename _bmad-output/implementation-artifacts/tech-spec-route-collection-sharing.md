---
title: 'Route Collection Sharing with Multi-Trace Map & POI Annotations'
slug: 'route-collection-sharing'
created: '2026-03-25'
status: 'completed'
stepsCompleted: [1, 2, 3, 4]
tech_stack: [python, typescript, fastapi, maplibre, postgresql, next.js]
files_to_modify: [backend/app/db/models.py, backend/app/api/collections.py, frontend/app/collections/page.tsx, backend/tests/test_collections.py, e2e/tests/collections.spec.ts]
code_patterns: [fastapi-router, pydantic-response-model, maplibre-geojson-layers, sport-color-coding, auth-optional-public-view]
test_patterns: [pytest, playwright, auth_headers-fixture, data-testid-selectors]
---

# Tech-Spec: Route Collection Sharing with Multi-Trace Map & POI Annotations

**Created:** 2026-03-25

## Overview

### Problem Statement

No way to share a group of routes (event variants, multi-day trip stages) on a single map with safety-critical POI annotations. Event organizers can't warn riders about portage sections, mark water points, or show multiple route variants (e.g., POU100: 100 miles + 100 km + bikepacking bonus) on one shareable page.

Reference: VisuGPX (https://www.visugpx.com/zgLFHkc0sP) — POU100 2026 event page.

### Solution

Public collection page (`/collections?id=xxx`) showing all routes on one MapLibre map with distinct colors, POI icons from RouteAnnotation, stage ordering, combined stats, and a shareable URL.

### Scope

**In Scope (MVP):**
- `visibility` on RouteCollection (public/unlisted/private)
- `position` on RouteCollectionItem (stage ordering)
- `collection_id` on RouteAnnotation (collection-level POIs)
- Public GET endpoint (auth optional, visibility check)
- Annotation CRUD endpoints (collection-level + route-level)
- Frontend page `/collections?id=xxx` with multi-trace map + POI emoji icons
- Elevation profile toggle per route
- Collection stats (total km, D+, route count)
- GPX download links per route
- pytest + Playwright tests

**Out of Scope (v1.1):**
- `difficulty` field on collection
- `image_url` on annotations (photo POIs)
- Visibility cascade warning UI
- Lazy-load annotations on zoom
- Discover/browse public collections page
- 3D view, stitched multi-stage elevation, surface overlay, print view
- Open Graph meta tags for link previews (requires SSR)

## Context for Development

### Codebase Patterns

- **FastAPI routers:** Protected = `Depends(get_current_user)`, public = `get_current_user_optional` or no auth
- **Pydantic response models:** All endpoints return typed `BaseModel` classes
- **Visibility enum:** Postgres type `visibility_enum` already exists — reuse it (`Enum("public", "unlisted", "private", name="visibility_enum", create_type=False)`)
- **MapLibre layers:** GeoJSON sources + line layers. Compare mode uses `COMPARE_COLORS = ['#e63946', '#457b9d', '#2a9d8f', '#e9c46a']` and `COMPARE_DASH = [undefined, [8,4], [4,4], [12,4,4,4]]`
- **French UI:** All text in French, emoji for icons
- **GPX export:** `GET /routes/{id}/gpx` — uses route-level visibility check (independent of collection)
- **Static export:** Next.js `output: 'export'` — NO dynamic `[id]` routes. Use query params: `/collections?id=xxx`
- **Private resource pattern:** Return 404 (not 403) for non-owners to prevent info disclosure

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `backend/app/db/models.py:169-190` | RouteCollection + RouteCollectionItem models |
| `backend/app/db/models.py:459-472` | RouteAnnotation model (author_id, icon, text, lat, lon, route_id) |
| `backend/app/api/collections.py:1-257` | Existing CRUD under `/me/collections` + compare endpoint |
| `backend/app/api/routes.py:383-432` | Public routes listing pattern (auth optional) |
| `backend/app/api/routes.py:988-1050` | GPX export endpoint (route-level visibility) |
| `frontend/app/map/page.tsx:1475-2008` | Compare mode map layers (COMPARE_COLORS, COMPARE_DASH) |
| `frontend/components/ElevationProfile.tsx` | SVG elevation profile |
| `frontend/lib/constants.ts:1-48` | SPORT_COLORS, SPORT_ICONS |
| `backend/tests/conftest.py` | Pytest fixtures: `client`, `auth_headers` |

### Technical Decisions

1. **Visibility on RouteCollection:** Use existing `visibility_enum` type (NOT plain String). Default: `"private"`.
2. **`position` on RouteCollectionItem:** Integer, default 0. Auto-increment: `COALESCE(MAX(position), -1) + 1`.
3. **Collection-level annotations:** RouteAnnotation gets optional `collection_id`. `route_id` becomes nullable. CHECK constraint: `route_id IS NOT NULL OR collection_id IS NOT NULL`.
4. **Why not TripPOI?** Different semantics (stage_id, lodging_type). RouteAnnotation already has route-level annotations — extending is cleaner than a third POI table.
5. **Collection colors + dash:** Route `i` gets `COMPARE_COLORS[i % 4]` + `COMPARE_DASH[i % 4]`. Dash pattern disambiguates routes 0 vs 4 (same color, different dash).
6. **Public endpoint routing:** New router `/collections` (separate from `/me/collections`). Register in `main.py`.
7. **Frontend page:** `/collections?id=xxx` query param (static export constraint).
8. **Payload size:** Geometry simplified via `ST_AsGeoJSON(ST_Simplify(ST_GeomFromGeoJSON(geometry_geojson), 0.0001))`. Note: `geometry_geojson` is stored as Text, not PostGIS geometry — requires `ST_GeomFromGeoJSON()` wrapping. Target: <200KB gzip for 5 routes.
9. **Icon validation:** Pydantic `Literal["water", "food", "camping", "refuge", "danger", "portage", "photo", "bike-shop", "info"]`. Icon values stored/returned verbatim (no alias transformation).
10. **Private resources → 404:** Private collections and private routes return 404 to non-owners (not 403) to prevent existence disclosure.
11. **Annotation author_id:** Set to `current_user.user_id` on create. PUT/DELETE check `annotation.author_id == current_user.user_id`.
12. **Collection size limits:** Max 20 routes per collection, max 200 annotations per collection. Enforced at API level.
13. **Batch queries:** Public endpoint uses `WHERE route_id IN (...)` for batch route fetch + `WHERE collection_id = :id OR route_id IN (...)` for annotations. No N+1.
14. **`updated_at` touch:** When adding/removing routes or annotations, explicitly update `collection.updated_at = now()` (ORM `onupdate` only fires on column changes to the collection row itself).

## Implementation Plan

### Phase 1: Backend Schema + Migration

- [x] Task 1: Add columns to models
  - File: `backend/app/db/models.py`
  - Action: Add `visibility` to RouteCollection: `Column(Enum("public", "unlisted", "private", name="visibility_enum", create_type=False), default="private", nullable=False)`
  - Action: Add `position` to RouteCollectionItem: `Column(Integer, default=0, nullable=False)`
  - Action: Add `collection_id` to RouteAnnotation: `Column(UUID(as_uuid=False), ForeignKey("route_collections.id", ondelete="CASCADE"), nullable=True, index=True)`
  - Action: Change `route_id` on RouteAnnotation to `nullable=True`
  - Action: Add `__table_args__` with `CheckConstraint("route_id IS NOT NULL OR collection_id IS NOT NULL", name="ck_annotation_has_parent")` (import `CheckConstraint` from sqlalchemy)

- [x] Task 2: Create Alembic migration
  - File: `backend/alembic/versions/xxxx_add_collection_sharing.py`
  - Action: `ALTER TABLE route_collections ADD COLUMN visibility visibility_enum DEFAULT 'private' NOT NULL`
  - Action: `ALTER TABLE route_collection_items ADD COLUMN position INTEGER DEFAULT 0 NOT NULL`
  - Action: `ALTER TABLE route_annotations ADD COLUMN collection_id UUID REFERENCES route_collections(id) ON DELETE CASCADE`
  - Action: `ALTER TABLE route_annotations ALTER COLUMN route_id DROP NOT NULL`
  - Action: `ALTER TABLE route_annotations ADD CONSTRAINT ck_annotation_has_parent CHECK (route_id IS NOT NULL OR collection_id IS NOT NULL)`
  - Notes: Downgrade reverses all operations. No `image_url` — deferred to v1.1.

### Phase 2: Backend API

- [x] Task 3: Public collection endpoint
  - File: `backend/app/api/collections.py`
  - Action: Create `collections_public_router = APIRouter(prefix="/collections", tags=["collections"])`
  - Action: `GET /collections/{id}` with `get_current_user_optional`
  - Action: Visibility: public/unlisted = accessible to all. Private = 404 for non-owners (not 403).
  - Action: Batch-fetch routes: `db.query(Route).filter(Route.id.in_(route_ids)).all()` — no N+1
  - Action: Private routes: include in list with `geometry_geojson: null`, label "Route privée"
  - Action: Simplify geometries: `SELECT ST_AsGeoJSON(ST_Simplify(ST_GeomFromGeoJSON(:geojson), 0.0001))` for each visible route
  - Action: Batch-fetch annotations: `WHERE collection_id = :id OR route_id IN (:visible_route_ids)`
  - Action: Stats: sum distance_m + elevation_gain_m from visible routes only
  - Action: Response includes `updated_at`
  - Action: Register in `main.py`: `app.include_router(collections_public_router)`

- [x] Task 4: Update existing collection CRUD + fix compare endpoint
  - File: `backend/app/api/collections.py`
  - Action: Update `CollectionOut` schema: add `visibility`, `updated_at` fields
  - Action: Add `visibility` to create/update Pydantic schemas + DB writes
  - Action: Add optional `position` to add-route endpoint — default: `COALESCE(MAX(position), -1) + 1`
  - Action: Add `PUT /me/collections/{id}/routes/{route_id}` for position reorder
  - Action: Touch `collection.updated_at = datetime.now(UTC)` on add/remove route
  - Action: Enforce limit: max 20 routes per collection (return 409 if exceeded)
  - Action: Fix existing `GET /me/collections/{id}/compare`: add route visibility check (exclude private routes not owned by requester)

- [x] Task 5: Collection annotation CRUD
  - File: `backend/app/api/collections.py`
  - Action: `POST /me/collections/{id}/annotations` — owner only. Set `author_id = current_user.user_id`.
  - Action: Schema: `{icon: Literal[...9 values...], text: str | None, lat: float, lon: float, route_id: uuid | None}`
  - Action: Enforce limit: max 200 annotations per collection (return 409 if exceeded)
  - Action: `PUT /me/collections/{id}/annotations/{ann_id}` — check `annotation.author_id == current_user.user_id`
  - Action: `DELETE /me/collections/{id}/annotations/{ann_id}` — same author check
  - Action: Touch `collection.updated_at` on annotation create/update/delete
  - Notes: Read via public endpoint (Task 3) — no separate GET annotations endpoint needed (inline in collection response).

### Phase 3: Frontend Collection Page

- [x] Task 6: Create collection page
  - File: `frontend/app/collections/page.tsx`
  - Action: Read `id` from `useSearchParams().get('id')` — show "Collection introuvable" if missing or fetch fails
  - Action: Fetch `GET /collections/{id}` on mount (no auth required for public)
  - Action: Full-screen MapLibre map with collapsible left sidebar
  - Action: Each route: GeoJSON line layer with `COMPARE_COLORS[i % 4]` color + `COMPARE_DASH[i % 4]` dash pattern
  - Action: Sidebar: collection name, description, total stats, route list sorted by position
  - Action: Route item: color dot + dash indicator, name, sport badge, distance, elevation, GPX link
  - Action: Private routes: "Route privée" label, no map trace, no GPX link
  - Action: Click route → highlight (opacity 0.9), show ElevationProfile below map
  - Action: Map fits bounds of all visible routes on load

- [x] Task 7: POI markers on map
  - File: `frontend/app/collections/page.tsx`
  - Action: Annotations as MapLibre `symbol` layer with emoji `text-field`
  - Action: Icon map: `{water: '💧', food: '🍽', camping: '⛺', refuge: '🏠', danger: '⚠️', portage: '🚧', photo: '📷', 'bike-shop': '🔧', info: 'ℹ️'}`
  - Action: Click → MapLibre Popup with text
  - Action: Danger/portage: `text-size: 24`, `text-halo-color: '#dc2626'`

- [x] Task 8: Elevation profile toggle
  - File: `frontend/app/collections/page.tsx`
  - Action: Click route in sidebar → show `ElevationProfile` with route's color
  - Action: No elevation data → "Pas de données d'altitude"
  - Notes: Reuse existing `ElevationProfile` component

### Phase 4: Tests

- [x] Task 9: Backend tests
  - File: `backend/tests/test_collections.py`
  - Tests:
    - Public collection → GET without auth → 200 with routes + annotations
    - Private collection → GET without auth → 404 (not 403)
    - Unlisted collection → GET without auth → 200
    - Add routes → positions auto-increment (0, 1, 2)
    - PUT route position → order changes in response
    - Collection-level annotation (route_id=None, collection_id set) → valid, in response
    - Route-level annotation (route_id set, collection_id set) → valid
    - Annotation with route_id=None AND collection_id=None → 422
    - Invalid icon → 422
    - Private route in public collection → listed, geometry null
    - Stats exclude private routes
    - Annotation CRUD: owner can create/update/delete, non-owner gets 404
    - Author check: annotation.author_id must match current_user for PUT/DELETE
    - Collection limit: 21st route → 409
    - Annotation limit: 201st annotation → 409
    - `updated_at` changes when route added/removed/annotation created

- [x] Task 10: E2E tests
  - File: `e2e/tests/collections.spec.ts`
  - Tests:
    - Create 2 routes → collection → add routes → set public → visit `/collections?id={id}` → 2 colored lines on map
    - Sidebar shows stats (km, D+, count)
    - Click route in sidebar → elevation profile appears
    - Create danger annotation → POI marker visible on map with popup
    - Private collection → "Collection introuvable"
    - GPX download → `page.waitForEvent('download')` → Content-Type `application/gpx+xml`

### Acceptance Criteria

- [x] AC 1: Given a public collection with 3 routes, when visiting `/collections?id={id}` without auth, then 3 routes visible with different colors + dash patterns, stats shown.
- [x] AC 2: Given a private collection, when visiting without auth, then "Collection introuvable" (404, no existence disclosure).
- [x] AC 3: Given 5 annotations (2 danger, 1 water, 2 food), when viewing, then 5 emoji markers on map, clickable with popup text.
- [x] AC 4: Given routes at positions [0,1,2], then listed in order, colored with COMPARE_COLORS + COMPARE_DASH by index.
- [x] AC 5: Given a collection-level annotation (route_id=null), then appears at lat/lon on map.
- [x] AC 6: Given clicking a route in sidebar, then elevation profile appears below map in route's color.
- [x] AC 7: Given a public route in collection, when clicking GPX button, then file downloads as `application/gpx+xml`.
- [x] AC 8: Given a private route in public collection (non-owner viewing), then listed as "Route privée", no geometry, no GPX link.
- [x] AC 9: Given owner POSTing `{icon: "danger", text: "Portage 50m", lat: 43.65, lon: 3.87}`, then created and visible in public response.
- [x] AC 10: Given mobile (375px) with 3 routes + 10 annotations, then gzip response <200KB, map renders <3s.

## Additional Context

### Dependencies

- No new external dependencies
- Alembic migration, existing MapLibre/ElevationProfile/COMPARE_COLORS

### Testing Strategy

- **pytest:** Visibility, annotation CRUD, icon validation, stats, position, limits, author checks, updated_at
- **Playwright:** Full flow create → share → view → interact with map/POIs/elevation/GPX
- **Manual:** Mobile test on real device

### Notes

- **Reviews addressed (R1 + R2):**
  - Public router at `/collections` separate from `/me/collections`
  - CHECK constraint + Pydantic Literal for data integrity
  - `visibility_enum` reuse, 404 not 403, batch queries, `ST_GeomFromGeoJSON` wrapping
  - Query params for static export, COMPARE_DASH for 5+ route disambiguation
  - author_id set/checked, collection size limits, updated_at touch, compare endpoint fix
- **Deferred to v1.1:**
  - `difficulty`, `image_url`, visibility cascade warning, discover page, OG meta tags, stitched elevation

## Review Notes
- Adversarial review completed (2026-03-25)
- Findings: 15 total, 7 fixed, 5 deferred (MVP-acceptable), 2 noise
- Resolution approach: auto-fix
- Critical fix: annotation query scoped to collection_id only (no cross-collection leak)
- High fix: route auth check on add-to-collection (private routes rejected for non-owners)
- Medium fixes: downgrade migration safety, _touch_updated_at consistency, Pydantic max_length, owner_id removed from public response
