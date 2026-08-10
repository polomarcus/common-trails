'use client';

import { useEffect, useRef } from 'react';
import { SPORT_COLORS } from '@/lib/constants';
import { haversineKm } from '@/lib/geo';
import { registerDirectionArrow, directionArrowLayout } from '@/lib/map-arrows';

function computeMarkerPositions(coords: number[][]): { type: string; lon: number; lat: number; label: string }[] {
  const markers: { type: string; lon: number; lat: number; label: string }[] = [];
  if (coords.length < 2) return markers;
  markers.push({ type: 'start', lon: coords[0][0], lat: coords[0][1], label: '' });
  let cumKm = 0, nextMark = 10;
  for (let i = 1; i < coords.length; i++) {
    const segKm = haversineKm([coords[i - 1][0], coords[i - 1][1]], [coords[i][0], coords[i][1]]);
    const prevCum = cumKm;
    cumKm += segKm;
    while (cumKm >= nextMark) {
      const t = Math.max(0, Math.min(1, segKm > 0 ? (nextMark - prevCum) / segKm : 0));
      markers.push({ type: 'km', lon: coords[i - 1][0] + t * (coords[i][0] - coords[i - 1][0]), lat: coords[i - 1][1] + t * (coords[i][1] - coords[i - 1][1]), label: `${nextMark}` });
      nextMark += 10;
    }
  }
  markers.push({ type: 'end', lon: coords[coords.length - 1][0], lat: coords[coords.length - 1][1], label: '' });
  return markers;
}

export interface TraceMapPhoto {
  id: string;
  lon: number;
  lat: number;
  thumb: string;
}

export interface SiblingRoute {
  id: string;
  name: string;
  sport: string;
  coords: number[][];
}

interface TraceMapProps {
  coords: number[][];
  sport?: string;
  height?: number;
  parentCoords?: number[][];  // shown as dashed orange line (diff overlay)
  siblingRoutes?: SiblingRoute[];
  photos?: TraceMapPhoto[];
  onPhotoClick?: (photo: TraceMapPhoto) => void;
}

/**
 * Full MapLibre map for trace/route detail pages.
 * Uses OpenFreeMap "bright" tiles — same style as /map.
 * Fits bounds to the trace automatically. Client-only (no SSR).
 */
