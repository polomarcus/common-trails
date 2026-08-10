'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import dynamic from 'next/dynamic';
import { SPORT_COLORS } from '@/lib/constants';
import { API_URL } from '@/lib/api-client';
import { getToken } from '@/lib/auth';
import { useI18n } from '@/lib/i18n';
import { buildFilesConsentFields } from '@/lib/strava-archive-consent';
import { addMapSourcesAndLayers, wireMapEventHandlers } from '@/lib/init-map-layers';
import { trace as perfTrace } from '@/lib/perf-trace';
import { type GpxPreviewData, waypointsToGpx, parseGpxFile } from '@/lib/gpx-utils';
import { BASEMAPS } from '@/lib/basemap-styles';
import { ImportModal, PhotoLightbox } from '@/components/map/MapModals';
import ExportHeatmapModal from '@/components/ExportHeatmapModal';
import MapToolbar from '@/components/map/MapToolbar';
import MapLegend from '@/components/map/MapLegend';
import SelectedActivityCard from '@/components/map/SelectedActivityCard';
import ElevationProfile from '@/components/ElevationProfile';
import PublicRoutesPanel from '@/components/PublicRoutesPanel';
import { useLayerToggles } from '@/hooks/useLayerToggles';
import type { LayerToggles, WaymarkedToggles } from '@/hooks/useLayerToggles';
import { useHeatmapControl } from '@/hooks/useHeatmapControl';
import { useUserMenu } from '@/hooks/useUserMenu';
import { useMapLayerSync } from '@/hooks/useMapLayerSync';
import { NORMAL, HIGH_CONTRAST } from '@/lib/routing-style';
import { type ActivityItem, getTrackCenter, getTrackBbox, computeTraceMarkerPositions } from '@/lib/map-utils';

const Map = dynamic(() => import('@/components/Map'), { ssr: false });

const IS_OFFLINE = process.env.NEXT_PUBLIC_MAP_OFFLINE === 'true';

// The in-app WASM router + drag-edit route editor were decommissioned
// (chore/decommission-wasm-routing). /map is now a VIEW-ONLY map: community
// heatmap + sport filters + place search + basemap switch + legend + user
// activity display. Empty inert values feed the shared useMapLayerSync hook
// (kept untouched — it optional-chains every removed source).
const EMPTY_VARIANTS: never[] = [];
const EMPTY_VARIANT_IDS = new Set<string>();

