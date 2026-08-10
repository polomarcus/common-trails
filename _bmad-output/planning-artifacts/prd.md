---
stepsCompleted: ['step-01-init', 'step-02-discovery', 'step-02b-vision', 'step-02c-executive-summary', 'step-03-success', 'step-04-journeys', 'step-05-domain', 'step-06-innovation', 'step-07-project-type', 'step-08-scoping', 'step-09-functional', 'step-10-nonfunctional', 'step-11-polish']
classification:
  projectType: 'web_app'
  domain: 'general'
  complexity: 'medium'
  projectContext: 'brownfield'
inputDocuments:
  - '_bmad-output/project-context.md'
  - '_bmad-output/brainstorming/brainstorming-session-2026-03-14-1400.md'
  - '_bmad-output/implementation-artifacts/tech-spec-fix-routing-quality-proposals-e2e.md'
  - 'docs/vision.md'
  - 'docs/UI.md'
  - 'docs/ROUTING.md'
  - 'docs/routing-architecture.md'
  - 'docs/routing-cost-model.md'
  - 'docs/dataset.md'
  - 'docs/privacy.md'
  - 'docs/development.md'
workflowType: 'prd'
documentCounts:
  briefs: 0
  research: 0
  brainstorming: 1
  projectDocs: 8
  projectContext: 1
  techSpecs: 1
---

# Product Requirements Document — Common Trails

**Author:** Paulleclercq
**Date:** 2026-03-14

## Executive Summary

Common Trails is an open-source off-road route planner for gravel, MTB, and bikepacking.

Existing tools (Komoot, Strava) use heatmap data for routing, but the results can be messy — routes follow other users' traces blindly, creating detours or taking roads when a trail is right there. Cyclists compensate by cross-referencing multiple apps (IGN Rando for GR traces, Komoot for routing, Strava for popularity). Common Trails replaces this workflow with a single tool that combines community heatmaps, official trail networks (GR/GT/PR/DFCI), and gradient-aware terrain intelligence. The difference isn't having heatmap data — it's how the routing engine blends signals into routes a real off-road cyclist would actually ride.

The product targets off-road cyclists in France, starting with the Hérault department where community data is densest. Growth comes through local clubs importing Strava traces — riders import their activities, the local heatmap becomes rich, routing works, word spreads. Data is licensed ODbL — contributors own the heatmap, not the platform.

V1 scope is deliberately narrow: make routing quality excellent in one geography before expanding features or regions.

### What Makes This Special

**Routing quality is the foundation.** The routing engine blends community heatmaps, official trail networks (GR/GT/PR/DFCI), and gradient-aware terrain analysis through a sport-specific cost model. Two waypoints 5km apart on a GR trail → the route follows it perfectly.

**Discovery is the hook.** The heatmap reveals popular routes near you that you've never taken — breaking the habit loop. It's not just a planner, it's a trail discovery engine.

**Open source is the moat.** Community-owned data (ODbL) ensures the platform can't be enshittified. This is a community tool, not a startup.

## Project Classification

- **Type:** Web application (SPA, static export, map-first interface)
- **Domain:** Outdoor recreation / off-road cycling
- **Complexity:** Medium (geospatial routing, multi-source data fusion, client-side graph)
- **Context:** Brownfield — core infrastructure built, routing engine functional but immature. PRD focuses on routing quality, UX, and onboarding.

## Success Criteria

### User Success

- A gravel/MTB route in the Hérault follows heatmap and sentiers balisés (GR/PR/GT/DFCI) without manual micromanagement
- Advanced users can override the routing by placing waypoints off-heatmap — the router respects their intent
- First route creation feels fast and reliable — no absurd detours, no broken recalculations
- A friend uses Common Trails for their Sunday ride without being told how

### Business Success

- **3-month target:** 15 active users (created account + planned at least one route)
- Growth through word-of-mouth in local riding circles — no paid marketing
- At least one local club with multiple members importing traces

### Technical Success

- Client graph routes on heatmap + trail edges with < 5% fallback to backend
- Route editing responsive (< 1s per segment recalculation)
- Stable enough for daily use — no data loss, no showstopper bugs
- Works on mobile browser (not optimized, but functional)

### Measurable Outcomes

