---
stepsCompleted: [1, 2, 3, 4]
inputDocuments: []
session_topic: 'Common Trails UX — pain points, friction, and first impressions'
session_goals: 'Prioritized list of UX pain points to fix; Onboarding & first-time user experience ideas'
selected_approach: 'ai-recommended'
techniques_used: ['Role Playing', 'Five Whys', 'SCAMPER Method']
ideas_generated: 26
session_active: false
workflow_completed: true
context_file: ''
---

# Brainstorming Session Results

**Facilitator:** Paulleclercq
**Date:** 2026-03-14

## Session Overview

**Topic:** Common Trails UX — pain points, friction, and first impressions
**Goals:**
1. Prioritized list of UX pain points to fix
2. Onboarding & first-time user experience ideas

### Session Setup

_Brainstorming session focused on improving the Common Trails cycling map app UX for off-road cyclists (gravel/XC MTB). The app currently supports GPX traces, community heatmaps, smart routing, and a route editor. Key areas explored: routing trust, map interaction friction, sidebar usability, sport filtering UX, onboarding flow for new users, and differentiation through trace integrity (Crouzet methodology)._

## Technique Selection

**Approach:** AI-Recommended Techniques
**Analysis Context:** Common Trails UX with focus on pain points and onboarding for off-road cyclists (gravel/XC MTB)

**Recommended Techniques:**

- **Role Playing:** Embody off-road user personas (gravel rider, XC MTB rider) to surface friction points from real user perspectives
- **Five Whys:** Drill into the top pain points to uncover root causes — UI issue vs. missing feature vs. confusing flow
- **SCAMPER Method:** Systematically generate onboarding ideas through 7 lenses (Substitute, Combine, Adapt, Modify, Put to other use, Eliminate, Reverse)

**AI Rationale:** This sequence moves from empathy (understanding pain) → analysis (root causes) → structured generation (concrete solutions), ensuring onboarding ideas address real user needs rather than assumptions.

## Technique Execution Results

### Role Playing — Off-Road User Personas

**Personas explored:** Gravel rider (Cevennes bikepacking), XC MTB rider (Ardèche weekend)

#### Pain Points Identified

