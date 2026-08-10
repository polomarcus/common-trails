---
stepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics', 'step-03-create-stories', 'step-04-final-validation']
status: 'complete'
completedAt: '2026-03-14'
inputDocuments:
  - '_bmad-output/planning-artifacts/prd.md'
  - '_bmad-output/planning-artifacts/architecture.md'
---

# Common Trails - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for Common Trails, decomposing the requirements from the PRD and Architecture into implementable stories.

## Requirements Inventory

### Functional Requirements

- FR1: Rider can create a route by placing waypoints on the map
- FR2: Rider can drag existing waypoints to modify a route
- FR3: Rider can delete a waypoint and the route recalculates automatically through remaining points
- FR4: Rider can insert a waypoint on an existing route segment
- FR5: Rider can override routing by placing waypoints off-heatmap — the router respects their intent instead of snapping back
- FR6: System routes between waypoints following heatmap edges, GR/PR/GT/DFCI trails, and terrain intelligence through a sport-specific cost model
- FR7: System applies signal stacking bonus when multiple data sources converge on the same segment (e.g., heatmap + GR)
- FR8: System recalculates only the affected segment when a waypoint changes, not the entire route
- FR9: Rider can select their sport type (Route, Gravel, VTT, Off-road)
- FR10: Rider can filter the community heatmap by sport type
- FR11: Sport filter is visually grouped with the heatmap layer toggle (not separated)
- FR12: System displays the heatmap with brightness indicating popularity (brighter = more riders)
- FR13: Rider can toggle map layers on/off (Heatmap, DFCI, Mes traces)
- FR14: System shows a simplified layers panel with 3 essential layers visible and additional layers behind "More"
- FR15: Rider can view the elevation profile of their route with slope-gradient colors
- FR16: Elevation profile and heatmap support color blindness (alternative encoding beyond red/green)
- FR17: Rider can authenticate via Strava OAuth
- FR18: Rider can import activities from Strava (traces populate personal and community heatmap)
- FR19: Rider can upload GPX files manually
- FR20: Rider can export a route as GPX file
- FR21: Rider can share a route via URL (viewable without account)
- FR22: System routes using a cascading architecture: client graph → personal traces → straight line
- FR23: Client graph loads z14 tiles and builds routable edges from heatmap, DFCI, and trail data
- FR24: Client graph synchronizes tile loading before attempting route computation (no race conditions)
- FR25: System applies gradient-aware filtering — trails with excessive slope for the selected sport are deprioritized
- FR26: System provides route segment calculation in < 1s client-side
- FR27: Admin can monitor application errors and routing failures via Sentry
- FR28: Admin can track client graph fallback rate (% of routing requests falling back to backend)
- FR29: Admin can monitor Strava import job completion and failures
- FR30: Admin can run reference route E2E test suite to validate routing quality regression
- FR31: Admin can tune routing cost model parameters via configuration (no code deploy)

### NonFunctional Requirements

- NFR1: Client-side route segment calculation completes in < 1s (p95)
- NFR2: Backend fallback route calculation completes in < 3s (p95)
- NFR3: Map initial viewport loads (tiles + heatmap) in < 2s on 4G connection
- NFR4: Static frontend bundle < 2MB gzipped (rural riders on poor mobile coverage)
- NFR5: Route editor interaction (drag/delete/insert waypoint) provides visual feedback within 100ms (optimistic rendering)
- NFR6: All data in transit encrypted via HTTPS (TLS 1.2+)
- NFR7: JWT tokens expire after 7 days; refresh requires re-authentication
- NFR8: Heatmap data enforces K-anonymity (K≥2 in production) — no cell exposes a single rider's trace
- NFR9: Private data (activities, activity_cells) never exposed through public API endpoints
- NFR10: Strava OAuth tokens stored server-side, never sent to frontend
- NFR11: Heatmap visualization readable under deuteranopia and protanopia — use brightness/opacity variation, not color alone
- NFR12: Elevation profile slope gradient provides pattern or luminance variation alongside color encoding
- NFR13: All interactive elements reachable via keyboard navigation
- NFR14: Map controls have minimum touch target size of 44×44px on mobile
- NFR15: Strava OAuth flow completes in < 5s (login redirect → token received)
- NFR16: External routing fallback (BRouter, OSRM) gracefully degrades — timeout after 5s, fall to next cascade level, never block the UI
- NFR17: Strava API rate limits handled gracefully — queue imports, retry with backoff, notify user of delays
- NFR18: System functions without any single external dependency — Strava down → GPX upload works; BRouter down → OSRM fallback; OSRM down → straight line