| Metric | Target | How to measure |
|--------|--------|----------------|
| Active users (3 months) | 15 | Accounts with ≥ 1 route created |
| Routing quality | Heatmap + sentiers balisés followed | Manual testing on 10 reference routes near Montpellier |
| Route creation speed | Comparable to Komoot | Time to create a 20km route |
| Client graph success rate | > 95% | Backend fallback counter |
| Word-of-mouth signal | 1 unsolicited user | Someone discovers it without being told by you |

## User Journeys

### Journey 1: Léa — Gravel Rider Discovers a New Route

**Persona:** Léa, 32, rides gravel in the Cevennes. Uses Komoot but frustrated by routes that ignore trails. Heard about Common Trails from a riding buddy.

**Opening Scene:** Saturday morning, Léa wants to plan a 40km bikepacking loop in the Hérault for tomorrow. She opens Common Trails for the first time. She logs in with Strava — 30 seconds, done. The map centers on Montpellier and she sees a heatmap filtered to Gravel (her sport was asked at login).

**Rising Action:** She navigates to the Pic Saint-Loup area she knows well. The heatmap shows bright lines on trails she's ridden... but also a trail she's never noticed, 2km east of her usual loop. "47 gravel riders this month" appears when she hovers. She's intrigued. She taps "Plan a route", drops a start point at her car park, and places a waypoint on that unknown trail. The route follows the heatmap and a GR segment perfectly — no micromanaging, no waypoint painting every 200m.

**Climax:** She adds a third waypoint to loop back. The route computes in under a second, follows DFCI tracks and a PR trail. She checks the elevation profile — green and yellow, no brutal climbs. She exports GPX to her Garmin.

**Resolution:** Sunday evening, she's back. The route was perfect — the unknown trail was a beautiful ridge path she'd ridden past for 2 years without knowing. She sends the Common Trails link to her riding group on WhatsApp: "Try this, it's better than Komoot for gravel."

**Requirements revealed:** Strava OAuth login, sport selection at first login, heatmap filtered by sport, heatmap hover stats, route creation following heatmap + GR/PR/DFCI, fast segment recalculation (< 1s), elevation profile with slope colors, GPX export, URL sharing.

### Journey 2: Nico — Club MTB Rider Plans a Group Ride

**Persona:** Nico, 28, XC MTB, rides 3x/week near Montpellier. Member of a 40-person club. Half the club has Strava. Tired of paying €60/year for Komoot Premium.

**Opening Scene:** Nico's club captain asks him to plan next Sunday's group ride — 25km, moderate difficulty, near Saint-Guilhem-le-Désert. Nico opens Common Trails. He imported his Strava traces last week, so the heatmap around Montpellier is rich with his rides and his club mates' traces.

**Rising Action:** He sets sport to VTT and navigates to Saint-Guilhem. The heatmap shows a cluster of MTB traces on the GR de Saint-Guilhem. He drops a start point at the parking lot and a waypoint 8km along the GR. The router follows the GR perfectly. He adds two more waypoints to create a loop, using DFCI tracks for the return. One segment goes on a road — he drags a waypoint onto a nearby heatmap trail to override. The router respects his intent and reroutes through the trail.

**Climax:** Route done in 4 minutes. 25km, 550m D+, all on trails and DFCI tracks. He checks the route — no weird detours, no roads where trails exist. He copies the URL and posts it in the club WhatsApp group: "Sunday ride, who's in?"

**Resolution:** 12 riders show up. The route was solid. Three club members ask "what app is this?" and sign up that evening. They import their Strava traces. The local heatmap grows.

**Requirements revealed:** Strava bulk import, sport filter (VTT), routing follows heatmap + GR + DFCI, user override (drag waypoint off-heatmap), fast route creation (< 5 minutes for 25km), URL sharing, no paywall for core features.

### Journey 3: Paul — Admin Maintaining the Platform

**Persona:** Paul, solo developer, maintains Common Trails. Needs to monitor data quality, routing performance, and user activity without spending hours on ops.

**Opening Scene:** Monday morning. Paul checks if the weekend went smoothly. He opens Sentry and the logs. Did anyone hit a routing error? Did the client graph fallback rate spike?

