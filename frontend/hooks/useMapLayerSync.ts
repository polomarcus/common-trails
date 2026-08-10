'use client';

import { useEffect } from 'react';
import { SPORT_COLORS } from '@/lib/constants';
import { WAYMARKED_LAYERS } from '@/lib/basemap-styles';
import { API_URL } from '@/lib/api-client';
import { getToken } from '@/lib/auth';
import { getTrackBbox } from '@/lib/map-utils';
import {
  LINE_OPACITY_RAMP, GLOW_OPACITY_RAMP, LINE_WIDTH_RAMP, HEAT_OPACITY_RAMP,
  COMMUNITY_TRAILS_HEAT_LAYER,
} from '@/lib/community-heatmap-layers';
import type { ActivityItem } from '@/lib/map-utils';
import { buildProfileData } from '@/lib/elevation-profile';
import type { LayerToggles, WaymarkedToggles } from '@/hooks/useLayerToggles';

export const VARIANT_COLORS = ['#9c27b0', '#00897b', '#e65100', '#1565c0'];

export function useMapLayerSync(params: {
  mapInstance: unknown;
  layersReady: React.MutableRefObject<boolean>;
  layers: LayerToggles;
  setLayers: React.Dispatch<React.SetStateAction<LayerToggles>>;
  waymarked: WaymarkedToggles;
  setWaymarked: React.Dispatch<React.SetStateAction<WaymarkedToggles>>;
  highContrast: boolean;
  rv: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  heatmapSport: string;
  heatmapDays: number | null;
  loadCommunityHeatmap: (...args: any[]) => void; // eslint-disable-line @typescript-eslint/no-explicit-any
  loadDfciTrails: (...args: any[]) => void; // eslint-disable-line @typescript-eslint/no-explicit-any
  selectedActivity: ActivityItem | null;
  viewedRoute: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  editParentRoute: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  routeVariants: any[]; // eslint-disable-line @typescript-eslint/no-explicit-any
  visibleVariantIds: Set<string>;
  photoData: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  setPhotoData: (v: any) => void; // eslint-disable-line @typescript-eslint/no-explicit-any
  setLightboxPhotos: (v: any[]) => void; // eslint-disable-line @typescript-eslint/no-explicit-any
  setLightboxPhoto: (v: any) => void; // eslint-disable-line @typescript-eslint/no-explicit-any
  photoMarkersRef: React.MutableRefObject<any[]>; // eslint-disable-line @typescript-eslint/no-explicit-any
  maplibreModuleRef: React.MutableRefObject<any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  layersBeforeActivityRef: React.MutableRefObject<LayerToggles | null>;
  layersBeforeRouteRef: React.MutableRefObject<LayerToggles | null>;
  waymarkedBeforeRouteRef: React.MutableRefObject<WaymarkedToggles | null>;
  updateTraceMarkers: (map: any, coords: number[][]) => void; // eslint-disable-line @typescript-eslint/no-explicit-any
  clearTraceMarkers: () => void;
  viewedSurface: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  viewedRouteProfileDataRef: React.MutableRefObject<any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  routeTooltipRef: React.MutableRefObject<HTMLDivElement | null>;
}): void {
  const {
    mapInstance, layersReady, layers, setLayers,
    waymarked, setWaymarked,
    highContrast, rv,
    heatmapSport, heatmapDays,
    loadCommunityHeatmap, loadDfciTrails,
    selectedActivity, viewedRoute, editParentRoute,
    routeVariants, visibleVariantIds,
    photoData, setPhotoData,
    setLightboxPhotos, setLightboxPhoto,
    photoMarkersRef, maplibreModuleRef,
    layersBeforeActivityRef, layersBeforeRouteRef, waymarkedBeforeRouteRef,
    updateTraceMarkers, clearTraceMarkers,
    viewedSurface, viewedRouteProfileDataRef, routeTooltipRef,
  } = params;

  // ── Community heatmap layer toggle + reload on pan/zoom ──────────────────

  useEffect(() => {
    if (!mapInstance || !layersReady.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = mapInstance as any;

    // Default data-driven opacity expressions — SSOT from community-heatmap-layers.ts
    // (must stay identical to the base specs; imported so a retune can't diverge).
    const lineOpacity = LINE_OPACITY_RAMP;
    const heatOpacity = HEAT_OPACITY_RAMP;

    if (layers.heatmap) {
      // Show: raster density heatmap (PRIMARY) + thin crisp line (z14+) + arrows.
      // ensure visible first (instant), then fade opacity to the data-driven ramp.
      if (m.getLayer(COMMUNITY_TRAILS_HEAT_LAYER)) {
        m.setLayoutProperty(COMMUNITY_TRAILS_HEAT_LAYER, 'visibility', 'visible');
        m.setPaintProperty(COMMUNITY_TRAILS_HEAT_LAYER, 'heatmap-opacity', heatOpacity);
      }
      // Guarded like the sibling layers: the community source/layers are skipped
      // entirely when NEXT_PUBLIC_HEATMAP_URL is unset in a prod build (see
      // init-map-layers.ts), so never assume the line layer exists.
      if (m.getLayer('community-trails-line')) {
        m.setLayoutProperty('community-trails-line', 'visibility', 'visible');
        m.setPaintProperty('community-trails-line', 'line-opacity', lineOpacity);
      }
      if (m.getLayer('community-trails-arrows')) m.setLayoutProperty('community-trails-arrows', 'visibility', 'visible');
      if (m.getLayer('community-trails-hit')) m.setLayoutProperty('community-trails-hit', 'visibility', 'visible');
      loadCommunityHeatmap(mapInstance, heatmapSport, heatmapDays);
    } else {
      // Hide: fade opacity to 0 first, then set visibility:none after transition (300ms)
      if (m.getLayer(COMMUNITY_TRAILS_HEAT_LAYER)) m.setPaintProperty(COMMUNITY_TRAILS_HEAT_LAYER, 'heatmap-opacity', 0);
      if (m.getLayer('community-trails-line')) m.setPaintProperty('community-trails-line', 'line-opacity', 0);
      if (m.getLayer('community-trails-hit')) m.setLayoutProperty('community-trails-hit', 'visibility', 'none');
      if (m.getLayer('community-trails-arrows')) m.setLayoutProperty('community-trails-arrows', 'visibility', 'none');
      const t = setTimeout(() => {
        if (m.getLayer(COMMUNITY_TRAILS_HEAT_LAYER)) m.setLayoutProperty(COMMUNITY_TRAILS_HEAT_LAYER, 'visibility', 'none');
        if (m.getLayer('community-trails-line')) m.setLayoutProperty('community-trails-line', 'visibility', 'none');
      }, 320);
      return () => clearTimeout(t);
    }
  }, [mapInstance, layers.heatmap, heatmapSport, heatmapDays, loadCommunityHeatmap]);

  // ── DFCI trails layer toggle ────────────────────────────────────────────

  useEffect(() => {
    if (!mapInstance || !layersReady.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = mapInstance as any;
    if (layers.dfci) {
      m.setLayoutProperty('dfci-trails-casing', 'visibility', 'visible');
      m.setLayoutProperty('dfci-trails-line', 'visibility', 'visible');
      m.setLayoutProperty('dfci-trails-label', 'visibility', 'visible');
      loadDfciTrails(mapInstance);
    } else {
      m.setLayoutProperty('dfci-trails-casing', 'visibility', 'none');
      m.setLayoutProperty('dfci-trails-line', 'visibility', 'none');
      m.setLayoutProperty('dfci-trails-label', 'visibility', 'none');
    }
  }, [mapInstance, layers.dfci, loadDfciTrails]);

  // ── Hillshade layer toggle ────────────────────────────────────────────────

  useEffect(() => {
    if (!mapInstance || !layersReady.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = mapInstance as any;
    m.setLayoutProperty('hillshade-layer', 'visibility', layers.hillshade ? 'visible' : 'none');
  }, [mapInstance, layers.hillshade]);

  // ── Waymarked Trails overlay toggle ────────────────────────────────────────
  useEffect(() => {
    if (!mapInstance || !layersReady.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = mapInstance as any;
    for (const wl of WAYMARKED_LAYERS) {
      const layerId = `waymarked-${wl.key}-layer`;
      const lyr = m.getLayer(layerId);
      if (lyr) {
        const vis = waymarked[wl.key] ? 'visible' : 'none';
        m.setLayoutProperty(layerId, 'visibility', vis);
        console.log(`[waymarked-toggle] ${wl.key}: visibility=${vis}`);
      } else {
        console.warn(`[waymarked-toggle] layer ${layerId} NOT FOUND`);
      }
    }
    // Persist to localStorage
    try { localStorage.setItem('cc_waymarked', JSON.stringify(waymarked)); } catch { /* ignore */ }
  }, [mapInstance, waymarked]);

  // ── Route visibility mode — update paint props when toggling Normal/HC ────

  useEffect(() => {
    if (!mapInstance || !layersReady.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = mapInstance as any;
    // Draft layers
    if (m.getLayer('route-draft-outer-casing')) {
      m.setPaintProperty('route-draft-outer-casing', 'line-width', rv.outerCasingWidth);
      m.setPaintProperty('route-draft-outer-casing', 'line-opacity', rv.outerCasingOpacity);
      m.setPaintProperty('route-draft-outer-casing', 'line-blur', rv.outerCasingBlur);
    }
    if (m.getLayer('route-draft-casing')) {
      m.setPaintProperty('route-draft-casing', 'line-width', rv.innerCasingWidth);
      m.setPaintProperty('route-draft-casing', 'line-opacity', rv.innerCasingOpacity);
      m.setPaintProperty('route-draft-casing', 'line-blur', rv.innerCasingBlur);
    }
    if (m.getLayer('route-draft-line')) {
      m.setPaintProperty('route-draft-line', 'line-width', rv.coreWidth);
    }
    if (m.getLayer('route-draft-surface-line')) {
      m.setPaintProperty('route-draft-surface-line', 'line-width', rv.surfaceWidth);
    }
    // Viewed route layers
    if (m.getLayer('viewed-route-outer-casing')) {
      m.setPaintProperty('viewed-route-outer-casing', 'line-width', rv.viewOuterCasingWidth);
      m.setPaintProperty('viewed-route-outer-casing', 'line-opacity', rv.outerCasingOpacity);
      m.setPaintProperty('viewed-route-outer-casing', 'line-blur', rv.outerCasingBlur);
    }
    if (m.getLayer('viewed-route-inner-casing')) {
      m.setPaintProperty('viewed-route-inner-casing', 'line-width', rv.viewInnerCasingWidth);
      m.setPaintProperty('viewed-route-inner-casing', 'line-opacity', rv.innerCasingOpacity);
      m.setPaintProperty('viewed-route-inner-casing', 'line-blur', rv.innerCasingBlur);
    }
    if (m.getLayer('viewed-route-line')) {
      m.setPaintProperty('viewed-route-line', 'line-width', rv.viewCoreWidth);
    }
    // Profile hover layers
    if (m.getLayer('profile-hover-segment-outer-casing')) {
      m.setPaintProperty('profile-hover-segment-outer-casing', 'line-width', rv.hoverOuterCasingWidth);
      m.setPaintProperty('profile-hover-segment-outer-casing', 'line-opacity', rv.outerCasingOpacity);
    }
    if (m.getLayer('profile-hover-segment-casing')) {
      m.setPaintProperty('profile-hover-segment-casing', 'line-width', rv.hoverInnerCasingWidth);
      m.setPaintProperty('profile-hover-segment-casing', 'line-opacity', rv.innerCasingOpacity);
    }
    if (m.getLayer('profile-hover-segment-line')) {
      m.setPaintProperty('profile-hover-segment-line', 'line-width', rv.hoverCoreWidth);
    }
    // Heatmap + DFCI layers — boost width & opacity in high-contrast mode
    const hc = highContrast;
    if (m.getLayer('community-trails-line')) {
      // High-contrast stays bold (a11y toggle); default uses the SSOT thinned
      // ramp so the pâté fix actually reaches /map (was the divergence bug).
      m.setPaintProperty('community-trails-line', 'line-width', hc
        ? ['interpolate', ['exponential', 1.5], ['zoom'],
            10, ['interpolate', ['linear'], ['get', 'heat_score'], 0, 1, 0.3, 2, 0.7, 4, 1, 6],
            13, ['interpolate', ['linear'], ['get', 'heat_score'], 0, 2, 0.3, 4, 0.7, 7, 1, 10],
            16, ['interpolate', ['linear'], ['get', 'heat_score'], 0, 2.5, 0.3, 5, 0.7, 8, 1, 11]]
        : LINE_WIDTH_RAMP);
      m.setPaintProperty('community-trails-line', 'line-opacity', hc ? 1.0 : LINE_OPACITY_RAMP);
    }
    if (m.getLayer('community-trails-glow')) {
      m.setPaintProperty('community-trails-glow', 'line-opacity', hc
        ? ['interpolate', ['linear'], ['get', 'heat_score'], 0, 0.15, 0.5, 0.4, 1.0, 0.6]
        : GLOW_OPACITY_RAMP);
    }
    if (m.getLayer('dfci-trails-line')) {
      m.setPaintProperty('dfci-trails-line', 'line-width', hc ? 4 : 2.5);
      m.setPaintProperty('dfci-trails-line', 'line-opacity', hc ? 1 : 0.9);
    }
    if (m.getLayer('dfci-trails-casing')) {
      m.setPaintProperty('dfci-trails-casing', 'line-width', hc ? 6 : 4.5);
    }
  }, [mapInstance, rv, highContrast]);

  // ── Personal traces layer toggle ──────────────────────────────────────────

  useEffect(() => {
    if (!mapInstance || !layersReady.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = mapInstance as any;
    const tracesVis = layers.myTraces ? 'visible' : 'none';
    m.setLayoutProperty('user-activities-line', 'visibility', tracesVis);
    m.setLayoutProperty('user-activities-glow', 'visibility', tracesVis);
    m.setLayoutProperty('user-activities-hit', 'visibility', tracesVis);
  }, [mapInstance, layers.myTraces]);

  // ── Cell coverage layer toggle ─────────────────────────────────────────────

  useEffect(() => {
    if (!mapInstance || !layersReady.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = mapInstance as any;
    const cellsVis = layers.myCells ? 'visible' : 'none';
    m.setLayoutProperty('user-cells-fill', 'visibility', cellsVis);
    m.setLayoutProperty('user-cells-outline', 'visibility', cellsVis);
  }, [mapInstance, layers.myCells]);

  // ── Photo layer toggle + fetch ────────────────────────────────────────────
  useEffect(() => {
    if (!mapInstance || !layersReady.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = mapInstance as any;
    const vis = layers.photos ? 'visible' : 'none';
    if (m.getLayer('user-photos-clusters')) m.setLayoutProperty('user-photos-clusters', 'visibility', vis);
    if (m.getLayer('user-photos-cluster-count')) m.setLayoutProperty('user-photos-cluster-count', 'visibility', vis);
    if (m.getLayer('user-photos-point')) m.setLayoutProperty('user-photos-point', 'visibility', vis);
    // Show/hide DOM markers
    photoMarkersRef.current.forEach((marker: any) => {
      marker.getElement().style.display = layers.photos ? 'block' : 'none';
    });
    // Fetch photos on first enable
    if (layers.photos && !photoData) {
      const token = getToken();
      if (token) {
        fetch(`${API_URL}/me/photos`, { credentials: 'include' })
          .then(r => r.ok ? r.json() : null)
          .then(data => {
            if (data) {
              setPhotoData(data);
              m.getSource('user-photos')?.setData(data);
            }
          })
          .catch(() => {});
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapInstance, layers.photos]);

  // Update photo source data when photoData changes
  useEffect(() => {
    if (!mapInstance || !layersReady.current || !photoData) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = mapInstance as any;
    m.getSource('user-photos')?.setData(photoData);

    // Create/update DOM markers for unclustered photos
    // Clean up existing markers
    photoMarkersRef.current.forEach((marker: any) => marker.remove());
    photoMarkersRef.current = [];

    if (!layers.photos) return;

    const allPhotos = photoData.features || [];
    setLightboxPhotos(allPhotos);

    // Listen for sourcedata to manage DOM photo markers
    const updateMarkers = () => {
      if (!m.getSource('user-photos') || !m.isSourceLoaded('user-photos')) return;

      // Remove old markers
      photoMarkersRef.current.forEach((marker: any) => marker.remove());
      photoMarkersRef.current = [];

      // Query unclustered features in current viewport
      const features = m.querySourceFeatures('user-photos', {
        filter: ['!', ['has', 'point_count']],
      });

      // Deduplicate by photo id
      const seen = new Set<string>();
      for (const f of features) {
        const pid = f.properties?.id;
        if (!pid || seen.has(pid)) continue;
        seen.add(pid);

        const el = document.createElement('div');
        el.style.width = '48px';
        el.style.height = '48px';
        el.style.borderRadius = '50%';
        el.style.border = '3px solid #fff';
        el.style.boxShadow = '0 2px 8px rgba(0,0,0,0.3)';
        el.style.overflow = 'hidden';
        el.style.cursor = 'pointer';
        el.style.backgroundImage = `url(${f.properties.url_thumb})`;
        el.style.backgroundSize = 'cover';
        el.style.backgroundPosition = 'center';

        el.addEventListener('click', (e) => {
          e.stopPropagation();
          const idx = allPhotos.findIndex((p: any) => p.properties?.id === pid);
          const photo = allPhotos[idx >= 0 ? idx : 0];
          if (photo) {
            setLightboxPhoto({
              url_medium: photo.properties.url_medium,
              caption: photo.properties.caption,
              activity_name: photo.properties.activity_name,
              index: idx >= 0 ? idx : 0,
            });
          }
        });

        if (!maplibreModuleRef.current) continue;
        const marker = new maplibreModuleRef.current.Marker({ element: el })
          .setLngLat(f.geometry.coordinates)
          .addTo(m);
        photoMarkersRef.current.push(marker);
      }
    };

    // Use a debounced approach: update markers on moveend and sourcedata
    m.on('moveend', updateMarkers);
    m.on('sourcedata', (e: any) => {
      if (e.sourceId === 'user-photos' && e.isSourceLoaded) updateMarkers();
    });
    // Initial update
    setTimeout(updateMarkers, 200);

    return () => {
      m.off('moveend', updateMarkers);
      photoMarkersRef.current.forEach((marker: any) => marker.remove());
      photoMarkersRef.current = [];
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapInstance, photoData, layers.photos]);

  // ── Highlight selected activity on the map ────────────────────────────────

  useEffect(() => {
    if (!mapInstance || !layersReady.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = mapInstance as any;
    if (selectedActivity) {
      const color = SPORT_COLORS[selectedActivity.sport] ?? '#2d6a4f';
      m.getSource('selected-activity')?.setData({
        type: 'FeatureCollection',
        features: [{
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: selectedActivity.coords },
          properties: { color },
        }],
      });
      // Dim all other traces so selected one pops
      m.setPaintProperty('user-activities-line', 'line-opacity', 0.25);
      m.setPaintProperty('user-activities-glow', 'line-opacity', 0.06);
      // Save current layer toggles and hide overlays so the trace is clearly visible
      if (!layersBeforeActivityRef.current) {
        layersBeforeActivityRef.current = { ...layers };
      }
      setLayers(prev => ({ ...prev, heatmap: false, dfci: false, myTraces: false }));
      // Show start/end + km DOM markers
      updateTraceMarkers(m, selectedActivity.coords);
    } else {
      m.getSource('selected-activity')?.setData({ type: 'FeatureCollection', features: [] });
      // Restore full opacity when nothing is selected
      m.setPaintProperty('user-activities-line', 'line-opacity', 0.92);
      m.setPaintProperty('user-activities-glow', 'line-opacity', 0.18);
      // Restore layer toggles saved before activity selection
      if (layersBeforeActivityRef.current) {
        setLayers(() => layersBeforeActivityRef.current!);
        layersBeforeActivityRef.current = null;
      }
      // Clear markers only if no viewed route is showing either
      if (!viewedRoute) {
        clearTraceMarkers();
      }
    }
  }, [mapInstance, selectedActivity, viewedRoute]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Sync viewed route onto the map ────────────────────────────────────────

  useEffect(() => {
    if (!mapInstance || !layersReady.current) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = mapInstance as any;
    m.getSource('viewed-route')?.setData({
      type: 'FeatureCollection',
      features: viewedRoute?.coords && viewedRoute.coords.length >= 2
        ? [{ type: 'Feature', geometry: { type: 'LineString', coordinates: viewedRoute.coords }, properties: {} }]
        : [],
    });
    // Parent route (fork origin) — dashed orange line for comparison
    // Falls back to editParentRoute so the parent stays visible while editing a fork
    const parentCoords = viewedRoute?.parentCoords ?? editParentRoute?.coords;
    m.getSource('viewed-route-parent')?.setData({
      type: 'FeatureCollection',
      features: parentCoords && parentCoords.length >= 2
        ? [{ type: 'Feature', geometry: { type: 'LineString', coordinates: parentCoords }, properties: {} }]
        : [],
    });
    if (viewedRoute?.coords && viewedRoute.coords.length >= 2) {
      // Fit to both the fork AND the parent so both are visible
      const allPts = [
        ...viewedRoute.coords,
        ...(viewedRoute.parentCoords ?? []),
      ];
      const bbox = getTrackBbox(allPts);
      m.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 60, maxZoom: 16 });
      // Hide overlays so the route is clearly visible
      if (!layersBeforeRouteRef.current) {
        layersBeforeRouteRef.current = { ...layers };
        waymarkedBeforeRouteRef.current = { ...waymarked };
      }
      setLayers(prev => ({ ...prev, heatmap: false, dfci: false, myTraces: false }));
      setWaymarked({ hiking: false, cycling: false, mtb: false });
      // Show start/end + km DOM markers (only if no selected activity is overriding)
      if (!selectedActivity) {
        updateTraceMarkers(m, viewedRoute.coords);
      }
    } else {
      // Restore layers when route is closed
      if (layersBeforeRouteRef.current) {
        setLayers(() => layersBeforeRouteRef.current!);
        layersBeforeRouteRef.current = null;
      }
      if (waymarkedBeforeRouteRef.current) {
        setWaymarked(waymarkedBeforeRouteRef.current);
        waymarkedBeforeRouteRef.current = null;
      }
      if (!selectedActivity) {
        clearTraceMarkers();
      }
    }
  }, [mapInstance, viewedRoute, selectedActivity, editParentRoute]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Sync variant overlays onto the map ──────────────────────────────────────
  useEffect(() => {
    if (!mapInstance || !layersReady.current) return;
    const m = mapInstance as any; // eslint-disable-line @typescript-eslint/no-explicit-any
    const features = routeVariants
      .filter((v) => visibleVariantIds.has(v.id) && v.coords && v.coords.length >= 2)
      .map((v, i) => ({
        type: 'Feature' as const,
        geometry: { type: 'LineString' as const, coordinates: v.coords! },
        properties: { color: VARIANT_COLORS[i % VARIANT_COLORS.length], name: v.name },
      }));
    m.getSource('variant-overlays')?.setData({ type: 'FeatureCollection', features });
  }, [mapInstance, routeVariants, visibleVariantIds]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Build profile data for viewed route (hover tooltip in view mode) ───
  useEffect(() => {
    if (viewedRoute?.coords && viewedRoute.coords.length >= 2) {
      viewedRouteProfileDataRef.current = buildProfileData(viewedRoute.coords as number[][], viewedSurface?.segments);
    } else {
      viewedRouteProfileDataRef.current = null;
    }
    // Hide tooltip when viewed route changes
    if (routeTooltipRef.current) routeTooltipRef.current.style.display = 'none';
  }, [viewedRoute, viewedSurface]); // eslint-disable-line react-hooks/exhaustive-deps
}
