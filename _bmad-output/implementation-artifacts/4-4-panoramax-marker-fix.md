# Story 4.4: Panoramax Marker Position Fix

Status: pending

## Story

As a user viewing a shared route with Panoramax photos,
I want photo markers to appear at the correct positions on the route,
So that I can see street-level imagery that corresponds to the actual map location.

## Acceptance Criteria

1. **Given** a route has Panoramax photos available
   **When** the photo strip is displayed
   **Then** each photo thumbnail corresponds to the correct position along the route

2. **Given** a user zooms in or out on the map
   **When** Panoramax markers are visible
   **Then** markers stay at their correct geographic positions (no drift)

3. **Given** a route with photos
   **When** the user hovers a photo thumbnail
   **Then** the map highlights the corresponding location on the route

## Architectural Context

Current implementation in `frontend/app/map/page.tsx`:
- Samples ~8 evenly-spaced points along route coords (lines 583-610)
- Fetches Panoramax API for each sampled coordinate
- Photos stored in `panoramaxPhotos` state with `{id, lon, lat, thumb}`
- Bug: markers cluster in wrong positions and drift on zoom — likely coordinate projection or marker anchor issue

## Tasks / Subtasks

- [ ] Task 1: Diagnose marker positioning bug (AC: #1)
  - [ ] 1.1: Inspect how `panoramaxPhotos` coordinates are used for marker placement
  - [ ] 1.2: Check if lon/lat from Panoramax API response are used vs sampled coords
  - [ ] 1.3: Verify coordinate order (lon,lat vs lat,lon)

- [ ] Task 2: Fix marker drift on zoom (AC: #2)
  - [ ] 2.1: Ensure markers use MapLibre Marker or GeoJSON source (not HTML overlay with pixel positioning)
  - [ ] 2.2: If using HTML markers, verify anchor point and map projection binding

- [ ] Task 3: Verify hover highlight works correctly (AC: #3)
