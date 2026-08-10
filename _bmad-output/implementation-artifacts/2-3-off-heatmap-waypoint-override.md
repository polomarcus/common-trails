# Story 2.3: Off-Heatmap Waypoint Override

Status: done

## Story

As a rider who knows the terrain,
I want to place a waypoint away from heatmap trails and have the router respect my intent,
So that I can take a path I know even if the community hasn't ridden it.

## Acceptance Criteria

1. **Given** a rider places a waypoint on an area with no heatmap coverage
   **When** the route is computed to/from that waypoint
   **Then** the route goes through the waypoint location without snapping it to the nearest heatmap trail
   **And** the route uses the nearest graph edge as far as possible, then a straight line to the off-heatmap waypoint

2. **Given** a rider drags a waypoint from a heatmap trail to a position 200m away
   **When** the segments are recalculated
   **Then** the route follows the rider's intent through the dragged position, not back to the heatmap trail
   **And** the waypoint marker stays at the dragged position

3. **Given** adjacent segments connect to heatmap/trail edges
   **When** a middle waypoint is placed off-heatmap
   **Then** the route transitions from heatmap → straight line → heatmap smoothly without loops or backtracking

4. **Given** the route has off-heatmap segments
   **When** the route is displayed on the map
   **Then** off-heatmap portions are shown with distinct styling (orange dashed line, not red failed-segment)
   **And** an informational message (not error) appears: "X segment(s) hors réseau — ligne droite"

5. **Given** the cursor hovers in an area with no graph edges within 200m
   **When** the user is in route mode
   **Then** the cursor snap indicator shows the un-snapped position (no ghost-snap to a distant edge)

## Architectural Constraint

The client graph contains **only** heatmap + DFCI + trail edges — no full OSM road network. When a waypoint is placed off-graph, the best available path is: route on graph to nearest edge → straight line to waypoint. The AC reference to "OSM roads/tracks" in the epic is aspirational — the MVP implementation uses straight-line bridging. This is consistent with ADR-2 (prefer honesty over unverified routes).

## Tasks / Subtasks

