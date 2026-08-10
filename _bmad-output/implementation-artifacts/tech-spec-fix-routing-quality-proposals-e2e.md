---
title: 'Routing Quality, Method Detection, Proposals & E2E Coverage'
slug: 'fix-routing-quality-proposals-e2e'
created: '2026-03-14'
updated: '2026-03-18'
status: 'completed'
tech_stack: ['Next.js 16.1.6', 'TypeScript 5.5.2', 'MapLibre GL 4.3.2', 'FastAPI', 'PostgreSQL 17 + PostGIS', 'Playwright 1.51.1']
files_modified:
  - frontend/app/map/page.tsx
  - frontend/lib/client-graph.ts
  - frontend/lib/routing-worker.ts
  - frontend/lib/routing-state.ts
  - backend/app/services/routing.py
  - e2e/tests/routing-quality-montpellier.spec.ts
  - e2e/tests/client-routing.spec.ts
---

# Routing Quality, Method Detection, Proposals & E2E Coverage

**Created:** 2026-03-14 | **Updated:** 2026-03-18 (post-implementation reconciliation)

## Status: COMPLETED

All root causes fixed, all acceptance criteria met. This document now serves as **architecture reference** for the routing subsystem.

---

## Architecture Overview

### Routing Cascade (client → server → external → straight line)

1. **Client-side A\* routing** (<15ms) — Web Worker with full graph preload
2. **Server smart routing** — In-memory Dijkstra with heatmap/DFCI/trails
3. **BRouter** (sport-specific profiles) — only when <2 heatmap proposals exist
4. **OSRM** (generic road/foot) — only when <2 total non-fallback proposals exist
5. **Straight line** — last resort

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Full graph preload (not per-tile) | Eliminates tile race conditions entirely (was RC1a/RC1b) |
| `detectedMethod` separate from `method` | 15+ call sites compare `method === 'client_graph'` — changing it breaks routing logic |
| Bridge edge exclusion from method detection | Synthetic connectors dilute trail/heatmap ratios |
| 30% threshold for method classification | Off-road routes mix trail with short OSM connectors — 30% trail distance is significant |
| BRouter/OSRM threshold `< 2` (not `< 3`) | Off-road routing prioritizes local trail knowledge over generic road routers |
| Segment preservation on recalculation | Good segments kept when sport change would produce worse (straight-line) results |

---

## Current Implementation Reference

### Client-Side Routing

#### Web Worker Architecture (`frontend/lib/routing-worker.ts`)

The routing engine runs in a Web Worker to avoid blocking the UI:

- **`worker.reinit(sport)`** — Recreates the graph with new sport profile
- **`worker.loadFullGraph(sport, apiUrl)`** — Fetches complete graph from `GET /routing/graph/{sport}/full.json`
- **`worker.route(from, to)`** — Runs A\* in the worker, returns `ClientRouteResult`
- **Heartbeat monitoring** with auto-fallback to main thread if worker becomes unresponsive

#### Client Graph (`frontend/lib/client-graph.ts`)

**`clientRoute()`** (line ~800-903):
- A\* pathfinding with sport-aware cost model
- Returns `ClientRouteResult`:
  ```typescript
  interface ClientRouteResult {
    coords: [number, number][];
    method: string;           // always 'client_graph' (for routing logic)
    detectedMethod: string;   // 'community_heatmap' | 'dfci_trails' | 'marked_trails' | 'ev_trails' | 'client_graph'
    isClientRouted: boolean;  // always true
    distanceM: number;
    offHeatmap?: { from: boolean; to: boolean };
  }
  ```

**Method detection** (line ~746-762):
- Walk path edges, classify by **distance-weighted** category
- Exclude bridge edges (`isBridgeEdge()` at line ~1243-1246: `surfaceIdx === 4 && highwayIdx === 9 && trailIdx === 0 && userCount === 0`)
- Trail edges (`trailIdx > 0`) classified by type: DFCI, GR/GT/PR (marked), EV
- Heatmap edges (`userCount > 0 && trailIdx === 0`)
- Thresholds: trail > 30% → trail type, heatmap > 30% → community_heatmap, else → client_graph

