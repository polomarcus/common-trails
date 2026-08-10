'use client';

import { useEffect, useRef, useState } from 'react';
import type { StyleSpecification } from 'maplibre-gl';

// Start heavy dynamic imports at module-eval time (not in useEffect).
// This eliminates ~200-400ms of React mount delay from the waterfall:
//   Before: HTML → JS parse → React mount → useEffect → import('maplibre-gl')
//   After:  HTML → JS parse → import('maplibre-gl') ← starts immediately
//                            → React mount (parallel)
const _maplibrePromise = typeof window !== 'undefined'
  ? Promise.all([
      import('maplibre-gl'),
      import('maplibre-gl/dist/maplibre-gl.css'),
      import('@/lib/pmtiles-protocol').then((m) => m.registerPMTiles()),
    ])
  : null;

interface MapProps {
  /** Set to true to use an inline minimal style (no external tile server — for testing/CI) */
  offlineMode?: boolean;
  /** Optional MapLibre style URL or style spec object. Falls back to offlineMode if not set. */
  styleUrl?: string | Record<string, unknown>;
  /** Initial center [lon, lat] */
  center?: [number, number];
  /** Initial zoom level */
  zoom?: number;
  /** Called when map is ready, receives the map instance */
  onMapReady?: (map: unknown) => void;
  className?: string;
  style?: React.CSSProperties;
}

/**
 * Minimal MapLibre style for offline/test mode.
 * No external tile requests — deterministic and CI-safe.
 */
const OFFLINE_STYLE: StyleSpecification = {
  version: 8,
  name: 'Chemins Communs Offline',
  sources: {},
  layers: [
    {
      id: 'background',
      type: 'background',
      paint: {
        'background-color': '#e8f4e8',
      },
    },
  ],
};

/**
 * Default online basemap: OpenFreeMap "bright" vector style.
 * Free, no API key. Vector tiles → crisp fonts at all zoom levels,
 * customizable road hierarchy — much more readable than raster PNG tiles.
 * https://openfreemap.org
 */
const DEFAULT_STYLE_URL = 'https://tiles.openfreemap.org/styles/bright';

export default function Map({
  offlineMode,
  styleUrl,
  center = [3.877, 43.62],
  zoom = 12,
  onMapReady,
  className,
  style,
}: MapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<unknown>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Determine if we should use offline mode
  const useOffline =
    offlineMode ??
    process.env.NEXT_PUBLIC_MAP_OFFLINE === 'true' ??
    !styleUrl;

  const resolvedStyle = useOffline
    ? OFFLINE_STYLE
    : (styleUrl || process.env.NEXT_PUBLIC_MAP_STYLE_URL || DEFAULT_STYLE_URL);

  useEffect(() => {
    if (!containerRef.current) return;

    let mapInstance: unknown = null;
    let cancelled = false;

    const initMap = async () => {
      try {
        // Await the module-level promise (started at module-eval, not useEffect)
        const [maplibregl] = await _maplibrePromise!;

        if (cancelled || !containerRef.current) return;

        // @ts-ignore maplibre dynamic import default
        mapInstance = new maplibregl.default.Map({
          container: containerRef.current,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          style: resolvedStyle as any,
          center,
          zoom,
          attributionControl: {},
          fadeDuration: 0, // instant tile render — no 300ms fade lag on pan/zoom
          // (prefetchZoomDelta lives in maplibre-gl >= 5; we're on 4.3.2,
          // where the option is silently ignored. Re-add once we upgrade.)
        });

        if (!useOffline) {
          // @ts-ignore maplibre dynamic import default
          const geolocate = new maplibregl.default.GeolocateControl({
            positionOptions: { enableHighAccuracy: false },
            trackUserLocation: false,
          });
          // @ts-expect-error maplibre types
          mapInstance.addControl(geolocate);
          // @ts-expect-error maplibre types
          mapInstance.on('load', () => {
            if (!cancelled) {
              geolocate.trigger();
            }
          });
        }

        // @ts-expect-error maplibre types
        mapInstance.on('load', () => {
          if (!cancelled) {
            setLoaded(true);
            if (onMapReady) onMapReady(mapInstance);
          }
        });

        // @ts-expect-error maplibre types
        mapInstance.on('error', (e: unknown) => {
          // In offline mode, tile errors are expected — ignore them
          if (!useOffline) {
            console.warn('MapLibre error:', e);
          }
        });

        mapRef.current = mapInstance;
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load map');
        }
      }
    };

    initMap();

    return () => {
      cancelled = true;
      if (mapInstance) {
        try {
          // @ts-expect-error maplibre types
          mapInstance.remove();
        } catch {
          // ignore cleanup errors
        }
      }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#f0f0e8',
          color: '#666',
          fontSize: 14,
          ...style,
        }}
        className={className}
        data-testid="map-error"
      >
        Carte non disponible : {error}
      </div>
    );
  }

  return (
    <div style={{ position: 'relative', ...style }} className={className}>
      <div
        ref={containerRef}
        style={{ width: '100%', height: '100%' }}
        data-testid="map-container"
      />
      {!loaded && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: '#e8f4e8',
            color: '#2d6a4f',
            fontSize: 14,
          }}
          data-testid="map-loading"
        >
          Chargement de la carte…
        </div>
      )}
    </div>
  );
}
