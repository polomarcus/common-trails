# Drag-edit waypoint design — where we are, where we should go

**Status:** design v3, 2026-05-30 — Paul resolved all 5 questions, the immediate-bug fix is implemented (off-by-one in line-drag waypoint insert), tests pin both the splice contract and the no-U-turn invariant. **Two follow-ups queued: Q2's (2b) snap-to-OSM-road and Q3's downstream-quality recalculation.** Triggered by Paul's bug report — when dragging a new waypoint from the source itinerary, the router adds two detours (visible as a U-turn pattern) instead of routing direct between the new position and its neighbours.

**Audience:** Paul + future Claude sessions + the [routing-client agent](../.claude/agents/routing-client.md) when it picks this back up.

**Fix shipped** in `frontend/lib/map-utils.ts` + `frontend/lib/init-map-layers.ts`:
- New pure function [`computeLineDragInsert(waypoints, segIdx, dropPt)`](../frontend/lib/map-utils.ts) — encapsulates the correct splice with documented index convention.
- Line-drag handler in `init-map-layers.ts` now calls `computeLineDragInsert` instead of inlining `splice(segIdx + 1, 0, dropPt)`. The off-by-one — root cause of the U-turn — is gone.
- New helper [`findNoBacktrackViolations(coords, waypoints, opts)`](../frontend/lib/map-utils.ts) — geometric invariant for runtime diagnostics + tests.
- Bug found and fixed in the initial implementation of `findNoBacktrackViolations`: the cosLimit formula used `cos(180 - maxReverseDeg)` (e.g. `cos(30°)`), which fired on any 30°+ turn instead of actual U-turns. Now correctly `cos(maxReverseDeg)` so only true reversals (> 150°) trigger.

**Tests** ([`route-line-drag.test.ts`](../frontend/lib/__tests__/route-line-drag.test.ts), 12 tests, all passing):
- `computeLineDragInsert`: inserts at `segIdx` (the bug fix), returns correct prev/next neighbours, handles edge cases (first/last path segment, anchor segIdx=0 returns null, out-of-range returns null, input not mutated).
- `findNoBacktrackViolations`: clean route emits no issues, the synthetic U-turn-pattern coords correctly trigger a `sharp reversal` issue (the screenshot reproduction), missed waypoints and out-of-order visits are detected, degenerate inputs are handled.

**Cancel/undo:** yes, `routeHistory: {past, future}` + `pushSnapshotRef.current()` at [`page.tsx`](../frontend/app/map/page.tsx) — Cmd-Z reverts the last waypoint operation. The line-drag handler must call `pushSnapshotRef.current()` BEFORE the splice (verify; click-insert at `useRouteEditorInteraction.ts:334` already does — keep behaviour identical).

---

## 1. Drag interactions, today

The route editor has THREE distinct drag/click interactions, each handled in a different code path:

| Interaction | Trigger | Effect on `waypoints` | Effect on `segments` | Re-route scope |
|---|---|---|---|---|
| **Click on map (far from route)** | `click`, > 40 px from any segment | APPEND `newPt` to end | Append new segment from previous endpoint | 1 segment (last) |
| **Click on map (near route, < 40 px)** | `click`, near a segment at index `i` | **INSERT** `newPt` at index `i` | **SPLIT** `segments[i]` → re-route both halves in parallel | 2 segments (the split) |
| **Drag existing waypoint marker** | `mousedown` on marker → `mousemove` → `mouseup` | REPLACE at the same index | Re-route the two adjacent segments | 2 segments (adjacent to dragged marker) |
| **Drag the line itself** ("rubber-band") | `mousedown` on the polyline → drag | INSERT mid-drag (`lineDragSegIdxRef`) | Live-update via `AsyncDragRouter` (~16 ms throttle) | 2 segments (rubber-banded) |

Code anchors:
- Click-insert: [`frontend/hooks/useRouteEditorInteraction.ts:330-392`](../frontend/hooks/useRouteEditorInteraction.ts)
- Waypoint-marker drag: same file, uses `wpDragIdxRef` and `AsyncDragRouter` (`createAsyncDragRouter` in [`frontend/lib/snap-to-road.ts:56`](../frontend/lib/snap-to-road.ts))
- Line drag (ghost marker): `isDraggingLineRef` + `lineDragSegIdxRef` in [`frontend/app/map/page.tsx:417-418`](../frontend/app/map/page.tsx), plumbing in `useRouteEditorInteraction.ts`
- Waypoint list lives at [`page.tsx:211`](../frontend/app/map/page.tsx) as `[lon, lat][]`; `segments` at `routeSegmentsRef.current` as `[lon, lat][][]` (one polyline per consecutive waypoint pair).