### Additional Requirements

- AR1: `/readyz` endpoint — returns 503 until background data loaded (Cloud Run startup probe)
- AR2: Feature flags — `NEXT_PUBLIC_FEATURES_MVP_ONLY=true` hides dormant features (trips, forks, PRs, tags)
- AR3: Sentry routing tags — `routing.level`, `routing.method`, `routing.sport` on every routing request
- AR4: Golden test file — JSON with edge inputs + expected costs for cross-language cost model parity testing
- AR5: K-anonymity pytest enumeration test — breaks CI if new endpoint skips K filter
- AR6: Tile preloading fix — viewport-based aggressive preloading before routing fires
- AR7: Cloud Scheduler warm-keeping — ping `/healthz` every 55 min during 7h-22h, min_instances=0

### UX Design Requirements

No UX Design specification document exists. UX requirements are embedded within PRD functional requirements (FR11, FR14, FR16) and NFRs (NFR11-NFR14).

### FR Coverage Map

| FR | Epic | Description |
|----|------|-------------|
| FR1 | Epic 2 | Create route by placing waypoints |
| FR2 | Epic 2 | Drag waypoints to modify route |
| FR3 | Epic 2 | Delete waypoint with auto-recalculation |
| FR4 | Epic 2 | Insert waypoint on existing segment |
| FR5 | Epic 2 | Override routing off-heatmap |
| FR6 | Epic 1 | Route follows heatmap + trails via cost model |
| FR7 | Epic 1 | Signal stacking bonus |
| FR8 | Epic 2 | Per-segment recalculation only |
| FR9 | Epic 3 | Sport type selection |
| FR10 | Epic 3 | Heatmap filter by sport |
| FR11 | Epic 3 | Sport filter grouped with heatmap toggle |
| FR12 | Epic 3 | Heatmap brightness = popularity |
| FR13 | Epic 3 | Layer toggles |
| FR14 | Epic 3 | Simplified layers panel |
| FR15 | Epic 3 | Elevation profile with slope colors |
| FR16 | Epic 3 | Color blindness support |
| FR17 | Epic 4 | Strava OAuth |
| FR18 | Epic 4 | Strava import |
| FR19 | Epic 4 | GPX upload |
| FR20 | Epic 4 | GPX export |
| FR21 | Epic 4 | URL sharing |
| FR22 | Epic 1 | Cascading routing architecture |
| FR23 | Epic 1 | z14 tile loading + edge building |
| FR24 | Epic 1 | Tile sync (no race conditions) |
| FR25 | Epic 1 | Gradient-aware filtering |
| FR26 | Epic 1 | <1s client-side routing |
| FR27 | Epic 5 | Sentry error monitoring |
| FR28 | Epic 5 | Fallback rate tracking |
| FR29 | Epic 5 | Import job monitoring |
| FR30 | Epic 5 | Reference route E2E test suite |
| FR31 | Epic 5 | Config-level cost model tuning |

## Epic List

### Epic 1: Routing Engine Reliability
Users get routes that follow heatmap + GR/PR/GT/DFCI trails correctly in under 1 second — no absurd detours, no tile race conditions, no silent heatmap ignoring.
**FRs covered:** FR6, FR7, FR22, FR23, FR24, FR25, FR26
**ARs covered:** AR4, AR6

### Epic 2: Route Editing Experience
Riders plan a 25km route in under 5 minutes — adding, dragging, deleting, inserting waypoints with instant feedback. Editing one segment doesn't break others. Off-heatmap overrides are respected.
**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR8

### Epic 3: Sport-Aware Map & Accessibility
Riders select their sport and see only relevant heatmap traces. Layers panel is clean and simple. Elevation profile and heatmap are readable by color-blind riders.
**FRs covered:** FR9, FR10, FR11, FR12, FR13, FR14, FR15, FR16

### Epic 4: Data Import, Export & Sharing
Riders import Strava history, upload GPX files, export routes to Garmin, and share URLs with friends — all working reliably with graceful error handling.
**FRs covered:** FR17, FR18, FR19, FR20, FR21