- [x] Task 1: Modify `clientRoute()` to handle snap misses gracefully (AC: #1, #3)
  - [x] 1.1: When `snapToRoad()` returns null for one endpoint, attempt extended snap (2000m radius) to find the nearest graph vertex
  - [x] 1.2: If extended snap finds a vertex, route from/to that vertex and prepend/append straight-line segment to the actual waypoint position
  - [x] 1.3: If both endpoints miss snap, return straight-line with `method: 'off_heatmap'` (not null)
  - [x] 1.4: Add `offHeatmap: { from: boolean, to: boolean }` to `ClientRouteResult`
  - [x] 1.5: Add `straightLinePrefix/Suffix` coordinates to the result for the bridging segments

- [x] Task 2: Propagate off-heatmap info through Worker and routing cascade (AC: #1)
  - [x] 2.1: Worker `route` message handler already passes through full `ClientRouteResult` including `offHeatmap` (no change needed)
  - [x] 2.2: In `fetchSmartSegmentWithClientRouting()`: when client route returns `offHeatmap`, do NOT fall through to server routing — user intent is off-graph
  - [x] 2.3: Set segment method to `'off_heatmap'` (not `'no_route'`)

- [x] Task 3: New segment state `'off_heatmap'` distinct from `'failed'` (AC: #4)
  - [x] 3.1: Add `offHeatmapSegmentIdxs: Set<number>` state (parallel to `failedSegmentIdxs`)
  - [x] 3.2: In the routing result handler: if segment has `offHeatmap`, add to `offHeatmapSegmentIdxs` (not `failedSegmentIdxs`)
  - [x] 3.3: Update `isBadSegment()` to NOT flag off-heatmap segments as bad

- [x] Task 4: Distinct map styling for off-heatmap segments (AC: #4)
  - [x] 4.1: New MapLibre source + layer `route-draft-offheatmap-line`: orange dashed (`#f59e0b`, dasharray `[6, 4]`, opacity 0.7)
  - [x] 4.2: Update the segment rendering effect to populate this source from `offHeatmapSegmentIdxs`
  - [x] 4.3: Clean up source/layer in `useEffect` return (MapLibre lifecycle rule)

- [x] Task 5: Informational UI message for off-heatmap segments (AC: #4)
  - [x] 5.1: In route editor panel, show info banner (blue/amber, not red) when `offHeatmapSegmentIdxs.size > 0`
  - [x] 5.2: Message: "X segment(s) hors réseau — ligne droite" with tooltip explaining the constraint
  - [x] 5.3: Do NOT suggest "ajoutez un point intermédiaire" — the user intentionally went off-graph

- [x] Task 6: Cursor snap feedback for off-heatmap areas (AC: #5)
  - [x] 6.1: In `snapCursorToRoad()`: when `snapped: false`, keep cursor at raw position (current behavior, already works — verified)
  - [x] 6.2: Verify cursor doesn't ghost-snap to distant edges — max snap distance for cursor preview stays at 300m (verified)

- [x] Task 7: Handle drag-to-off-heatmap scenario (AC: #2)
  - [x] 7.1: In `createAsyncDragRouter()`: when snap fails during drag, show straight line to drag position (already works via fallback — verified)
  - [x] 7.2: On mouseup/drag end: removed waypoint snap-on-drop to use raw dragged position (consistent with click handler)
  - [x] 7.3: Verify waypoint marker stays at dragged position — `waypointsRef.current` stores raw coords (verified, fixed drop snap)

- [x] Task 8: Unit tests (AC: #1, #3)
  - [x] 8.1: `client-graph.test.ts`: test snap miss → `offHeatmap: { from: false, to: true }` with straight-line suffix
  - [x] 8.2: Test both endpoints miss → straight line with `method: 'off_heatmap'`
  - [x] 8.3: Test extended snap finds vertex → hybrid route (graph + straight line bridge)
  - [x] 8.4: Test reversed direction (from off-graph, to on-graph) with correct coord order
  - [x] 8.5: Updated existing tests that expected `null` on snap miss to expect `off_heatmap` result

- [x] Task 9: E2E test (AC: #1, #4)
  - [x] 9.1: Place waypoint in area with no graph coverage → verify off-heatmap MapLibre layer exists
  - [x] 9.2: Verify info message (not error) in route editor panel
  - [x] 9.3: Verify no failed-segment error banner appears

## Dev Notes

### Current Snap Pipeline (what exists today)

The snap-to-route pipeline has 4 layers with different snap radii:

| Layer | Function | Snap radius | On miss |
|-------|----------|-------------|---------|
| Cursor preview | `snapCursorToRoad()` | 300m | Returns raw position, `snapped: false` |
| Drag preview | `createAsyncDragRouter()` → `worker.snap()` | 300m | Straight line to drag pos |
| Client routing | `clientRoute()` → `snapToRoad()` | 800m (via `fetchSmartSegmentWithClientRouting`) | Returns `null` → cascade to server |
| Server fallback | `fetchSmartSegment()` | N/A (server-side) | `no_route` sentinel → 2-point straight line |

**The core problem:** When `clientRoute()` returns `null` on snap miss, the cascade treats it as a routing failure — falling through to server routing, which also likely fails, resulting in a generic "failed segment" with no distinction from actual routing errors. The user sees the same red dashed line and error message whether they intentionally went off-heatmap or whether routing genuinely broke.

**The fix:** `clientRoute()` should handle snap miss as a valid outcome (user intent), not a failure. When snap fails, it should build a hybrid route (graph portion + straight-line bridge) and return it with an `offHeatmap` flag. The UI should then display this as informational (orange styling) not as an error (red styling).

### Key Files to Modify

| File | What to change |
|------|---------------|
| `frontend/lib/client-graph.ts` | `clientRoute()` snap miss handling, extended snap, hybrid route construction |
| `frontend/lib/routing-worker-client.ts` | Forward `offHeatmap` field from Worker response |
| `frontend/app/map/page.tsx` | `fetchSmartSegmentWithClientRouting()` off-heatmap detection, new segment state, MapLibre layer, UI message |
| `frontend/lib/__tests__/client-graph.test.ts` | Unit tests for snap miss scenarios |
| `e2e/tests/client-routing.spec.ts` | E2E test for off-heatmap visual feedback |

### Critical Constraints

1. **NEVER snap waypoints** — `waypointsRef.current` stores the raw clicked/dragged position. Snap only affects route computation start/end, not the waypoint marker.
2. **NEVER call `fetchSmartSegment()` from UI** — always `fetchSmartSegmentWithClientRouting()`. [Source: project-context.md#Routing Anti-Patterns]
3. **Per-segment recalculation** — only recalculate the 1-2 segments adjacent to the changed waypoint, splice `routeSegmentsRef.current`. Never recalculate the entire route. [Source: project-context.md#Routing Anti-Patterns]
4. **MapLibre layer cleanup** — any new source/layer must be cleaned up in `useEffect` return. [Source: project-context.md#MapLibre GL]
5. **`@/*` imports only** — never relative paths. [Source: project-context.md#TypeScript]
6. **Coordinate order** — always `[lon, lat]`, use `point[0]/point[1]` for iteration. [Source: project-context.md#Coordinate Order Convention]

### Existing Functions to Reuse (DO NOT reinvent)

- `snapToRoad(graph, lon, lat, maxM)` in `client-graph.ts` — extend its radius, don't create a duplicate
- `haversineM(a, b)` in `client-graph.ts` — distance calculation
- `createSegmentMeta(result, status)` in `page.tsx` — segment metadata factory
- `isBadSegment(result, from, to)` in `page.tsx` — update, don't duplicate
- Failed segment rendering effect (~line 3845) — model the off-heatmap effect on this pattern

### State Management Pattern

Follow the existing `failedSegmentIdxs` pattern exactly:
```
const [offHeatmapSegmentIdxs, setOffHeatmapSegmentIdxs] = useState<Set<number>>(new Set());
```
- Set in the routing result handler (same place `failedSegmentIdxs` is set)
- Clear when segment is recalculated successfully on-graph
- Use in rendering effect for the orange dashed MapLibre layer
- Show/hide UI info banner based on `.size > 0`

### Testing Approach

**Unit tests** (`npx tsx frontend/lib/__tests__/client-graph.test.ts`):
- Build a small test graph with edges only in one area
- Call `clientRoute()` with one waypoint on-graph, one off-graph
- Assert: returns non-null result with `offHeatmap.to === true`
- Assert: coords include straight-line bridge to off-graph waypoint
- Assert: method is `'off_heatmap'` or `'client_graph'` with flag

**E2E tests** (`cd e2e && npx playwright test`):
- Use the mock graph data (already seeded in E2E fixtures)
- Click in an area known to have no graph edges
- Assert: orange dashed line visible (check for MapLibre layer `route-draft-offheatmap-line`)
- Assert: info message visible in panel
- Assert: no red failed-segment styling

### Project Structure Notes

- All changes are frontend-only — no backend modifications needed
- New state (`offHeatmapSegmentIdxs`) follows existing React state patterns in `page.tsx`
- New MapLibre layer follows existing `route-draft-failed-line` pattern
- Unit tests in `frontend/lib/__tests__/client-graph.test.ts` (existing file)
- E2E tests in `e2e/tests/client-routing.spec.ts` (existing file, add new test)

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 2.3] — Original story ACs
- [Source: _bmad-output/planning-artifacts/prd.md#FR5] — Override routing off-heatmap
- [Source: _bmad-output/planning-artifacts/architecture.md#ADR-2] — 3-level cascade, straight line is intentional
- [Source: _bmad-output/project-context.md#Routing Anti-Patterns] — Never call fetchSmartSegment directly
- [Source: _bmad-output/project-context.md#Routing Engine Architecture] — VIP snap bias 4.0, cascade architecture
- [Source: docs/routing-architecture.md] — Full routing cascade documentation
- [Source: frontend/lib/client-graph.ts:514-575] — Current snapToRoad implementation
- [Source: frontend/lib/client-graph.ts:795-852] — Current clientRoute implementation
- [Source: frontend/app/map/page.tsx:682-731] — fetchSmartSegmentWithClientRouting
- [Source: frontend/app/map/page.tsx:394-412] — computeIntentStrength (not related to off-heatmap)
- [Source: frontend/app/map/page.tsx:491-499] — isBadSegment definition
- [Source: frontend/app/map/page.tsx:3845-3865] — Failed segment rendering effect
- [Source: frontend/lib/snap-to-road.ts] — Cursor and drag snap utilities

## Dev Agent Record

### Agent Model Used
Claude Opus 4.6

### Debug Log References
- Unit tests: 230 passed, 0 failed (`npx tsx frontend/lib/__tests__/client-graph.test.ts`)
- E2E tests: 9/9 passed (off-heatmap test consistently passes; drag tests 5+6 pre-existing flaky)
- Frontend build: passes with no TypeScript errors

### Completion Notes List
- Task 1: Added extended snap (2000m) and off-heatmap bridging to `clientRoute()`. Both/one/no endpoint miss cases handled.
- Task 2: Worker already passes through `ClientRouteResult` fields. Updated `fetchSmartSegmentWithClientRouting()` to short-circuit on `offHeatmap`.
- Task 3: Added `'off_heatmap'` to `SegmentState` type, `offHeatmapSegmentIdxs` state, updated `isBadSegment()`.
- Task 4: Added MapLibre source `route-draft-offheatmap` + layer with orange dashed styling (#f59e0b, dasharray [6,4]).
- Task 5: Added amber info banner "X segment(s) hors réseau — ligne droite" with tooltip.
- Task 6: Verified `snapCursorToRoad()` already returns raw position on miss — no changes needed.
- Task 7: Removed waypoint snap-on-drop from line drag handler for consistency with click handler (story constraint: "NEVER snap waypoints").
- Task 8: Added 15 off-heatmap unit tests + updated 2 existing tests that expected `null` on snap miss.
- Task 9: Added E2E test verifying off-heatmap layer existence and no error banner on off-graph waypoints.
- Also improved drag test stability (added timing pauses for map hover registration).

### File List
- `frontend/lib/client-graph.ts` — `ClientRouteResult.offHeatmap` field, `clientRoute()` extended snap + off-heatmap handling
- `frontend/lib/routing-state.ts` — `'off_heatmap'` added to `SegmentState` union
- `frontend/app/map/page.tsx` — `offHeatmapSegmentIdxs` state, `isBadSegment()` update, `fetchSmartSegmentWithClientRouting()` off-heatmap short-circuit, MapLibre layer, info banner, segment handlers, reset points, removed drop snap
- `frontend/lib/__tests__/client-graph.test.ts` — 15 new off-heatmap tests, 2 updated existing tests
- `e2e/tests/client-routing.spec.ts` — new off-heatmap E2E test, drag test stability improvements
