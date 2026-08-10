# Story 6.1: Base Layer Switcher (Satellite, Topo, OpenCycle)

Status: ready-for-dev

## Story

As a traceur planning a route,
I want to switch between satellite imagery, topographic maps, and OpenCycle overlay,
So that I can cross-reference terrain reality with map data — the way expert traceurs work.

## Context (Crouzet methodology)

Crouzet describes his core workflow: "heatmap à 50% de transparence, image satellite à 50%, et fond carto OpenCycle." This layered approach is how experienced riders discover rideable paths invisible on any single map source. Currently CC only offers the default MapLibre style — no satellite, no topo, no OpenCycle.

## Acceptance Criteria

1. **Given** a rider is on the map page
   **When** they click the base layer control
   **Then** a popover shows 3-4 base layer options with thumbnail previews:
   - Default (MapLibre vector tiles — current)
   - Satellite (aerial imagery)
   - Topo IGN (French IGN topo 25k via WMTS)
   - OpenCycle (OpenCycleMap / Thunderforest)

2. **Given** a rider selects "Satellite" base layer
   **When** the map re-renders
   **Then** the satellite raster tiles replace the vector tiles
   **And** all overlays (heatmap, DFCI, route, traces) remain visible on top
   **And** the heatmap opacity automatically increases to 70% for readability on dark imagery

3. **Given** a rider has selected a non-default base layer
   **When** they reload the page or return later
   **Then** their base layer preference is persisted (localStorage)

4. **Given** a base layer uses a raster tile source with an API key (Thunderforest, IGN)
   **When** the API key is missing or invalid
   **Then** that option is hidden or greyed out with "Clé API requise"
   **And** the app never crashes — graceful fallback to default

5. **Given** a rider is in route editing mode with satellite base layer
   **When** the heatmap overlay is active
   **Then** the combined view shows heatmap trails over real terrain — enabling the Crouzet "cross-reference" workflow

## Tasks / Subtasks

- [ ] Task 1: Create BaseLayerSwitcher component (AC: #1)
  - [ ] 1.1: Floating control button (bottom-right area, near existing layer controls)
  - [ ] 1.2: Popover with thumbnail grid (2x2) for each base layer option
  - [ ] 1.3: Active layer highlighted, click to switch

- [ ] Task 2: Implement raster tile source swapping (AC: #2)
  - [ ] 2.1: Define base layer configs: id, name, thumbnail, tile URL template, attribution
  - [ ] 2.2: On switch: remove current base sources/layers, add new raster source + layer at z-index 0
  - [ ] 2.3: For vector→raster switch: keep MapLibre style layers that are overlays (heatmap, route, DFCI)
  - [ ] 2.4: Auto-adjust heatmap opacity based on base layer brightness (satellite = 70%, others = 50%)

- [ ] Task 3: Tile sources configuration (AC: #4)
  - [ ] 3.1: Satellite: MapTiler Satellite or Esri World Imagery (free tier)
  - [ ] 3.2: IGN: `data.geopf.fr` WMTS (free, no API key needed since 2023 IGN open data)
  - [ ] 3.3: OpenCycle: Thunderforest tiles (API key via env var `NEXT_PUBLIC_THUNDERFOREST_KEY`)
  - [ ] 3.4: Fallback: hide unavailable layers, never break the app

- [ ] Task 4: Persist preference (AC: #3)
  - [ ] 4.1: Save selected base layer id to localStorage `cc-base-layer`
  - [ ] 4.2: On map init, restore saved preference (default if invalid/missing)

## Dev Notes

### Tile Sources (free/open)

- **Satellite**: Esri World Imagery (`server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`) — free, attribution required
- **IGN Topo**: `data.geopf.fr` WMTS — French government open data since Jan 2023, no key needed
- **OpenCycle**: Thunderforest — free tier 150k tiles/month, needs API key

### MapLibre Considerations

- Switching from vector to raster base = replacing the style's base layers, not the full style
- Must preserve overlay sources (heatmap-trails, route-draft, dfci, etc.)
- `map.getStyle().layers` can be filtered by source to identify overlays vs base

## References

- Crouzet article: "heatmap le traçage social pour gravel et VTT" (2022)
- IGN Géoplateforme: https://geoservices.ign.fr/
- MapLibre raster source docs
