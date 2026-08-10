/**
 * init-map-layers.ts — view-only map layer + event setup for /map.
 *
 * The in-app WASM router + drag-edit route editor were decommissioned
 * (chore/decommission-wasm-routing). This module now builds ONLY the
 * read-only display layers (heatmap, DFCI, user activities/cells/photos,
 * selected-activity highlight, variant overlays) and wires the view-only
 * interactions (trace hover popup, activity selection, heatmap inspect,
 * photo clusters).
 *
 * Two exported functions:
 *   1. addMapSourcesAndLayers — pure layer/source setup
 *   2. wireMapEventHandlers   — view-only event wiring (refs + setters)
 */

import { WAYMARKED_LAYERS } from '@/lib/basemap-styles';
import { registerDirectionArrow, directionArrowLayout } from '@/lib/map-arrows';
import {
  communityTrailsSourceSpec, communityTrailsLineLayerSpec,
  communityTrailsArrowLayerSpec, communityTrailsHeatLayerSpec,
  LINE_CRISP_MINZOOM,
} from '@/lib/community-heatmap-layers';
import type { ActivityItem } from '@/lib/map-utils';
import { trace } from '@/lib/perf-trace';
import {
  DFCI_COLOR, DFCI_COLOR_WHITE, DFCI_LINE_WIDTH, DFCI_DASH_ARRAY,
} from '@/lib/routing-style';
import { communityPmtilesUrl, resolveCommunityPmtilesUrl } from '@/lib/cdn-cache';

// ── Event wiring interfaces (view-only) ──────────────────────────────
export interface MapEventRefs {
  popupHideTimerRef: React.MutableRefObject<ReturnType<typeof setTimeout> | null>;
  exploreModeRef: React.MutableRefObject<boolean>;
  activitiesRef: React.MutableRefObject<ActivityItem[]>;
}

export interface MapEventSetters {
  setTracePopup: (v: any) => void;
  setSelectedActivity: (v: any) => void;
  setHeatmapPopup: (v: any) => void;
  setHeatHover: (v: any) => void;
}

// =====================================================================
// 1. addMapSourcesAndLayers — pure layer setup (view-only)
// =====================================================================

