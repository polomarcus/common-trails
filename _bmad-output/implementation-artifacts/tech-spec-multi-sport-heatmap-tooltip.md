---
title: 'Multi-Sport Merged Heatmap Tooltip'
slug: 'multi-sport-heatmap-tooltip'
created: '2026-03-23'
status: 'implementation-complete'
stepsCompleted: [1, 2, 3, 4]
tech_stack: [typescript, next.js, maplibre]
files_to_modify: [frontend/app/map/page.tsx]
code_patterns: [maplibre-queryRenderedFeatures, react-state-hover]
test_patterns: [playwright]
---

# Tech-Spec: Multi-Sport Merged Heatmap Tooltip

**Created:** 2026-03-23

## Overview

### Problem Statement

After OSM edge grouping, multiple sports (running, road, gravel, MTB) share the same OSM road segment. Each sport creates its own `heat_edge` with the same geometry but different `edge_key` (sport prefix differs). When hovering in "Tous" (all sports) view, MapLibre returns only the topmost feature — so the tooltip shows "56 passages RUNNING" but hides the gravel/MTB/road data underneath.

### Solution

Pure frontend change (~20 lines in `map/page.tsx`): on hover, use `queryRenderedFeatures` to get ALL overlapping features, group by sport, display combined tooltip. Only relevant in "Tous" view — when a specific sport filter is active, existing single-feature behavior is correct.

### Scope

**In Scope:**
- Merged tooltip showing all sports with their pass counts (only in "Tous" view)
- Combined total + per-sport breakdown in tooltip

**Out of Scope:**
- Backend changes (all data already in GeoJSON features)
- Merging overlapping sport lines into single line (imperceptible visual difference)
- Sport-specific line colors/styles
- Changing the edge grouping logic (already working)

## Context for Development

### Codebase Patterns

- Heatmap data: GeoJSON FeatureCollection fetched from `/heatmap/trails` → set on `community-trails` source
- Hover detection: `community-trails-hit` invisible 16px-wide line layer
- State: `heatHover` holds `{x, y, sport, userCount, passCount} | null`
- Sport filter: `heatmapSport` state — `'all'` for "Tous" view, or specific sport name
- MapLibre `queryRenderedFeatures(point, {layers})` returns all features at a pixel (not just topmost)

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `frontend/app/map/page.tsx:1310-1313` | `heatHover` state — `{x, y, sport, userCount, passCount} \| null` |
| `frontend/app/map/page.tsx:1214` | `heatmapSport` state — `'all'` or specific sport |
| `frontend/app/map/page.tsx:3324-3335` | Mousemove handler — reads `e.features?.[0]` only |
| `frontend/app/map/page.tsx:3319-3321` | Mouseleave handler — clears tooltip |
| `frontend/app/map/page.tsx:7805-7827` | Tooltip JSX — renders single sport line |
| `frontend/app/map/page.tsx:2538-2544` | `community-trails-hit` layer definition |

### Technical Decisions

1. **Frontend-only**: All sport data already in GeoJSON features. No backend changes.
2. **`queryRenderedFeatures`**: Replace `e.features?.[0]` with `map.queryRenderedFeatures(point, {layers: ['community-trails-hit']})` to get all overlapping features at that pixel.
3. **Scoped to "Tous" view**: When a specific sport is selected, only one feature exists per segment — existing behavior is correct.
4. **`user_count`**: Use `max()` across sports (same user with different sports = 1 contributor).
5. **Tooltip format**:
   ```
   3 contributeurs · 71 passages
   ├ 56 running · 12 gravel · 3 mtb
   ```

## Implementation Plan

### Tasks

- [x] Task 1: Update `heatHover` state type
  - File: `frontend/app/map/page.tsx:1310-1313`
  - Change type from `{x, y, sport, userCount, passCount}` to `{x, y, sports: Array<{sport: string, userCount: number, passCount: number}>}`

- [x] Task 2: Update mousemove handler to query all features at point
  - File: `frontend/app/map/page.tsx:3324-3335`
  - Replace `e.features?.[0]` with `m.queryRenderedFeatures(m.project(e.lngLat), {layers: ['community-trails-hit']})`
  - Group features by `sport`, summing `pass_count`, taking `max(user_count)`
  - Build `sports` array sorted by `passCount` desc

- [x] Task 3: Update tooltip JSX for multi-sport display
  - File: `frontend/app/map/page.tsx:7805-7827`
  - First line: total contributors (max across sports) · total passages (sum)
  - Second line: per-sport breakdown inline (e.g., `56 running · 12 gravel · 3 mtb`)
  - Single sport: no change in appearance (array has 1 element)

### Acceptance Criteria

- [x] AC 1: Given a road segment with running (56 passages) and gravel (12 passages) edges in "Tous" view, when hovering, then the tooltip shows combined total (68 passages) and per-sport breakdown.
- [x] AC 2: Given a road segment with only one sport, when hovering, then the tooltip shows that single sport (no visual regression from current behavior).
- [x] AC 3: Given explore mode or route mode is active, when hovering on heatmap, then no tooltip is shown (existing behavior preserved).
- [x] AC 4: Given a specific sport filter is selected (e.g., "Route"), when hovering, then the tooltip shows only that sport's data.

## Additional Context

### Dependencies

- No new dependencies

### Testing Strategy

- Manual: hover over Le Lez area in "Tous" view where multiple sports overlap
- Playwright: existing heatmap E2E tests should still pass

### Notes

- `queryRenderedFeatures` works because all sport edges share the same geometry after OSM grouping — they overlap at exact same pixel coordinates.
- `user_count` across sports uses `max()` not `sum()` — same user cycling and running is still 1 contributor.