The data model invariant: **`segments.length === waypoints.length - 1`** at all times outside an in-flight re-route.

---

## 2. The bug Paul reported

Reproduced at `http://localhost:3787/map?lat=43.65734&lon=3.87867&zoom=13.6` (Clapiers / Montpellier).

**Symptom.** User drags a new waypoint off the source line (markers labelled "3" and "4" in the screenshot, placed left of the original route). Expected: the trace passes A → B → 3 → 4 → C → D directly. Actual: the trace goes A → B → original-path-fragment → detour-out-to-3 → back-to-original → detour-out-to-4 → back-to-original → C → D. Two visible U-turns.

**Root cause — diagnosed but unverified.** The routing-client agent's last breadcrumb before being stopped:

> "Now I need to fix the fallback re-route path — it uses `prevPt = updatedPts[segIdx]` and `nextPt = updatedPts[segIdx + 2]` which is now wrong"

That index pattern is consistent with the **line-drag insert** path (a new waypoint at index `segIdx+1` so the neighbours are at `segIdx` and `segIdx+2`). The bug is likely one of:

- (a) **`segments` not spliced at the insert point.** When the new waypoint is inserted at index `i+1`, the OLD `segments[i]` should be removed and replaced by the two new re-routed halves. If the old segment fragment is kept (e.g. appended rather than replaced via `segs.splice(i, 1, half1, half2)`), the rendered geometry contains both old-route-up-to-X and new-detour, producing the U-turn.
- (b) **Async re-route lands AFTER a second drag.** The `AsyncDragRouter` is throttled at 16 ms but each call returns whenever it returns; a stale result from drag-1 could mutate `routeSegmentsRef` after drag-2 has already inserted, leaving phantom segments.
- (c) **Cost-function bias.** The heat-edge cost function prefers higher `pass_count`; the original line has a much higher pass_count than the detour, so even with correct waypoints the router scores the back-to-original-then-out-and-back path as cheaper than the direct path. Compounded by the 99.89 % `user_count=1` noise (see [`project_dedup_bug.md`](../../.claude/projects/-Users-paulleclercq-projects-common-trails/memory/project_dedup_bug.md)).

Causes (a) and (b) are bugs. Cause (c) is a quality issue that may show up even AFTER (a) + (b) are fixed.

---

## 3. Design decisions (Paul reviewed)

### Q1 — Insert vs replace on drag-from-line → **INSERT** ✓

Confirmed by Paul. The current code already does insert; the bug is in the splice mechanics, not the semantics.

### Q2 — Snap on release → **(2b) snap to nearest OSM road** ✓ — follow-up PR

Paul confirmed: when the user releases the dragged waypoint, it should LAND on the nearest OSM road (routable way) within ~30 m of the cursor. So a drag onto a field places the waypoint at the closest road, not in the field.

**This is a NEW feature.** It is NOT part of the current U-turn bug fix because:
- The U-turn was caused by an off-by-one in the waypoint splice — orthogonal to snap behaviour.
- The WASM router does not currently expose a "nearest routable point" query.

**Follow-up scope** (queued for a separate PR):
- Add `nearestRoutable(lon, lat, maxM)` to the WASM router (regional `.fgraph` already has the road graph; the function would do an indexed nearest-neighbour search and return `{lon, lat, distM} | null`).
- Wire `init-map-layers.ts` line-drag `dragend` to call it: if `result != null` and `result.distM < SNAP_M`, replace `dropPt` with `result.point`; otherwise keep `dropPt` (free-place fallback for off-road areas).
- Test: drag onto a known field → waypoint lands on the nearest road. Drag onto a road → unchanged.

Until shipped, today's behaviour is preserved: the waypoint lands at the cursor (free-place).

### Q3 — Commit once per drag + recalculate downstream if needed → **today's behaviour + a downstream-quality check follow-up** ✓

