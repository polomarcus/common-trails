'use client';

import { useState, useCallback, useEffect } from 'react';
import { API_URL } from '@/lib/api-client';
import {
  HEATMAP_DIM_OPACITY, HEATMAP_DEFAULT_OPACITY,
} from '@/lib/routing-style';
import { trace, traceWith } from '@/lib/perf-trace';
import {
  LINE_OPACITY_RAMP, HEAT_OPACITY_RAMP, COMMUNITY_TRAILS_HEAT_LAYER, withMult,
} from '@/lib/community-heatmap-layers';

export interface HeatmapPopup {
  x: number;
  y: number;
  lngLat: [number, number];
  sport: string;
  userCount: number;
  passCount: number;
  forwardCount: number;
  backwardCount: number;
  edgeCoords: number[][];
}

export interface HeatHover {
  x: number;
  y: number;
  sports: Array<{ sport: string; userCount: number; passCount: number }>;
  totalUsers: number;
  totalPasses: number;
}

export interface HeatmapControlState {
  heatmapSport: string;
  setHeatmapSport: (s: string) => void;
  heatmapDays: number | null;
  setHeatmapDays: (d: number | null) => void;
  heatmapLoading: boolean;
  dfciCount: number | null;
  exploreMode: boolean;
  setExploreMode: React.Dispatch<React.SetStateAction<boolean>>;
  exploreSport: string;
  setExploreSport: (s: string) => void;
  heatmapPopup: HeatmapPopup | null;
  setHeatmapPopup: (p: HeatmapPopup | null) => void;
  heatHover: HeatHover | null;
  setHeatHover: (h: HeatHover | null) => void;
  loadCommunityHeatmap: (map: unknown, sport: string, days: number | null) => Promise<void>;
  loadDfciTrails: (map: unknown) => Promise<void>;
}

/**
 * Hook managing heatmap layer controls: sport/time filters, tile loading,
 * DFCI trails, explore mode, and predictive tile prefetch.
 */
