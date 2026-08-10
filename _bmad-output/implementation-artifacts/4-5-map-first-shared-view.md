# Story 4.5: Map-First Shared Route View

Status: pending

## Story

As a recipient opening a shared route link,
I want to see a full-screen interactive map with the route,
So that I immediately understand the route geography without scrolling.

## Acceptance Criteria

1. **Given** a user opens `/map?route={id}`
   **When** the page loads
   **Then** the map occupies the full viewport
   **And** the route line is prominently visible on the map

2. **Given** the shared view is loaded
   **When** the user sees the stats overlay
   **Then** a compact badge shows: route name, distance, D+, sport icon
   **And** the badge does not obscure the route on the map

3. **Given** the shared view on desktop
   **When** the elevation profile is shown
   **Then** it is displayed in a collapsible panel (expanded by default on desktop)
   **And** collapsing it gives more map space

4. **Given** the shared view on mobile (viewport < 768px)
   **When** the page loads
   **Then** the elevation profile is collapsed by default
   **And** the user can swipe/tap to expand it

5. **Given** a non-authenticated user opens the shared view
   **When** they see the page
   **Then** a subtle sign-up CTA is visible (e.g. top-right "Sign up" button)
   **And** no login wall or nag popup appears

## Architectural Context

Current viewedRoute panel (lines 6215-6628 in page.tsx) is a bottom sheet that takes ~45% of the viewport for elevation + surface + Panoramax. The redesign should:
- Make the map full-screen (100vh)
- Float a compact stats card over the map (absolute positioned)
- Make elevation profile a collapsible overlay or bottom drawer
- Keep surface classification and Panoramax accessible but not dominant

## Tasks / Subtasks

- [ ] Task 1: Redesign viewedRoute panel layout (AC: #1, #2)
  - [ ] 1.1: Replace bottom panel with floating stats card (top-left or bottom-left)
  - [ ] 1.2: Stats card: route name, sport icon, distance, elevation gain
  - [ ] 1.3: Map takes full viewport height when viewing a shared route

- [ ] Task 2: Collapsible elevation profile (AC: #3, #4)
  - [ ] 2.1: Elevation profile in a drawer/panel that can collapse
  - [ ] 2.2: Desktop: expanded by default, with collapse toggle
  - [ ] 2.3: Mobile: collapsed by default, expand on tap

- [ ] Task 3: Sign-up CTA for non-authenticated users (AC: #5)
  - [ ] 3.1: Detect if user is not logged in
  - [ ] 3.2: Show subtle "Sign up" button in top-right area
  - [ ] 3.3: No popup, no wall — just a visible button

- [ ] Task 4: Action buttons (share, export, fork, edit) in stats card
  - [ ] 4.1: Compact icon row in the stats card
  - [ ] 4.2: Share button from Story 4.3 included here
