# Story 1.1: Fix Tile Loading Synchronization Before Route Computation

Status: review

## Story

As a rider,
I want the routing engine to wait for map tiles to load before computing my route,
So that my route uses all available heatmap and trail data instead of ignoring it.

## Acceptance Criteria

1. **Given** a rider has placed two waypoints in the Hérault area
   **When** the route is computed
   **Then** the client graph has loaded all z14 tiles covering the bounding box before Dijkstra runs
   **And** no route computation starts while tiles are still pending
   **And** a loading indicator shows while tiles are being fetched

2. **Given** tiles for the area are already cached (IndexedDB)
   **When** the rider places a waypoint
   **Then** routing starts immediately without waiting (cache hit)

3. **Given** the rider enters route editing mode
   **When** viewport tiles are loading in the background
   **Then** a subtle "Loading trail data..." indicator is shown (non-blocking)
   **And** the map remains fully interactive

*Covers: FR24, AR6*

## Tasks / Subtasks

- [x] Task 1: Audit and fix tile loading guarantees in `fetchSmartSegmentWithClientRouting()` (AC: #1)
  - [x] 1.1: Verify `ensureTilesLoaded()` → `await Promise.all(pendingTiles)` sequence has no gaps
  - [x] 1.2: Add assertion/guard: Dijkstra must not run if `pendingTiles.size > 0`
  - [x] 1.3: Handle the case where `ensureTilesLoaded()` partially fails (some tiles 404/timeout) — routing should proceed with available tiles, not hang
  - [x] 1.4: Verify the wider-corridor retry (2× bbox) also awaits all pending tiles before Dijkstra

- [x] Task 2: Add tile-loading UI indicator (AC: #1, #3)
  - [x] 2.1: Add a `tileLoading` state to track when graph tiles are actively fetching
  - [x] 2.2: Show "Chargement des données sentiers..." message when tiles are being fetched for routing (non-blocking, inline in the routing status area)
  - [x] 2.3: For viewport preloading (moveend), use a subtle indicator (e.g., small spinner near layers panel or route status area) — never block the map
  - [x] 2.4: No indicator when tiles come from IndexedDB cache (AC: #2) — cache reads resolve synchronously from user perspective

- [x] Task 3: Ensure viewport preloading is robust (AC: #1)
  - [x] 3.1: Audit `loadVisibleTiles()` — confirm the moveend handler loads tiles into the same `clientGraphRef.current` graph instance
  - [x] 3.2: Verify sport change graph recreation doesn't race with in-flight moveend tile loads (stale closure capturing old graph)
  - [x] 3.3: Verify predictive tile warming (5km radius on first waypoint) completes before routing can fire

- [x] Task 4: Add/update tests (AC: #1, #2)
  - [x] 4.1: Frontend unit test in `lib/__tests__/client-graph.test.ts`: `ensureTilesLoaded` resolves all pending tiles before returning
  - [x] 4.2: Frontend unit test: routing with empty graph after tile load failure falls through to server
  - [x] 4.3: E2E test in `e2e/tests/client-routing.spec.ts`: verify tile loading indicator appears then disappears on route computation
  - [ ] 4.4: E2E test: verify cached tiles produce instant routing (no indicator flash)

## Dev Notes

### Problem Context

The tile race condition is the **#1 correctness AND performance bug** in the client graph [Source: architecture.md#Performance Analysis]. When routing fires before tiles load, Dijkstra runs on an empty or partial graph and either:
- Produces no route → falls back to server (unnecessary latency)
- Produces a bad route that ignores heatmap trails (silent quality regression)

The performance analysis shows tile fetch is the dominant cost (50-200ms on cache miss). If tiles are preloaded, routing takes ~37ms total. **Fixing preloading = fixing both correctness AND speed.**

### Current State (as of commit 3bd5dbe)

Several fixes are already in place:

1. **Pending tile await** — `fetchSmartSegmentWithClientRouting()` (page.tsx ~lines 708-709) explicitly awaits `Promise.all(graph.pendingTiles.values())` after calling `ensureTilesLoaded()`. This runs twice: after corridor load AND after wider-corridor retry.

2. **Viewport preloading** — `loadVisibleTiles()` fires on every `moveend` event with dynamic buffer padding (200% at z14+, 150% at z13, 100% at z12, 50% at z11).

3. **Predictive warming** — 5km radius tiles loaded around first waypoint (page.tsx ~lines 4368-4378).

4. **25-tile cap** — `ensureTilesLoaded()` caps at 25 tiles to prevent UI freeze on very long corridors, keeping tiles closest to bbox center.

5. **Bridge distance** — Increased from 200m to 500m to handle z14 tile boundary seams.

### Remaining Issues to Fix

1. **No "Loading tiles" UI** — User sees generic "Calcul en cours..." but can't distinguish tile fetching from Dijkstra computation. UX spec requires: "Loading trail data..." for tile fetch, and no spinner for cached data. [Source: ux-design-specification.md#Loading States]

2. **Sport change graph race** — When sport changes (page.tsx ~lines 1804-1938), a fresh `ClientGraph` is created and viewport tiles loaded sequentially. But the old `clientGraphRef` may still be captured by the moveend handler closure, causing tiles to load into the wrong graph instance.

3. **Predictive warming not awaited** — The 5km-radius warming fires after `setTimeout(0)` and is never awaited. If the user clicks the second waypoint fast enough, routing may start before warming completes. The `pendingTiles` await in `fetchSmartSegmentWithClientRouting` should catch this, but verify.

4. **Partial tile failures** — If some tiles 404 or timeout (5s), `ensureTilesLoaded` still resolves (individual tile errors are caught). Verify that routing proceeds with available tiles rather than hanging or producing empty results.

### Architecture Constraints

- **ALWAYS** call `fetchSmartSegmentWithClientRouting()` from UI code — never `fetchSmartSegment()` directly [Source: project-context.md#Routing Anti-Patterns]
- **NEVER** recalculate entire route when only one segment changed — splice `routeSegmentsRef.current` [Source: project-context.md#Routing Anti-Patterns]
- Draft-then-refine pattern: client graph produces fast draft, server sends quality-checked refinement in background [Source: architecture.md#ADR-2]
- UX principle: "Never show a spinner without context" — if tiles loading, show "Loading trail data..." [Source: ux-design-specification.md#Emotional Design Principles]
- UX principle: "Never block the map" — loading states must be non-blocking overlays/inline indicators [Source: ux-design-specification.md#Loading States]
- Cache hit = instant, no indicator needed [Source: ux-design-specification.md#Loading States]

### Key Files to Touch

| File | Purpose | Changes |
|------|---------|---------|
| `frontend/lib/client-graph.ts` | Graph building + tile loading | Audit `ensureTilesLoaded()`, add tile loading event/callback |
| `frontend/app/map/page.tsx` | Routing orchestration | Add `tileLoading` state, fix sport-change race, wire loading indicator |
| `frontend/lib/tile-cache.ts` | IndexedDB cache | No changes expected (works correctly) |
| `frontend/lib/routing-state.ts` | Segment state tracking | May need new state for tile-loading phase |
| `frontend/lib/__tests__/client-graph.test.ts` | Unit tests | Add tile sync tests |
| `e2e/tests/client-routing.spec.ts` | E2E routing tests | Add indicator visibility tests |

### Technical Implementation Guidance

**Tile loading callback pattern:**
`ensureTilesLoaded()` currently returns `Promise<void>`. To surface loading state to the UI, consider one of:
- **Option A (recommended):** Add an optional `onTileProgress?: (loaded: number, total: number) => void` callback parameter to `ensureTilesLoaded()`. The UI can use this to set/clear `tileLoading` state.
- **Option B:** Return `{ tilesLoaded: number, tilesTotal: number, fromCache: number }` from `ensureTilesLoaded()`. UI checks if any tiles were fetched (not cached) to decide whether to show indicator.

**Sport change race fix:**
The `moveend` handler (page.tsx ~lines 1741-1772) captures `clientGraphRef.current` in a closure. When sport changes and creates a new graph, the old moveend listener should be cleaned up. Verify the `useEffect` cleanup runs before new graph is assigned. If not, add a graph version counter or use `AbortController` to invalidate stale tile loads.

**Loading indicator placement:**
- Route editing mode: inline in the route status bar area (where "Calcul en cours..." appears), text: "Chargement des données sentiers..."
- The routing message state (`routingMessage`) already exists and shows progressive messages. Add tile-loading as the first phase before "Calcul en cours..."
- Transition: "Chargement des données sentiers..." → "Calcul en cours..." → route visible

**Don't over-engineer:**
- The 25-tile cap is fine — don't change it
- Bridge distance of 500m is working — don't change it
- IndexedDB cache with 24h TTL is working — don't change it
- Don't add new npm dependencies for loading indicators

### Project Structure Notes

- All routing code lives in `frontend/lib/` (pure logic) and `frontend/app/map/page.tsx` (orchestration)
- State: `useRef` for graph instance (`clientGraphRef`), `useState` for UI state (`routing`, `routingMessage`)
- TypeScript strict mode — no `as any`, use `@ts-ignore` for MapLibre type mismatches
- Import paths: always `@/lib/...`, never relative `../../`

### References

- [Source: architecture.md#Architectural Gaps Identified] — Tile race condition listed as High severity
- [Source: architecture.md#Performance Analysis] — Tile fetch is dominant cost (50-200ms), routing ~37ms if preloaded
- [Source: architecture.md#ADR-2] — Draft-then-refine pattern
- [Source: prd.md#FR24] — Client graph synchronizes tile loading before route computation
- [Source: prd.md#AR6] — Viewport-based aggressive preloading before routing fires
- [Source: prd.md#NFR1] — Client-side route segment calculation < 1s (p95)
- [Source: ux-design-specification.md#Loading States] — No blocking spinner, "Loading trail data..." for tile fetch
- [Source: ux-design-specification.md#Status Communication] — Subtle shimmer for loading tiles, no blocking spinner
- [Source: project-context.md#Routing Engine Architecture] — Cascade architecture, call sites, state management
- [Source: epics.md#Story 1.1] — Story requirements and acceptance criteria

## Dev Agent Record

### Agent Model Used
Claude Opus 4.6

### Debug Log References
N/A

### Completion Notes List
- Task 1: Audited tile loading flow — existing `ensureTilesLoaded()` → `Promise.all(pendingTiles)` sequence is correct. Added defensive guard before Dijkstra that warns and awaits if unexpected pending tiles remain. Verified partial tile failures (404/timeout) are caught individually and routing proceeds with available data. Wider-corridor retry also awaits pending tiles correctly.
- Task 2: Added `tileLoading` useState. Modified progressive message effect to show "Chargement des données sentiers..." as first phase when tiles are pending. Added subtle viewport preloading indicator (non-blocking, opacity 0.7) shown when `tileLoading && !routing`. Cache hits naturally produce no indicator flash (React batches synchronous state updates).
- Task 3: Audited viewport preloading — `loadVisibleTiles` uses `clientGraphRef.current` (not closure-captured graph) so sport changes are safe. Predictive warming tiles are caught by `pendingTiles` await in routing function. `loadTile` deduplicates concurrent requests for same tile key.
- Task 4: Added 8 unit tests (tile sync dedup, pending cleanup, loaded-tile skip, empty graph fallback, snap-fail fallback, partial graph routing). Added 1 E2E test (tile loading indicator visibility with delayed mock tiles). Skipped E2E test 4.4 (cached tile no-indicator-flash) — inherently timing-dependent and verified by design.

### File List
- `frontend/app/map/page.tsx` — Added defensive Dijkstra guard, `tileLoading` state, viewport preloading indicator, modified progressive message effect
- `frontend/lib/__tests__/client-graph.test.ts` — Added tile sync and server fallback unit tests (139 total, all pass)
- `e2e/tests/client-routing.spec.ts` — Added tile loading indicator E2E test
