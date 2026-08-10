# Story 6.2: Freehand Trace Mode (Manual Point-to-Point Without Auto-Routing)

Status: ready-for-dev

## Story

As an expert traceur who knows the terrain,
I want to draw my route manually point-by-point without the router auto-calculating paths,
So that I have full control over every meter of my trace — like drawing on a paper map.

## Context (Crouzet methodology)

Crouzet prefers manual tracing over automatic routing: he places points one by one on the heatmap, following visible heat trails with his eyes. While our heatmap-based router automates his workflow well, purist traceurs want the option to bypass routing entirely — especially when they know a path exists that isn't in our graph (field knowledge, recent survey, etc.).

This is NOT a replacement for smart routing — it's an alternative mode for power users.

## Acceptance Criteria

1. **Given** a rider is in route editing mode
   **When** they click the mode toggle
   **Then** they can switch between "Auto" (current heatmap routing) and "Manuel" (freehand)
   **And** the active mode is clearly indicated (badge/toggle in the route editor panel)

2. **Given** "Manuel" mode is active
   **When** the rider clicks on the map
   **Then** a waypoint is placed at the exact click position (no snap to graph)
   **And** consecutive waypoints are connected by straight-line segments
   **And** the line is styled distinctly (dashed, different color) to indicate no routing was applied

3. **Given** a rider has placed points in "Manuel" mode
   **When** they switch back to "Auto" mode
   **Then** existing waypoints are preserved
   **And** only NEW segments (from new waypoints) use auto-routing
   **And** existing manual segments remain as straight lines (no auto-recalculation)

4. **Given** a rider has a mixed route (some auto, some manual segments)
   **When** the route is exported as GPX
   **Then** the GPX contains the full trace with both auto-routed and manual segments
   **And** manual segments are included as straight lines between waypoints

5. **Given** "Manuel" mode is active
   **When** the rider places a waypoint
   **Then** no routing computation is triggered (no worker message, no loading spinner)
   **And** the segment appears instantly (< 50ms)

6. **Given** a rider is in "Manuel" mode
   **When** they drag an existing waypoint
   **Then** only the two adjacent segments update (straight lines to new position)
   **And** the rest of the route is unchanged

## Tasks / Subtasks

- [ ] Task 1: Add mode toggle to route editor panel (AC: #1)
  - [ ] 1.1: State: `routingMode: 'auto' | 'manual'` in route editor state
  - [ ] 1.2: Toggle UI: segmented control or pill toggle "Auto | Manuel" near sport selector
  - [ ] 1.3: Visual feedback: mode badge on the map (small indicator)

- [ ] Task 2: Implement manual segment logic (AC: #2, #5)
  - [ ] 2.1: When `routingMode === 'manual'`: click handler places waypoint at exact lngLat
  - [ ] 2.2: No call to `fetchSmartSegmentWithClientRouting()` — just create segment with `method: 'manual'`
  - [ ] 2.3: Segment geometry = `[prevWaypoint, newWaypoint]` (straight line)
  - [ ] 2.4: New segment method `'manual'` with distinct styling (blue dashed? or use existing off-heatmap orange dashed)

- [ ] Task 3: Mixed mode support (AC: #3)
  - [ ] 3.1: Each segment stores its `routingMode` at creation time
  - [ ] 3.2: Mode switch doesn't retroactively change existing segments
  - [ ] 3.3: On mode switch back to auto, new segments route normally

- [ ] Task 4: Drag support in manual mode (AC: #6)
  - [ ] 4.1: On waypoint drag in manual mode: recalculate adjacent segments as straight lines
  - [ ] 4.2: No routing worker involvement — instant geometry update

- [ ] Task 5: GPX export compatibility (AC: #4)
  - [ ] 5.1: Manual segments included as-is in GPX track
  - [ ] 5.2: No special metadata needed — a line is a line in GPX

## Dev Notes

### UX Consideration

The "Manuel" mode is an expert feature. It should be discoverable but not prominent — the default remains "Auto" which serves 90% of users well. Consider placing it as a secondary option or behind a "Mode avancé" disclosure.

### Relationship to Off-Heatmap Segments (Story 2.3)

Manual segments are conceptually similar to off-heatmap segments (straight lines), but the intent is different:
- Off-heatmap = auto-routing couldn't find a path → fallback to straight line
- Manual = user deliberately chose straight line → no routing attempted

Consider reusing the same styling or differentiating slightly (manual = user choice = blue dashed, off-heatmap = system fallback = orange dashed).

## References

- Crouzet article 2: "le GPS vélo pour les nuls" — preference for manual control
- Current routing: `frontend/lib/routing-worker-client.ts`
- Route editor: `frontend/app/map/page.tsx`