**`ensureTilesLoaded()`** (line ~439-465):
- 25 tile cap — drops furthest tiles from corridor center when exceeded
- Logs warning when capping

**Bridge edge detection** (`isBridgeEdge()` at line ~1243-1246):
- Created by `bridgeNearbyVertices()` (line ~304-362) with `BRIDGE_COST_FACTOR = 1.2`
- Excluded from method classification and GeoJSON overlay rendering

#### Graph Lifecycle (`frontend/app/map/page.tsx`)

**Sport change flow** (line ~1831-1963):
- `routeSport` deliberately excluded from useEffect deps
- Graph recreation handled sequentially inside the recalc effect:
  1. `worker.reinit(routeSport)` — create fresh graph
  2. `worker.loadFullGraph(routeSport, API_URL)` — load complete graph
  3. Only then run routing with the loaded graph
- AbortController prevents stale results from rapid sport toggling

**Segment preservation** (`frontend/lib/routing-state.ts` line ~69-87):
- `shouldPreserveSegment()` keeps good geometry when recalculation produces worse results (straight lines)
- Prevents losing quality segments on sport change or map pan

#### Routing Function (`fetchSmartSegmentWithClientRouting`)

**Location:** `frontend/app/map/page.tsx` line ~686-703

**Usage across the codebase:**
- Sport recalculation (line ~2026)
- Local corridor re-route around new waypoint (line ~3153)
- Fallback full re-route for short segments (line ~3209)
- Full recalc after moveend (line ~4497)
- Add new waypoint (line ~4643)
- Waypoint drag live preview (line ~4946)
- Waypoint deletion merging (lines ~5298, ~5344)

**Returns:** `SmartSegmentResult` with `method`, `coords`, `qualityScore`, `warnings`, `heatUsedRatio`, `offHeatmap`

### Method Badge UI

**Badge rendering** (`frontend/app/map/page.tsx` line ~6313-6332):

| Method | Label | Color |
|--------|-------|-------|
| `client_graph` | OSM | #0891b2 |
| `community_heatmap` | Heatmap | #2d6a4f |
| `personal_traces` | Mes traces | #8e44ad |
| `dfci_trails` | 🔥 DFCI | #d97706 |
| `marked_trails` | 🥾 Sentier balisé | #059669 |
| `ev_trails` | 🚴 EuroVelo | #2563eb |
| `brouter` | BRouter | #2563eb |
| `osrm` | OSRM | #b45309 |

**Display logic:** Uses `routeDetectedMethod || routeMethod` — detected method takes priority for badge.

**Detail panel** (line ~7346-7465): French prose explanation per method, data source, license, methodology.

**Method priority for multi-segment routes** (line ~448-451):
```typescript
community_heatmap: 4, personal_traces: 3, dfci_trails: 3,
marked_trails: 3, brouter: 2, osrm: 1
```

### Multi-Proposal UI

**State** (line ~1243-1246):
- `proposalPending`, `proposals`, `proposalHoverIdx`, `constrainedCorridor`

**Bottom sheet** (line ~7909-8080+):
- Fixed position, max-height 35vh
- Card grid (160px min-width, flex wrapping)
- Labels: "Popularité", "Direct", "Explorateur", "Surface"
- Hover effects with index tracking
- ESC key dismissal
- All-fallback badge when no good routes found

### Backend Proposals

**File:** `backend/app/services/routing.py`

**Proposal diversity** (line ~2414-3289):
- 3 Dijkstra profile variants: shortest, balanced, explorer
- Corridor penalties prevent route overlap
- Similarity detection + OSRM diversity pass

**External router thresholds:**
- BRouter fallback (line ~2807): `len(non_fb) < 2 and _external_ok`
- OSRM fallback (line ~2872): `len(non_fb_osrm_fb) < 2 and _external_ok`
- Hybrid routing only for routes > 20km (`_direct_km > 20`)

**Cold-start detection** (line ~3283-3289): All-external flag when no heatmap proposals produced.

---

## Root Causes (Historical — All Fixed)