**[Pain Point #1]**: Heatmap Sport Filter Missing
_Concept_: As a gravel rider, I want to filter the heatmap by sport type (gravel, MTB, road) to see only relevant community data. Road cyclist heatmap noise hides the off-road gold.
_Novelty_: Not just "show/hide heatmap" — it's about trust. If I see gravel-only traces, I trust the surface is rideable for me.

**[Pain Point #2]**: Routing Ignores Heatmap Trust
_Concept_: When a beautiful heatmap trail exists right next to my route, the routing engine should prefer it over theoretical road networks. The heatmap IS the ground truth — real cyclists rode there. Same for DFCI tracks, PR/GR/GT marked trails.
_Novelty_: This is the Crouzet philosophy — trust the trace, trust the community. Komoot routes theoretically; Common Trails should route empirically.

**[Pain Point #3]**: Absurd Detour Loops
_Concept_: The routing creates weird loops and detours that no real cyclist would ever ride. It feels like the algorithm was never tested by someone actually riding off-road.
_Novelty_: This isn't just a bug — it's a credibility killer. One absurd detour and the gravel rider thinks "this app doesn't understand off-road."

**[Pain Point #4]**: Gradient-Aware Trail Filtering
_Concept_: PR/GR/GT trails are trusted conditionally — only when the gradient is bikeable. A 25% hiking trail is not a gravel route. The trust hierarchy needs a slope gate: heatmap (always) > DFCI (always) > PR/GR/GT (if gradient OK) > OSM (last resort).
_Novelty_: No app does gradient-conditional trail trust today. This could be a genuine differentiator.

**[Pain Point #5]**: Heatmap Proximity Routing Failure
_Concept_: When a heatmap trail runs 50m parallel to the computed route, the router should snap to it. This is the #1 frustration from Komoot — you can SEE the community trail right there, but the algorithm ignores it for some theoretical OSM way.
_Novelty_: This is where Common Trails can kill Komoot. "We route where people actually ride" vs. "we route where the map says roads exist."

**[Pain Point #6]**: Route Editing Latency
_Concept_: Modifying a route feels sluggish — dragging waypoints or changing the route takes too long to respond. For a gravel rider fine-tuning a bikepacking route, this is constant friction. Komoot is snappy here.
_Novelty_: Performance is UX. A 3-second delay on every edit makes route planning feel painful over 15+ waypoints.

**[Pain Point #7]**: Waypoint Delete Doesn't Recalculate
_Concept_: When I delete a waypoint, the route should automatically recalculate through the remaining points. Instead it removes the point without recalculating.
_Novelty_: This feels like a bug more than a design choice. Every routing app recalculates on delete.

**[Pain Point #8]**: No Estimated Time
_Concept_: Distance and elevation are there, but no estimated ride time. For bikepacking planning, time is everything — "Can I make it to the next water point before dark?" Need sport-aware ETA (gravel ≠ MTB ≠ road speeds).
_Novelty_: Time estimation for off-road is hard (surface, gradient, fatigue) but even a rough estimate beats nothing.

**[Pain Point #9]**: URL Sharing Works (positive!)
_Concept_: Direct URL sharing is exactly what this rider expects. Simple, no account needed to view. This is a strength to preserve.

**[Pain Point #10]**: Heatmap Sport Ambiguity (reinforces #1)
_Concept_: Same issue as gravel rider — the MTB rider sees heatmap but can't confirm it's MTB-specific. They're guessing from terrain context. Universal off-road frustration.

**[Pain Point #11]**: No Trail Overlay (DFCI/GR/PR/GT)
_Concept_: The MTB rider wants to SEE the GR/PR/GT/DFCI network on the map as distinct, named, toggleable layers — especially the GTA (Grande Traversée de l'Ardèche). Not visible as named layers currently.
_Novelty_: Komoot doesn't show DFCI. IGN Rando shows GR/PR but has terrible routing. Common Trails showing both heatmap + official trail network = killer combo for off-road.

**[Pain Point #12]**: Waypoint Every 200m = Micromanagement Hell
_Concept_: To follow the GTA + GR + heatmap, the rider has to drop a waypoint every 200 meters because the routing doesn't naturally follow these trails. This is insane for a 20km route — potentially 100 waypoints. The rider doesn't trust the router to follow the heatmap/GR, so they manually "paint" the route point by point.
_Novelty_: The fix isn't "better drag UX" — it's making the routing algorithm heatmap-aware so the rider can trust it with longer segments.

**[Pain Point #13]**: Recalculation Cascade Frustration
_Concept_: Adding a corrective waypoint triggers a recalculation that shifts OTHER segments already fixed. Fix one section, it breaks another — whack-a-mole loop.
_Novelty_: Compounding effect of slow recalc + untrusted routing = exponential frustration.

**[Pain Point #14]**: 10 Minutes vs 3 Minutes — The Komoot Benchmark
_Concept_: A 20km MTB route should take 3 minutes to plan, not 10. Common Trails is 3x slower for route creation. Dealbreaker for adoption.
_Novelty_: Speed-to-value. The rider will go back to Komoot if planning takes 3x longer, even if the heatmap data is better.

**[Pain Point #15]**: Sport Filter Disconnected from Heatmap
_Concept_: The sport filter chips (Tous/Route/Gravel/VTT/Off-road) are at the bottom of the layer panel, visually disconnected from the "Heatmap communautaire" toggle they control. New user has no idea the chips affect the heatmap.
_Novelty_: The feature exists — the UX just hides the connection.

### Five Whys — Root Cause Analysis

#### Cluster 1: Routing Doesn't Follow Heatmap/Trails

```
Surface: Routing ignores heatmap/trails
  └─ Why? Router doesn't have heatmap as routable edges
    └─ Why? Client graph bypasses backend (which has the data)
      └─ Why? Client graph is flaky, falls back to backend
        └─ Why? Tile race conditions + buggy graph construction
          └─ ROOT CAUSE: Client graph was added recently on top of
             a backend-first architecture. It's immature — tile
             loading isn't synchronized, and edge parsing from
             tiles produces incomplete/broken graphs.
```

#### Cluster 2: Route Editing is Slow and Unreliable

```
Surface: Route editing is 3x slower than Komoot
  └─ Why? Every edit = backend round-trip
    └─ Why? Client graph fails (same root as Cluster 1)
  └─ Why? UI blocks during recalculation
    └─ Why? No optimistic rendering + no edit queuing
      └─ Why? Entire route re-renders per segment change
        └─ ROOT CAUSE (branch 2): Route editor architecture is
           synchronous and monolithic — no per-segment state,
           no background refinement, no edit queue.
```

#### Cluster 3: No Sport-Specific Filtering

```
Surface: Can't filter heatmap by sport
  └─ Why? Users don't find the filter
    └─ Why? Sport chips are at bottom, far from heatmap toggle
      └─ Why? Filter was added after layers panel — appended, not integrated
        └─ ROOT CAUSE: Sport filter is not visually grouped
           with the heatmap layer. Should be nested under
           "🔥 Heatmap communautaire" as a sub-filter.
```

### SCAMPER — Onboarding Ideas

**[Onboarding #1]**: Sport Selection on First Login
_Concept_: Right after login, one question: "What do you ride?" → pre-filter heatmap to their sport.

**[Onboarding #2]**: Default Map Center = Montpellier (beta)
_Concept_: During beta, map defaults to Montpellier where community data is densest. First impression = "wow, there's data here."

**[Onboarding #3]**: Community Routes in Sidebar for New Users
_Concept_: New user has no traces → show popular community routes in their area/sport instead of empty sidebar.

**[Onboarding #4]**: Visible Sport-Routing Link
_Concept_: When user selects sport, routing engine visibly confirms: "Routing: Gravel mode 🪨" — clear that the algorithm adapts.

**[Onboarding #5]**: Heatmap Hover Stats
_Concept_: Hover/tap on heatmap segment → tooltip: "Ridden by 23 gravel riders this month." Social proof at segment level.

**[Onboarding #6]**: Trail Cards
_Concept_: Tap a heatmap cluster → card with: distance, surface type, elevation profile, difficulty, rider count, photos. Like AllTrails for off-road cycling.

**[Onboarding #7]**: Suggested Loops
_Concept_: "Popular gravel loops near you" — auto-generated from heatmap density. 3-5 loops with stats. One tap to preview, one tap to start editing.

**[Onboarding #8]**: Simplified Layers Panel
_Concept_: Default to 3 layers: Heatmap, DFCI, Mes traces. Rest behind "More layers" toggle. Progressive disclosure.

**[Onboarding #9]**: Heatmap as Onboarding Guide
_Concept_: No tutorial, no walkthrough. One subtle tooltip: "Brighter = more riders. Tap to explore." The heatmap is self-explanatory.

**[Onboarding #10]**: Kill the Empty State
_Concept_: New user should never see empty sidebar or "you have no traces." Replace with community content. Every screen has content from minute one.

**[Onboarding #11]**: App-Proposed Routes
_Concept_: After login + sport selection, app proposes a route: "Here's a popular gravel loop near Montpellier — 30km, 450m D+, ridden by 38 riders." First value in under 60 seconds.

## Idea Organization and Prioritization

### Thematic Organization

| Theme | Ideas | Summary |
|-------|-------|---------|
| **1. Routing Trust & Reliability** | PP #2, #3, #4, #5, #12, #13 + RC1 | Client graph immaturity → routing ignores heatmap, creates detours |
| **2. Route Editing Performance** | PP #6, #7, #14 + RC2 | Synchronous UI + backend fallback = 3x slower than Komoot |
| **3. Sport-Aware UX** | PP #1, #10, #15 + RC3, OB #4, #8 | Sport filter exists but hidden; layers panel needs simplification |
| **4. First-Time Experience** | OB #1, #2, #3, #9, #10, #11 | Kill empty state, propose routes, heatmap as guide |
| **5. Data Richness & Social Proof** | PP #11, OB #5, #6, #7 | Heatmap hover stats, trail cards, suggested loops, trail overlays |

### Prioritization Results

**User-selected top 3 priorities:**

1. **Routing Trust & Reliability** — Everything falls apart if routing doesn't work
2. **Data Richness & Social Proof** — Make the heatmap Common Trails' killer differentiator
3. **First-Time Experience & Discovery** — Turn "empty app" into "app that knows where to ride"

### Action Plans

#### Priority 1: Routing Trust & Reliability

**Next Steps:**
1. Fix client graph tile race conditions — ensure tiles are loaded before routing attempts
2. Fix graph edge construction — audit tile parsing for missing/broken edges
3. Reduce backend fallback rate — measure current fallback % and set target (<5%)
4. Add heatmap edge priority weighting — client graph should prefer heatmap edges over OSM
5. Add gradient gate for PR/GR/GT edges (bikeable slope threshold)

**Success Indicators:** A 20km MTB route in 3 minutes, not 10. Zero absurd detour loops. Fallback to backend < 5%.

#### Priority 2: Data Richness & Social Proof

**Next Steps:**
1. Heatmap hover tooltip — segment rider count + sport breakdown + last ridden date
2. DFCI/GR/PR/GT as toggleable named trail overlays (distinct from heatmap)
3. Trail cards on tap — distance, surface, elevation mini-profile, rider count
4. Suggested loops engine — cluster heatmap density into popular loops with stats

**Success Indicators:** New user in unknown area (Ardèche scenario) finds a rideable loop in under 60 seconds without creating a route from scratch.

#### Priority 3: First-Time Experience & Discovery

**Next Steps:**
1. Sport selection on first login → pre-filter heatmap
2. Default map to Montpellier in beta (where data is densest)
3. Kill empty state — sidebar shows community routes when user has no traces
4. One-line heatmap tooltip on first visit: "Brighter = more riders. Tap to explore."
5. App-proposed route: "Popular gravel loop near you — 30km, 450m D+"

**Success Indicators:** New user gets value (a route to ride) within 60 seconds of login. Zero empty screens.

### Recommended Execution Order

```
Phase 1 (Foundation)  → Routing Trust (fix client graph)
Phase 2 (Richness)    → Data & Social Proof (heatmap hover, trail overlays)
Phase 3 (Onboarding)  → First-Time Experience (depends on Phase 1+2 working well)
```

### Quick Wins (parallel, low effort)

- Nest sport filter chips under heatmap toggle
- Simplified layers panel (3 essential + "More layers")
- Default map center = Montpellier
- Estimated ride time (sport-aware)

## Session Summary and Insights

**Key Achievements:**
- 26 ideas generated across 3 techniques (Role Playing, Five Whys, SCAMPER)
- 15 pain points identified from 2 off-road user personas
- 3 root causes uncovered through systematic analysis
- 11 onboarding ideas generated through SCAMPER
- 3 priorities with concrete action plans and success metrics

**Breakthrough Insights:**
- The #1 problem is routing trust, not routing features — the data exists, the client graph just can't use it reliably
- "Route where people actually ride" is Common Trails' core differentiator vs. Komoot's theoretical OSM routing
- The heatmap itself IS the onboarding — no tutorial needed, just one tooltip
- Empty states kill momentum — every screen should have community content from minute one
- The Komoot benchmark (3 min for 20km route) is the adoption threshold to beat

**Creative Facilitation Narrative:**
_Session explored Common Trails UX through the lens of off-road cyclists (gravel + XC MTB). The gravel rider in the Cevennes and the MTB rider in Ardèche both converged on the same core frustration: the routing doesn't trust the community data. The Five Whys revealed this is a technical architecture issue (immature client graph), not a missing feature. SCAMPER then flipped the onboarding model from "tool to build routes" to "app that knows where to ride" — leading to the app-proposed routes concept. The session's defining insight: Common Trails' advantage is empirical routing (where people actually ride) vs. Komoot's theoretical routing (where the map says roads exist)._