export function addMapSourcesAndLayers(m: any): void {
  // Register the direction-arrow sprite used by activity arrow layers.
  registerDirectionArrow(m);

  // ① Terrain DEM — hillshade source (Terrarium encoding, free AWS tiles)
  m.addSource('terrain-dem', {
    type: 'raster-dem',
    tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'],
    tileSize: 256,
    encoding: 'terrarium',
    maxzoom: 14,
  });
  m.addLayer({
    id: 'hillshade-layer',
    type: 'hillshade',
    source: 'terrain-dem',
    layout: { visibility: 'none' },
    paint: {
      'hillshade-shadow-color': '#3d2b1f',
      'hillshade-highlight-color': '#ffffff',
      'hillshade-exaggeration': 0.45,
      'hillshade-accent-color': '#3d2b1f',
    },
  });

  // ①b Waymarked Trails overlays (hiking GR/PR, cycling EuroVelo, MTB)
  for (const wl of WAYMARKED_LAYERS) {
    m.addSource(`waymarked-${wl.key}`, {
      type: 'raster',
      tiles: [`https://tile.waymarkedtrails.org/${wl.tileType}/{z}/{x}/{y}.png`],
      tileSize: 256,
      attribution: '© Waymarked Trails',
      minzoom: 0,
      maxzoom: 18,
    });
    m.addLayer({
      id: `waymarked-${wl.key}-layer`,
      type: 'raster',
      source: `waymarked-${wl.key}`,
      layout: { visibility: 'none' },
      paint: { 'raster-opacity': 0.85 },
    });
  }

  // ② Community trail heatmap (ODbL, K-anonymity enforced) — PMTiles source.
  // The PMTiles binary lives on the public GCS bucket in prod (baked into
  // NEXT_PUBLIC_HEATMAP_URL); only in dev is it served same-origin. Resolve it
  // via communityPmtilesUrl() — a hardcoded `${origin}/heatmap-display.pmtiles`
  // returns the SPA index.html (200) in prod, so the source loaded HTML and the
  // heatmap silently rendered nothing. The pmtiles:// protocol is registered in
  // components/Map.tsx before the map is constructed. Source/layer specs are
  // shared with the home hero (lib/community-heatmap-layers.ts).
  trace('heatmap', 'source-add (PMTiles)');
  // Resolve the STABLE pmtiles URL synchronously so map init never blocks on the
  // network (communityPmtilesUrl does no fetch). null ⇒ prod misconfigured
  // (NEXT_PUBLIC_HEATMAP_URL unset) — skip the source rather than point it at the
  // SPA origin (index.html/200 = a silently blank heatmap); env-url.ts already
  // logged a loud error. The community layers are only added when the source is.
  const stablePmtiles = communityPmtilesUrl(window.location.origin);
  if (stablePmtiles) {
    m.addSource('community-trails', communityTrailsSourceSpec(stablePmtiles));
    // ②-heat: raster-style DENSITY heatmap (maplibre `heatmap` type) over the
    // PMTiles `heat_points` point layer — the PRIMARY community visual at every
    // zoom. Overlapping traces accumulate into a clean purple→orange density
    // field (the Strava look) with the basemap showing through, replacing the
    // diffuse vector-line "pâté". Starts hidden; useMapLayerSync toggles it.
    m.addLayer(communityTrailsHeatLayerSpec());
    // ②-lines: THIN crisp core lines, street-zoom ONLY (z14+), layered ON TOP of
    // the raster so a road reads as a line rather than a chain of density blobs
    // when the rider zooms in. The wide blurred GLOW layer was REMOVED — it was
    // the main pâté driver; the raster now carries the density gradient.
    m.addLayer(communityTrailsLineLayerSpec({ minzoom: LINE_CRISP_MINZOOM }));
    // ②-arrows: Direction arrows on predominantly one-way MTB/gravel singletrack.
    // Starts hidden; useMapLayerSync toggles it with the heatmap. maplibre
    // auto-rotates the line-placed chevrons to the feature's travel direction.
    m.addLayer(communityTrailsArrowLayerSpec());
    // ②a Hit layer for heatmap click interaction (explore mode)
    m.addLayer({
      id: 'community-trails-hit',
      type: 'line',
      source: 'community-trails',
      'source-layer': 'trails',
      minzoom: 11,
      layout: { visibility: 'none', 'line-cap': 'round' },
      paint: { 'line-width': 16, 'line-opacity': 0 },
    });

    // ②a-err: Log vector tile load errors for debugging (per-tile, not fatal)
    m.on('error', (e: any) => {
      if (e.sourceId === 'community-trails') {
        console.warn('[heatmap] tile load error:', e.error?.message);
      }
    });

    // ②-fresh: NON-BLOCKING freshness upgrade. The source above uses the 24 h-
    // cached STABLE name so a returning visitor can render a day-old heatmap.
    // Resolve the freshness pointer off the critical path and, if it yields a
    // newer immutable snapshot, swap the source URL. Fire-and-forget: init has
    // already completed with a working source, so a slow/failed pointer just
    // leaves the stable name in place (never worse than today).
    resolveCommunityPmtilesUrl(window.location.origin)
      .then((fresh) => {
        if (!fresh || fresh === stablePmtiles) return;
        const src = m.getSource('community-trails');
        if (src && typeof src.setUrl === 'function') {
          trace('heatmap', 'source-upgrade (pointer)');
          src.setUrl(fresh);
        }
      })
      .catch(() => { /* keep the stable source */ });
  }

  // ②b DFCI fire-prevention tracks (south France) — red & white
  m.addSource('dfci-trails', {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: [] },
  });
  // White casing (wider, underneath)
  m.addLayer({
    id: 'dfci-trails-casing',
    type: 'line',
    source: 'dfci-trails',
    layout: { visibility: 'none', 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': DFCI_COLOR_WHITE,
      'line-width': ['interpolate', ['linear'], ['zoom'], 6, 2, 10, 4, 14, 6, 16, 8],
      'line-opacity': 0.9,
    },
  });
  // Red dashed line on top
  m.addLayer({
    id: 'dfci-trails-line',
    type: 'line',
    source: 'dfci-trails',
    layout: { visibility: 'none', 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': DFCI_COLOR,
      'line-width': ['interpolate', ['linear'], ['zoom'], 6, 1, 10, 2, 14, DFCI_LINE_WIDTH, 16, 5],
      'line-opacity': 0.9,
      'line-dasharray': DFCI_DASH_ARRAY,
    },
  });
  // DFCI labels — visible from zoom 6, "DFCI" far, "DFCI {ref}" close
  m.addLayer({
    id: 'dfci-trails-label',
    type: 'symbol',
    source: 'dfci-trails',
    minzoom: 6,
    layout: {
      visibility: 'none',
      'symbol-placement': 'line',
      'symbol-spacing': ['interpolate', ['linear'], ['zoom'], 6, 400, 10, 200, 14, 120],
      'text-field': [
        'step', ['zoom'],
        'DFCI',                                       // z6-13: just "DFCI"
        14, ['concat', 'DFCI ', ['get', 'ref']],     // z14+: "DFCI D34-A1"
      ],
      'text-size': ['interpolate', ['linear'], ['zoom'], 6, 8, 10, 9, 14, 11],
      'text-font': ['Noto Sans Bold'],
      'text-rotation-alignment': 'viewport',
      'text-allow-overlap': false,
      'text-padding': 4,
    },
    paint: {
      'text-color': DFCI_COLOR,
      'text-halo-color': DFCI_COLOR_WHITE,
      'text-halo-width': 2,
      'text-halo-blur': 0,
    },
  });

  // ③ User activity traces — glow halo + main line
  m.addSource('user-activities', {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: [] },
  });
  // Glow layer (rendered below main line)
  m.addLayer({
    id: 'user-activities-glow',
    type: 'line',
    source: 'user-activities',
    layout: { visibility: 'visible', 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': ['coalesce', ['get', 'color'], '#2d6a4f'],
      'line-width': 14,
      'line-opacity': 0.18,
      'line-blur': 6,
    },
  });
  // Main line on top
  m.addLayer({
    id: 'user-activities-line',
    type: 'line',
    source: 'user-activities',
    layout: { visibility: 'visible', 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': ['coalesce', ['get', 'color'], '#2d6a4f'],
      'line-width': 3,
      'line-opacity': 0.92,
    },
  });
  // Wide transparent hit area — makes traces easy to click/hover
  m.addLayer({
    id: 'user-activities-hit',
    type: 'line',
    source: 'user-activities',
    layout: { visibility: 'visible', 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': 'transparent', 'line-width': 20, 'line-opacity': 0 },
  });

  // ③c User cell coverage — visited zoom-14 grid cells
  m.addSource('user-cells', {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: [] },
  });
  m.addLayer({
    id: 'user-cells-fill',
    type: 'fill',
    source: 'user-cells',
    layout: { visibility: 'none' },
    paint: { 'fill-color': '#2d6a4f', 'fill-opacity': 0.18 },
  });
  m.addLayer({
    id: 'user-cells-outline',
    type: 'line',
    source: 'user-cells',
    layout: { visibility: 'none' },
    paint: { 'line-color': '#2d6a4f', 'line-width': 1, 'line-opacity': 0.4 },
  });

  // ③d User photos — clustered GeoJSON source
  m.addSource('user-photos', {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: [] },
    cluster: true,
    clusterMaxZoom: 14,
    clusterRadius: 50,
  });
  m.addLayer({
    id: 'user-photos-clusters',
    type: 'circle',
    source: 'user-photos',
    filter: ['has', 'point_count'],
    layout: { visibility: 'none' },
    paint: {
      'circle-color': '#4a90d9',
      'circle-radius': ['step', ['get', 'point_count'], 18, 10, 24, 50, 30],
      'circle-stroke-width': 2,
      'circle-stroke-color': '#fff',
    },
  });
  m.addLayer({
    id: 'user-photos-cluster-count',
    type: 'symbol',
    source: 'user-photos',
    filter: ['has', 'point_count'],
    layout: {
      visibility: 'none',
      'text-field': '{point_count_abbreviated}',
      'text-size': 13,
    },
    paint: { 'text-color': '#fff' },
  });
  // Individual (unclustered) photo hit circle — transparent, for click detection
  m.addLayer({
    id: 'user-photos-point',
    type: 'circle',
    source: 'user-photos',
    filter: ['!', ['has', 'point_count']],
    layout: { visibility: 'none' },
    paint: {
      'circle-radius': 24,
      'circle-opacity': 0,
    },
  });

  // ③b Selected activity highlight — bright glow + prominent line on top
  m.addSource('selected-activity', {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: [] },
  });
  // Outer glow (wide, blurred)
  m.addLayer({
    id: 'selected-activity-glow',
    type: 'line',
    source: 'selected-activity',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': '#ffffff',
      'line-width': 20,
      'line-opacity': 0.6,
      'line-blur': 12,
    },
  });
  // Inner bright line
  m.addLayer({
    id: 'selected-activity-line',
    type: 'line',
    source: 'selected-activity',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': ['coalesce', ['get', 'color'], '#fff'],
      'line-width': 5,
      'line-opacity': 1,
    },
  });
  m.addLayer({
    id: 'selected-activity-arrows',
    type: 'symbol',
    source: 'selected-activity',
    minzoom: 10,
    layout: directionArrowLayout(),
  });

  // ③c-bis Variant overlays — read-only alternate trace overlays
  m.addSource('variant-overlays', {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: [] },
  });
  m.addLayer({
    id: 'variant-overlays-line',
    type: 'line',
    source: 'variant-overlays',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': ['coalesce', ['get', 'color'], '#9c27b0'],
      'line-width': 3,
      'line-opacity': 0.7,
      'line-dasharray': [6, 3],
    },
  });
}