### Epic 5: Platform Reliability & Monitoring
Admin monitors routing quality, fallback rates, and import health. System is production-ready with readiness probes, feature flags, and K-anonymity safety nets.
**FRs covered:** FR27, FR28, FR29, FR30, FR31
**ARs covered:** AR1, AR2, AR3, AR5, AR7

## Epic 1: Routing Engine Reliability

Users get routes that follow heatmap + GR/PR/GT/DFCI trails correctly in under 1 second — no absurd detours, no tile race conditions, no silent heatmap ignoring.

### Story 1.1: Fix tile loading synchronization before route computation

As a rider,
I want the routing engine to wait for map tiles to load before computing my route,
So that my route uses all available heatmap and trail data instead of ignoring it.

**Acceptance Criteria:**

**Given** a rider has placed two waypoints in the Hérault area
**When** the route is computed
**Then** the client graph has loaded all z14 tiles covering the bounding box before Dijkstra runs
**And** no route computation starts while tiles are still pending
**And** a loading indicator shows while tiles are being fetched

**Given** tiles for the area are already cached
**When** the rider places a waypoint
**Then** routing starts immediately without waiting (cache hit)

*Covers: FR24, AR6*

### Story 1.2: Reliable edge construction from z14 tiles

As a rider,
I want the client graph to correctly parse all edges from tiles (heatmap, DFCI, trail),
So that routes can follow community traces and official trails without gaps.

**Acceptance Criteria:**

**Given** z14 tiles contain heatmap edges, DFCI edges, and trail edges
**When** the client graph builds its routable network
**Then** all edge types are parsed and added to the graph with correct coordinates
**And** no edges are dropped due to parsing errors or deduplication bugs
**And** edges from adjacent tiles are connected at shared nodes

**Given** a tile contains edges that cross tile boundaries
**When** the adjacent tile is also loaded
**Then** the cross-boundary edges are connected into a continuous path

*Covers: FR23*

### Story 1.3: Route follows heatmap and trail edges via cost model

As a rider,
I want routes to prefer heatmap trails and official paths (GR/PR/GT/DFCI) over generic OSM roads,
So that my route follows trails real cyclists have ridden.

**Acceptance Criteria:**

**Given** a route segment passes through an area with heatmap coverage
**When** heatmap edges exist within 100m of the straight-line path
**Then** the route follows heatmap edges instead of OSM road edges
**And** the cascade architecture (client graph → personal traces → straight line) is used

**Given** a DFCI track runs parallel to an OSM road between two waypoints
**When** the route is computed for gravel or MTB sport
**Then** the route prefers the DFCI track over the road (VIP snap bias 4.0)

**Given** no heatmap, DFCI, or trail edges exist in an area
**When** the route passes through that area
**Then** the route traces a straight line (no full OSM road network in the graph)

*Covers: FR6, FR22*

### Story 1.4: Signal stacking bonus for converging data sources

As a rider,
I want segments where multiple data sources agree (e.g., heatmap + GR trail) to be strongly preferred,
So that the highest-confidence paths are chosen by the router.

**Acceptance Criteria:**

**Given** a segment is both a popular heatmap trail AND a GR-marked trail
**When** the cost model evaluates this segment
**Then** it receives a compounded bonus stronger than either signal alone

**Given** a segment has heatmap + DFCI + GR convergence
**When** compared to a segment with only heatmap data
**Then** the triple-signal segment is preferred

**Given** two alternative paths exist with similar distance
**When** one has heatmap + trail signals and the other has only OSM data
**Then** the multi-signal path is chosen even if slightly longer

*Covers: FR7*

### Story 1.5: Gradient-aware trail filtering by sport

As a gravel rider,
I want trails with extreme gradients to be deprioritized for my sport,
So that my route doesn't send me up a 25% hiking trail.

**Acceptance Criteria:**

**Given** a GR/PR trail segment has a gradient > 15% and sport is gravel
**When** the cost model evaluates this segment
**Then** it receives a significant penalty compared to a flatter alternative

**Given** the same steep GR/PR segment and sport is MTB
**When** the cost model evaluates this segment
**Then** the penalty is reduced (MTB tolerates steeper gradients than gravel)

**Given** a DFCI track with moderate gradient (< 10%)
**When** evaluated for any off-road sport
**Then** no gradient penalty is applied

*Covers: FR25*

