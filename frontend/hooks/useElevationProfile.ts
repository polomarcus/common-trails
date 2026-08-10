'use client';

import { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { buildProfileData, findNearestByDistance, getHighlightCoords } from '@/lib/elevation-profile';
import type { ProfileData } from '@/lib/elevation-profile';
import type { ElevationProfileProHandle } from '@/components/ElevationProfilePro';
import { PROFILE_HOVER_DOT, PROFILE_HOVER_HALO } from '@/lib/routing-style';

export interface ElevationProfileState {
  profileData: ProfileData | null;
  profileDataRef: React.RefObject<ProfileData | null>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  profileProRef: any;
  viewedProfileData: ProfileData | null;
  setViewedProfileData: React.Dispatch<React.SetStateAction<ProfileData | null>>;
  viewedRouteProfileDataRef: React.MutableRefObject<ProfileData | null>;
  hoverSourceRef: React.MutableRefObject<'profile' | 'map' | null>;
  profileHoverMarkerRef: React.MutableRefObject<any>;
  handleProfileHover: (dist_m: number) => void;
  handleProfileHoverEnd: () => void;
  handleViewedProfileHover: (dist_m: number) => void;
  handleViewedProfileHoverEnd: () => void;
}

export function useElevationProfile(
  mapInstance: unknown,
  maplibreModuleRef: React.RefObject<any>,
  routeElevation: number[][] | null,
  routeSurface: { segments?: { from_idx: number; to_idx: number; surface: string }[] } | null,
  routeMode: boolean,
  routing: boolean,
): ElevationProfileState {
  // @ts-ignore — ref compatibility between React 18/19 types
  const profileProRef = useRef<ElevationProfileProHandle>(null);
  const profileHoverMarkerRef = useRef<any>(null);
  const profileDataRef = useRef<ProfileData | null>(null);
  const hoverSourceRef = useRef<'profile' | 'map' | null>(null);
  const viewedRouteProfileDataRef = useRef<ProfileData | null>(null);
  const [viewedProfileData, setViewedProfileData] = useState<ProfileData | null>(null);

  // Memoized profile data from elevation + surface
  const profileData = useMemo(
    () => buildProfileData(routeElevation ?? [], routeSurface?.segments),
    [routeElevation, routeSurface],
  );

  // Keep ref in sync for event handlers
  useEffect(() => {
    profileDataRef.current = profileData;
    if (profileHoverMarkerRef.current) {
      try { profileHoverMarkerRef.current.remove(); } catch { /* ignore */ }
      profileHoverMarkerRef.current = null;
    }
    hoverSourceRef.current = null;
  }, [profileData]);

  // Sync viewed profile data ref
  useEffect(() => {
    viewedRouteProfileDataRef.current = viewedProfileData;
  }, [viewedProfileData]);

  // Steep warning markers
  useEffect(() => {
    const m = mapInstance as any;
    if (!m?.getSource?.('steep-warnings')) return;

    if (!profileData || !routeMode) {
      (m.getSource('steep-warnings') as any).setData({ type: 'FeatureCollection', features: [] });
      return;
    }

    const STEEP_THRESHOLD = 15;
    const MIN_DIST_BETWEEN = 200;
    const features: any[] = [];
    let lastMarkerDist = -MIN_DIST_BETWEEN;

    for (const pt of profileData.points) {
      if (Math.abs(pt.grade) >= STEEP_THRESHOLD && (pt.dist_m - lastMarkerDist) >= MIN_DIST_BETWEEN) {
        features.push({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [pt.lon, pt.lat] },
          properties: { grade: `${pt.grade > 0 ? '+' : ''}${Math.round(pt.grade)}` },
        });
        lastMarkerDist = pt.dist_m;
      }
    }
    (m.getSource('steep-warnings') as any).setData({ type: 'FeatureCollection', features });
  }, [profileData, mapInstance, routeMode]);

  const handleProfileHover = useCallback((dist_m: number) => {
    hoverSourceRef.current = 'profile';
    const data = profileDataRef.current;
    if (!data || !mapInstance) return;
    const m = mapInstance as any;
    const idx = findNearestByDistance(data, dist_m);
    const pt = data.points[idx];
    if (!pt) return;

    if (profileHoverMarkerRef.current) {
      profileHoverMarkerRef.current.setLngLat([pt.lon, pt.lat]);
    } else if (maplibreModuleRef.current) {
      const el = document.createElement('div');
      el.setAttribute('data-testid', 'profile-hover-marker');
      el.style.cssText = 'pointer-events:none;width:24px;height:24px;position:relative;';
      const halo = document.createElement('div');
      halo.style.cssText = `position:absolute;inset:0;border-radius:50%;background:${PROFILE_HOVER_HALO};`;
      el.appendChild(halo);
      const dot = document.createElement('div');
      dot.style.cssText = `position:absolute;top:6px;left:6px;width:12px;height:12px;border-radius:50%;background:${PROFILE_HOVER_DOT};border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,0.3);`;
      el.appendChild(dot);
      profileHoverMarkerRef.current = new maplibreModuleRef.current.Marker({ element: el, anchor: 'center' })
        .setLngLat([pt.lon, pt.lat])
        .addTo(m);
    }

    try {
      const segCoords = getHighlightCoords(data, idx, 30);
      m.getSource('profile-hover-segment')?.setData({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: segCoords },
        properties: {},
      });
    } catch { /* source may not exist yet */ }
  }, [mapInstance, maplibreModuleRef]);

  const handleProfileHoverEnd = useCallback(() => {
    hoverSourceRef.current = null;
    if (profileHoverMarkerRef.current) {
      try { profileHoverMarkerRef.current.remove(); } catch { /* ignore */ }
      profileHoverMarkerRef.current = null;
    }
    if (mapInstance) {
      try {
        (mapInstance as any).getSource('profile-hover-segment')?.setData({
          type: 'FeatureCollection', features: [],
        });
      } catch { /* ignore */ }
    }
  }, [mapInstance]);

  const handleViewedProfileHover = useCallback((dist_m: number) => {
    hoverSourceRef.current = 'profile';
    const data = viewedRouteProfileDataRef.current;
    if (!data || !mapInstance) return;
    const m = mapInstance as any;
    const idx = findNearestByDistance(data, dist_m);
    const pt = data.points[idx];
    if (!pt) return;

    if (profileHoverMarkerRef.current) {
      profileHoverMarkerRef.current.setLngLat([pt.lon, pt.lat]);
    } else if (maplibreModuleRef.current) {
      const el = document.createElement('div');
      el.style.cssText = 'pointer-events:none;width:24px;height:24px;position:relative;';
      const halo = document.createElement('div');
      halo.style.cssText = `position:absolute;inset:0;border-radius:50%;background:${PROFILE_HOVER_HALO};`;
      el.appendChild(halo);
      const dot = document.createElement('div');
      dot.style.cssText = `position:absolute;top:6px;left:6px;width:12px;height:12px;border-radius:50%;background:${PROFILE_HOVER_DOT};border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,0.3);`;
      el.appendChild(dot);
      profileHoverMarkerRef.current = new maplibreModuleRef.current.Marker({ element: el, anchor: 'center' })
        .setLngLat([pt.lon, pt.lat])
        .addTo(m);
    }

    try {
      const segCoords = getHighlightCoords(data, idx, 30);
      m.getSource('profile-hover-segment')?.setData({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: segCoords },
        properties: {},
      });
    } catch { /* ignore */ }
  }, [mapInstance, maplibreModuleRef]);

  const handleViewedProfileHoverEnd = useCallback(() => {
    hoverSourceRef.current = null;
    if (profileHoverMarkerRef.current) {
      try { profileHoverMarkerRef.current.remove(); } catch { /* ignore */ }
      profileHoverMarkerRef.current = null;
    }
    if (mapInstance) {
      try {
        (mapInstance as any).getSource('profile-hover-segment')?.setData({
          type: 'FeatureCollection', features: [],
        });
      } catch { /* ignore */ }
    }
  }, [mapInstance]);

  return {
    profileData,
    profileDataRef,
    profileProRef,
    viewedProfileData, setViewedProfileData,
    viewedRouteProfileDataRef,
    hoverSourceRef,
    profileHoverMarkerRef,
    handleProfileHover,
    handleProfileHoverEnd,
    handleViewedProfileHover,
    handleViewedProfileHoverEnd,
  };
}
