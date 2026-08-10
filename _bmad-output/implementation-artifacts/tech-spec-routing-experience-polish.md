---
title: 'Routing Experience Polish — Proposals, Feedback, Snap & Drag UX'
slug: 'routing-experience-polish'
created: '2026-03-18'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['Next.js 16.1.6', 'TypeScript 5.5.2', 'MapLibre GL 4.3.2', 'FastAPI', 'PostgreSQL 17 + PostGIS', 'Playwright 1.51.1']
files_to_modify: ['frontend/app/map/page.tsx', 'frontend/lib/client-graph.ts', 'frontend/lib/routing-state.ts', 'frontend/lib/snap-to-road.ts', 'frontend/lib/routing-style.ts', 'frontend/lib/routing-worker.ts', 'frontend/lib/routing-worker-types.ts', 'frontend/lib/routing-worker-client.ts', 'frontend/components/ElevationProfile.tsx', 'backend/app/services/routing.py', 'backend/app/services/routing_types.py', 'e2e/tests/client-routing.spec.ts', 'e2e/tests/routing-quality-montpellier.spec.ts']
code_patterns: ['fetchSmartSegmentWithClientRouting cascade', 'shouldPreserveSegment BAD_METHODS', 'clientRoute detectedMethod', 'createAsyncDragRouter throttle+inflight', 'proposal diversity SIMILARITY_THRESHOLD', 'WAYPOINT_FILL circle-color', 'VIP_SNAP_BIAS snapToRoad', 'computeIntentStrength cap', 'isBridgeEdge detection', 'ProposalResult dataclass with surface_pct+elevation_gain_m']
test_patterns: ['Playwright E2E with heatmap fixture', 'registerAndLogin() helper', 'enterRouteMode() helper', 'routing-quality-montpellier geographic tests', 'off-heatmap-waypoint orange dashed line test']
---

# Tech-Spec: Routing Experience Polish — Proposals, Feedback, Snap & Drag UX

**Created:** 2026-03-18

## Overview

### Problem Statement

After fixing core routing quality (race conditions, method detection, badge rendering, BRouter suppression), several UX friction points remain that make the routing experience feel opaque rather than transparent:

1. **Proposals lack differentiation** — Quality scores shown but no explanation of *why* one proposal differs (surface%, elevation, heatmap usage). Similarity threshold (0.45) may reject genuinely diverse routes. BRouter fetches all 3 alternates even when the first is sufficient.

2. **Silent segment preservation** — When sport change produces worse results, old geometry is silently kept. No visual indicator. Worse: preservation logic doesn't check if the old segment was itself a fallback, AND doesn't check if old segment's sport matches the new sport (road segment preserved for MTB is wrong).

3. **Off-heatmap feels like failure** — Warning message blames "no community data" instead of framing as exploration opportunity. Snap radius is invisible — users can't understand why 501m drag becomes straight-line.

4. **Routing feedback gaps** — Loading messages don't distinguish tile load vs route calc vs server request. Dijkstra 200k iteration timeout is silent. "Add waypoint" suggestion appears too early (after 8s) and confuses users who just added one.

5. **Drag preview lag** — `inflight` flag blocks stale preview during fast drags, causing visual stutter. Route flickers between old and new position instead of smooth transition.

### Solution

Improve routing transparency and responsiveness across 4 axes, shipped in 3 independent phases. Design principle: **cyclists plan routes to have a good ride, not to operate software** — every interaction that isn't "show me where to ride" is friction.

1. **Phase 1 — Snap + Drag** (lowest risk, highest impact): Waypoint marker color-coding for snap state, Shift+drag precision mode, smooth drag preview, intent cap, confidence line style for uncertain segments.
2. **Phase 2 — Proposals** (medium risk, high impact): All proposals on map with distinct colors, factual labels showing non-obvious differentiators (D+, surface%), elevation profile swap on hover.
3. **Phase 3 — Feedback + Preservation** (medium risk, medium impact): Elapsed counter on spinner, segment preservation fix with subtle orange tint, off-heatmap reframing, E2E tests.

### Scope

**In Scope:**

#### Phase 1 — Snap + Drag (isolated to client-graph.ts + snap-to-road.ts + page.tsx drag handlers)
- **Waypoint marker color for snap state**: change the waypoint dot itself — green (snap ≤500m), orange (extended snap 500-2000m), red (no snap). No separate circle layer — information on the thing the user is looking at.
- **Shift+drag precision mode**: `maxSnapM: 50`, `vipBias: 1.0`, crosshair cursor. Falls back to off-heatmap if no vertex within 50m.
- **Drag preview fix**: stale route solid (opacity 1.0) + new preview semi-transparent (opacity 0.4, dashed). Non-blocking inflight requests.
- **Intent strength cap** (max 0.5 to prevent pure-distance routing on short segments).
- **Confidence line style**: dashed line on map for bridged/uncertain segments (solid = known edges, dashed = bridged). Visual, spatial, actionable — replaces bridge % number in panel.

#### Phase 2 — Proposals (contained to proposal UI in page.tsx)
- **All 3 proposals rendered on map** simultaneously with distinct colors (reuse `COMPARE_COLORS`).
- **Cards as legends**: hover card → bold that line + dim others (50% opacity). Click card OR click map line → select. Both flows work.
- **Factual differentiator labels**: show what the map CAN'T show — "450m D+" vs "320m D+", "65% gravel" vs "90% asphalte". Drop generic labels ("Plus direct" — the shortest line on map is obviously direct). Eliminate quality score number.
- **Elevation profile swaps** on proposal card hover (reuse existing ElevationProfile component).
- **Surface overlay** shown on each proposal line (reuse existing surface color logic).
- **Smarter BRouter/OSRM fetching** (sequential with early-exit).

