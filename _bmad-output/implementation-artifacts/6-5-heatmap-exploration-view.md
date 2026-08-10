# Story 6.5: Heatmap Exploration View

Status: ready-for-dev

## Story

As a rider exploring a new region,
I want a dedicated heatmap exploration mode where I can visually scan the territory for interesting trail clusters,
So that I can discover rideable zones and plan adventures based on where the community actually rides — not just what maps show.

## Context (Crouzet methodology)

Crouzet uses the heatmap not for routing but for **territorial discovery**: "la heatmap illumine le territoire de nos errances". He scans for bright zones (heavily ridden) and dim threads (hidden gems), then zooms in to cross-reference with satellite/topo. This is a contemplative, exploratory use — distinct from point-A-to-B routing. Currently CC's heatmap is a routing input, not an exploration tool.

## Acceptance Criteria

1. **Given** a rider opens the exploration view (toggle or dedicated tab)
   **When** the view loads
   **Then** the heatmap renders at full intensity (no route editor overlay, no waypoints, no sidebar clutter)
   **And** the map fills the viewport with minimal UI chrome
   **And** sport filter pills are visible (Road / Gravel / VTT / All) to filter heatmap by sport

2. **Given** the exploration view is active
   **When** the rider clicks on a bright heatmap zone
   **Then** a popup or panel shows zone stats:
   - Number of unique contributors in the area
   - Total passes (popularity score)
   - Dominant sport type
   **And** a "Tracer ici" CTA that switches to route editor centered on this zone

3. **Given** the rider is zoomed out (z8-z10)
   **When** the heatmap renders
   **Then** a heat density overlay shows regional hotspots (cluster aggregation)
   **And** zooming in progressively reveals individual trail lines (z12+)

4. **Given** the rider has personal activity data
   **When** they toggle "Mes zones inexplorées"
   **Then** community heatmap zones the rider hasn't visited are highlighted (using existing `/me/unexplored` data)
   **And** visited zones are dimmed, creating a "to-explore" visual map

5. **Given** the exploration view is active
   **When** the rider pans/zooms around
   **Then** the heatmap data loads progressively (no blocking spinner for the full dataset)
   **And** performance remains smooth even on dense areas (< 16ms frame time)

## Tasks / Subtasks

- [ ] Task 1: Exploration view toggle / route (AC: #1)
  - [ ] 1.1: Add "Explorer" mode toggle (alongside existing "Tracer" route editor mode)
  - [ ] 1.2: In exploration mode: hide route editor panel, hide waypoint controls, maximize map
  - [ ] 1.3: Show sport filter pills (floating, top-center or top-left)
  - [ ] 1.4: Sport filter updates heatmap layer `sport` parameter

- [ ] Task 2: Zone click interaction (AC: #2)
  - [ ] 2.1: Click handler on heatmap layer → identify clicked area (aggregate nearby edges)
  - [ ] 2.2: Query: count unique contributors, total passes, dominant sport within ~500m radius
  - [ ] 2.3: Popup with stats + "Tracer ici" button
  - [ ] 2.4: "Tracer ici" → switch to route editor, center map on clicked zone

- [ ] Task 3: Unexplored zones overlay (AC: #4)
  - [ ] 3.1: Fetch `/me/unexplored` data (already exists)
  - [ ] 3.2: Render as highlighted cells/areas on the map (glow or distinct color)
  - [ ] 3.3: Toggle: "Mes zones inexplorées" (only visible when logged in)
  - [ ] 3.4: Dim visited zones (reduce opacity of heatmap where user has cells)

- [ ] Task 4: Performance for dense heatmaps (AC: #5)
  - [ ] 4.1: Use existing tile-based loading (z14 tiles)
  - [ ] 4.2: At low zoom: aggregate/cluster instead of rendering all edges
  - [ ] 4.3: At high zoom: full edge detail
  - [ ] 4.4: Consider MapLibre heatmap layer type for low-zoom density visualization

## Dev Notes

### Distinction from Current Map

The current map page mixes routing, activity viewing, and heatmap in one view. The exploration mode is intentionally **reductive** — remove everything except the heatmap and discovery tools. Less is more for contemplative exploration.

### Low-Zoom Density

At z8-z10, rendering individual LineString edges would be overwhelming. Options:
- MapLibre `heatmap` layer type (point-based heat visualization) at low zoom
- Switch to `line` layer at z12+ for individual trails
- This is a progressive disclosure pattern

### "Tracer ici" Flow

The transition from exploration → route editor should be seamless: same map position, same zoom, just switch mode and add the first waypoint at the center of the explored zone.

## References

- Crouzet article 1: "la heatmap illumine le territoire de nos errances"
- Existing heatmap: `GET /heatmap/trails`, `GET /heatmap/stats`
- Unexplored zones: `GET /me/unexplored`
- MapLibre heatmap layer: https://maplibre.org/maplibre-style-spec/layers/#heatmap