### Story 1.6: Cross-language cost model parity test

As an admin,
I want automated tests confirming Python and TypeScript cost models produce equivalent results,
So that client-side and server-side routing make the same decisions.

**Acceptance Criteria:**

**Given** a golden test file with edge inputs (length, slope, surface, heatmap count, trail type)
**When** both Python (`routing.py`) and TypeScript (`client-graph.ts`) evaluate the same edges
**Then** the computed costs match within 1% tolerance

**Given** a new cost model parameter is changed
**When** the golden test is run
**Then** any drift between Python and TypeScript implementations is caught by CI

**Given** the golden test file contains at least 10 edge scenarios covering all sport types
**When** tests run
**Then** all scenarios pass for both languages

*Covers: AR4, FR26*

### Story 1.9: Dev mode Worker sync

As a developer,
I want `npm run dev` to automatically prebuild the routing Worker,
So that client-side routing works in development without manual build steps.

**Acceptance Criteria:**

**Given** a developer runs `npm run dev`
**When** the dev server starts
**Then** `routing-worker.js` is built from current source before Next.js starts
**And** changes to `routing-worker.ts` or `client-graph.ts` trigger a rebuild

**Given** `routing-worker.js` is stale (older than source files)
**When** the developer starts the dev server
**Then** it is rebuilt automatically

*Covers: FR26, NFR1*

### Story 1.10: Tile loading performance

As a rider,
I want entering route mode to feel instant,
So that I don't wait 10+ seconds on "Chargement des données sentiers..." before I can plan.

**Acceptance Criteria:**

**Given** a rider enters route mode at z14 in a well-covered area
**When** the initial viewport tile load starts
**Then** the first tiles (closest to viewport center) load within 2 seconds
**And** the tile loading does not block waypoint placement or map interaction

**Given** a rider at z14 with a 200% buffer requesting 25+ tiles
**When** the viewport preload fires
**Then** tiles are loaded progressively (center-out) rather than all-at-once
**And** the graph is usable for routing after the first batch of tiles completes

**Given** tiles are already cached in IndexedDB
**When** the rider enters route mode
**Then** the loading indicator does not appear (cache hit is near-instant)

*Covers: FR26, NFR1, NFR5*

### Story 1.11: Routing diagnostics

As a developer/admin,
I want clear feedback on why client routing was or wasn't used,
So that I can diagnose routing quality issues in production.

**Acceptance Criteria:**

**Given** a route is computed
**When** client routing succeeds
**Then** console.debug logs: graph size, snap distances, routing time, detected method

**Given** client routing fails
**When** the system falls back to server routing
**Then** console.debug logs the specific reason: empty graph, snap failed (with distances), dijkstra no-path, Worker timeout

**Given** the routing status badge is visible in the route editor
**When** a segment was computed
**Then** the badge shows whether client graph or server routing was used (e.g., "🔥 heatmap" vs "📡 serveur")

*Covers: FR28*

### Story 1.12: Worker health check

As a rider,
I want the routing engine to detect and recover from Worker failures,
So that routing still works even if the Web Worker is broken or unresponsive.

**Acceptance Criteria:**

**Given** the Worker is created
**When** it does not respond to `init` within 5 seconds
**Then** the system switches to main-thread fallback and logs a warning

**Given** the Worker JS file is stale or corrupted
**When** it fails to parse or crashes on first message
**Then** the system catches the error, switches to main-thread fallback, and shows no user-visible error

**Given** the Worker is healthy
**When** routing is active
**Then** a periodic heartbeat (every 30s) confirms the Worker is alive
**And** if 2 consecutive heartbeats fail, the system switches to fallback

*Covers: FR26, NFR18*

### Story 1.13: Client-side multi-proposal routing

As a rider,
I want the map to instantly show 3 diverse route proposals when I plan a long segment,
So that I can choose the corridor that best matches my ride goals without waiting.

**Acceptance Criteria:**

**Given** a rider places waypoints > 15km apart (or Alt+clicks)
**When** the route is computed
**Then** 3 route proposals are calculated client-side in < 100ms
**And** each proposal follows a different corridor (corridor penalties force diversity)
**And** proposals are displayed with distance, elevation, trail types, and surface stats

**Given** the terrain constrains routing to a single corridor (narrow valley, unique col)
**When** proposals are computed
**Then** the system detects > 50% overlap between all pairs
**And** flags the result as "corridor contraint" in the UI

