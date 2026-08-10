'use client';

import { useState, useEffect, useCallback, useRef, useMemo, memo } from 'react';
import Link from 'next/link';
import { API_URL } from '@/lib/api-client';
import { SPORT_COLORS, SPORT_ICONS } from '@/lib/constants';
import { formatDist } from '@/lib/format';
import { useT } from '@/lib/i18n';

interface PublicRoute {
  id: string;
  name: string;
  sport: string;
  distance_m?: number;
  elevation_gain_m?: number;
  center?: number[];
  geometry_geojson?: string;
}

interface Props {
  mapInstance: unknown | null;
  routeMode: boolean;
  viewedRouteId: string | null;
}

export default memo(function PublicRoutesPanel({ mapInstance, routeMode, viewedRouteId }: Props) {
  const t = useT();
  const [routes, setRoutes] = useState<PublicRoute[]>([]);
  const [collapsed, setCollapsed] = useState(true);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [bounds, setBounds] = useState<{ sw: [number, number]; ne: [number, number] } | null>(null);
  const fetchedRef = useRef(false);

  // Fetch all public routes once
  useEffect(() => {
    if (fetchedRef.current) return;
    fetchedRef.current = true;
    fetch(`${API_URL}/routes?visibility=public`)
      .then((r) => r.ok ? r.json() : [])
      .then((data) => setRoutes(data))
      .catch(() => {});
  }, []);

  // Track map bounds
  useEffect(() => {
    if (!mapInstance) return;
    const m = mapInstance as any;
    const update = () => {
      const b = m.getBounds();
      setBounds({
        sw: [b.getSouthWest().lng, b.getSouthWest().lat],
        ne: [b.getNorthEast().lng, b.getNorthEast().lat],
      });
    };
    m.on('moveend', update);
    setTimeout(update, 200);
    return () => m.off('moveend', update);
  }, [mapInstance]);

  // Filter routes by viewport
  const visible = useMemo(() => {
    if (!bounds || routes.length === 0) return [];
    return routes.filter((r) => {
      if (!r.center) return false;
      const [lon, lat] = r.center;
      return lon >= bounds.sw[0] && lon <= bounds.ne[0] &&
             lat >= bounds.sw[1] && lat <= bounds.ne[1];
    });
  }, [routes, bounds]);

  // Add/update map source for public routes
  useEffect(() => {
    if (!mapInstance) return;
    const m = mapInstance as any;

    // Create source + layer once
    if (!m.getSource('public-routes')) {
      m.addSource('public-routes', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      });
      m.addLayer({
        id: 'public-routes-line',
        type: 'line',
        source: 'public-routes',
        paint: {
          'line-color': ['get', 'color'],
          'line-width': 2.5,
          'line-opacity': 0.5,
        },
        layout: { 'line-cap': 'round', 'line-join': 'round' },
      });
      m.addSource('public-routes-highlight', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] },
      });
      m.addLayer({
        id: 'public-routes-highlight-line',
        type: 'line',
        source: 'public-routes-highlight',
        paint: {
          'line-color': ['get', 'color'],
          'line-width': 5,
          'line-opacity': 0.9,
        },
        layout: { 'line-cap': 'round', 'line-join': 'round' },
      });
    }

    // Update data
    const features: GeoJSON.Feature[] = [];
    for (const r of routes) {
      if (!r.geometry_geojson) continue;
      try {
        features.push({
          type: 'Feature',
          properties: { id: r.id, color: SPORT_COLORS[r.sport] ?? '#2d6a4f' },
          geometry: JSON.parse(r.geometry_geojson),
        });
      } catch { /* skip */ }
    }
    m.getSource('public-routes')?.setData({ type: 'FeatureCollection', features });
  }, [mapInstance, routes]);

  // Highlight hovered route on map
  useEffect(() => {
    if (!mapInstance) return;
    const m = mapInstance as any;
    const src = m.getSource('public-routes-highlight');
    if (!src) return;
    if (!hoveredId) {
      src.setData({ type: 'FeatureCollection', features: [] });
      return;
    }
    const route = routes.find((r) => r.id === hoveredId);
    if (!route?.geometry_geojson) {
      src.setData({ type: 'FeatureCollection', features: [] });
      return;
    }
    try {
      src.setData({
        type: 'FeatureCollection',
        features: [{
          type: 'Feature',
          properties: { color: SPORT_COLORS[route.sport] ?? '#2d6a4f' },
          geometry: JSON.parse(route.geometry_geojson),
        }],
      });
    } catch { /* skip */ }
  }, [mapInstance, hoveredId, routes]);

  // Hide map lines when viewing/editing a route
  useEffect(() => {
    if (!mapInstance) return;
    const m = mapInstance as any;
    const hidden = routeMode || !!viewedRouteId;
    try {
      m.getSource('public-routes')?.setData(
        hidden ? { type: 'FeatureCollection', features: [] } : (() => {
          const features: GeoJSON.Feature[] = [];
          for (const r of routes) {
            if (!r.geometry_geojson) continue;
            try {
              features.push({
                type: 'Feature',
                properties: { id: r.id, color: SPORT_COLORS[r.sport] ?? '#2d6a4f' },
                geometry: JSON.parse(r.geometry_geojson),
              });
            } catch { /* skip */ }
          }
          return { type: 'FeatureCollection', features };
        })(),
      );
    } catch { /* source may not exist */ }
  }, [mapInstance, routeMode, viewedRouteId, routes]);

  // Don't show panel when editing or viewing a specific route
  if (routeMode || viewedRouteId) return null;
  if (routes.length === 0) return null;

  return (
    <div style={{
      position: 'absolute',
      bottom: 72,
      left: 12,
      zIndex: 15,
      width: collapsed ? 'auto' : 260,
      maxHeight: collapsed ? 'auto' : 320,
      background: 'rgba(255,255,255,0.95)',
      backdropFilter: 'blur(12px)',
      borderRadius: 12,
      boxShadow: '0 2px 16px rgba(0,0,0,0.12)',
      border: '1px solid rgba(0,0,0,0.08)',
      overflow: 'hidden',
      display: 'flex',
      flexDirection: 'column',
      transition: 'width 0.2s',
    }}>
      {/* Header */}
      <button
        onClick={() => setCollapsed((v) => !v)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          gap: 8, padding: '8px 12px', background: 'none', border: 'none',
          cursor: 'pointer', width: '100%', textAlign: 'left',
          borderBottom: collapsed ? 'none' : '1px solid rgba(0,0,0,0.06)',
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 700, color: '#1a4731' }}>
          {collapsed ? `🗺️ ${visible.length}` : t('publicRoutes.title')}
        </span>
        {!collapsed && (
          <span style={{ fontSize: 11, color: '#888' }}>{t('publicRoutes.count', { count: String(visible.length), s: visible.length > 1 ? 's' : '' })}</span>
        )}
        <span style={{ fontSize: 10, color: '#aaa' }}>{collapsed ? '▲' : '▼'}</span>
      </button>

      {/* Route list */}
      {!collapsed && (
        <div style={{ overflowY: 'auto', flex: 1 }}>
          {visible.length === 0 && (
            <div style={{ padding: '16px 12px', textAlign: 'center', fontSize: 12, color: '#999' }}>
              {t('publicRoutes.noRoutes')}
            </div>
          )}
          {visible.map((r) => {
            const color = SPORT_COLORS[r.sport] ?? '#2d6a4f';
            const icon = SPORT_ICONS[r.sport] ?? '🗺️';
            return (
              <Link
                key={r.id}
                href={`/map?route=${r.id}`}
                style={{ textDecoration: 'none', color: 'inherit' }}
                onMouseEnter={() => setHoveredId(r.id)}
                onMouseLeave={() => setHoveredId(null)}
              >
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '7px 12px',
                  background: hoveredId === r.id ? 'rgba(45,106,79,0.06)' : 'transparent',
                  borderLeft: `3px solid ${hoveredId === r.id ? color : 'transparent'}`,
                  transition: 'all 0.15s',
                  cursor: 'pointer',
                }}>
                  <span style={{ fontSize: 14, flexShrink: 0 }}>{icon}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontSize: 12, fontWeight: 600, color: '#1a1a1a',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {r.name || t('common.noName')}
                    </div>
                    <div style={{ display: 'flex', gap: 6, fontSize: 10, color: '#888', marginTop: 1 }}>
                      <span style={{
                        padding: '0 4px', borderRadius: 3, fontSize: 9, fontWeight: 700,
                        background: color + '20', color,
                        textTransform: 'uppercase',
                      }}>
                        {r.sport}
                      </span>
                      {r.distance_m != null && <span>{formatDist(r.distance_m)}</span>}
                      {r.elevation_gain_m != null && r.elevation_gain_m > 0 && (
                        <span>↑{Math.round(r.elevation_gain_m)}m</span>
                      )}
                    </div>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
});
