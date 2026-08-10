---
stepsCompleted: [1, 2, 3, 4, 5]
inputDocuments:
  - '_bmad-output/project-context.md'
  - '_bmad-output/planning-artifacts/prd.md'
  - '_bmad-output/planning-artifacts/epics.md'
date: '2026-03-16'
author: Paulleclercq
---

# Product Brief: Common Trails — Route Sharing & Collections

## Executive Summary

Common Trails' shared route page is the app's front door — the first thing non-users see when a rider shares a link. Today the shared view splits the viewport 55/45 between map and elevation profile, has no multi-route support, and produces a generic link preview. This makes it a dead-end rather than an invitation.

**Goal:** Transform the shared route experience into the #1 source of new user discovery. A shared link should be beautiful enough to click, informative enough to understand, and compelling enough to sign up.

**Scope:** Redesigned map-first shared view, multi-route collections (2-4 routes via `?compare=id2,id3`), dynamic OG preview with route stats, share button UX with native sharing, mobile-first responsive layout, and Panoramax marker bug fix.

## Problem Statement

### Current state
- Shared view (`/map?route={id}`) dedicates ~55% to map, ~45% to elevation profile — elevation dominates mobile viewports, pushing the map below the fold on phones
- No multi-route display — club captains share 3 separate Komoot links that nobody clicks through
- OG preview is nonexistent (no meta tags) — links look like spam in WhatsApp/Telegram
- Panoramax photo markers cluster in wrong positions and drift on zoom
- No share button exists — users must manually copy the browser URL
- No progressive loading — full MapLibre + tiles + data = slow first paint on mobile 4G

### Competitive landscape
- **Komoot:** Image-first (not map-first), no multi-route, but excellent OG previews with photos
- **Strava:** Stats-first with tiny map, no multi-route, heavy nag walls
- **RideWithGPS:** Best map UX of competitors, but collections are paid-tier only
- **Nobody** shows community heatmap context on shared views — this is our unique angle

## Proposed Solution

### Phase 1 — Core Sharing Experience

#### 1. Map-first shared view
- Full-screen interactive MapLibre map with route overlay
- Compact stats badge overlay (distance, D+, sport icon) — always visible
- Collapsible elevation profile (collapsed by default on mobile, expanded on desktop)
- Community heatmap + DFCI + trail layers visible by default (with minimal legend for newcomers)
- Progressive loading: skeleton UI with route name + stats visible in <500ms, map tiles load behind

#### 2. Share button UX
- Prominent share icon on every route card + route detail view
- Mobile: `navigator.share()` for native WhatsApp/Telegram/iMessage picker
- Desktop: copy-link button with toast confirmation
- Share action generates a clean URL: `/routes/{slug}` or `/map?route={id}`

#### 3. Multi-route collections
- "Compare routes" button on My Routes page — select 2-4 routes → generates shareable URL
- URL format: `/map?route={id}&compare={id2},{id3}`
- Route limit: 4 max
- Side panel listing routes with color swatch + name + key stats (distance, D+)
- Tap route on map → highlights + shows details
- Toggle visibility per route
- Colorblind-safe palette (vary line style: solid/dashed/dotted, not just hue)
- Backend: new `GET /routes/batch?ids=1,2,3` endpoint for single-request multi-route fetch

#### 4. Dynamic OG preview
- Cloud Run serverless endpoint generates OG image per route
- Template: branded card with route name + distance + D+ + sport icon + mini map silhouette
- Returns HTML with proper `og:title`, `og:description`, `og:image` meta tags
- Static export constraint: shared links redirect through OG endpoint first, then client app
- Phase 1 minimum: text-based card (route name + stats on branded background, no map render)

#### 5. Mobile-first layout
- Bottom sheet for route stats (swipe up for elevation profile)
- Full-screen map by default
- Touch-optimized: large tap targets, pinch-to-zoom
- Lazy-load secondary layers (heatmap/DFCI) after initial route display

#### 6. Panoramax marker fix
- Fix clustering/positioning bug (markers in wrong location)
- Fix drift-on-zoom behavior

### Privacy defaults
- Shared view shows route data only — no user profile link, no activity history
- Creator name optional (toggle: "Show my name on shared view")
- Unlisted routes: shareable by URL, not indexed in public discovery
- Phase 1 default: route data + stats only, no user identity exposed