**Given** the client graph produces < 2 distinct proposals
**When** the proposal computation fails to diversify
**Then** the system falls back to the backend `/routing/proposals` endpoint

*Covers: FR6, FR22, FR26*

## Epic 2: Route Editing Experience

Riders plan a 25km route in under 5 minutes — adding, dragging, deleting, inserting waypoints with instant feedback. Editing one segment doesn't break others. Off-heatmap overrides are respected.

### Story 2.1: Per-segment recalculation on waypoint changes

As a rider,
I want only the affected segment to recalculate when I add, move, or insert a waypoint,
So that my already-fixed segments aren't broken by edits elsewhere.

**Acceptance Criteria:**

**Given** a route with 5 waypoints and 4 computed segments
**When** the rider drags waypoint 3 to a new position
**Then** only segments 2→3 and 3→4 are recalculated
**And** segments 1→2 and 4→5 remain unchanged
**And** the route line updates visually within 100ms (optimistic rendering)

**Given** the rider adds a new waypoint at the end of the route
**When** the route updates
**Then** only the new last segment is computed
**And** all previous segments remain intact

**Given** the rider inserts a waypoint on an existing segment between waypoints 2 and 3
**When** the route updates
**Then** the original segment 2→3 is replaced by two new segments (2→new and new→3)
**And** all other segments remain unchanged

*Covers: FR1, FR2, FR4, FR8*

### Story 2.2: Waypoint deletion with automatic route recalculation

As a rider,
I want deleting a waypoint to automatically recalculate the route through remaining points,
So that I don't have to manually re-route after removing a stop.

**Acceptance Criteria:**

**Given** a route with waypoints A → B → C → D
**When** the rider deletes waypoint B
**Then** the route recalculates the segment A → C automatically
**And** segment C → D remains unchanged
**And** the route line updates without a full page reload

**Given** a route with only 2 waypoints (start and end)
**When** the rider deletes one waypoint
**Then** the route is cleared and the remaining waypoint stays as a single marker

**Given** a route with 3+ waypoints
**When** the rider deletes the first waypoint
**Then** the new first waypoint becomes the start and the first segment recalculates

*Covers: FR3*

### Story 2.3: Off-heatmap waypoint override

As a rider who knows the terrain,
I want to place a waypoint away from heatmap trails and have the router respect my intent,
So that I can take a path I know even if the community hasn't ridden it.

**Acceptance Criteria:**

**Given** a rider places a waypoint on an area with no heatmap coverage
**When** the route is computed to/from that waypoint
**Then** the route goes through the waypoint location without snapping it to the nearest heatmap trail
**And** the route uses the best available path (OSM roads/tracks) to reach that waypoint

**Given** a rider drags a waypoint from a heatmap trail to a nearby road 200m away
**When** the segments are recalculated
**Then** the route follows the rider's intent through the road, not back to the heatmap trail

**Given** adjacent segments connect to heatmap/trail edges
**When** a middle waypoint is placed off-heatmap
**Then** the route transitions from heatmap → off-heatmap → heatmap smoothly without loops or backtracking

*Covers: FR5*

## Epic 3: Sport-Aware Map & Accessibility

Riders select their sport and see only relevant heatmap traces. Layers panel is clean and simple. Elevation profile and heatmap are readable by color-blind riders.

### Story 3.1: Sport filter nested under heatmap layer toggle

As a rider,
I want the sport filter chips to be visually grouped with the heatmap toggle,
So that I understand the chips control which heatmap data is shown.

**Acceptance Criteria:**

**Given** the layers panel is open
**When** the rider looks at the heatmap section
**Then** sport filter chips (Tous/Route/Gravel/VTT/Off-road) appear directly under the heatmap toggle as a sub-filter
**And** the chips are visually indented or nested to show they belong to the heatmap

**Given** the heatmap layer is toggled off
**When** the rider looks at the layers panel
**Then** the sport filter chips are hidden or dimmed (filtering a hidden layer makes no sense)

**Given** the rider selects "Gravel" sport filter
**When** the heatmap is visible
**Then** only gravel-tagged traces appear on the heatmap
**And** brightness still indicates popularity within that sport

*Covers: FR9, FR10, FR11, FR12*

### Story 3.2: Simplified layers panel