export function useHeatmapControl(
  mapInstance: unknown,
  heatmapEnabled: boolean,
  routeMode: boolean,
  routeSport: string,
): HeatmapControlState {
  const [heatmapSport, setHeatmapSport] = useState<string>('offroad');
  const [heatmapDays, setHeatmapDays] = useState<number | null>(null);
  const [heatmapLoading, setHeatmapLoading] = useState(false);
  const [dfciCount, setDfciCount] = useState<number | null>(null);
  const [exploreMode, setExploreMode] = useState(false);
  const [exploreSport, setExploreSport] = useState<string>('all');
  const [heatmapPopup, setHeatmapPopup] = useState<HeatmapPopup | null>(null);
  const [heatHover, setHeatHover] = useState<HeatHover | null>(null);

  // Register tile cache Service Worker on mount
  useEffect(() => {
    if ('serviceWorker' in navigator) {
      const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '';
      navigator.serviceWorker.register(`${basePath}/tile-cache-sw.js`).catch(() => {});
    }
  }, []);

  // Clear popup when explore mode or filters change
  useEffect(() => {
    setHeatmapPopup(null);
  }, [exploreMode, heatmapSport, heatmapDays]);

  // Auto-sync heatmap sport to routing sport when in route mode
  useEffect(() => {
    if (routeMode && routeSport) {
      setHeatmapSport(routeSport);
    }
  }, [routeMode, routeSport]);

  const loadCommunityHeatmap = useCallback(async (map: unknown, _sport: string, _days: number | null) => {
    // PMTiles source + protocol are set up once at map init. This callback is
    // now only used to attach lifecycle listeners on first call so first-paint
    // can be observed in dev. In production the perf-trace helper is a no-op,
    // so the listener block is effectively dead code on the prod bundle path.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = map as any;
    if (m.__heatmapTraced) return;
    m.__heatmapTraced = true;
    const tStart = performance.now();
    trace('heatmap', 'trace-attach');
    let sourceLoaded = false;
    let firstTile = false;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const onSourceData = (e: any) => {
      if (e.sourceId !== 'community-trails') return;
      if (e.isSourceLoaded && !sourceLoaded) {
        sourceLoaded = true;
        traceWith('heatmap', 'source-loaded', tStart, 'PMTiles header parsed');
      }
      if (e.tile && !e.tile.aborted && !firstTile) {
        firstTile = true;
        traceWith('heatmap', 'first-tile', tStart, 'first MVT tile decoded');
        m.once('render', () => {
          traceWith('heatmap', 'first-paint', tStart, 'frame painted');
          m.off('sourcedata', onSourceData);
        });
      }
    };
    m.on('sourcedata', onSourceData);
  }, []);

  const loadDfciTrails = useCallback(async (map: unknown) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = map as any;
    try {
      const resp = await fetch(`${API_URL}/heatmap/dfci`);
      if (!resp.ok) return;
      const geojson = await resp.json();
      m.getSource('dfci-trails')?.setData(geojson);
      setDfciCount(geojson?.features?.length ?? null);
    } catch { /* silently fail */ }
  }, []);

  // Predictive tile prefetch on pan
  useEffect(() => {
    if (!mapInstance || !heatmapEnabled) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = mapInstance as any;
    let prevCenter: { lng: number; lat: number } | null = null;

    const onMoveEnd = async () => {
      const center = m.getCenter();
      if (!prevCenter) { prevCenter = center; return; }
      const dlng = center.lng - prevCenter.lng;
      const dlat = center.lat - prevCenter.lat;
      prevCenter = center;
      if (Math.abs(dlng) < 0.001 && Math.abs(dlat) < 0.001) return;

      const bounds = m.getBounds();
      const w = bounds.getEast() - bounds.getWest();
      const h = bounds.getNorth() - bounds.getSouth();
      const predictLng = Math.sign(dlng) * w * 0.5;
      const predictLat = Math.sign(dlat) * h * 0.5;

      const zoom = Math.round(m.getZoom());
      const z = Math.min(zoom, 14);
      if (z < 6) return;

      const n = 2 ** z;
      const toTileX = (lng: number) => Math.floor(((lng + 180) / 360) * n);
      const toTileY = (lat: number) => Math.floor((1 - Math.log(Math.tan((lat * Math.PI) / 180) + 1 / Math.cos((lat * Math.PI) / 180)) / Math.PI) / 2 * n);

      const pWest = bounds.getWest() + predictLng;
      const pEast = bounds.getEast() + predictLng;
      const pNorth = Math.min(85, bounds.getNorth() + predictLat);
      const pSouth = Math.max(-85, bounds.getSouth() + predictLat);

      const xMin = toTileX(pWest);
      const xMax = toTileX(pEast);
      const yMin = toTileY(pNorth);
      const yMax = toTileY(pSouth);

      const src = m.getSource('community-trails');
      if (!src?._options?.tiles?.[0]) return;
      const tileUrl = src._options.tiles[0];

      const tiles: string[] = [];
      for (let x = xMin; x <= xMax && tiles.length < 20; x++) {
        for (let y = yMin; y <= yMax && tiles.length < 20; y++) {
          tiles.push(tileUrl.replace('{z}', String(z)).replace('{x}', String(x)).replace('{y}', String(y)));
        }
      }
      tiles.forEach(url => fetch(url, { priority: 'low' as RequestPriority }).catch(() => {}));
    };

    m.on('moveend', onMoveEnd);
    return () => m.off('moveend', onMoveEnd);
  }, [mapInstance, heatmapEnabled]);

  // Heatmap opacity mode control
  useEffect(() => {
    if (!mapInstance) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = mapInstance as any;
    if (!m.getLayer('community-trails-line')) return;
    const mult = exploreMode ? 1.0 : routeMode ? HEATMAP_DIM_OPACITY : HEATMAP_DEFAULT_OPACITY;
    // SSOT ramps imported from community-heatmap-layers.ts; scaled by the
    // route/explore-mode multiplier through `withMult` — NOT a hand-written
    // ['*', mult, ramp]. HEAT_OPACITY_RAMP is a ZOOM ramp, and maplibre REJECTS
    // ['*', mult, <zoom-interpolate>] (silently drops the paint value + fires an
    // error), so the raster opacity could never be set — route-mode dimming of
    // the raster never applied and every toggle logged an error. withMult folds
    // the multiplier into the zoom-ramp's output stops (legal); for the
    // data-driven line ramp it stays the simple ['*'] wrapper.
    m.setPaintProperty('community-trails-line', 'line-opacity', withMult(LINE_OPACITY_RAMP, mult));
    if (m.getLayer(COMMUNITY_TRAILS_HEAT_LAYER)) {
      m.setPaintProperty(COMMUNITY_TRAILS_HEAT_LAYER, 'heatmap-opacity',
        withMult(HEAT_OPACITY_RAMP, mult));
    }
  }, [mapInstance, routeMode, exploreMode]);

  return {
    heatmapSport, setHeatmapSport,
    heatmapDays, setHeatmapDays,
    heatmapLoading,
    dfciCount,
    exploreMode, setExploreMode,
    exploreSport, setExploreSport,
    heatmapPopup, setHeatmapPopup,
    heatHover, setHeatHover,
    loadCommunityHeatmap,
    loadDfciTrails,
  };
}