// =====================================================================
// 2. wireMapEventHandlers — view-only event wiring
// =====================================================================

export function wireMapEventHandlers(
  m: any,
  refs: MapEventRefs,
  setters: MapEventSetters,
): void {
  // ── Trace hover popup ─────────────────────────────────────────────────
  const cancelPopupHide = () => {
    if (refs.popupHideTimerRef.current) {
      clearTimeout(refs.popupHideTimerRef.current);
      refs.popupHideTimerRef.current = null;
    }
  };
  const schedulePopupHide = () => {
    cancelPopupHide();
    refs.popupHideTimerRef.current = setTimeout(() => setters.setTracePopup(null), 300);
  };
  const showPopup = (e: any) => {
    const feat = e.features?.[0];
    if (!feat) return;
    const pt = m.project(e.lngLat);
    cancelPopupHide();
    setters.setTracePopup({
      x: pt.x, y: pt.y,
      name: feat.properties?.name || 'Activite',
      sport: feat.properties?.sport || '',
      distance_m: feat.properties?.distance_m || 0,
      elevation_gain_m: feat.properties?.elevation_gain_m || 0,
      activity_date: feat.properties?.activity_date || feat.properties?.created_at || null,
      actId: feat.properties?.id || '',
    });
  };
  const movePopup = (e: any) => {
    const pt = m.project(e.lngLat);
    setters.setTracePopup((prev: any) => prev ? { ...prev, x: pt.x, y: pt.y } : null);
  };
  // Use the wider hit layer for hover/click — easier to interact with
  m.on('mouseenter', 'user-activities-hit', (e: unknown) => {
    m.getCanvas().style.cursor = 'pointer';
    showPopup(e);
  });
  m.on('mousemove', 'user-activities-hit', movePopup);
  m.on('mouseleave', 'user-activities-hit', () => {
    m.getCanvas().style.cursor = '';
    schedulePopupHide();
  });
  m.on('click', 'user-activities-hit', (e: any) => {
    const feat = e.features?.[0];
    const actId = feat?.properties?.id;
    if (!actId) return;
    const act = refs.activitiesRef.current.find((a) => a.id === actId);
    if (act) {
      setters.setSelectedActivity(act);
      const [minLon, minLat, maxLon, maxLat] = act.bbox;
      if (minLon === maxLon && minLat === maxLat) {
        (m as any).flyTo({ center: act.center, zoom: 14, speed: 1.0 });
      } else {
        (m as any).fitBounds([[minLon, minLat], [maxLon, maxLat]], { padding: 60, maxZoom: 16 });
      }
    }
  });

  // ── Heatmap explore mode: click, hover, cursor ────────────────────
  m.on('click', 'community-trails-hit', (e: any) => {
    if (!refs.exploreModeRef.current) return;
    const feat = e.features?.[0];
    if (!feat) return;
    const pt = m.project(e.lngLat);
    setters.setHeatmapPopup({
      x: pt.x, y: pt.y,
      lngLat: [e.lngLat.lng, e.lngLat.lat],
      sport: String(feat.properties?.sport || ''),
      userCount: Number(feat.properties?.user_count || 0),
      passCount: Number(feat.properties?.pass_count || 0),
      forwardCount: Number(feat.properties?.forward_count || 0),
      backwardCount: Number(feat.properties?.backward_count || 0),
      edgeCoords: feat.geometry?.coordinates || [],
    });
  });
  m.on('mouseenter', 'community-trails-hit', () => {
    m.getCanvas().style.cursor = 'pointer';
  });
  m.on('mouseleave', 'community-trails-hit', () => {
    m.getCanvas().style.cursor = '';
    setters.setHeatHover(null);
  });
  // Hover tooltip for heatmap segments (non-explore mode) — throttled to 60fps
  let heatHoverRaf = 0;
  m.on('mousemove', 'community-trails-hit', (e: any) => {
    if (refs.exploreModeRef.current) { setters.setHeatHover(null); return; }
    cancelAnimationFrame(heatHoverRaf);
    heatHoverRaf = requestAnimationFrame(() => {
      const pt = m.project(e.lngLat);
      const feats = m.queryRenderedFeatures(pt, { layers: ['community-trails-hit'] });
      if (!feats.length) return;
      const bySport: Record<string, { userCount: number; passCount: number }> = {};
      for (const f of feats) {
        const sport = String(f.properties?.sport || '');
        const uc = Number(f.properties?.user_count || 0);
        const pc = Number(f.properties?.pass_count || 0);
        const prev = bySport[sport];
        if (prev) {
          prev.userCount = Math.max(prev.userCount, uc);
          prev.passCount = Math.max(prev.passCount, pc);
        } else {
          bySport[sport] = { userCount: uc, passCount: pc };
        }
      }
      const sports: Array<{ sport: string; userCount: number; passCount: number }> = [];
      for (const [sport, v] of Object.entries(bySport)) sports.push({ sport, ...v });
      sports.sort((a, b) => b.passCount - a.passCount);
      setters.setHeatHover({
        x: pt.x, y: pt.y, sports,
        totalUsers: sports.length ? Math.max(...sports.map(s => s.userCount)) : 0,
      });
    });
  });

  // Deselect activity when clicking on empty map area
  m.on('click', (e: any) => {
    // In explore mode, don't deselect if clicking on a heatmap edge
    if (refs.exploreModeRef.current) {
      const heatHits = m.queryRenderedFeatures(e.point, { layers: ['community-trails-hit'] });
      if (heatHits.length > 0) return;
      setters.setHeatmapPopup(null);
    }
    const hits = m.queryRenderedFeatures(e.point, { layers: ['user-activities-hit'] });
    if (hits.length === 0) setters.setSelectedActivity(null);
  });

  // ── Photo clusters ───────────────────────────────────────────────────
  m.on('click', 'user-photos-clusters', (e: any) => {
    const feature = e.features?.[0];
    if (!feature) return;
    const clusterId = feature.properties.cluster_id;
    const source = m.getSource('user-photos') as any;
    source.getClusterExpansionZoom(clusterId, (err: any, zoom: number) => {
      if (err) return;
      m.easeTo({ center: feature.geometry.coordinates, zoom });
    });
  });
  m.on('mouseenter', 'user-photos-clusters', () => { m.getCanvas().style.cursor = 'pointer'; });
  m.on('mouseleave', 'user-photos-clusters', () => { m.getCanvas().style.cursor = ''; });
}