As a first-time rider,
I want to see only 3 essential layers by default,
So that I'm not overwhelmed by options I don't understand yet.

**Acceptance Criteria:**

**Given** a rider opens the layers panel
**When** viewing the default state
**Then** 3 essential layers are visible: Heatmap, DFCI, Mes traces
**And** additional layers are behind a "More layers" expandable section

**Given** the rider clicks "More layers"
**When** the section expands
**Then** all remaining layers (satellite, IGN, trail overlays, etc.) become visible
**And** the expanded state persists during the session

**Given** the rider toggles any layer on/off
**When** they close and reopen the layers panel
**Then** their toggle state is preserved

*Covers: FR13, FR14*

### Story 3.3: Color blindness support for heatmap and elevation profile

As a color-blind rider,
I want the heatmap and elevation profile to be readable without relying on red/green color differences,
So that I can use the app effectively.

**Acceptance Criteria:**

**Given** the heatmap is displayed
**When** viewed under deuteranopia or protanopia simulation
**Then** popularity differences are distinguishable through brightness/opacity variation, not color alone

**Given** the elevation profile is displayed with slope-gradient colors
**When** viewed under deuteranopia or protanopia simulation
**Then** slope severity is distinguishable through luminance variation or pattern overlay alongside color

**Given** the route line is displayed on the map
**When** overlaid on the heatmap
**Then** the route line remains clearly visible and distinct from heatmap colors under any color vision condition

*Covers: FR15, FR16, NFR11, NFR12*

## Epic 4: Data Import, Export & Sharing

Riders import Strava history, upload GPX files, export routes to Garmin, and share URLs with friends — all working reliably with graceful error handling.

### Story 4.1: Strava OAuth login and activity import

As a rider with a Strava account,
I want to log in with Strava and import my ride history,
So that my traces populate the community heatmap and I can see my personal rides on the map.

**Acceptance Criteria:**

**Given** a rider clicks "Connect with Strava"
**When** they complete the OAuth flow
**Then** they are redirected back to Common Trails with a valid session
**And** the OAuth flow completes in < 5s

**Given** a rider has authorized Strava access
**When** they trigger activity import
**Then** activities are imported in the background and the rider is notified of progress
**And** imported traces appear on the heatmap after ingestion

**Given** Strava API returns rate limit errors
**When** import is in progress
**Then** the system queues remaining imports, retries with backoff, and notifies the rider of the delay

**Given** Strava is unreachable
**When** a rider tries to connect
**Then** a clear error message is shown and GPX upload is offered as an alternative

*Covers: FR17, FR18, NFR15, NFR17*

### Story 4.2: GPX upload and export

As a rider,
I want to upload GPX files from any source and export my planned routes as GPX,
So that I can use Common Trails with any GPS device.

**Acceptance Criteria:**

**Given** a rider has a GPX file from their GPS device
**When** they upload it via the upload interface
**Then** the activity is imported with coordinates stored verbatim (no simplification, no snapping)
**And** the trace appears in "Mes traces" on the map

**Given** a rider has planned a route with waypoints
**When** they click export GPX
**Then** a GPX file is downloaded containing the full route geometry
**And** the GPX includes elevation data when available

**Given** a rider uploads a ZIP file containing multiple GPX files
**When** the upload completes
**Then** all GPX files in the ZIP are imported as separate activities

*Covers: FR19, FR20*

### Story 4.3: Route sharing via URL

As a rider,
I want to share my route with friends via a simple URL,
So that anyone can view the route without needing an account.

**Acceptance Criteria:**

**Given** a rider has created and saved a route
**When** they copy the route URL
**Then** the URL contains the route identifier and can be opened in any browser

**Given** a non-authenticated user opens a shared route URL
**When** the page loads
**Then** the route is displayed on the map with its full geometry and elevation profile
**And** no login is required to view the route

**Given** the route owner updates the route
**When** someone opens the same URL later
**Then** they see the latest version of the route

*Covers: FR21*

## Epic 5: Platform Reliability & Monitoring

Admin monitors routing quality, fallback rates, and import health. System is production-ready with readiness probes, feature flags, and K-anonymity safety nets.

### Story 5.1: Sentry routing observability tags

As an admin,
I want every routing request tagged with cascade level, method, and sport in Sentry,
So that I can track fallback rates and identify routing quality issues.

**Acceptance Criteria:**

