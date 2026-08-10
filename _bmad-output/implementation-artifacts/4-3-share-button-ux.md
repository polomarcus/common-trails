# Story 4.3: Share Button UX

Status: pending

## Story

As an existing user with saved routes,
I want a prominent share button on every route view,
So that I can easily share routes with friends via WhatsApp, Telegram, or any app.

## Acceptance Criteria

1. **Given** a user is viewing a route (viewedRoute panel)
   **When** they see the action buttons
   **Then** a share icon button is visible alongside Edit/Export/Fork buttons

2. **Given** a user taps the share button on mobile (navigator.share supported)
   **When** the native share sheet opens
   **Then** it pre-fills with route name as title and the route URL
   **And** the user can send via WhatsApp, Telegram, iMessage, etc.

3. **Given** a user clicks the share button on desktop (no navigator.share)
   **When** the URL is copied to clipboard
   **Then** a toast notification confirms "Link copied!"
   **And** the toast auto-dismisses after 2 seconds

4. **Given** a user shares a route
   **When** the recipient opens the URL
   **Then** the URL format is `/map?route={id}` (existing behavior, no change needed)

## Tasks / Subtasks

- [ ] Task 1: Add share button to viewedRoute action bar (AC: #1)
  - [ ] 1.1: Add share icon (SVG link/share icon) next to existing Edit/Export/Fork buttons
  - [ ] 1.2: Style consistently with existing action buttons

- [ ] Task 2: Implement share action (AC: #2, #3)
  - [ ] 2.1: On click, check `navigator.share` availability
  - [ ] 2.2: If available (mobile): call `navigator.share({ title, url })`
  - [ ] 2.3: If not available (desktop): `navigator.clipboard.writeText(url)` + show toast

- [ ] Task 3: Toast notification component (AC: #3)
  - [ ] 3.1: Simple toast that appears bottom-center, auto-dismisses after 2s
  - [ ] 3.2: "Link copied!" text with checkmark icon