export default function TraceMap({ coords, sport = 'road', height = 320, parentCoords, siblingRoutes, photos, onPhotoClick }: TraceMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<unknown>(null);
  const onPhotoClickRef = useRef(onPhotoClick);
  useEffect(() => { onPhotoClickRef.current = onPhotoClick; }, [onPhotoClick]);

  useEffect(() => {
    if (!containerRef.current || coords.length < 2) return;

    const color = SPORT_COLORS[sport] ?? '#2d6a4f';
    let cancelled = false;

    const init = async () => {
      const ml = await import('maplibre-gl');
      await import('maplibre-gl/dist/maplibre-gl.css');
      if (cancelled || !containerRef.current) return;

      const sibCoords = (siblingRoutes ?? []).flatMap((s) => s.coords);
      const allCoords = [...coords, ...(parentCoords && parentCoords.length > 0 ? parentCoords : []), ...sibCoords];
      const lons = allCoords.map((c) => c[0]);
      const lats = allCoords.map((c) => c[1]);
      const sw: [number, number] = [Math.min(...lons), Math.min(...lats)];
      const ne: [number, number] = [Math.max(...lons), Math.max(...lats)];

      // @ts-ignore maplibre dynamic import
      const map = new ml.default.Map({
        container: containerRef.current,
        style: 'https://tiles.openfreemap.org/styles/bright',
        bounds: [sw, ne],
        fitBoundsOptions: { padding: 52, animate: false },
        attributionControl: {},
      });

      mapRef.current = map;

      // @ts-ignore
      map.on('load', () => {
        if (cancelled) return;

        // Trace line source
        // @ts-ignore
        map.addSource('trace', {
          type: 'geojson',
          data: {
            type: 'Feature',
            geometry: { type: 'LineString', coordinates: coords },
            properties: {},
          },
        });

        // Parent route overlay (shown first, behind main route)
        if (parentCoords && parentCoords.length >= 2) {
          // @ts-ignore
          map.addSource('parent-trace', {
            type: 'geojson',
            data: { type: 'Feature', geometry: { type: 'LineString', coordinates: parentCoords }, properties: {} },
          });
          // @ts-ignore
          map.addLayer({
            id: 'parent-trace-line',
            type: 'line',
            source: 'parent-trace',
            layout: { 'line-cap': 'round', 'line-join': 'round' },
            paint: { 'line-color': '#e67e22', 'line-width': 2.5, 'line-dasharray': [4, 3], 'line-opacity': 0.75 },
          });
        }

        // Sibling route overlays (behind main route)
        if (siblingRoutes && siblingRoutes.length > 0) {
          for (let i = 0; i < siblingRoutes.length; i++) {
            const sib = siblingRoutes[i];
            if (sib.coords.length < 2) continue;
            const sibColor = SPORT_COLORS[sib.sport] ?? '#2d6a4f';
            // @ts-ignore
            map.addSource(`sibling-${i}`, {
              type: 'geojson',
              data: { type: 'Feature', geometry: { type: 'LineString', coordinates: sib.coords }, properties: {} },
            });
            // @ts-ignore
            map.addLayer({
              id: `sibling-${i}-line`,
              type: 'line',
              source: `sibling-${i}`,
              layout: { 'line-cap': 'round', 'line-join': 'round' },
              paint: { 'line-color': sibColor, 'line-width': 2.5, 'line-opacity': 0.6 },
            });
          }
        }

        // Shadow halo
        // @ts-ignore
        map.addLayer({
          id: 'trace-halo',
          type: 'line',
          source: 'trace',
          paint: { 'line-color': color, 'line-width': 10, 'line-opacity': 0.18 },
        });
        // Main line
        // @ts-ignore
        map.addLayer({
          id: 'trace-line',
          type: 'line',
          source: 'trace',
          layout: { 'line-cap': 'round', 'line-join': 'round' },
          paint: { 'line-color': color, 'line-width': 3.5 },
        });

        // Direction arrows
        registerDirectionArrow(map);
        // @ts-ignore
        map.addLayer({
          id: 'trace-arrows',
          type: 'symbol',
          source: 'trace',
          minzoom: 10,
          layout: directionArrowLayout(),
        });

        // Start/end/km DOM markers
        const positions = computeMarkerPositions(coords);
        for (const pos of positions) {
          const el = document.createElement('div');
          if (pos.type === 'start') {
            el.style.cssText = 'width:20px;height:20px;border-radius:50%;background:#27ae60;border:2.5px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:center;color:#fff;font-size:10px;font-weight:700;';
            el.textContent = '▶';
          } else if (pos.type === 'end') {
            el.style.cssText = 'width:20px;height:20px;border-radius:50%;background:#1a1a1a;border:2.5px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:center;font-size:12px;';
            el.textContent = '🏁';
          } else {
            el.style.cssText = 'min-width:22px;height:22px;border-radius:11px;background:#fff;border:2px solid #555;box-shadow:0 2px 6px rgba(0,0,0,0.25);display:flex;align-items:center;justify-content:center;color:#333;font-size:10px;font-weight:700;font-family:system-ui,sans-serif;padding:0 3px;';
            el.textContent = pos.label;
          }
          // @ts-ignore
          new ml.default.Marker({ element: el, anchor: 'center' }).setLngLat([pos.lon, pos.lat]).addTo(map);
        }

        // Panoramax photo markers
        if (photos && photos.length > 0) {
          for (const photo of photos) {
            // Use a wrapper so MapLibre's translate is on the wrapper and our
            // scale hover is on the inner button — avoids overwriting MapLibre's
            // transform and snapping the marker to (0, 0) on mouseenter.
            const wrapper = document.createElement('div');
            const btn = document.createElement('button');
            btn.style.cssText = 'width:28px;height:28px;border-radius:50%;background:#fff;border:2px solid #555;box-shadow:0 2px 8px rgba(0,0,0,0.3);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:14px;padding:0;transition:transform 0.1s;';
            btn.textContent = '📷';
            btn.title = `Photo Panoramax (${photo.lat.toFixed(4)}, ${photo.lon.toFixed(4)})`;
            btn.addEventListener('mouseenter', () => { btn.style.transform = 'scale(1.2)'; });
            btn.addEventListener('mouseleave', () => { btn.style.transform = ''; });
            btn.addEventListener('click', (e) => {
              e.stopPropagation();
              onPhotoClickRef.current?.(photo);
            });
            wrapper.appendChild(btn);
            // @ts-ignore
            new ml.default.Marker({ element: wrapper, anchor: 'center' }).setLngLat([photo.lon, photo.lat]).addTo(map);
          }
        }
      });
    };

    init();

    return () => {
      cancelled = true;
      if (mapRef.current) {
        // @ts-ignore
        (mapRef.current as { remove(): void }).remove();
        mapRef.current = null;
      }
    };
  }, [coords, sport, photos, parentCoords, siblingRoutes]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      ref={containerRef}
      style={{ width: '100%', height, borderRadius: 12, overflow: 'hidden' }}
    />
  );
}