**Given** a routing request is processed
**When** the response is sent
**Then** Sentry captures custom tags: `routing.level` (client/personal/straight), `routing.method` (heatmap/personal/straight_line), `routing.sport` (road/gravel/mtb/offroad)

**Given** an admin opens the Sentry dashboard
**When** they filter by `routing.level`
**Then** they can see the percentage of requests at each cascade level
**And** identify if fallback rate exceeds 5%

**Given** a routing error occurs
**When** Sentry captures the exception
**Then** the routing tags are attached to the error event for diagnosis

*Covers: FR27, FR28, AR3*

### Story 5.2: Readiness probe and Cloud Scheduler warm-keeping

As an admin,
I want a `/readyz` endpoint that returns 503 until background data is loaded,
So that Cloud Run doesn't serve routes without DFCI/trail data during cold starts.

**Acceptance Criteria:**

**Given** the backend has just started
**When** `/readyz` is called before DFCI and trail data have loaded
**Then** it returns HTTP 503

**Given** DFCI edges, trail edges, and enrichment data have finished loading
**When** `/readyz` is called
**Then** it returns HTTP 200

**Given** Cloud Scheduler is configured to ping `/healthz` every 55 minutes during 7h-22h
**When** the schedule fires
**Then** it keeps the Cloud Run instance warm and avoids cold starts during active hours
**And** the instance scales to zero outside 7h-22h

*Covers: AR1, AR7*

### Story 5.3: Feature flags for dormant features

As an admin,
I want dormant features (trips, forks, PRs, tags) hidden behind a feature flag,
So that the MVP UI is clean and focused without deleting existing code.

**Acceptance Criteria:**

**Given** `NEXT_PUBLIC_FEATURES_MVP_ONLY=true` is set
**When** the app renders
**Then** trips page, route PRs section, route tags section, and fork buttons are hidden
**And** navigation links to hidden features are removed

**Given** `NEXT_PUBLIC_FEATURES_MVP_ONLY=false` (or unset)
**When** the app renders
**Then** all features are visible as before

**Given** a user navigates directly to `/trips` via URL while flag is true
**When** the page loads
**Then** the user is redirected to `/map` or shown a "coming soon" message

*Covers: AR2*

### Story 5.4: K-anonymity endpoint enumeration test

As an admin,
I want a CI test that verifies all heatmap endpoints enforce K-anonymity filtering,
So that adding a new endpoint can't accidentally expose individual rider data.

**Acceptance Criteria:**

**Given** the pytest test suite runs
**When** it enumerates all routes registered under `/heatmap/*` and `/routing/graph/*`
**Then** it verifies each endpoint applies the K-anonymity filter (`user_count >= K`)

**Given** a developer adds a new endpoint under `/heatmap/`
**When** that endpoint does not apply the K filter
**Then** the enumeration test fails and CI blocks the merge

*Covers: AR5, NFR8*

### Story 5.5: Reference route E2E test suite

As an admin,
I want an automated E2E test suite with 10 reference routes near Montpellier,
So that routing quality regressions are caught before deployment.

**Acceptance Criteria:**

**Given** 10 reference routes defined with start/end coordinates and expected behavior
**When** the Playwright E2E suite runs
**Then** each route is computed and validated against expected criteria (follows heatmap, uses DFCI/GR, no absurd detours)

**Given** a routing change causes a reference route to take a road instead of a known trail
**When** the test suite runs
**Then** the affected test fails with a clear message about which route deviated

**Given** the reference routes cover gravel, MTB, and mixed sport types
**When** all tests pass
**Then** routing quality is validated across all primary use cases

*Covers: FR30*

### Story 5.6: Config-level cost model tuning and import monitoring

As an admin,
I want to adjust routing cost model parameters without a code deploy and monitor import job health,
So that I can fix routing issues and track data ingestion without downtime.

**Acceptance Criteria:**

**Given** the admin changes a cost model weight in the configuration (env var or config file)
**When** the backend restarts (or config is reloaded)
**Then** routing uses the updated weights without a code change or redeployment

**Given** Strava import jobs are running
**When** the admin checks the import status
**Then** they can see completed, failed, and in-progress import counts via Sentry or API

**Given** an import job fails
**When** the failure is captured
**Then** Sentry records the error with context (user, activity count, failure reason)

*Covers: FR31, FR29*