export default function MapPage() {
  const { t, locale } = useI18n();
  const dateLocale = locale === 'en' ? 'en-GB' : 'fr-FR';
  const router = useRouter();

  // ── Layer toggles (heatmap / dfci / traces / cells / photos / basemap) ───
  const {
    layers, setLayers, waymarked, setWaymarked,
    highContrast, setHighContrast,
    showLayersMenu, setShowLayersMenu, showMoreLayers, setShowMoreLayers,
    showBasemapMenu, setShowBasemapMenu, basemapKey, setBasemapKey,
    layersMenuRef, basemapMenuRef,
  } = useLayerToggles();

  // Photo layer state
  const [photoData, setPhotoData] = useState<any>(null);
  const [lightboxPhoto, setLightboxPhoto] = useState<{
    url_medium: string; caption?: string; activity_name?: string; index: number;
  } | null>(null);
  const [lightboxPhotos, setLightboxPhotos] = useState<any[]>([]);
  const photoMarkersRef = useRef<any[]>([]);
  const rv = highContrast ? HIGH_CONTRAST : NORMAL;

  // Backend readiness — single fetch with retries (non-blocking)
  const [backendReady, setBackendReady] = useState(false);
  const [backendEdges, setBackendEdges] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const fetchSummary = async () => {
      const delays = [0, 1500, 3000]; // immediate, then 1.5s, then 3s
      for (const delay of delays) {
        if (cancelled) return;
        if (delay > 0) await new Promise((r) => setTimeout(r, delay));
        try {
          const res = await fetch(`${API_URL}/heatmap/summary`);
          if (res.ok) {
            const data = await res.json();
            if (!cancelled) { setBackendEdges(data.total_edges ?? 0); setBackendReady(true); }
            return;
          }
        } catch { /* backend not up yet */ }
      }
    };
    fetchSummary();
    return () => { cancelled = true; };
  }, []);

  // The map's "Import / Contribute" affordance is a thin signpost to the
  // single canonical import place (/strava) — no in-map upload form.
  const [showImportModal, setShowImportModal] = useState(false);
  const [showExportModal, setShowExportModal] = useState(false);

  // User menu — state + actions from useUserMenu hook
  const reloadActivitiesRef = useRef<(() => void) | null>(null);
  const {
    showUserMenu, setShowUserMenu,
    userEmail, stravaName, lastSyncedAt,
    stravaSyncing, stravaSyncMsg,
    handleLogout,
  } = useUserMenu(reloadActivitiesRef);

  // Trace hover popup
  const [tracePopup, setTracePopup] = useState<{
    x: number; y: number;
    name: string; sport: string; distance_m: number;
    elevation_gain_m: number; activity_date: string | null;
    actId: string;
  } | null>(null);
  const popupHideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const exploreModeRef = useRef(false);

  // Map instance (received from Map.onMapReady)
  const [mapInstance, setMapInstance] = useState<unknown>(null);
  const mapInstanceRef = useRef<unknown>(null);

  // ── Heatmap control hook (routing/editor decommissioned → never in route mode) ──
  const {
    heatmapSport, setHeatmapSport,
    heatmapDays, setHeatmapDays,
    heatmapLoading, dfciCount,
    exploreMode, setExploreMode,
    exploreSport, setExploreSport,
    heatmapPopup, setHeatmapPopup,
    heatHover, setHeatHover,
    loadCommunityHeatmap, loadDfciTrails,
  } = useHeatmapControl(mapInstance, layers.heatmap, false, '');

  // Beta banner (dismissible, persisted in localStorage)
  const [betaDismissed, setBetaDismissed] = useState(false);
  useEffect(() => {
    if (localStorage.getItem('cc_beta_dismissed') === '1') setBetaDismissed(true);
  }, []);

  // GPX drag & drop preview (contribution path — parse a dropped .gpx, preview,
  // then upload to /gpx/upload). Independent of the removed route editor.
  const [gpxPreview, setGpxPreview] = useState<GpxPreviewData | null>(null);
  // In-map GPX-preview import feedback (was silent: no success/error, double-
  // submittable). 'idle' | 'importing' | 'done' | 'error'.
  const [gpxImport, setGpxImport] = useState<'idle' | 'importing' | 'done' | 'error'>('idle');
  const [gpxDragOver, setGpxDragOver] = useState(false);

  const layersReady = useRef(false);
  const maplibreModuleRef = useRef<any>(null);
  const traceMarkersRef = useRef<any[]>([]);

  // ── Sidebar state ─────────────────────────────────────────────────────────
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  // Surface a personal-routes load failure instead of silently leaving the
  // sidebar empty (a 500 used to be swallowed → invisible blank). Non-fatal:
  // the rest of the map still works.
  const [activitiesError, setActivitiesError] = useState<boolean>(false);
  const activitiesRef = useRef<ActivityItem[]>([]);
  const [selectedActivity, setSelectedActivity] = useState<ActivityItem | null>(null);
  const layersBeforeActivityRef = useRef<LayerToggles | null>(null);

  // Inert refs kept only to satisfy the shared useMapLayerSync signature
  // (viewed-route / variant-overlays handling is a no-op now).
  const layersBeforeRouteRef = useRef<LayerToggles | null>(null);
  const waymarkedBeforeRouteRef = useRef<WaymarkedToggles | null>(null);
  const viewedRouteProfileDataRef = useRef<any>(null);
  const routeTooltipRef = useRef<HTMLDivElement | null>(null);

  // Keep activitiesRef in sync so map event handlers can access current list
  useEffect(() => { activitiesRef.current = activities; }, [activities]);
  useEffect(() => { exploreModeRef.current = exploreMode; }, [exploreMode]);
  useEffect(() => { mapInstanceRef.current = mapInstance; }, [mapInstance]);

  /** Remove all trace DOM markers from the map. */
  const clearTraceMarkers = useCallback(() => {
    for (const m of traceMarkersRef.current) {
      try { m.remove(); } catch { /* ignore */ }
    }
    traceMarkersRef.current = [];
  }, []);

  /** Create DOM markers (start/end/km) on the map for a given trace. */
  const updateTraceMarkers = useCallback(async (map: any, coords: number[][]) => {
    clearTraceMarkers();
    const positions = computeTraceMarkerPositions(coords);
    if (positions.length === 0) return;

    const maplibregl = await import('maplibre-gl');
    maplibreModuleRef.current = maplibregl.default; // cache for synchronous use elsewhere

    for (const pos of positions) {
      const el = document.createElement('div');

      if (pos.type === 'start') {
        el.style.cssText = 'width:22px;height:22px;border-radius:50%;background:#27ae60;border:2.5px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:700;cursor:default;';
        el.textContent = '▶';
      } else if (pos.type === 'end') {
        el.style.cssText = 'width:22px;height:22px;border-radius:50%;background:#1a1a1a;border:2.5px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:center;font-size:13px;cursor:default;';
        el.textContent = '🏁';
      } else {
        // km marker
        el.style.cssText = 'min-width:24px;height:24px;border-radius:12px;background:#fff;border:2px solid #555;box-shadow:0 2px 6px rgba(0,0,0,0.25);display:flex;align-items:center;justify-content:center;color:#333;font-size:11px;font-weight:700;font-family:system-ui,sans-serif;padding:0 4px;cursor:default;';
        el.textContent = pos.label;
      }

      // @ts-ignore maplibre dynamic import
      const marker = new maplibregl.default.Marker({ element: el, anchor: 'center' })
        .setLngLat([pos.lon, pos.lat])
        .addTo(map);
      traceMarkersRef.current.push(marker);
    }
  }, [clearTraceMarkers]);

  // ── Map layer initialisation ──────────────────────────────────────────────
  const initMapLayers = useCallback((map: unknown) => {
    if (layersReady.current) return;
    const m = map as any;

    // Pure layer/source setup (view-only) — extracted to lib/init-map-layers.ts
    addMapSourcesAndLayers(m);

    // View-only event wiring (trace popup, activity select, heatmap inspect, photos)
    wireMapEventHandlers(m, {
      popupHideTimerRef,
      exploreModeRef,
      activitiesRef,
    }, {
      setTracePopup,
      setSelectedActivity,
      setHeatmapPopup,
      setHeatHover,
    });

    // Finalize: mark ready + maplibre pre-cache
    layersReady.current = true;
    if (!maplibreModuleRef.current) {
      import('maplibre-gl').then((ml) => { maplibreModuleRef.current = ml.default; });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Load user activities from API ─────────────────────────────────────────
  const loadActivities = useCallback(async (map: unknown) => {
    const token = getToken();
    if (!token) return;
    const m = map as any;
    try {
      const resp = await fetch(`${API_URL}/me/activities?limit=5000`, {
        credentials: 'include',
      });
      if (!resp.ok) {
        // Surface the failure — a 500 here used to leave the sidebar silently
        // empty with no signal. Keep it non-fatal (the map itself still works).
        console.error(`[map] /me/activities failed: HTTP ${resp.status}`);
        setActivitiesError(true);
        return;
      }
      setActivitiesError(false);
      const geojson = await resp.json();

      // Populate sidebar activity list
      const items: ActivityItem[] = geojson.features.filter((f: any) => f.geometry?.coordinates?.length > 0).map((f: any) => {
        const coords = f.geometry.coordinates as [number, number][];
        return {
          id: f.properties.id || '',
          name: f.properties.name || t('map.activityDefaultName'),
          sport: f.properties.sport || 'road',
          distance_m: f.properties.distance_m || 0,
          elevation_gain_m: f.properties.elevation_gain_m ?? null,
          center: getTrackCenter(coords),
          bbox: getTrackBbox(coords),
          activity_date: f.properties.activity_date ?? null,
          created_at: f.properties.created_at ?? null,
          coords,
          provider: f.properties.provider ?? 'file',
          provider_activity_id: f.properties.provider_activity_id ?? null,
          total_photo_count: f.properties.total_photo_count ?? 0,
        };
      });
      setActivities(items);

      // If ?activity=<id> is in the URL, select and fly to that activity
      const actParam = new URLSearchParams(window.location.search).get('activity');
      if (actParam) {
        const target = items.find((a) => a.id === actParam);
        if (target) {
          setSelectedActivity(target);
          const m2 = map as any;
          const [minLon, minLat, maxLon, maxLat] = target.bbox;
          m2.fitBounds([[minLon, minLat], [maxLon, maxLat]], { padding: 60, maxZoom: 15 });
        }
      }

      geojson.features = geojson.features.map((f: any) => ({
        ...f,
        properties: {
          ...f.properties,
          color: SPORT_COLORS[f.properties?.sport] ?? '#2d6a4f',
        },
      }));
      m.getSource('user-activities')?.setData(geojson);

      // Load user cell coverage
      try {
        const cellResp = await fetch(`${API_URL}/me/cells`, {
          credentials: 'include',
        });
        if (cellResp.ok) {
          const cellGeojson = await cellResp.json();
          m.getSource('user-cells')?.setData(cellGeojson);
        }
      } catch { /* cell layer is optional */ }

      // Jump to the most recent activity (only if no specific activity/position requested)
      const hasPositionParam = new URLSearchParams(window.location.search).get('lat') !== null;
      if (!actParam && !hasPositionParam && geojson.features.length > 0) {
        const first = geojson.features[geojson.features.length - 1];
        const coords = first?.geometry?.coordinates;
        if (coords && coords.length > 0) {
          m.jumpTo({ center: coords[0], zoom: 13 });
        }
      }
    } catch (e) {
      // Non-fatal — the map still works — but no longer silent: surface it so a
      // real failure (network / 500) is visible rather than an empty sidebar.
      console.error('[map] /me/activities load error:', e);
      setActivitiesError(true);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Called once the MapLibre instance is ready ───────────────────────────
  const handleMapReady = useCallback(
    (map: unknown) => {
      perfTrace('init', 'map-ready');
      layersReady.current = false; // reset so initMapLayers re-runs on new map instance
      setMapInstance(map);
      // Expose map instance for E2E tests
      (window as any).__mapInstance = map;
      initMapLayers(map);
      perfTrace('init', 'init-map-layers done');
      // Apply ?lat=&lon=&zoom= URL params (shared map links)
      const params = new URLSearchParams(window.location.search);
      const lat = parseFloat(params.get('lat') || '');
      const lon = parseFloat(params.get('lon') || '');
      const z = parseFloat(params.get('zoom') || '');
      if (!isNaN(lat) && !isNaN(lon)) {
        (map as any).jumpTo({ center: [lon, lat], zoom: isNaN(z) ? 13 : z });
      }
      loadActivities(map);
    },
    [initMapLayers, loadActivities],
  );

  // ── Reload activities after import ────────────────────────────────────────
  const reloadActivities = useCallback(() => {
    if (mapInstance) loadActivities(mapInstance);
  }, [mapInstance, loadActivities]);

  // ── Update URL with map position on move (shareable links) ────────────────
  useEffect(() => {
    if (!mapInstance) return;
    const m = mapInstance as any;
    let timeout: ReturnType<typeof setTimeout>;
    const onMoveEnd = () => {
      clearTimeout(timeout);
      timeout = setTimeout(() => {
        const center = m.getCenter();
        const zoom = m.getZoom();
        const params = new URLSearchParams(window.location.search);
        params.set('lat', center.lat.toFixed(5));
        params.set('lon', center.lng.toFixed(5));
        params.set('zoom', zoom.toFixed(1));
        const url = `${window.location.pathname}?${params.toString()}`;
        window.history.replaceState(null, '', url);
      }, 500);
    };
    m.on('moveend', onMoveEnd);
    return () => { m.off('moveend', onMoveEnd); clearTimeout(timeout); };
  }, [mapInstance]);

  // Wire reloadActivities ref so the user-menu hook can call it after import
  reloadActivitiesRef.current = reloadActivities;

  // ── Layer toggle/sync effects (extracted to useMapLayerSync hook) ──
  useMapLayerSync({
    mapInstance, layersReady, layers, setLayers, waymarked, setWaymarked,
    highContrast, rv, heatmapSport, heatmapDays,
    loadCommunityHeatmap, loadDfciTrails,
    selectedActivity, viewedRoute: null, editParentRoute: null,
    routeVariants: EMPTY_VARIANTS, visibleVariantIds: EMPTY_VARIANT_IDS,
    photoData, setPhotoData,
    setLightboxPhotos, setLightboxPhoto,
    photoMarkersRef, maplibreModuleRef,
    layersBeforeActivityRef, layersBeforeRouteRef, waymarkedBeforeRouteRef,
    updateTraceMarkers, clearTraceMarkers,
    viewedSurface: null, viewedRouteProfileDataRef, routeTooltipRef,
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* CSS keyframes */}
      <style>{`@keyframes profHoverPulse{0%,100%{transform:scale(1);opacity:.4}50%{transform:scale(1.6);opacity:.1}}@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}@keyframes shimmer{0%{background-position:-200px 0}100%{background-position:200px 0}}@keyframes spin{to{transform:rotate(360deg)}}`}</style>

      {/* Backend loading pill — non-blocking floating indicator */}
      {!backendReady && (
        <div style={{
          position: 'fixed', top: 12, left: '50%', transform: 'translateX(-50%)',
          background: 'rgba(45,106,79,0.9)', color: '#fff',
          padding: '6px 16px', borderRadius: 20,
          fontSize: 12, fontWeight: 600,
          display: 'flex', alignItems: 'center', gap: 8,
          zIndex: 1000, backdropFilter: 'blur(4px)',
          boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
          pointerEvents: 'none',
        }}>
          <span style={{
            width: 14, height: 14, border: '2px solid rgba(255,255,255,0.3)',
            borderTopColor: '#fff', borderRadius: '50%',
            animation: 'spin 0.8s linear infinite', flexShrink: 0,
          }} />
          {t('map.loadingCommunityTraces')}{backendEdges > 0 ? t('map.edgesSuffix', { count: backendEdges.toLocaleString(dateLocale) }) : ' ...'}
        </div>
      )}

      {/* Beta banner — dismissible, persisted in localStorage */}
      {!betaDismissed && (
        <div style={{
          background: 'linear-gradient(90deg, #1a4731, #2d6a4f)',
          color: '#fff',
          padding: '8px 20px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 10,
          fontSize: 13,
          zIndex: 999,
        }}>
          <span style={{ fontSize: 15 }}>🧪</span>
          <span>
            {t('map.betaBanner')}
            <a href="/strava" style={{ color: '#90edb5', marginLeft: 6, fontWeight: 700, textDecoration: 'underline' }}>
              {t('map.betaImportLink')}
            </a>
          </span>
          <button
            onClick={() => { setBetaDismissed(true); localStorage.setItem('cc_beta_dismissed', '1'); }}
            style={{
              background: 'none', border: 'none', color: 'rgba(255,255,255,0.5)',
              fontSize: 16, cursor: 'pointer', padding: '0 4px', flexShrink: 0,
            }}
            aria-label={t('common.close')}
          >
            ✕
          </button>
        </div>
      )}

      {/* Personal-routes load failure — non-blocking, dismissible notice.
          Replaces the old silent `return` so a 500 is visible, not invisible. */}
      {activitiesError && (
        <div
          role="alert"
          style={{
            background: '#7a1f1f',
            color: '#fff',
            padding: '8px 20px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 10,
            fontSize: 13,
            zIndex: 999,
          }}
        >
          <span style={{ fontSize: 15 }}>⚠️</span>
          <span>{t('map.activitiesLoadError')}</span>
          <button
            onClick={() => setActivitiesError(false)}
            style={{
              background: 'none', border: 'none', color: 'rgba(255,255,255,0.6)',
              fontSize: 16, cursor: 'pointer', padding: '0 4px', flexShrink: 0,
            }}
            aria-label={t('common.close')}
          >
            ✕
          </button>
        </div>
      )}

      {/* Heatmap loading pill */}
      {heatmapLoading && (
        <div style={{
          position: 'fixed', top: 12, left: '50%', transform: 'translateX(-50%)',
          background: 'rgba(45,106,79,0.9)', color: '#fff',
          padding: '6px 16px', borderRadius: 20,
          fontSize: 12, fontWeight: 600,
          display: 'flex', alignItems: 'center', gap: 8,
          zIndex: 50, backdropFilter: 'blur(4px)',
          boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
        }}>
          <span style={{
            width: 14, height: 14, border: '2px solid rgba(255,255,255,0.3)',
            borderTopColor: '#fff', borderRadius: '50%',
            animation: 'spin 0.8s linear infinite', flexShrink: 0,
          }} />
          {t('map.loadingHeatmap')}
        </div>
      )}

      {/* ── Toolbar (extracted to MapToolbar component) ── */}
      <MapToolbar
        layers={layers} setLayers={setLayers}
        waymarked={waymarked} setWaymarked={setWaymarked}
        highContrast={highContrast} setHighContrast={setHighContrast}
        showLayersMenu={showLayersMenu} setShowLayersMenu={setShowLayersMenu}
        showMoreLayers={showMoreLayers} setShowMoreLayers={setShowMoreLayers}
        showBasemapMenu={showBasemapMenu} setShowBasemapMenu={setShowBasemapMenu}
        basemapKey={basemapKey} setBasemapKey={setBasemapKey}
        layersMenuRef={layersMenuRef} basemapMenuRef={basemapMenuRef}
        heatmapSport={heatmapSport} setHeatmapSport={setHeatmapSport}
        heatmapDays={heatmapDays} setHeatmapDays={setHeatmapDays}
        dfciCount={dfciCount}
        exploreMode={exploreMode} setExploreMode={setExploreMode}
        showUserMenu={showUserMenu} setShowUserMenu={setShowUserMenu}
        userEmail={userEmail} stravaName={stravaName}
        stravaSyncing={stravaSyncing} stravaSyncMsg={stravaSyncMsg}
        lastSyncedAt={lastSyncedAt}
        handleLogout={handleLogout}
        setShowImportModal={setShowImportModal}
        setShowExportModal={setShowExportModal}
        activitiesCount={activities.length}
        mapInstance={mapInstance}
        dateLocale={dateLocale}
      />

      {/* ── Map + overlays ── */}
      <div
        style={{ flex: 1, position: 'relative' }}
        onDragOver={(e) => {
          // Only react to OS file drags. Internal drags would otherwise paint
          // the overlay over the map and block panning. The "Files" type is
          // present on OS-originated file drops; absent for everything else.
          if (!e.dataTransfer.types.includes('Files')) return;
          e.preventDefault(); e.stopPropagation();
          setGpxDragOver(true);
        }}
        onDragLeave={(e) => {
          if (!e.dataTransfer.types.includes('Files')) return;
          e.preventDefault(); e.stopPropagation();
          setGpxDragOver(false);
        }}
        onDrop={(e) => {
          if (!e.dataTransfer.types.includes('Files')) return;
          e.preventDefault(); e.stopPropagation(); setGpxDragOver(false);
          const file = e.dataTransfer.files[0];
          if (!file || !file.name.toLowerCase().endsWith('.gpx')) return;
          const reader = new FileReader();
          reader.onload = () => {
            const data = parseGpxFile(reader.result as string);
            if (!data) return;
            setGpxPreview(data);
            // Fit map to GPX bounds
            const m = mapInstance as any;
            if (m?.fitBounds) {
              m.fitBounds(
                [[data.bbox[0], data.bbox[1]], [data.bbox[2], data.bbox[3]]],
                { padding: 60, maxZoom: 15 },
              );
            }
          };
          reader.readAsText(file);
        }}
      >

        {/* GPX drag overlay */}
        {gpxDragOver && (
          <div data-testid="gpx-drop-overlay" style={{
            position: 'absolute', inset: 0, zIndex: 200,
            background: 'rgba(147, 51, 234, 0.12)', border: '3px dashed #9333ea',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            pointerEvents: 'none', borderRadius: 0,
          }}>
            <div style={{
              background: '#fff', borderRadius: 12, padding: '18px 28px',
              boxShadow: '0 4px 16px rgba(0,0,0,0.15)', fontSize: 15, fontWeight: 600, color: '#7c3aed',
            }}>
              {t('map.gpxDropHint')}
            </div>
          </div>
        )}

        <Map
          key={IS_OFFLINE ? 'offline' : basemapKey}
          offlineMode={IS_OFFLINE}
          styleUrl={IS_OFFLINE ? undefined : (process.env.NEXT_PUBLIC_MAP_STYLE_URL || BASEMAPS.find(b => b.key === basemapKey)?.style)}
          style={{ width: '100%', height: '100%' }}
          onMapReady={handleMapReady}
          data-testid="map"
        />

        {/* Public routes panel — shows routes in current viewport */}
        <PublicRoutesPanel
          mapInstance={mapInstance}
          routeMode={false}
          viewedRouteId={null}
        />

        {/* Map legend — contextual, shows only active layers */}
        <MapLegend layers={layers} waymarked={waymarked} />

        {/* ── GPX Preview panel (dropped-file preview + contribute) ── */}
        {gpxPreview && (
          <div style={{
            position: 'absolute', bottom: 56, left: '50%', transform: 'translateX(-50%)',
            zIndex: 50, background: '#fff', borderRadius: 12,
            boxShadow: '0 4px 20px rgba(0,0,0,0.15)', border: '1px solid #e5e7eb',
            padding: '14px 18px', maxWidth: 380, width: '90%',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 14, color: '#1f2937' }}>{gpxPreview.name}</div>
                <div style={{ fontSize: 12, color: '#6b7280', marginTop: 2 }}>
                  {gpxPreview.distanceKm.toFixed(1)} km
                  {gpxPreview.elevGain > 0 && <> &middot; D+ {Math.round(gpxPreview.elevGain)} m</>}
                  {gpxPreview.elevLoss > 0 && <> &middot; D- {Math.round(gpxPreview.elevLoss)} m</>}
                </div>
              </div>
              <button
                onClick={() => setGpxPreview(null)}
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  fontSize: 18, color: '#9ca3af', padding: '0 4px', lineHeight: 1,
                }}
                title={t('map.closePreview')}
              >&times;</button>
            </div>
            <ElevationProfile coords={gpxPreview.coords as number[][]} width={340} height={64} />
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              <button
                disabled={gpxImport === 'importing'}
                onClick={async () => {
                  const tok = getToken();
                  if (!tok) {
                    router.push('/?redirect=/map');
                    return;
                  }
                  setGpxImport('importing');
                  // Build a minimal GPX string and upload as a contribution
                  const gpxStr = waypointsToGpx(
                    gpxPreview.coords.map(c => [c[0], c[1]] as [number, number]),
                    gpxPreview.name,
                  );
                  const blob = new Blob([gpxStr], { type: 'application/gpx+xml' });
                  const form = new FormData();
                  form.append('file', blob, `${gpxPreview.name}.gpx`);
                  form.append('sport', 'mtb');
                  form.append('contribute_heatmap', 'true');
                  // ODbL consent: importing IS the consent act — send the standard
                  // wording so the backend records the audit row and actually
                  // publishes (no consent ⇒ ingested personal-only).
                  const consentFields = buildFilesConsentFields(
                    t('strava.archive.consentLabel'), locale, null,
                  );
                  for (const [k, v] of Object.entries(consentFields)) form.append(k, v);
                  try {
                    const res = await fetch(`${API_URL}/gpx/upload`, {
                      method: 'POST', credentials: 'include', body: form,
                    });
                    if (res.ok || res.status === 202) {
                      // Show a brief "imported ✓" before closing so the import
                      // isn't a silent no-op (the panel used to just vanish).
                      setGpxImport('done');
                      setTimeout(() => { setGpxPreview(null); setGpxImport('idle'); }, 1800);
                    } else {
                      setGpxImport('error');
                    }
                  } catch { setGpxImport('error'); }
                }}
                style={{
                  flex: 1, padding: '7px 0', borderRadius: 8, border: 'none',
                  background: gpxImport === 'done' ? '#16a34a' : gpxImport === 'error' ? '#dc2626' : '#7c3aed',
                  color: '#fff', fontWeight: 600, fontSize: 12,
                  cursor: gpxImport === 'importing' ? 'wait' : 'pointer',
                  opacity: gpxImport === 'importing' ? 0.7 : 1,
                }}
              >
                {gpxImport === 'importing' ? t('map.importing')
                  : gpxImport === 'done' ? `✓ ${t('map.imported')}`
                  : t('map.import')}
              </button>
            </div>
            {gpxImport === 'error' && (
              <p style={{ color: '#dc2626', fontSize: 11.5, margin: '8px 0 0', textAlign: 'center' }}>
                {t('map.importError')}
              </p>
            )}
          </div>
        )}

        {/* Trace hover popup — cliquable, reste visible quand la souris s'y déplace */}
        {tracePopup && (
          <div
            onMouseEnter={() => {
              if (popupHideTimerRef.current) {
                clearTimeout(popupHideTimerRef.current);
                popupHideTimerRef.current = null;
              }
            }}
            onMouseLeave={() => {
              popupHideTimerRef.current = setTimeout(() => setTracePopup(null), 300);
            }}
            onClick={() => {
              // Select activity on map (show detail card) instead of navigating away
              const act = activities.find((a) => a.id === tracePopup.actId);
              if (act) setSelectedActivity(act);
              setTracePopup(null);
            }}
            style={{
              position: 'absolute',
              left: Math.min(tracePopup.x + 12, typeof window !== 'undefined' ? window.innerWidth - 240 : 500),
              top: tracePopup.y - 10,
              background: '#fff',
              borderRadius: 10,
              padding: '10px 14px',
              boxShadow: '0 4px 16px rgba(0,0,0,0.18)',
              fontSize: 13,
              cursor: 'pointer',
              zIndex: 30,
              maxWidth: 220,
              lineHeight: 1.4,
            }}
          >
            <div style={{ fontWeight: 700, marginBottom: 3, fontSize: 14 }}>{tracePopup.name}</div>
            <div style={{ display: 'flex', gap: 8, color: '#555', fontSize: 12, marginBottom: 2 }}>
              {tracePopup.sport && (
                <span style={{ color: '#888', textTransform: 'uppercase', fontSize: 10, fontWeight: 600 }}>
                  {tracePopup.sport}
                </span>
              )}
              {tracePopup.activity_date && (
                <span style={{ color: '#999', fontSize: 11 }}>
                  {new Date(tracePopup.activity_date).toLocaleDateString(dateLocale, { day: 'numeric', month: 'short', year: 'numeric' })}
                </span>
              )}
            </div>
            <div style={{ display: 'flex', gap: 10, color: '#333', fontSize: 12, fontWeight: 500 }}>
              {tracePopup.distance_m > 0 && (
                <span>{(tracePopup.distance_m / 1000).toFixed(1)} km</span>
              )}
              {tracePopup.elevation_gain_m > 0 && (
                <span>{Math.round(tracePopup.elevation_gain_m)} m D+</span>
              )}
            </div>
          </div>
        )}

        {/* ── Heatmap explore popup ── */}
        {heatmapPopup && exploreMode && (
          <div
            style={{
              position: 'absolute',
              left: Math.min(heatmapPopup.x + 14, (typeof window !== 'undefined' ? window.innerWidth - 260 : 500)),
              top: heatmapPopup.y - 10,
              background: '#fff',
              borderRadius: 12,
              padding: '14px 16px',
              boxShadow: '0 4px 20px rgba(0,0,0,0.22)',
              fontSize: 13,
              zIndex: 40,
              width: 240,
              lineHeight: 1.5,
            }}
          >
            <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6, color: '#1a4731' }}>
              {t('map.segmentHeatmap')}
            </div>
            <div style={{ color: '#555', marginBottom: 4 }}>
              {t('map.contributors', { count: heatmapPopup.userCount, s: heatmapPopup.userCount > 1 ? 's' : '' })}
              {' '}&middot;{' '}
              {t('map.passes', { count: heatmapPopup.passCount, s: heatmapPopup.passCount > 1 ? 's' : '', es: heatmapPopup.passCount > 1 ? 'es' : '' })}
            </div>
            {heatmapPopup.sport && (
              <div style={{ fontSize: 11, color: '#888', textTransform: 'uppercase', marginBottom: 8 }}>
                {heatmapPopup.sport}
              </div>
            )}
            {/* Direction display — gravel/mtb/offroad only, arrows oriented along path.
                Only shown when real per-direction counts exist (raw build fills
                forward_count/backward_count for mtb/gravel); never a fake 50/50. */}
            {['gravel', 'mtb', 'offroad'].includes(heatmapPopup.sport)
              && (heatmapPopup.forwardCount + heatmapPopup.backwardCount) > 0 && (() => {
              const total = heatmapPopup.forwardCount + heatmapPopup.backwardCount;
              const fwdPct = Math.round((heatmapPopup.forwardCount / total) * 100);
              const bwdPct = 100 - fwdPct;
              // Compute local bearing at the segment closest to the click point
              const ec = heatmapPopup.edgeCoords;
              let bearingDeg = 90; // default: east
              if (ec.length >= 2) {
                const [cLon, cLat] = heatmapPopup.lngLat;
                const cosLat = Math.cos(cLat * Math.PI / 180);
                // Find the nearest segment to the click
                let bestIdx = 0;
                let bestDist = Infinity;
                for (let i = 0; i < ec.length - 1; i++) {
                  // Project click onto segment [ec[i], ec[i+1]], measure squared distance
                  const ax = (ec[i][0] - cLon) * cosLat, ay = ec[i][1] - cLat;
                  const bx = (ec[i + 1][0] - cLon) * cosLat, by = ec[i + 1][1] - cLat;
                  const dx = bx - ax, dy = by - ay;
                  const t = Math.max(0, Math.min(1, ((-ax) * dx + (-ay) * dy) / (dx * dx + dy * dy || 1)));
                  const px = ax + t * dx, py = ay + t * dy;
                  const d2 = px * px + py * py;
                  if (d2 < bestDist) { bestDist = d2; bestIdx = i; }
                }
                // Bearing of that segment
                const [lon1, lat1] = ec[bestIdx];
                const [lon2, lat2] = ec[bestIdx + 1];
                const toRad = Math.PI / 180;
                const dLon = (lon2 - lon1) * toRad;
                const y = Math.sin(dLon) * Math.cos(lat2 * toRad);
                const x = Math.cos(lat1 * toRad) * Math.sin(lat2 * toRad) - Math.sin(lat1 * toRad) * Math.cos(lat2 * toRad) * Math.cos(dLon);
                bearingDeg = (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
              }
              // SVG arrow points right (90°), so rotate by bearing - 90
              const fwdRot = bearingDeg - 90;
              const bwdRot = fwdRot + 180;
              return (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: '#555', marginBottom: 4 }}>
                    {t('map.directionOfTravel')}
                  </div>
                  {/* Forward bar */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                    <svg width="14" height="14" viewBox="0 0 14 14" style={{ flexShrink: 0, transform: `rotate(${fwdRot}deg)` }}>
                      <path d="M2 7 L10 7 M10 7 L7 4 M10 7 L7 10" stroke="#3b82f6" strokeWidth="1.8" strokeLinecap="round" fill="none" />
                    </svg>
                    <div style={{ flex: 1, height: 6, background: '#e5e7eb', borderRadius: 3, overflow: 'hidden' }}>
                      <div style={{ width: `${fwdPct}%`, height: '100%', background: '#3b82f6', borderRadius: 3 }} />
                    </div>
                    <span style={{ fontSize: 11, fontWeight: 600, color: '#3b82f6', minWidth: 56, textAlign: 'right' }}>{heatmapPopup.forwardCount} · {fwdPct}%</span>
                  </div>
                  {/* Backward bar */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <svg width="14" height="14" viewBox="0 0 14 14" style={{ flexShrink: 0, transform: `rotate(${bwdRot}deg)` }}>
                      <path d="M2 7 L10 7 M10 7 L7 4 M10 7 L7 10" stroke="#9ca3af" strokeWidth="1.8" strokeLinecap="round" fill="none" />
                    </svg>
                    <div style={{ flex: 1, height: 6, background: '#e5e7eb', borderRadius: 3, overflow: 'hidden' }}>
                      <div style={{ width: `${bwdPct}%`, height: '100%', background: '#9ca3af', borderRadius: 3 }} />
                    </div>
                    <span style={{ fontSize: 11, fontWeight: 600, color: '#9ca3af', minWidth: 56, textAlign: 'right' }}>{heatmapPopup.backwardCount} · {bwdPct}%</span>
                  </div>
                </div>
              );
            })()}
          </div>
        )}

        {/* Heatmap hover tooltip (non-explore mode) */}
        {heatHover && !exploreMode && (
          <div
            style={{
              position: 'absolute',
              left: heatHover.x + 12,
              top: heatHover.y - 36,
              background: 'rgba(255,255,255,0.95)',
              borderRadius: 8,
              padding: '5px 10px',
              boxShadow: '0 2px 8px rgba(0,0,0,0.18)',
              fontSize: 11,
              zIndex: 40,
              pointerEvents: 'none',
              whiteSpace: 'nowrap',
              backdropFilter: 'blur(4px)',
            }}
          >
            <span style={{ fontWeight: 600 }}>{t('map.contributors', { count: heatHover.totalUsers, s: heatHover.totalUsers > 1 ? 's' : '' })}</span>
            {heatHover.sports.length > 0 && (
              <div style={{ marginTop: 2, color: '#666', fontSize: 10 }}>
                {heatHover.sports.map((s, i) => (
                  <span key={s.sport}>
                    {i > 0 && ' · '}
                    {t('map.passes', { count: s.passCount, s: s.passCount > 1 ? 's' : '', es: s.passCount > 1 ? 'es' : '' })}{' '}
                    <span style={{ textTransform: 'uppercase' }}>{s.sport}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── Explore mode floating sport filter ── */}
        {exploreMode && (
          <div
            data-testid="explore-sport-filter"
            style={{
              position: 'absolute',
              top: 52,
              left: '50%',
              transform: 'translateX(-50%)',
              display: 'flex',
              gap: 4,
              background: 'rgba(255,255,255,0.95)',
              backdropFilter: 'blur(8px)',
              borderRadius: 20,
              padding: '4px 8px',
              boxShadow: '0 2px 12px rgba(0,0,0,0.12)',
              zIndex: 30,
            }}
          >
            {([
              { value: 'all', label: t('map.sportFilter.all') },
              { value: 'road', label: t('sport.road') },
              { value: 'gravel', label: t('sport.gravel') },
              { value: 'mtb', label: t('sport.mtb') },
              { value: 'offroad', label: t('sport.offroad') },
              { value: 'running', label: t('map.sportFilter.running') },
            ] as { value: string; label: string }[]).map(({ value, label }) => (
              <button
                key={value}
                onClick={() => {
                  setExploreSport(value);
                  setHeatmapSport(value);
                }}
                style={{
                  padding: '3px 10px', borderRadius: 14, border: 'none',
                  background: exploreSport === value ? '#3b82f6' : 'transparent',
                  color: exploreSport === value ? '#fff' : '#555',
                  cursor: 'pointer', fontSize: 12, fontWeight: exploreSport === value ? 700 : 400,
                }}
              >
                {label}
              </button>
            ))}
          </div>
        )}

        {/* Selected activity — floating detail card */}
        {selectedActivity && (
          <SelectedActivityCard
            activity={selectedActivity}
            dateLocale={dateLocale}
            onClose={() => setSelectedActivity(null)}
          />
        )}
      </div>

      {/* ── Import modal (thin signpost → /strava) ── */}
      {showImportModal && (
        <ImportModal onClose={() => setShowImportModal(false)} />
      )}

      {/* ── Export heatmap modal (PRD #391, Phase 1 + Phase 2) ── */}
      {showExportModal && (
        <ExportHeatmapModal
          onClose={() => setShowExportModal(false)}
          initialBbox={(() => {
            // Seed the bbox-editor from the current viewport so the
            // MBTiles + GeoJSON formats default to "the area I'm looking at".
            const m = mapInstanceRef.current as { getBounds?: () => unknown } | null;
            if (!m?.getBounds) return null;
            const b = m.getBounds() as {
              getWest: () => number; getSouth: () => number;
              getEast: () => number; getNorth: () => number;
            } | null;
            if (!b) return null;
            return {
              minLon: b.getWest(),
              minLat: b.getSouth(),
              maxLon: b.getEast(),
              maxLat: b.getNorth(),
            };
          })()}
        />
      )}

      {/* ── Photo Lightbox ── */}
      {lightboxPhoto && (
        <PhotoLightbox
          photo={lightboxPhoto}
          photos={lightboxPhotos}
          onClose={() => setLightboxPhoto(null)}
          onNavigate={setLightboxPhoto}
        />
      )}
    </div>
  );
}