Paul confirmed two things:
1. **"Only commit ONCE per drag."** ✓ Already the case — the rubber-band drag is throttled at 16 ms but only `dragend` commits to the waypoints/segments arrays.
2. **"Sometimes the route does not make sense after one drag only, and we should recalculate a small portion."** Today only the TWO adjacent segments (`waypoints[i-1] → newPt` and `newPt → waypoints[i+1]`) are re-routed. If the drag pulls the route off in a direction that requires re-routing the NEXT segment too (e.g. the original `waypoints[i+1] → waypoints[i+2]` is now inefficient given the new shape), the route looks bent.

**Follow-up scope** (queued for a separate PR):
- After a drag commit, compute a "quality delta" on the *next* segment (`waypoints[i+1] → waypoints[i+2]`) — e.g. by comparing the actual routed distance to the haversine between endpoints, or by detecting >150° turns at the boundary.
- If the delta exceeds a threshold, transparently re-route that next segment too. Bound the cascade to N=1 or N=2 segments downstream (and ditto upstream) to preserve the "drag-edit feels instant" motto.
- Test: a drag that creates a wonky downstream segment triggers a single extra re-route; a drag that doesn't, doesn't.

Until shipped, the current "2 adjacent halves only" behaviour stays.

### Q4 — Roll back on both-halves-failed → **no** ✓

Confirmed. User intent preserved; straight-line + failed badge.

### Q5 — Auto-name new waypoint → **no change** ✓

Confirmed.

---

## 4. Where we should go — recommended fix

Surgical. Keep the data model. Fix the splice. Pin with tests.

### Fix sketch (subject to the routing agent's verification)

1. In the **line-drag handler** (where the bug lives — find by following `lineDragSegIdxRef`):
   - On `dragstart` from the line at segment index `i`, freeze `oldSegment = segments[i]` and a `tempWaypoint = newPt` (cursor position).
   - On `dragmove` (throttled), call `AsyncDragRouter` for the two adjacent segments: `waypoints[i] → tempWaypoint` and `tempWaypoint → waypoints[i+1]`. Render the two preview halves; do NOT mutate `waypoints` yet.
   - On `dragend`, atomically:
     - `waypoints.splice(i+1, 0, finalPt)` — INSERT at index `i+1`.
     - `segments.splice(i, 1, finalHalf1, finalHalf2)` — REMOVE old segment, INSERT two new ones in its place.
     - `segmentMeta.splice(...)` mirrors `segments`.
     - Shift `failedSegmentIdxs` and `offHeatmapSegmentIdxs` for indices > `i`.

2. The agent's flagged `prevPt = updatedPts[segIdx]` / `nextPt = updatedPts[segIdx + 2]` is correct AFTER the splice. The bug is probably that either:
   - the splice happens, but a stale `routeSegmentsRef.current[i]` is still rendered (race with throttle), or
   - the splice uses the wrong indices (off-by-one — the click-insert handler at `useRouteEditorInteraction.ts:336` uses `splice(insertIdx, 0, ...)` for waypoints and `splice(nearestSegIdx, 1, ...)` for segments — verify the LINE-drag path does the same).

### Tests added (12 passing, the contract pinned)

[`frontend/lib/__tests__/route-line-drag.test.ts`](../frontend/lib/__tests__/route-line-drag.test.ts) — 12 tests across two suites:

**`computeLineDragInsert`** (7 tests) — the bug-fix contract:
- Inserts `dropPt` at `segIdx` (not `segIdx + 1` — pins the off-by-one fix).
- Returns correct `prevPt` and `nextPt` neighbours.
- Handles drag on the first path segment (segIdx = 1).
- Handles drag on the last path segment.
- Returns `null` for the anchor (segIdx = 0).
- Returns `null` for out-of-range `segIdx`.
- Does NOT mutate the input array.

**`findNoBacktrackViolations`** (5 tests) — the runtime invariant:
- Clean route emits no issues.
- Broken U-turn fixture (synthetic reproduction of the screenshot pattern) FAILS the `> 150°` check — pins "if the user's eye sees a U-turn, the test catches it".
- Missed waypoints (line never gets close) are detected.
- Out-of-order waypoint visits are detected.
- Degenerate inputs (short lines) handled gracefully.

**Wired up:** `vitest.config.ts` updated to include the new file. `npx vitest run` covers it.