#### Phase 3 — Feedback + Preservation (page.tsx + routing-state.ts + E2E)
- **Elapsed counter on spinner**: appears after 2s ("Calcul... 3s"). After 10s: "Itinéraire complexe, patientez...". After 20s: offer server fallback. No phase icons, no progressive rendering — simple, 5 lines of code.
- **Segment preservation fix**: don't preserve road segments for off-road sport, don't preserve fallbacks. Always auto-recalculate on sport change (no confirmation prompt — cyclist wants a different route, answer is always "yes"). Preserved segments shown with subtle **orange tint** (not lock icon + prompt). Cyclist sees it and can manually drag to fix, or ignore.
- **Off-heatmap reframing**: positive reinforcement on known trails ("Sentier fréquenté par N cyclistes"), explorer CTA for unknown zones ("Nouvelle zone — votre trace enrichira la communauté").
- **E2E tests**: proposal diversity, rapid sport toggle, proposal selection, segment preservation on downgrade.

**Out of Scope:**
- New routing algorithms (contraction hierarchies)
- IndexedDB / offline tile cache
- New sport profiles or cost model changes
- Backend routing service architecture refactor
- Mobile touch gestures (Shift+drag is desktop-only)
- Winter trail closure data (requires external data source)
- Proposal segment mixing ("take segment A from proposal 1 + segment B from proposal 3") — future iteration
- Animated route morphing during drag (smooth rubber band) — future iteration
- Progressive rendering / ghost line — eliminated per First Principles (simple spinner + counter is sufficient for <10% slow case)
- Phase icons (📡→🧮→✨) — eliminated per First Principles (cyclist doesn't care which phase is running)
- Sport change confirmation prompt — eliminated per First Principles (answer is always "yes, recalculate")
- Lock icon on preserved segments — eliminated per First Principles (orange tint is zero-interaction, lock icon adds a prompt)
- Snap radius circle overlay — eliminated per First Principles (waypoint marker color is less visual noise, same info)

## Context for Development

### User Persona Insights (Focus Group)

| Persona | Key Need | Quote |
|---------|----------|-------|
| Marc (road, 45) | Elevation gain in proposals, precision | "Show me elevation gain — that's what decides my ride" |
| Léa (gravel, 32) | Surface % in proposals, exploration framing | "Surface percentage is HUGE for me... I pick blindly and discover mid-ride I'm on asphalt" |
| Thibaut (MTB, 28) | Trail type over heatmap %, exact waypoint placement | "The VIP bias drives me crazy. I want to put a waypoint on an exact rock feature" |
| Sylvie (e-bike, 58) | Plain French labels, reassuring loading, safety framing | "Three routes with numbers means nothing to me. Can you just say 'Plus facile'?" |

### SCAMPER Insights Applied

| Lens | Insight | Applied |
|------|---------|---------|
| **Combine** | Snap state communicated via waypoint marker color, not separate circle | Snap UX (simplified by First Principles) |
| **Adapt** | Elevation profile swaps on proposal hover (like Strava effort comparison) | Proposal comparison |
| **Put to Other Use** | Existing surface overlay rendered on each proposal line | Proposal differentiation |
| **Eliminate** | Quality score number removed — factual labels are sufficient | Proposal cards |
| **Eliminate** | "Add waypoint" suggestion removed — replaced with reassuring counter only | Loading feedback |
| **Reverse** | All proposals shown on map simultaneously — tap to select (map-first) | Proposal flow |
| **Reverse** | Positive reinforcement on known trails replaces negative off-heatmap warning | Off-heatmap framing |

### Cross-Functional War Room Decisions

| # | Topic | Decision | Rationale |
|---|-------|----------|-----------|
| 1 | Map-first proposals | Keep cards as legends + render all 3 lines on map. Hover = bold/dim. Click card OR line = select. | Backward-compatible: both flows work. |
| 2 | Snap feedback | Waypoint marker color (green/orange/red) instead of circle layer. | Less visual noise. Info on what user looks at. |
| 3 | Shift+drag | `maxSnapM: 50`, `vipBias: 1.0`, crosshair cursor. | Power users get precision. |
| 4 | Shipping strategy | 3 phases, each independently shippable. Phase 1 = lowest risk + highest impact. | Isolated PRs reduce blast radius. |

### First Principles Simplifications

| Original | Simplified To | Rationale |
|----------|--------------|-----------|
| 500m snap radius circle layer | Waypoint marker color (green/orange/red) | Map is the interface — info on what user looks at, not a separate overlay |
| Sport change confirmation prompt | Auto-recalculate + orange tint on preserved segments | Cyclist always wants recalculation — prompt is friction |
| 🔒 lock icon + click to force recalc | Orange tint (zero-interaction visual cue) | Lock adds a decision where there shouldn't be one |
| Phase icons 📡→🧮→✨ | Spinner + elapsed counter after 2s | Cyclist doesn't care which phase is running |
| Progressive rendering (ghost → refined) | Spinner + elapsed counter + message at 10s/20s | Complex system for <10% of cases; 5 lines of code achieve same emotional outcome |
| Generic labels ("Plus plat", "Plus direct") | Factual non-obvious differentiators ("450m D+", "65% gravel") | Map lines already show spatial differences — labels should show what map CAN'T |
| Bridge % in stats panel | Dashed line on map for uncertain segments | Visual, spatial, actionable — replaces number without action |

### Codebase Patterns (with exact anchors)

#### Phase 1 — Snap + Drag

- **Waypoint marker color**: `WAYPOINT_FILL = '#e11d48'` at `routing-style.ts:78`. Used in map layer at `page.tsx:2894` (`'circle-color': WAYPOINT_FILL`). Change: set color dynamically based on snap result (green/orange/red).
- **VIP snap bias**: `VIP_SNAP_BIAS = 4.0` at `client-graph.ts:492`, applied at lines 543, 557. `snapToRoad()` at line 517 takes `maxM = 200` param. For Shift+drag: add `vipBias` param, caller passes `1.0`.
- **Drag handlers**: `onWpMousedown` + `onWpMousemove` at `page.tsx:4828-4876`. Currently calls `worker.snap(x, y, 300)`. Shift key detection: `e.originalEvent.shiftKey`.
- **Drag preview**: `createAsyncDragRouter()` at `snap-to-road.ts:121-187`. 50ms throttle + `inflight` boolean. **Bug**: `if ((now - lastCall < throttleMs || inflight) && lastResult)` returns stale result when inflight=true — causes visual stutter. Fix: remove inflight blocking, let new requests supersede old.
- **Intent strength**: `computeIntentStrength()` at `page.tsx:394-412`. Caps at `Math.min(1.0, ...)` — change to `Math.min(0.5, ...)`.
- **Bridge detection**: `isBridgeEdge()` at `client-graph.ts:1244-1246` (`surfaceIdx === 4 && highwayIdx === 9 && trailIdx === 0 && userCount === 0`). Currently only used for method detection exclusion. **Gap**: bridge info NOT returned per-segment in route results → needs addition for confidence line style (dashed on bridged segments).

#### Phase 2 — Proposals

- **Proposal layers**: `page.tsx:2757-2835` — 3 source+layer pairs with double-casing structure (outline + fill). Currently only visible on hover via paint property updates.
- **Proposal hover**: `page.tsx:4100-4162` — bold/dim paint property updates. Already sets opacity on hover. Extend: show all 3 simultaneously, dim non-hovered to 50%.
- **Proposal cards**: `page.tsx:7978-8051` — already show elevation gain, surface bar, delta values. **Discovery**: labels like "Popularité", "Direct", "Explorateur" are generic → replace with factual differentiators from metadata.
- **ElevationProfile**: `ElevationProfile.tsx:5-9` — props `{ coords: number[][], width?, height? }`. Expects 3D `[lon, lat, elevation]`. Reuse: pass proposal coords on hover.
- **ProposalResult dataclass**: `routing_types.py:142-246` — already has `elevation_gain_m`, `surface_pct`, `surface_segments`, `delta_distance_m`, `delta_elevation_m`. Backend data is ready.
- **BRouter parallel fetch**: `routing.py:2815-2870` — `ThreadPoolExecutor(max_workers=3)`, `len(non_fb) < 2` threshold. Optimize: sequential with early-exit after first good result.
- **Proposal colors**: `routing-style.ts:100-135` — `PROPOSAL_POPULARITY`, `PROPOSAL_DIRECT`, `PROPOSAL_EXPLORER` already defined.

#### Phase 3 — Feedback + Preservation

- **Loading messages**: `page.tsx:1903-1925` — tiered at 0/3/8/15s with 500ms update interval. **Issue**: "Ajouter un waypoint intermédiaire" suggestion at 8s confuses users. Replace with elapsed counter + reassuring message.
- **shouldPreserveSegment**: `routing-state.ts:69-87` — checks `BAD_METHODS` (`straight_line`, `no_route`, `fallback`). **Gaps**: (1) doesn't check if old segment was itself a fallback, (2) doesn't check if old segment's sport matches new sport. Fix both.
- **Sport change recalc**: `page.tsx:1988-2005` — calls shouldPreserveSegment. Currently preserves silently. Add: orange tint via line-color override on preserved segment GeoJSON.
- **Off-heatmap layer**: `page.tsx:2696-2712` — `#f59e0b` orange, dashed `[6,4]`. Off-heatmap detection at lines 706-720 (`cr.offHeatmap` check).
- **Route draft GeoJSON**: `page.tsx:4036-4050` — single feature via `flattenSegments()`. For confidence line style, need per-segment features with bridge ratio property.
- **E2E test patterns**: `client-routing.spec.ts` — `registerAndLogin()` lines 17-28, `enterRouteMode()` lines 67-76, off-heatmap test lines 586-662. Add new tests for proposal diversity, rapid sport toggle, preservation on downgrade.

### Files to Reference (with line anchors)

| File | Purpose | Key Lines |
| ---- | ------- | --------- |
| `frontend/app/map/page.tsx` | Main routing orchestration | drag:4828-4876, proposals:2757-2835, loading:1903-1925, preservation:1988-2005, cards:7978-8051, intent:394-412, off-heatmap:706-720, waypoint-fill:2894 |
| `frontend/lib/client-graph.ts` | A*/Dijkstra, snap, bridging | snapToRoad:517, VIP_SNAP_BIAS:492, clientRoute:800-903, isBridgeEdge:1244-1246 |
| `frontend/lib/routing-state.ts` | Segment preservation | shouldPreserveSegment:69-87, SegmentMeta:8-14, BAD_METHODS:64-67 |
| `frontend/lib/snap-to-road.ts` | Drag router | createAsyncDragRouter:121-187, throttle+inflight bug:143-146 |
| `frontend/lib/routing-style.ts` | Colors + constants | WAYPOINT_FILL:78, proposal colors:100-135, surface colors:92-97 |
| `frontend/components/ElevationProfile.tsx` | SVG elevation profile | props:5-9, handles missing elevation |
| `backend/app/services/routing.py` | Proposal generation | BRouter:2815-2870, OSRM:2893-2947, early-exit:2860 |
| `backend/app/services/routing_types.py` | Proposal data model | ProposalResult:142-246 (elevation_gain_m, surface_pct, delta_*) |
| `e2e/tests/client-routing.spec.ts` | Client routing E2E | registerAndLogin:17-28, enterRouteMode:67-76, off-heatmap:586-662 |
| `e2e/tests/routing-quality-montpellier.spec.ts` | Geographic quality | 11 tests, 3 areas, proposal tests P1-P2 |

### Key Investigation Findings

1. **Proposal cards already have metadata** — `elevation_gain_m`, `surface_pct`, `delta_*` are computed in backend `ProposalResult` and rendered in cards. Work is relabeling, not data plumbing.
2. **Proposal layers already exist** — 3 source+layer pairs with double-casing. Currently show/hide on hover. Work is "show all, dim on hover" not "create layers".
3. **Bridge info gap** — `isBridgeEdge()` exists but bridge ratio is NOT included in route result coords/segments. Need to annotate per-segment GeoJSON with `bridgeRatio` for confidence line style.
4. **Inflight bug in drag router** — `createAsyncDragRouter` blocks new previews while inflight, returning stale results. This is the root cause of drag stutter. Fix: let new requests supersede (cancel previous on new drag event).
5. **Loading messages already tiered** — 0/3/8/15s thresholds exist. The 8s "add waypoint" suggestion is the pain point. Simple message replacement, not architecture change.
6. **shouldPreserveSegment has two gaps** — no sport check, no fallback-of-fallback check. Both are 2-3 line additions to the existing function.

### Technical Decisions

- Preserve `method` field unchanged for routing logic; use `detectedMethod` for display (established pattern)
- Proposal metadata added to existing `RouteProposal` schema, no new API endpoints
- All proposals rendered as MapLibre line layers with distinct colors (reuse `COMPARE_COLORS`)
- Waypoint marker color driven by snap result: green (#22c55e) / orange (#f59e0b) / red (#ef4444) — set via marker element style, no extra layer
- Shift+drag detected via `e.originalEvent.shiftKey`. Sets `maxSnapM: 50`, `vipBias: 1.0`. Crosshair cursor via `map.getCanvas().style.cursor = 'crosshair'`
- Intent strength cap is a constant change, no UI toggle needed
- Preserved segments: orange tint via line-color override on that segment's GeoJSON feature. No lock icon, no prompt. Cyclist can drag to fix manually.
- Auto-recalculate on sport change (always). Preservation logic only triggers when new result is worse — cyclist never prompted.
- Off-heatmap messaging: waypoint marker color for immediate feedback, detail panel text for post-route context
- Proposal labels: factual differentiators computed from proposal metadata delta (e.g., min elevation = show "320m D+", max gravel = show "65% gravel"). No generic labels.
- Confidence line style: solid line = known edges, dashed line = bridged segments. Set via `line-dasharray` on per-segment GeoJSON features where bridge ratio > 30%.
- Loading: existing spinner + `setInterval` elapsed counter after 2s. Message at 10s ("Itinéraire complexe..."), offer at 20s ("Essayer le routage serveur ?"). ~5 lines of code.
- Drag preview: old route at opacity 1.0, new preview at opacity 0.4 with dashed line. Non-blocking inflight.
- Elevation profile on proposal hover: pass proposal coords to existing `ElevationProfile` component, swap via state.
- Proposal map interaction: hover card → bold line + dim others (50% opacity). Click card OR click line → select.

## Implementation Plan

### Phase 1 — Snap + Drag (PR #1)

- [ ] Task 1: Waypoint marker dynamic color based on snap state
  - File: `frontend/lib/routing-style.ts`
  - Action: Export 3 snap-state colors: `SNAP_COLOR_OK = '#22c55e'` (green, ≤500m), `SNAP_COLOR_EXTENDED = '#f59e0b'` (orange, 500-2000m), `SNAP_COLOR_NONE = '#ef4444'` (red, no snap). Keep `WAYPOINT_FILL` as default.
  - File: `frontend/lib/client-graph.ts`
  - Action: `snapToRoad()` (line 517) already returns `SnapResult` (lines 481-486) which includes `distM: number` — the snap distance. No change needed to the return type. The caller uses `result.distM` to determine snap color.
  - File: `frontend/app/map/page.tsx`
  - Action: At line ~2894 (`'circle-color': WAYPOINT_FILL`), replace static fill with a data-driven expression: `['get', 'snapColor']` from GeoJSON feature properties. When building waypoint GeoJSON features, set `snapColor` property based on `snapDistanceM`: ≤500 → green, ≤2000 → orange, else → red.
  - Notes: Waypoint GeoJSON is rebuilt on every drag move (line ~4850). The `snapDistanceM` is available from the snap result. No new layer needed.

- [ ] Task 2: Shift+drag precision mode
  - File: `frontend/lib/client-graph.ts`
  - Action: Add optional `vipBias` parameter to `snapToRoad()` (line 517): `snapToRoad(graph, lon, lat, maxM = 200, vipBias = VIP_SNAP_BIAS)`. Pass through to candidate scoring at lines 543, 557 instead of hardcoded `VIP_SNAP_BIAS`.
  - File: `frontend/lib/routing-worker-types.ts`
  - Action: Extend the `snap` message type (line 14: `{ type: 'snap'; id: number; lon: number; lat: number; maxM?: number }`) to also include `vipBias?: number`.
  - File: `frontend/lib/routing-worker.ts`
  - Action: In the `snap` message handler (line ~79), pass `msg.vipBias` through to `snapToRoad()` as the new parameter.
  - File: `frontend/lib/routing-worker-client.ts`
  - Action: Extend the `snap()` proxy method (line ~402) to accept and forward `{ maxM, vipBias }` params in the worker message.
  - File: `frontend/app/map/page.tsx`
  - Action: In `onWpMousemove` handler (line ~4840-4876), detect `e.originalEvent.shiftKey`. When Shift held: call `worker.snap(x, y, 50, 1.0)` (maxM=50, vipBias=1.0) and set `map.getCanvas().style.cursor = 'crosshair'`. When Shift released: restore `cursor = 'grab'` and use default snap radius/bias.
  - Notes: 4 files need changes for Shift+drag (types → worker → client proxy → page handler). Falls back to off-heatmap (straight line) if no vertex within 50m — same as existing off-heatmap behavior. Desktop-only feature.

- [ ] Task 3: Fix drag preview stutter (non-blocking inflight)
  - File: `frontend/lib/snap-to-road.ts`
  - Action: In `createAsyncDragRouter()` (line 121-187), replace `inflight` boolean with a monotonically increasing `requestId` counter. Each drag event increments `requestId`. When the async route resolves, check `if (thisRequestId === requestId)` before applying result. This makes old inflight results silently discarded instead of blocking new previews.
  - File: `frontend/app/map/page.tsx`
  - Action: At the drag preview rendering site (route draft GeoJSON, line ~4036-4050), render two layers: (1) current committed route at opacity 1.0 solid, (2) drag preview at opacity 0.4 dashed (`line-dasharray: [8, 4]`). On drag end, promote preview to committed.
  - Notes: Key behavior change: user sees stable old route while new one computes, then smooth swap. No more flicker.

- [ ] Task 4: Intent strength cap
  - File: `frontend/app/map/page.tsx`
  - Action: At `computeIntentStrength()` (line ~394-412), change the final cap from `Math.min(1.0, ...)` to `Math.min(0.5, ...)`. This prevents short segments from becoming pure distance routing (all cost on distance, none on trail quality).
  - Notes: Single constant change. Affects all routing. If too aggressive, can be tuned later. No UI toggle.

- [ ] Task 5: Confidence line style for bridged segments
  - File: `frontend/lib/client-graph.ts`
  - Action: In `clientRoute()` (line ~800-903), when building the result coords, also build a parallel `bridgeRatios: number[]` array — one entry per segment between waypoints. For each segment, compute `bridgeDistanceM / totalDistanceM` using `isBridgeEdge()` on each path edge. Return as part of `ClientRouteResult`.
  - File: `frontend/app/map/page.tsx`
  - Action: **MapLibre GL 4.3.2 does NOT support data-driven `line-dasharray` expressions** (added in v5.8+). Instead, create a SEPARATE overlay layer `route-draft-bridged` with `line-dasharray: [6, 4]` and a dedicated GeoJSON source `route-draft-bridged-src`. When building route GeoJSON, split: segments with bridgeRatio > 0.3 go into the bridged source (dashed), all others go into the main `route-draft` source (solid). Both layers use the same line styling except dasharray. Update ALL 4 `route-draft` setData call sites (lines ~3080, ~4036, ~4896, ~4906) to also set `route-draft-bridged-src` data in sync. The `route-draft-surface` overlay and `route-draft-hit` hit-area layer continue to use the main `route-draft` source with ALL segments (bridged and non-bridged) for surface overlay sync and click detection.
  - Notes: Two-layer approach avoids MapLibre version upgrade. Visual-only change — routing behavior unchanged. The dashed style communicates "we're less certain about this part" without adding numbers.

### Phase 2 — Proposals (PR #2)

- [ ] Task 6: Show all 3 proposals on map simultaneously
  - File: `frontend/app/map/page.tsx`
  - Action: Proposals are controlled via **data replacement** (setting GeoJSON features on sources), NOT via `visibility` layout property. Currently at the proposal hover handler (line ~4098-4162), only the hovered proposal's source gets populated with coordinates (`src.setData({features: [...]})`), while others get emptied. Change this: when proposals arrive, populate ALL 3 sources with their respective coordinates immediately. On hover card index `i`, change paint properties: set proposal `i` to `line-opacity: 1.0` and all others to `line-opacity: 0.35`. On hover exit, restore all to `1.0`. On click card or click map line → select (dismiss proposals, apply selected route — clear all 3 sources).
  - Notes: Proposal colors already defined in `routing-style.ts:100-135` (`PROPOSAL_POPULARITY`, `PROPOSAL_DIRECT`, `PROPOSAL_EXPLORER`). No new layers needed — existing 3 source+layer pairs just need all data populated simultaneously instead of one at a time.

- [ ] Task 7: Factual differentiator labels on proposal cards
  - File: `frontend/app/map/page.tsx`
  - Action: At proposal card rendering (line ~7978-8051), replace the backend-provided `p.label` (currently "Meilleur communautaire", "Plus direct (heatmap)", "Explorateur") with **computed factual differentiators** from proposal metadata. Logic:
    - Find the proposal with min `elevation_gain_m` → show `"{N}m D+"` as its label
    - Find the proposal with max `surface_pct.gravel + surface_pct.terre` → show `"{N}% gravel"` as its label
    - Find the proposal with max `heat_used_ratio` → show `"{N}% communauté"` as its label
    - If two proposals tie, use the secondary differentiator (e.g., distance delta: `"+{N} km"`)
    - Remove the `qualityScore` display entirely
    - Labels are **computed client-side**, ignoring backend `p.label`
  - File: `backend/app/services/routing_types.py`
  - Action: `ProposalResult` (line 142-246) has `elevation_gain_m`, `surface_pct`. Note: `delta_distance_m` and `delta_elevation_m` are NOT in `ProposalResult` — they are computed at the API layer in `backend/app/api/routing.py` lines ~399-400 and injected into the response. Verify `heat_used_ratio` exists; if missing, add it to `ProposalResult` (computed same as `heatUsedRatio` in segment results).
  - Notes: All data needed for labels is already in the frontend proposal response. The relabeling is a pure frontend change — backend labels are simply ignored.

- [ ] Task 8: Elevation profile swap on proposal card hover
  - File: `frontend/app/map/page.tsx`
  - Action: Add state `proposalElevationCoords: number[][] | null`. On proposal card mouseenter, set to `proposals[i].coords`. On mouseleave, set to `null`. Render `<ElevationProfile coords={proposalElevationCoords} width={320} height={80} />` inside or below the proposal bottom sheet (line ~7909). Only render when `proposalElevationCoords` is non-null.
  - Notes: ElevationProfile component (ElevationProfile.tsx:5-9) already handles 3D coords. Proposals already have coords from backend. Zero new components.

- [ ] Task 9: Smarter BRouter/OSRM fetching (sequential with early-exit)
  - File: `backend/app/services/routing.py`
  - Action: At BRouter fetch (line ~2815-2870), replace `ThreadPoolExecutor(max_workers=3)` parallel fetch with sequential: fetch alternate 0, check if it passes similarity threshold, only fetch alternate 1 if needed, only fetch alternate 2 if needed. Same for OSRM (line ~2893-2947). Keep the `len(non_fb) < 2` threshold — only enter BRouter/OSRM section when insufficient heatmap proposals.
  - Notes: Saves 1-4s on most requests where first BRouter result is sufficient. Fallback behavior unchanged. The existing `_external_ok` gating and sport filtering remain.

### Phase 3 — Feedback + Preservation (PR #3)

- [ ] Task 10: Elapsed counter on routing spinner
  - File: `frontend/app/map/page.tsx`
  - Action: At loading message logic (line ~1903-1925), replace the tiered message system with:
    - 0-2s: existing spinner, no text change
    - 2s+: show `"Calcul... {elapsed}s"` with `setInterval(500)` updating `elapsedS` state
    - 10s+: change to `"Itinéraire complexe, patientez...  {elapsed}s"`
    - 20s+: change to `"Itinéraire complexe ({elapsed}s) — "` + clickable `"Essayer le routage serveur ?"` that triggers server fallback
    - Remove the 15s "Ajouter un waypoint intermédiaire" suggestion entirely (at line ~1918-1919, not 8s as initially thought — the 8s tier shows "Calcul long...")
  - Notes: ~10 lines of code. The `setInterval` clears on routing completion. Server fallback button: `forceServer` does NOT exist as a parameter. Instead, call the existing server-side `fetchSmartSegment()` directly (bypasses client graph) or set `engine` to force server routing. Check exact function signature at line ~686-694 — the cascade function takes `(from, to, sport, token, engine, intentStrength, worker)`; passing `worker = null` or using a separate server-only call path achieves the fallback.

- [ ] Task 11: Fix shouldPreserveSegment gaps
  - File: `frontend/lib/routing-state.ts`
  - Action: Change `shouldPreserveSegment()` signature (line 69-87) to accept **`oldMeta: SegmentMeta`** and **`currentSport: string`** as additional parameters. Add two checks before returning `true`:
    1. **Old segment was itself a fallback**: if `oldMeta.method` is in `BAD_METHODS` (line 80), return `false` (don't preserve a straight line).
    2. **Sport mismatch**: if `oldMeta.sport` differs from `currentSport`, return `false`.
  - Action: Update `SegmentMeta` interface (line 8-14) to include `sport: string`.
  - File: `frontend/app/map/page.tsx`
  - Action: At segment metadata creation sites (wherever `SegmentMeta` is constructed), populate `sport` from current `routeSport`. At `shouldPreserveSegment` call site (line ~1993), pass `segmentMetaRef.current[si + 1]` as `oldMeta` and `routeSport` as `currentSport`. The old meta is already available via `segmentMetaRef`.
  - Notes: Function signature change from `(newMethod, newCoords, existingCoords, fromPt, toPt)` to include `(oldMeta, currentSport, newMethod, newCoords, existingCoords, fromPt, toPt)`. Prevents road segments being preserved for MTB, and prevents fallback segments from being sticky.

- [ ] Task 12: Orange tint on preserved segments
  - File: `frontend/app/map/page.tsx`
  - Action: When `shouldPreserveSegment()` returns `true` and old geometry is kept (line ~1988-2005), mark that segment index as `preserved: true` in state. At route draft GeoJSON construction (line ~4036-4050), set `properties.preserved = true` on the preserved segment's Feature. At the route-draft-line layer paint, add color expression: `['case', ['get', 'preserved'], '#f59e0b', ROUTE_COLOR]` — orange for preserved, normal for others.
  - Notes: Subtle visual cue. Cyclist sees it and can drag to fix, or leave as-is. No prompt, no interaction required.

- [ ] Task 13: Off-heatmap positive reframing
  - File: `frontend/lib/client-graph.ts`
  - Action: In `extractRouteStats()` (called by `clientRoute()`, line ~717), add `maxUserCount: number` to the returned stats object. Compute by iterating path edges and tracking the max `userCount` value encountered. Add `maxUserCount` to the `ClientRouteResult` interface.
  - File: `frontend/lib/routing-worker.ts`
  - Action: Ensure `maxUserCount` is passed through in the worker response message (add to the result type in `routing-worker-types.ts`).
  - File: `frontend/app/map/page.tsx`
  - Action: At off-heatmap detection (line ~706-720), store `maxUserCount` from the routing result. In the route detail panel, when showing route metadata for on-heatmap segments with `maxUserCount > 0`, display: `"Sentier fréquenté par {N} cyclistes"`.
  - Action: For off-heatmap segments, replace the current warning text with positive explorer framing: `"Nouvelle zone — votre trace enrichira la communauté"`. The orange waypoint marker (from Task 1) already provides immediate visual feedback.
  - Notes: Requires data plumbing: `extractRouteStats` → `ClientRouteResult` → worker message → page.tsx. Not just text replacement. The text appears in the route detail/stats panel, not as a modal or toast.

- [ ] Task 14: E2E tests for new behaviors
  - File: `e2e/tests/client-routing.spec.ts`
  - Action: Add 4 new tests inside the existing `test.describe('Client-side routing')` block:
    1. **Proposal diversity**: enter route mode, add 2 waypoints in coverage area, verify 3 proposal sources have non-empty GeoJSON data (check `map.getSource('proposal-popularity')._data.features.length > 0` for each of the 3 proposal sources — proposals use data replacement, not visibility toggle).
    2. **Rapid sport toggle**: enter route mode, add 2 waypoints, toggle sport 3 times rapidly (offroad → road → mtb), verify final route exists and no console errors. Checks AbortController prevents stale results.
    3. **Proposal selection**: add 2 waypoints to trigger proposals, click first proposal card, verify proposals dismissed and route applied (route-draft source has coordinates).
    4. **Segment preservation on downgrade**: add 3 waypoints (good coverage), switch to sport with poor coverage, verify first segment geometry is preserved (coords match within tolerance).
  - File: `e2e/tests/routing-quality-montpellier.spec.ts`
  - Action: Add 1 test:
    5. **Factual labels**: trigger proposals in coverage area, verify proposal cards contain numeric differentiators (regex match for `\d+m D\+` or `\d+%`) instead of generic labels.
  - Notes: All tests use existing `registerAndLogin()` and `enterRouteMode()` helpers. Use Montpellier fixture area for coverage. Tests should be independent and not depend on each other.

## Acceptance Criteria

### Phase 1 — Snap + Drag

- [ ] AC1: Given a waypoint placed within 500m of a graph vertex, when the snap resolves, then the waypoint marker is green (#22c55e).
- [ ] AC2: Given a waypoint placed 500-2000m from the nearest vertex, when the snap resolves, then the waypoint marker is orange (#f59e0b).
- [ ] AC3: Given a waypoint placed >2000m from any vertex, when the snap falls back to straight line, then the waypoint marker is red (#ef4444).
- [ ] AC4: Given the user holds Shift while dragging a waypoint, when the drag is in progress, then the cursor is crosshair and snap radius is 50m with vipBias 1.0.
- [ ] AC5: Given the user is rapidly dragging a waypoint, when a new drag position arrives while a route is computing, then the old route stays visible (solid, opacity 1.0) and the preview appears semi-transparent (opacity 0.4, dashed) without flicker.
- [ ] AC6: Given `computeIntentStrength()` is called for a short segment, when the distance is small, then the returned value never exceeds 0.5.
- [ ] AC7: Given a route segment with >30% bridge edges, when the route renders on the map, then that segment appears as a dashed line (not solid).
- [ ] AC8: Given a route segment with <30% bridge edges, when the route renders, then that segment appears as a solid line.

### Phase 2 — Proposals

- [ ] AC9: Given proposals are computed (2+ waypoints, Alt+click or >15km), when proposals appear, then all 3 proposal lines are visible on the map simultaneously with distinct colors.
- [ ] AC10: Given all 3 proposals are visible, when the user hovers a proposal card, then that proposal's line is bold (opacity 1.0) and the other two dim to 35% opacity.
- [ ] AC11: Given all 3 proposals are visible, when the user clicks a proposal card OR clicks a proposal line on the map, then that proposal is selected as the active route and the proposal UI dismisses.
- [ ] AC12: Given proposals are displayed, when the user reads proposal card labels, then labels show factual numeric differentiators (e.g., "320m D+", "65% gravel") instead of generic text.
- [ ] AC13: Given proposals are displayed, when the user hovers a proposal card, then an elevation profile for that proposal appears below the card sheet.
- [ ] AC14: Given proposals are computed, when BRouter is needed, then alternates are fetched sequentially (not all 3 in parallel) with early-exit when sufficient diversity is reached.

### Phase 3 — Feedback + Preservation

- [ ] AC15: Given routing is in progress for >2 seconds, when the spinner is visible, then an elapsed counter appears ("Calcul... 3s").
- [ ] AC16: Given routing exceeds 10 seconds, when the counter updates, then the message changes to "Itinéraire complexe, patientez...".
- [ ] AC17: Given routing exceeds 20 seconds, when the counter updates, then a clickable "Essayer le routage serveur ?" link appears.
- [ ] AC18: Given routing is in progress, when the 15-second mark passes, then NO "Ajouter un waypoint" suggestion appears (removed).
- [ ] AC19: Given a sport change from MTB to road, when a road segment was previously preserved from a straight-line fallback, then the old fallback is NOT preserved (recalculates fresh).
- [ ] AC20: Given a sport change from road to MTB, when the road segment's sport doesn't match the new sport, then the old segment is NOT preserved (recalculates fresh).
- [ ] AC21: Given a segment is preserved after sport change (new result is worse, old was genuine quality), when the route renders, then the preserved segment has orange tint (#f59e0b).
- [ ] AC22: Given a route segment is on well-known trails, when the route detail panel is open, then it shows "Sentier fréquenté par N cyclistes" (positive framing).
- [ ] AC23: Given a route segment is off-heatmap, when the route detail panel is open, then it shows "Nouvelle zone — votre trace enrichira la communauté" (explorer framing, not warning).
- [ ] AC24: Given all changes are complete, when `npm run build` is run, then the build succeeds with zero TypeScript errors.

## Additional Context

### Dependencies

- No new external libraries required. All features use existing MapLibre GL, React state, and backend routing infrastructure.
- Phase 2 (proposals) depends on the existing `ProposalResult` dataclass — verify `heat_used_ratio` field exists before implementing Task 7.
- Phase 3 E2E tests (Task 14) should be written after Phase 1-3 code changes are complete, but can be developed in parallel using the existing fixture data.

### Testing Strategy

**Automated (E2E — Playwright):**
- 5 new E2E tests in Task 14 covering: proposal visibility, rapid sport toggle, proposal selection, segment preservation, factual labels.
- Existing E2E tests (14 routing-quality + 9 client-routing + 18 routing-coherence + 27 routing-heatmap-fixture + 49 core + 10 trips) must continue to pass — run full suite after each phase.

**Manual testing checklist:**
- Phase 1: drag waypoints in/out of coverage, observe green/orange/red marker. Hold Shift while dragging. Rapidly drag to verify no flicker. Check dashed segments near graph edges.
- Phase 2: trigger proposals (Alt+click or >15km route), verify 3 lines visible, hover/click cards, read labels (should show D+ and surface%), observe elevation profile swap.
- Phase 3: create a slow route (edge of fixture), observe elapsed counter. Toggle sport on a good route, observe orange tint on preserved segments. Place waypoint far from coverage, check detail panel text.

**Build verification:**
- `npm run build` (static export) after each phase
- `docker compose exec backend pytest -q` (backend unchanged in Phase 1, modified in Phase 2 Task 9)

### Notes

**High-risk items:**
- Task 5 (confidence line style) uses a separate `route-draft-bridged` overlay layer instead of data-driven dasharray (MapLibre 4.3.2 limitation). Must keep both layers in sync across all 4 `route-draft` setData call sites (lines ~3080, ~4036, ~4896, ~4906). The main `route-draft` source keeps ALL segments for surface overlay and hit detection compatibility.
- Task 3 (drag preview) changes the inflight concurrency model. The requestId approach prevents stale results but removes the throttle guarantee — rapid drags fire many parallel worker requests. The Worker is single-threaded so they serialize, but could saturate the message queue. Consider keeping the 50ms throttle while removing only the inflight blocking.
- Task 9 (sequential BRouter) changes from parallel to sequential — verify no timeout regression on slow BRouter responses. Add per-request timeout of 3s.
- Task 2 (Shift+drag) touches 4 files in the worker chain: types → worker → client proxy → page handler. All must be updated consistently.

**Known limitations:**
- Shift+drag precision mode is desktop-only (no touch equivalent). Documented in Out of Scope.
- Bridge ratio is computed from path edges, not geometric analysis. A segment could have 40% bridge edges by distance but appear visually solid if bridges are short connectors between long known edges.
- Elapsed counter uses `setInterval` which may drift slightly — acceptable for UX counter (not timing-critical).

**Future considerations (out of scope, noted for reference):**
- Proposal segment mixing ("take segment A from proposal 1 + segment B from proposal 3") — natural extension of map-first proposals.
- Animated route morphing during drag — would replace the opacity-swap approach in Task 3 with smooth interpolation.
- Mobile Shift+drag equivalent (long-press precision mode?) — requires touch gesture research.
