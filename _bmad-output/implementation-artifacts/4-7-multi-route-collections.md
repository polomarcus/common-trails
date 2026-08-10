# Story 4.7: Multi-Route Collections

Status: pending

## Story

As a club captain,
I want to share 2-4 route proposals in a single link,
So that my group can see all options on one map and compare them.

## Acceptance Criteria

1. **Given** a user opens `/map?route={id}&compare={id2},{id3}`
   **When** the page loads
   **Then** all routes are displayed on the map simultaneously with distinct colors
   **And** the map fits to show all routes

2. **Given** a multi-route view is loaded
   **When** the user sees the legend panel
   **Then** each route has a color swatch, name, distance, and D+
   **And** the user can toggle individual route visibility

3. **Given** a user taps a route line on the map
   **When** the route is selected
   **Then** it is highlighted (thicker line) and its details expand in the legend

4. **Given** the multi-route view
   **When** routes overlap
   **Then** colorblind-safe styling differentiates them (solid/dashed/dotted + distinct hues)

5. **Given** a user is on "My Routes" or public routes list
   **When** they want to create a comparison
   **Then** a "Compare" mode lets them select 2-4 routes and generates the shareable URL

## Depends On

- Story 4.6: Batch Route Endpoint (for fetching multiple routes in one call)

## Tasks / Subtasks

- [ ] Task 1: Parse `compare` URL parameter (AC: #1)
  - [ ] 1.1: In route loading logic, detect `compare` param
  - [ ] 1.2: Fetch all routes via batch endpoint
  - [ ] 1.3: Store in `comparedRoutes` state array

- [ ] Task 2: Multi-route map rendering (AC: #1, #4)
  - [ ] 2.1: Add MapLibre sources/layers for each compared route
  - [ ] 2.2: Color palette: 4 colorblind-safe colors with distinct line styles
  - [ ] 2.3: Fit map bounds to encompass all routes

- [ ] Task 3: Legend panel (AC: #2, #3)
  - [ ] 3.1: Side panel or bottom sheet listing all routes
  - [ ] 3.2: Color swatch + name + distance + D+ per route
  - [ ] 3.3: Toggle visibility checkbox per route
  - [ ] 3.4: Click to highlight/select a route

- [ ] Task 4: Compare mode in route list (AC: #5)
  - [ ] 4.1: "Compare" toggle button in sidebar
  - [ ] 4.2: Multi-select checkboxes on route cards (max 4)
  - [ ] 4.3: "Share comparison" button generates URL and triggers share action