**Bug found during testing.** The first version of `findNoBacktrackViolations` used `cosLimit = cos(180 - maxReverseDeg)` which fired on any 30°+ turn (real cycling routes have many). Fixed to `cosLimit = cos(maxReverseDeg)` so only true U-turns (> 150°) trigger.

### What NOT to change in this fix

- The data model (`waypoints` + `segments` arrays). It's correct as-is.
- The cascade (WASM → backend → BRouter → OSRM). The bug is in the editor splice, not the routing layer.
- The heat-edge cost function. Cause (c) is real but separate — track it under [`project_dedup_bug.md`](../../.claude/projects/-Users-paulleclercq-projects-common-trails/memory/project_dedup_bug.md).
- The motto (50 ms drag-edit). Keep re-route scope at 2 segments.

---

## 5. Out-of-scope follow-ups

- **Heatmap cost-function quality.** Even with the splice fix, dragging close to the original line may produce wobbles because the cost-function still prefers the original (higher `pass_count`) edges. The honest fix is reducing heat_edges noise (the 99.89 % `user_count=1` problem). Defer; track in `project_dedup_bug.md`.
- **Multi-segment drag-from-line.** Drag through THREE waypoints at once. Not a friends-beta concern.
- **Undo of a drag-from-line.** `pushSnapshotRef.current()` already snapshots before the splice in the click-insert path — verify the line-drag path does the same.
- **Backend `/routing` cascade.** Known-broken at scale; not relevant here.

---

## 6. How to verify after the fix

Manual, in Paul's browser at `http://localhost:3787/map?lat=43.65734&lon=3.87867&zoom=13.6`:

1. Load a route from `data/gpx/` that passes through the area.
2. Drag a point ON the line to a position 200 m to the left.
3. Expected: the trace passes through the dragged point and continues to the next waypoint WITHOUT returning to the original line.
4. Drag a SECOND new point further along the route.
5. Expected: both drags retained, no zig-zag between them.
6. Press Cmd-Z. The last drag should undo cleanly.

Automated: the 2 tests above run in `npm test` / `vitest`. Failing-first → fix → passing.

---

## See also

- [`project_motto.md`](../.claude/projects/-Users-paulleclercq-projects-common-trails/memory/project_motto.md) — the 50 ms target
- [`project_routing_wasm_architecture.md`](../.claude/projects/-Users-paulleclercq-projects-common-trails/memory/project_routing_wasm_architecture.md) — WASM cascade
- [`project_crouzet_methodology.md`](../.claude/projects/-Users-paulleclercq-projects-common-trails/memory/project_crouzet_methodology.md) — never alter the GPX
- [`project_dedup_bug.md`](../.claude/projects/-Users-paulleclercq-projects-common-trails/memory/project_dedup_bug.md) — cost-function noise

---

## 7. Routing-graph constants (2026-05-31 + 2026-06-01 update)

The drag-edit handler relies on the WASM router seeing a graph that's *complete enough* — broken bridges or motorway under-passages would otherwise force the cascade out to straight-line, killing the 50 ms feel. Three constants are load-bearing:

- **`_CRITICAL_CONNECTOR_MAX_EDGES = 10 000`** — separate budget from the OSM `max_edges` cap. Any `osm_road_edges` row with `bridge_yes OR tunnel_yes` (plus its 150 m neighbourhood) is always included in `area.pb`, regardless of `skip_osm`. See PR #381 / #383 (motorway under-passage CTE fix).
- **150 m neighbourhood radius** — `ST_DWithin(o.geometry::geography, b.geometry::geography, 150)`. Note the GiST bbox prefilter pattern shipped in PR #386: `o.geometry && ST_Expand(b.geometry, 0.0015)` BEFORE the geography `ST_DWithin`. Without the prefilter, a fresh Occitanie import (7 M `osm_road_edges`) made the query do a full table scan → `area.pb` hung indefinitely.
- **1.30× cost penalty for `is_critical_connector=true && user_count=0`** (Rust `wasm-router/src/cost.rs::edge_cost()`, PR #384). Critical-connector edges are fallback-only — used only when no heat_edge alternative exists. After bumping this constant, **rebuild fgraph shards** via `bash wasm-router/build.sh --prebuild-only`. Old shards keep working (penalty dormant) but the new behavior only activates after rebuild.

The drag-edit splice contract (Section 1 / Section 2 above) is unaffected by these changes — they only affect *which edges are routable*, not *which segments get re-spliced on drag*.
