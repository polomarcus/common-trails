'use client';

import { useEffect, useRef, useCallback } from 'react';
import { SPORT_COLORS, POI_TYPES } from '@/lib/constants';

const STAGE_COLORS = [
  '#e11d48', '#7c3aed', '#0891b2', '#059669',
  '#d97706', '#dc2626', '#4f46e5', '#0d9488',
];

export interface TripStageMap {
  id: string;
  day_index: number;
  title?: string;
  route_geometry_geojson?: string | null;
  route_sport?: string;
}

export interface TripPOIMap {
  id: string;
  type: string;
  lon: number;
  lat: number;
  name?: string;
}

interface TripMapProps {
  stages: TripStageMap[];
  pois: TripPOIMap[];
  height?: number;
  selectedStageIdx?: number | null;
  onStageClick?: (idx: number) => void;
  /** Called when user clicks on a route line to place a POI. stageIdx = index in stages array. */
  onAddPOI?: (lon: number, lat: number, stageIdx: number) => void;
  /** Whether POI placement mode is active. */
  poiMode?: boolean;
}

export default function TripMap({ stages, pois, height = 400, selectedStageIdx, onStageClick, onAddPOI, poiMode }: TripMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<unknown>(null);
  const onStageClickRef = useRef(onStageClick);
  const onAddPOIRef = useRef(onAddPOI);
  const poiModeRef = useRef(poiMode);
  useEffect(() => { onStageClickRef.current = onStageClick; }, [onStageClick]);
  useEffect(() => { onAddPOIRef.current = onAddPOI; }, [onAddPOI]);
  useEffect(() => { poiModeRef.current = poiMode; }, [poiMode]);

  // Ghost marker for hover preview
  const ghostMarkerRef = useRef<{ marker: unknown; remove: () => void } | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    // Collect all coords for bounds
    const allCoords: number[][] = [];
    const stageGeometries: { coords: number[][]; color: string }[] = [];

    for (let i = 0; i < stages.length; i++) {
      const s = stages[i];
      if (!s.route_geometry_geojson) continue;
      try {
        const geojson = JSON.parse(s.route_geometry_geojson);
        const coords: number[][] = geojson.coordinates || [];
        if (coords.length < 2) continue;
        allCoords.push(...coords);
        stageGeometries.push({
          coords,
          color: STAGE_COLORS[i % STAGE_COLORS.length],
        });
      } catch { continue; }
    }

    // Add POI coords
    for (const p of pois) {
      allCoords.push([p.lon, p.lat]);
    }

    if (allCoords.length < 2) return;

    let cancelled = false;

    const init = async () => {
      const ml = await import('maplibre-gl');
      await import('maplibre-gl/dist/maplibre-gl.css');
      if (cancelled || !containerRef.current) return;

      const lons = allCoords.map((c) => c[0]);
      const lats = allCoords.map((c) => c[1]);
      const sw: [number, number] = [Math.min(...lons), Math.min(...lats)];
      const ne: [number, number] = [Math.max(...lons), Math.max(...lats)];

      // @ts-ignore maplibre dynamic import
      const map = new ml.default.Map({
        container: containerRef.current,
        style: 'https://tiles.openfreemap.org/styles/bright',
        bounds: [sw, ne],
        fitBoundsOptions: { padding: 40 },
        attributionControl: false,
      });
      mapRef.current = map;

      map.on('load', () => {
        if (cancelled) return;

        // Collect all line layer IDs for hover events
        const lineLayerIds: string[] = [];

        // Add stage layers
        stageGeometries.forEach((sg, i) => {
          const sourceId = `trip-stage-${i}`;
          const lineId = `trip-stage-line-${i}`;
          // Wider invisible hit area for easier hover
          const hitId = `trip-stage-hit-${i}`;
          const isSelected = selectedStageIdx === i;

          map.addSource(sourceId, {
            type: 'geojson',
            data: {
              type: 'Feature',
              properties: { stageIdx: i },
              geometry: { type: 'LineString', coordinates: sg.coords },
            },
          });

          map.addLayer({
            id: lineId,
            type: 'line',
            source: sourceId,
            paint: {
              'line-color': sg.color,
              'line-width': isSelected ? 5 : 3,
              'line-opacity': isSelected || selectedStageIdx == null ? 0.9 : 0.4,
            },
            layout: { 'line-cap': 'round', 'line-join': 'round' },
          });

          // Invisible wider hit layer for easier mouse interaction
          map.addLayer({
            id: hitId,
            type: 'line',
            source: sourceId,
            paint: {
              'line-color': 'transparent',
              'line-width': 20,
              'line-opacity': 0,
            },
          });

          lineLayerIds.push(hitId);

          // Start/end markers per stage
          const start = sg.coords[0];
          const end = sg.coords[sg.coords.length - 1];

          const startEl = document.createElement('div');
          startEl.style.cssText = `width:16px;height:16px;border-radius:50%;background:${sg.color};border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,0.3);cursor:pointer;`;
          startEl.title = `Jour ${i + 1} — Départ`;
          startEl.addEventListener('click', () => onStageClickRef.current?.(i));
          // @ts-ignore
          new ml.default.Marker({ element: startEl }).setLngLat([start[0], start[1]]).addTo(map);

          if (i === stageGeometries.length - 1) {
            const endEl = document.createElement('div');
            endEl.style.cssText = `width:16px;height:16px;border-radius:50%;background:#333;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,0.3);`;
            endEl.title = 'Arrivée';
            // @ts-ignore
            new ml.default.Marker({ element: endEl }).setLngLat([end[0], end[1]]).addTo(map);
          }
        });

        // POI markers
        for (const p of pois) {
          const el = document.createElement('div');
          const icon = POI_TYPES[p.type]?.icon || '📌';
          el.style.cssText = 'font-size:18px;cursor:pointer;filter:drop-shadow(0 1px 2px rgba(0,0,0,0.5));';
          el.textContent = icon;
          el.title = p.name || p.type;
          // @ts-ignore
          new ml.default.Marker({ element: el }).setLngLat([p.lon, p.lat]).addTo(map);
        }

        // ── POI placement: hover ghost + click to add ──────────────────
        if (lineLayerIds.length > 0) {
          // Create ghost marker element (hidden until hover)
          const ghostEl = document.createElement('div');
          ghostEl.style.cssText = 'width:24px;height:24px;border-radius:50%;background:rgba(45,106,79,0.6);border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,0.3);pointer-events:none;transition:opacity 0.1s;opacity:0;';
          // @ts-ignore
          const ghost = new ml.default.Marker({ element: ghostEl }).setLngLat([0, 0]).addTo(map);
          ghostMarkerRef.current = { marker: ghost, remove: () => ghost.remove() };

          // Snap point to nearest segment on all routes
          const snapToRoute = (lngLat: { lng: number; lat: number }): [number, number] => {
            let bestDist = Infinity;
            let bestPoint: [number, number] = [lngLat.lng, lngLat.lat];

            for (const sg of stageGeometries) {
              for (let j = 0; j < sg.coords.length - 1; j++) {
                const a = sg.coords[j];
                const b = sg.coords[j + 1];
                const snapped = snapPointToSegment(lngLat.lng, lngLat.lat, a[0], a[1], b[0], b[1]);
                const dx = snapped[0] - lngLat.lng;
                const dy = snapped[1] - lngLat.lat;
                const d = dx * dx + dy * dy;
                if (d < bestDist) {
                  bestDist = d;
                  bestPoint = snapped;
                }
              }
            }
            return bestPoint;
          };

          for (const layerId of lineLayerIds) {
            map.on('mousemove', layerId, (e: { lngLat: { lng: number; lat: number } }) => {
              if (!poiModeRef.current) { ghostEl.style.opacity = '0'; return; }
              const snapped = snapToRoute(e.lngLat);
              ghost.setLngLat(snapped);
              ghostEl.style.opacity = '1';
              map.getCanvas().style.cursor = 'crosshair';
            });

            map.on('mouseleave', layerId, () => {
              ghostEl.style.opacity = '0';
              map.getCanvas().style.cursor = '';
            });

            map.on('click', layerId, (e: { lngLat: { lng: number; lat: number }; originalEvent: MouseEvent }) => {
              if (!poiModeRef.current) return;
              e.originalEvent.stopPropagation();
              const snapped = snapToRoute(e.lngLat);
              const stageIdx = parseInt(layerId.replace('trip-stage-hit-', ''), 10);
              onAddPOIRef.current?.(snapped[0], snapped[1], stageIdx);
            });
          }
        }
      });
    };

    init();
    return () => {
      cancelled = true;
      if (ghostMarkerRef.current) { ghostMarkerRef.current.remove(); ghostMarkerRef.current = null; }
      if (mapRef.current) { (mapRef.current as { remove: () => void }).remove(); mapRef.current = null; }
    };
  }, [stages, pois, selectedStageIdx]);

  return (
    <div ref={containerRef} style={{ width: '100%', height, borderRadius: 12, overflow: 'hidden' }} />
  );
}

/** Snap a point to the nearest position on a line segment. */
function snapPointToSegment(
  px: number, py: number,
  ax: number, ay: number,
  bx: number, by: number,
): [number, number] {
  const dx = bx - ax;
  const dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return [ax, ay];
  const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lenSq));
  return [ax + t * dx, ay + t * dy];
}