| RC | Problem | Fix | Status |
|----|---------|-----|--------|
| RC1a | Sport change race condition — graph and routing in separate useEffects | Merged into single sequential effect, routeSport excluded from deps | ✅ Fixed |
| RC1b | First-route tile timing — waypoints before tiles loaded | Full graph preload eliminates the issue; corridor retry with 2× bbox as fallback | ✅ Fixed |
| RC2 | Corridor bbox too tight for short segments | Fixed 5km corridor (`corridor_km: 5.0`), 25-tile cap in ensureTilesLoaded | ✅ Fixed |
| RC3 | Badge shows "Client" instead of data source | `detectedMethod` field with distance-weighted edge classification | ✅ Fixed |
| RC4 | BRouter/OSRM called eagerly (2-5s latency) | Threshold changed from `< 3` to `< 2` on both BRouter and OSRM | ✅ Fixed |

---

## E2E Test Coverage

### routing-quality-montpellier.spec.ts

11 tests covering 3 geographic areas + proposal behavior:

| Test | Area | Sport | Validates |
|------|------|-------|-----------|
| N1 | Saint-Mathieu → Pic Saint-Loup | offroad | DFCI/GR trail usage |
| N2 | Claret → Valflaunès | mtb | Heatmap edge preference |
| N3 | Les Matelles → St-Martin-de-Londres | gravel | Trail usage |
| N4 | Garrigues heatmap belt | offroad | Known coverage zone |
| C1-C3 | Montpellier garrigue belt | mixed | Central fixture coverage |
| E1-E2 | Cévennes foothills | offroad | Edge of fixture |
| P1 | Proposal density | offroad | Coverage area produces proposals |
| P2 | Proposal response time | offroad | Response < 8s |
| Segment | Multi-waypoint | offroad | First segment preserved when adding 3rd waypoint |

### client-routing.spec.ts

9 tests covering core routing mechanics:

- Full graph fetch on route mode entry
- Server fallback on empty tiles
- Tile loading indicator UI
- Routable network overlay
- Waypoint drag + recalculation
- 3-waypoint middle drag
- Drag preview visual updates
- Steep grade warnings
- Off-heatmap waypoint orange dashed line

### routing-coherence.spec.ts

Additional coherence tests for edge cases.

---

## Acceptance Criteria (All Met)

- [x] AC1: Sport change uses correct profile (not stale graph)
- [x] AC2: Routes follow heatmap traces near Cabrière
- [x] AC3: Adding 3rd waypoint preserves first segment geometry
- [x] AC4: Heatmap routes show "Heatmap" badge
- [x] AC5: DFCI routes show "🔥 DFCI" badge
- [x] AC6: GR/GT routes show "🥾 Sentier balisé" badge
- [x] AC7: Proposals with ≥2 heatmap results skip BRouter
- [x] AC8: Proposals with 0 heatmap results still call BRouter
- [x] AC9: ≥8/10 Montpellier tests show method ≠ brouter/osrm
- [x] AC10: `npm run build` succeeds
- [x] AC11: Low zoom (<11) gracefully falls back to server

---

## Post-Spec Enhancements (added after initial implementation)

| Feature | Commit | Description |
|---------|--------|-------------|
| Segment preservation | 3c6f51a | `shouldPreserveSegment()` keeps good geometry on recalculation |
| Activity limit 5000 | 3c6f51a | Users with 1000+ Strava traces see all activities |
| Smart route naming | a98a1af | Reverse geocode start point → "{Sport} — départ {place}" |
| Route inline editing | a98a1af | contentEditable name + PATCH on blur in detail panel |
| Surface/elevation stats | ba3e573 | Compact stats bar with D-/surface summary |
| Variant toggles | 6b245b7 | Toggleable variant overlays with distinct dashed lines |
| Panoramax modal | 6b245b7 | In-app photo viewer with surface/altitude context |

---

## Code Review Notes (Historical)

Adversarial code review completed with 8 findings:
- 5 real (3 fixed, 2 acknowledged as intentional)
- 1 noise, 1 undecided (mitigated), 1 accepted tradeoff
- Key fixes: inverse-Mercator tile-to-lat formula, `setRouteDetectedMethod(null)` on clear/load, catch-all for unknown methods