**Rising Action:** He sees 3 new users signed up over the weekend (Nico's club mates). 8 Strava imports completed, adding 45 new activities to the heatmap. Client graph success rate: 94% — slightly below the 95% target. He checks the fallback logs — most failures are in a specific tile area north of Montpellier where heatmap coverage is sparse.

**Climax:** He reviews the sparse coverage area. The issue is a missing tile edge connection between two DFCI tracks. He adjusts the graph edge threshold in the cost model config — no code deploy needed. He runs the 10 reference routes test suite to verify routing quality hasn't regressed.

**Resolution:** Tests pass. Fallback rate improves to 97% after the config tweak. He checks the ODbL heatmap export — data is clean, K-anonymity is respected. Total time: 20 minutes. Back to building features.

**Requirements revealed:** User signup monitoring, Strava import job status, client graph fallback rate metric, routing error logs, reference route test suite (E2E), heatmap coverage visibility, config-level cost model tuning, K-anonymity compliance monitoring.

### Journey Requirements Summary

| Capability | Léa | Nico | Admin |
|-----------|-----|------|-------|
| Strava OAuth + import | ✓ | ✓ | monitor |
| Sport selection / filter | ✓ | ✓ | |
| Heatmap routing (follows traces) | ✓ | ✓ | quality metrics |
| GR/PR/GT/DFCI routing | ✓ | ✓ | |
| User override (off-heatmap waypoints) | | ✓ | |
| Fast recalculation (< 1s) | ✓ | ✓ | |
| Elevation profile | ✓ | | |
| GPX export | ✓ | | |
| URL sharing | ✓ | ✓ | |
| Heatmap hover stats | ✓ | | |
| Fallback rate monitoring | | | ✓ |
| Import job monitoring | | | ✓ |
| Reference route test suite | | | ✓ |
| Config-level cost model tuning | | | ✓ |

## Innovation & Novel Patterns

### Routing Innovation

**Multi-signal routing blend.** Common Trails is the first open-source tool to combine community heatmaps, official trail networks (GR/GT/PR/DFCI), and gradient-aware terrain analysis into a single sport-specific cost model. Komoot and Strava use heatmaps; IGN Rando has GR/PR trails. None blend all three signals with slope awareness.

**Signal stacking bonus.** When multiple signals converge on the same segment (e.g., heatmap + GT, or heatmap + DFCI + GR), the cost model compounds the bonus. A segment that is both a popular heatmap trail AND a GR route gets a stronger preference than either signal alone. This produces routes that follow the highest-confidence paths — where community experience and official trail designation agree.

**Opinionated but overridable routing.** The router trusts community data by default (heatmap > DFCI > GR/PR > OSM) but respects user intent when a waypoint is deliberately placed off-heatmap. The algorithm has opinions, but the rider has the final word.

**Empirical routing paradigm.** Routing based on "where people actually rode" rather than "where the map says roads exist." This inverts the traditional approach where OSM road data is truth and heatmap is a supplement.

### Validation Approach

- **10 reference routes near Montpellier** — manually verified routes covering gravel, MTB, GR trails, DFCI tracks. Automated E2E tests check routing quality doesn't regress.
- **A/B comparison with Komoot** — same start/end points, compare route quality side by side.
- **User feedback loop** — "Was this route correct?" post-ride signal (future).

### Routing Risks

- **Heatmap sparsity** — In areas with few traces, routing falls back to a straight line. The user always sees where gaps exist, encouraging more trace contributions.
- **Stale trail data** — GR/DFCI reroutes or closures not reflected. Mitigation: heatmap recency signals ("last ridden 3 months ago" vs. "last week") to flag potentially stale segments.
- **Over-trust in heatmap** — A popular but dangerous trail (cliff, private property) could be reinforced by the heatmap. Mitigation: user reports + override capability + disclaimer that routes are algorithmic suggestions.

## Web App Requirements

Single-page application with static export (Next.js `output: 'export'`), served from CDN. Map-first interface using MapLibre GL. No SSR, no real-time features. Routing computation client-side with server fallback.

### Browser Support

- Modern evergreen browsers: Chrome, Safari, Firefox, Edge (latest 2 versions)
- Mobile browsers: Safari iOS, Chrome Android
- No IE11, no legacy browser support

### SEO Strategy

- Landing page optimized for off-road cycling keywords (gravel route planner, VTT planificateur, bikepacking itinéraire, planificateur gravel France)
- Map app behind login — not indexable
- Static landing page (`/`) separate from the app (`/map`)

### Static Export Constraints

- No API routes in Next.js — all API calls go to FastAPI backend
- No `getServerSideProps`, no SSR — all client-side rendering
- Deployment: static files to Cloud Storage + CDN
- SPA routing: `serve.py` handles client-side routing fallback

### Mobile Browser

- Route planning often happens on phone (at the trailhead, in the car)
- Touch interactions: tap to add waypoint, long-press to drag
- Responsive layout, collapsible sidebar for small screens

### Offline (future)

- Not in V1 scope, but architecture should not prevent future PWA/offline support
- Tile caching via service worker for riders in areas with no signal

## Product Scope

### MVP Strategy

**Approach:** Problem-solving MVP — fix the core experience (routing quality) so it's genuinely usable for off-road route planning near Montpellier. No new features until the existing ones work reliably.

**Resource:** Solo developer + AI copilot. Ruthless prioritization is survival.

**Core User Journeys Supported:** Léa (gravel route creation) + Nico (MTB club ride planning)

### MVP Feature Set (Phase 1)

| Feature | Why MVP | Status |
|---------|---------|--------|
| Fix client graph reliability (tile races, edge construction) | Without this, routing is broken | Partially built, needs fixing |
| Routing follows heatmap + GR/PR/GT/DFCI edges | Core differentiator | Partially built, needs fixing |
| Signal stacking bonus (heatmap + trail = stronger preference) | Makes routing "feel right" | In cost model, needs tuning |
| User override (waypoints off-heatmap respected) | Advanced users need control | Needs validation |
| Route editing responsive (optimistic rendering) | 3x Komoot benchmark | Needs rework |
| Sport filter nested under heatmap toggle | Users can't find it currently | UX fix |
| Simplified layers panel (3 essential + "More") | Reduces first-visit cognitive load | UX fix |
| GPX export | Users need to ride the route | Built |
| Mobile browser functional | Route planning happens on phone | Needs testing |
| Color blindness safe heatmap/elevation | Accessibility baseline | Needs implementation |
| Admin monitoring via Sentry | Track errors, fallback rate, import failures | Sentry integrated, needs dashboards/alerts |

**Explicitly NOT in MVP:** Heatmap hover stats, app-proposed routes, sport selection at first login, estimated ride time, trail cards, SEO landing page, custom admin dashboard.

### Phase 2 — Growth (after routing works reliably)

- Sport selection on first login → heatmap pre-filter
- Visible sport-routing confirmation ("Routing: Gravel mode") — makes the sport↔routing connection explicit
- App-proposed routes ("Popular gravel loop near you")
- Heatmap hover stats ("23 gravel riders this month")
- Kill empty state — community routes for new users
- Heatmap onboarding tooltip: "Brighter = more riders. Tap to explore." — no tutorial needed
- Estimated ride time (sport-aware)
- Default map center = Montpellier in beta
- SEO landing page for off-road cycling keywords
- Garmin Connect / Komoot GPX import (reduce Strava dependency)

### Phase 3 — Expansion (after 15+ active users and word-of-mouth signal)

- Geographic expansion beyond Hérault (Ardèche, Alps, Brittany...)
- Trail cards (distance, surface, elevation, rider count)
- Suggested loops engine from heatmap density clustering
- Trail difficulty / technicality indicators
- Bikepacking logistics (water points, resupply)
- Club features (shared routes, group heatmap)
- DFCI/GR/GT as distinct named toggleable overlays
- Mobile app (PWA or native)

### Project Risks

**Technical:** Client graph immaturity is the #1 risk. Mitigation: fix tile races and edge construction before any new features. 10 reference route E2E tests as regression safety net.

**Market:** Cold start outside Montpellier. Mitigation: don't expand geographically until routing quality is proven locally. One city done right > five cities done poorly.

**Resource:** Solo dev burnout. Mitigation: MVP scope is deliberately minimal — 11 items, mostly fixing existing features. Sentry for monitoring instead of custom admin tools.

**Platform:** Strava API dependency. Mitigation: GPX upload always works as fallback. Phase 2 adds Garmin/Komoot import alternatives.

## Functional Requirements

### Route Planning

- FR1: Rider can create a route by placing waypoints on the map
- FR2: Rider can drag existing waypoints to modify a route
- FR3: Rider can delete a waypoint and the route recalculates automatically through remaining points
- FR4: Rider can insert a waypoint on an existing route segment
- FR5: Rider can override routing by placing waypoints off-heatmap — the router respects their intent instead of snapping back
- FR6: System routes between waypoints following heatmap edges, GR/PR/GT/DFCI trails, and terrain intelligence through a sport-specific cost model
- FR7: System applies signal stacking bonus when multiple data sources converge on the same segment (e.g., heatmap + GR)
- FR8: System recalculates only the affected segment when a waypoint changes, not the entire route

### Sport & Heatmap

- FR9: Rider can select their sport type (Route, Gravel, VTT, Off-road)
- FR10: Rider can filter the community heatmap by sport type
- FR11: Sport filter is visually grouped with the heatmap layer toggle (not separated)
- FR12: System displays the heatmap with brightness indicating popularity (brighter = more riders)

### Map & Layers

- FR13: Rider can toggle map layers on/off (Heatmap, DFCI, Mes traces)
- FR14: System shows a simplified layers panel with 3 essential layers visible and additional layers behind "More"
- FR15: Rider can view the elevation profile of their route with slope-gradient colors
- FR16: Elevation profile and heatmap support color blindness (alternative encoding beyond red/green)

### Data Import & Export

- FR17: Rider can authenticate via Strava OAuth
- FR18: Rider can import activities from Strava (traces populate personal and community heatmap)
- FR19: Rider can upload GPX files manually
- FR20: Rider can export a route as GPX file
- FR21: Rider can share a route via URL (viewable without account)

### Routing Engine

- FR22: System routes using a cascading architecture: client graph → personal traces → straight line
- FR23: Client graph loads z14 tiles and builds routable edges from heatmap, DFCI, and trail data
- FR24: Client graph synchronizes tile loading before attempting route computation (no race conditions)
- FR25: System applies gradient-aware filtering — trails with excessive slope for the selected sport are deprioritized
- FR26: System provides route segment calculation in < 1s client-side

### Admin & Monitoring

- FR27: Admin can monitor application errors and routing failures via Sentry
- FR28: Admin can track client graph fallback rate (% of routing requests falling back to backend)
- FR29: Admin can monitor Strava import job completion and failures
- FR30: Admin can run reference route E2E test suite to validate routing quality regression
- FR31: Admin can tune routing cost model parameters via configuration (no code deploy)

## Non-Functional Requirements

### Performance

- NFR1: Client-side route segment calculation completes in < 1s (p95)
- NFR2: Backend fallback route calculation completes in < 3s (p95)
- NFR3: Map initial viewport loads (tiles + heatmap) in < 2s on 4G connection
- NFR4: Static frontend bundle < 2MB gzipped (rural riders on poor mobile coverage)
- NFR5: Route editor interaction (drag/delete/insert waypoint) provides visual feedback within 100ms (optimistic rendering)

### Security

- NFR6: All data in transit encrypted via HTTPS (TLS 1.2+)
- NFR7: JWT tokens expire after 7 days; refresh requires re-authentication
- NFR8: Heatmap data enforces K-anonymity (K≥2 in production) — no cell exposes a single rider's trace
- NFR9: Private data (activities, activity_cells) never exposed through public API endpoints
- NFR10: Strava OAuth tokens stored server-side, never sent to frontend

### Accessibility

- NFR11: Heatmap visualization readable under deuteranopia and protanopia (most common color blindness forms) — use brightness/opacity variation, not color alone
- NFR12: Elevation profile slope gradient provides pattern or luminance variation alongside color encoding
- NFR13: All interactive elements reachable via keyboard navigation
- NFR14: Map controls have minimum touch target size of 44×44px on mobile

### Integration

- NFR15: Strava OAuth flow completes in < 5s (login redirect → token received)
- NFR16: Routing fallback gracefully degrades — when no known edges exist, straight line is shown rather than an unverified route
- NFR17: Strava API rate limits handled gracefully — queue imports, retry with backoff, notify user of delays
- NFR18: System functions without any single external dependency — Strava down → GPX upload works; routing is fully client-side (no external routing API dependency)
