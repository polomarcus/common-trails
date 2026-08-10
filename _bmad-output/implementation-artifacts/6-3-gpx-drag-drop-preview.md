# Story 6.3: GPX Drag & Drop Preview (No Account Required)

Status: ready-for-dev

## Story

As a cyclist who just downloaded a GPX from a friend or the web,
I want to drag & drop it on the map to instantly see the trace,
So that I can preview and evaluate a route without creating an account or importing anything.

## Context (Crouzet methodology)

Crouzet uses VisuGPX as his go-to tool for quick GPX verification — drop a file, see the trace, check it against heatmap/satellite. This zero-friction preview is what brings users to visualization tools. Currently CC requires login + upload to see any GPX. A drag & drop preview would capture the VisuGPX use case and funnel users toward account creation.

## Acceptance Criteria

1. **Given** a visitor (logged in or not) is on the map page
   **When** they drag a .gpx file over the map area
   **Then** a drop zone overlay appears ("Déposer le GPX pour l'afficher")
   **And** when dropped, the trace renders on the map within 500ms

2. **Given** a GPX file has been dropped
   **When** the trace renders
   **Then** the map auto-fits to the trace bounding box
   **And** the trace is displayed as a colored line (distinct from route-draft and heatmap)
   **And** a summary panel shows: distance, elevation gain/loss, sport estimate, elevation profile

3. **Given** a GPX file has been dropped
   **When** the visitor clicks "Importer" in the summary panel
   **Then** if logged in → the GPX is uploaded via existing `/gpx/upload` endpoint
   **And** if not logged in → prompt "Créez un compte pour sauvegarder" with login CTA

4. **Given** a GPX file with 3D coordinates (elevation) has been dropped
   **When** the summary panel renders
   **Then** the elevation profile SVG is displayed (reusing existing ElevationProfile component)
   **And** D+/D- are calculated client-side from the GPX elevation data

5. **Given** a multi-track GPX file has been dropped
   **When** the trace renders
   **Then** all tracks/segments are displayed
   **And** the summary shows aggregate stats across all tracks

6. **Given** a preview trace is displayed
   **When** the visitor drops another GPX file
   **Then** the previous preview is replaced by the new one
   **And** only one preview is active at a time

7. **Given** a preview trace is displayed
   **When** the visitor clicks "✕ Fermer" in the summary panel
   **Then** the preview trace and panel are removed from the map

## Tasks / Subtasks

- [ ] Task 1: GPX client-side parser (AC: #1, #4, #5)
  - [ ] 1.1: Parse GPX XML in browser (DOMParser or lightweight lib like `gpxparser`)
  - [ ] 1.2: Extract tracks → array of `[lon, lat, ele?][]`
  - [ ] 1.3: Calculate client-side: distance (haversine), elevation gain/loss (from ele tags), bbox
  - [ ] 1.4: Handle multi-track GPX (concatenate or display separately)

- [ ] Task 2: Drag & drop zone (AC: #1)
  - [ ] 2.1: Detect dragenter/dragover on map container → show overlay
  - [ ] 2.2: Filter: only accept `.gpx` files (check name + MIME)
  - [ ] 2.3: On drop: read file as text, parse, render

- [ ] Task 3: Map rendering of preview trace (AC: #2, #6, #7)
  - [ ] 3.1: Add MapLibre source `gpx-preview` (GeoJSON LineString)
  - [ ] 3.2: Layer `gpx-preview-line`: solid purple/teal line, 3px, distinct from route-draft (red) and heatmap (yellow)
  - [ ] 3.3: `map.fitBounds()` on trace bbox with padding
  - [ ] 3.4: Replace on new drop, remove on close

- [ ] Task 4: Summary panel (AC: #2, #3, #4)
  - [ ] 4.1: Floating panel (bottom or side) with: filename, distance, D+/D-, sport estimate
  - [ ] 4.2: ElevationProfile SVG (reuse existing component with client-side elevation data)
  - [ ] 4.3: "Importer" button → auth check → upload or login prompt
  - [ ] 4.4: "✕ Fermer" button → remove preview

- [ ] Task 5: Sport estimation heuristic (AC: #2)
  - [ ] 5.1: Simple heuristic based on avg slope and distance: road (<3% avg, >50km), gravel (mixed), MTB (>5% sections), off-road
  - [ ] 5.2: Display as suggestion, not assertion ("Probablement Gravel")

## Dev Notes

### No Server Required

The entire preview feature works client-side. No API calls, no auth, no upload. This is deliberate — zero friction, instant feedback. The server is only contacted if the user explicitly clicks "Importer".

### GPX Parsing

Consider using the browser's native `DOMParser` for GPX (it's just XML). Lightweight, no dependency needed. Extract `<trkpt lat="" lon=""><ele>` elements.

### Funnel Value

This is a top-of-funnel feature: visitor drops a GPX → sees it works well → creates account → imports → becomes contributor. Every visualization tool (VisuGPX, gpx.studio, Nakarte) gets users this way.

## References

- VisuGPX: https://www.visugpx.com/ (Crouzet's preferred tool)
- gpx.studio: https://gpx.studio/ (similar drag & drop approach)
- Existing ElevationProfile: `frontend/app/map/page.tsx`
