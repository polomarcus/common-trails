# Story 6.4: Waypoint Annotations & Mini-Roadbook

Status: ready-for-dev

## Story

As a traceur sharing a route with other riders,
I want to annotate specific points along my trace with notes and icons,
So that my route becomes a narrative — a roadbook that guides riders through the experience, not just the geometry.

## Context (Crouzet methodology)

Crouzet describes GPS routing as "un nouvel art narratif" — the traceur tells a story through their route. Currently CC routes are pure geometry (waypoints + line). A traceur can't annotate "attention gué ici", "vue panoramique exceptionnelle", "ravitaillement boulangerie" along the trace. These annotations transform a GPX file from navigation data into a guided experience — and they export into the GPX as `<wpt>` elements, making them visible on Garmin/Coros GPS devices.

## Acceptance Criteria

1. **Given** a rider is editing their own route
   **When** they right-click (or long-press on mobile) on a point along the route line
   **Then** a context menu appears with "Ajouter une annotation"
   **And** clicking it opens an annotation editor at that position

2. **Given** the annotation editor is open
   **When** the rider fills in the form
   **Then** they can set:
   - Icon type (from preset list: water, food, viewpoint, danger, info, photo, shelter, bike-shop)
   - Short text (max 140 chars — e.g., "Gué profond après pluie, pousser le vélo")
   **And** the annotation is saved and displayed on the map

3. **Given** a route has annotations
   **When** any user views the route (public or shared)
   **Then** annotations are displayed as icons on the map at their positions
   **And** clicking an icon shows the annotation text in a popup

4. **Given** a route with annotations is exported as GPX
   **When** the GPX file is loaded on a GPS device
   **Then** annotations are included as `<wpt>` elements with `<name>` (icon type) and `<desc>` (text)
   **And** GPS devices display them as waypoints along the track

5. **Given** a route has 3+ annotations
   **When** the route detail panel is open
   **Then** a "Roadbook" section lists all annotations in route-order (by distance from start)
   **And** each entry shows: km marker, icon, text

6. **Given** a rider is editing annotations
   **When** they click an existing annotation icon on the map
   **Then** they can edit the text, change the icon, or delete the annotation

## Tasks / Subtasks

- [ ] Task 1: Backend — annotation model + API (AC: #1, #2, #6)
  - [ ] 1.1: Add `RouteAnnotation` model: id, route_id, position (lon/lat), distance_from_start_m, icon_type, text, created_at
  - [ ] 1.2: `POST /routes/{id}/annotations` — create annotation (owner only)
  - [ ] 1.3: `GET /routes/{id}/annotations` — list annotations (public for public routes)
  - [ ] 1.4: `PUT /routes/{id}/annotations/{ann_id}` — edit (owner only)
  - [ ] 1.5: `DELETE /routes/{id}/annotations/{ann_id}` — delete (owner only)
  - [ ] 1.6: Alembic migration for route_annotations table

- [ ] Task 2: Frontend — annotation editor (AC: #1, #2, #6)
  - [ ] 2.1: Right-click / long-press handler on route line → context menu
  - [ ] 2.2: Annotation form: icon selector (grid of 8 preset icons) + text input
  - [ ] 2.3: Save → POST to API → add marker to map

- [ ] Task 3: Frontend — annotation display (AC: #3)
  - [ ] 3.1: Load annotations with route data
  - [ ] 3.2: MapLibre symbol layer for annotation icons (sprite or emoji fallback)
  - [ ] 3.3: Click handler → popup with annotation text

- [ ] Task 4: Roadbook panel (AC: #5)
  - [ ] 4.1: In route detail panel, section "Roadbook" if annotations exist
  - [ ] 4.2: List sorted by distance_from_start_m
  - [ ] 4.3: Each entry: `km 12.3 🚰 "Fontaine au village — eau potable"`

- [ ] Task 5: GPX export with waypoints (AC: #4)
  - [ ] 5.1: In `/routes/{id}/gpx` endpoint, add `<wpt>` elements for each annotation
  - [ ] 5.2: `<wpt lat="" lon=""><name>{icon_emoji} {text_short}</name><desc>{text_full}</desc><sym>{icon_type}</sym></wpt>`
  - [ ] 5.3: Standard GPX waypoint format — compatible with Garmin/Coros/Wahoo

## Dev Notes

### Icon Types (Preset List)

| Icon | Key | Usage |
|---|---|---|
| 🚰 | `water` | Water point, fountain |
| 🍞 | `food` | Bakery, restaurant, shop |
| 👁️ | `viewpoint` | Panoramic view, scenic point |
| ⚠️ | `danger` | Hazard, difficult passage |
| ℹ️ | `info` | General info, navigation note |
| 📸 | `photo` | Photo spot |
| 🏠 | `shelter` | Shelter, bivouac, refuge |
| 🔧 | `bike-shop` | Bike shop, repair point |

### Position Calculation

When user right-clicks on the route line, we need to:
1. Find the nearest point on the route geometry to the click position
2. Calculate `distance_from_start_m` along the route to that point
3. Store the exact snapped position (on the line, not the click position)

### Fork Behavior

When a route is forked, annotations are **not copied** to the fork. The fork author creates their own narrative. This aligns with Crouzet's view that the narrative is personal — you don't copy someone else's story, you write your own.

## References

- Crouzet article 2: "un nouvel art narratif" — GPS routing as storytelling
- GPX 1.1 spec: `<wpt>` element
- Existing GPX export: `backend/app/api/routes.py`