### Phase 2 — Deferred
- SEO: SSR/pre-rendering for public routes (Google indexing)
- Voting/reactions on collections ("which route do you prefer?")
- GPX download from shared view (policy decision pending)
- Route labels in collections ("Option A: coastal, Option B: hills")
- Photo attachments on shared routes
- Map thumbnail in OG preview (server-side map rendering)

## Key Differentiators

1. **Map-first sharing** — unoccupied territory, every competitor puts something else first
2. **Heatmap context** — recipients see *why* this path was chosen (community riding data visible)
3. **Free multi-route comparison** — RWGPS charges for this, others don't have it
4. **Surface data on free share** — RWGPS gates behind premium
5. **Zero-friction viewing** — no account wall, no nag popups, subtle sign-up CTA

## Risk Register

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| OG preview looks generic → low click-through | High | High | P0: Cloud Run endpoint with stats, not logo-only |
| No share button → users don't discover sharing | Very High | High | P0: prominent share icon + native sharing |
| Slow mobile load → high bounce rate | Medium-High | High | P0: progressive loading, skeleton UI |
| Confusing collections (no legend) | Medium | Medium | P1: color swatch panel, route toggle, line styles |
| Privacy exposure via shared links | Low | Critical | P1: anonymous by default, no profile links |
| No SEO → zero organic discovery | Medium | Low (Phase 1) | P2: accept link-driven only, plan SSR later |

## Success Metrics (Targets)

- Shared links become #1 source of new user discovery within 6 months
- OG preview click-through rate > 30% (vs industry ~2-5% for unknown brands)
- Mobile bounce rate < 40% on shared route pages
- Multi-route collections created by > 20% of active users with 3+ routes
- Signup conversion from shared view > 5%

## Target Users

### Primary personas (design for these first)

| Persona | Who | Key behavior | Success looks like |
|---------|-----|-------------|-------------------|
| **Existing User** | Has routes on Common Trails, wants to share | Shares 1 route to a friend or social media | One-tap share, beautiful preview, friend clicks through |
| **Club Captain** | Organizes group rides, manages 10-30 riders | Shares 2-4 route proposals for group vote | Multi-route link in WhatsApp, everyone sees all options at once |

### Secondary personas (benefit but don't drive design decisions)

| Persona | Who | Relevance |
|---------|-----|-----------|
| **Solo Bikepacker** | Plans multi-day, shares for feedback | Same as Existing User but values elevation/surface data more |
| **Curious Friend** | Non-cyclist receiving a link on mobile | The conversion target — zero-friction viewing matters most for them |

### Primary conversion funnel
Existing User / Club Captain **shares** → Curious Friend **clicks OG preview** → **views map** (no account) → **signs up** (subtle CTA)

## Scope & Boundaries

### Phase 1 — In Scope

| # | Feature | Primary persona | Effort |
|---|---------|----------------|--------|
| 1 | Share button (native share + copy-link) | Existing User | Small |
| 2 | Map-first shared view (full-screen map, stats overlay, collapsible elevation) | Both | Medium |
| 3 | Multi-route collections (compare UI, `?compare=` URL, legend panel, 4 max) | Club Captain | Medium-Large |
| 4 | Batch route endpoint (`GET /routes/batch?ids=`) | Club Captain | Small |
| 5 | Dynamic OG preview (Cloud Run endpoint, stats on branded template) | Both | Medium |
| 6 | Mobile-first responsive layout (bottom sheet, skeleton UI, progressive loading) | Both | Medium |
| 7 | Panoramax marker fix (position + zoom drift) | Existing User | Small |
| 8 | Privacy defaults (anonymous sharing, no profile exposure) | Both | Small |

### Phase 2 — Out of Scope

- SEO / SSR for public routes
- Voting/reactions on collections
- GPX download from shared view
- Route labels/descriptions in collections
- Photo attachments on shared routes
- Map thumbnail in OG image (server-side map rendering)
- Newcomer heatmap legend

### Key Architectural Constraint

Static export (`output: 'export'`) prevents per-route OG meta tags at build time. The Cloud Run OG endpoint is the minimum viable solution — a single serverless function that returns HTML with proper meta tags, not an architecture migration to SSR.

---

*Product brief complete. Elicitation methods applied: User Persona Focus Group, Comparative Analysis Matrix, Critique and Refine, Pre-mortem Analysis.*

*Next steps: PRD update → Epic/story breakdown → Architecture decisions → Implementation.*
