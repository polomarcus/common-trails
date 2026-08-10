'use client';

import { useState, useEffect, useRef, useCallback } from 'react';

export interface LayerToggles {
  heatmap: boolean;
  dfci: boolean;
  myTraces: boolean;
  myCells: boolean;
  hillshade: boolean;
  photos: boolean;
}

export interface WaymarkedToggles {
  hiking: boolean;
  cycling: boolean;
  mtb: boolean;
}

export interface LayerToggleState {
  layers: LayerToggles;
  setLayers: React.Dispatch<React.SetStateAction<LayerToggles>>;
  waymarked: WaymarkedToggles;
  setWaymarked: React.Dispatch<React.SetStateAction<WaymarkedToggles>>;
  highContrast: boolean;
  setHighContrast: (b: boolean) => void;
  showLayersMenu: boolean;
  setShowLayersMenu: React.Dispatch<React.SetStateAction<boolean>>;
  showMoreLayers: boolean;
  setShowMoreLayers: React.Dispatch<React.SetStateAction<boolean>>;
  showBasemapMenu: boolean;
  setShowBasemapMenu: React.Dispatch<React.SetStateAction<boolean>>;
  basemapKey: string;
  setBasemapKey: (k: string) => void;
  layersMenuRef: React.RefObject<HTMLDivElement>;
  basemapMenuRef: React.RefObject<HTMLDivElement>;
  /** True while heatmap is fading in/out (Crouzet discovery technique). */
  heatmapFading: boolean;
  /** Toggle heatmap with fade animation (triggered by H key or button). */
  toggleHeatmapWithFade: () => void;
}

export function useLayerToggles(): LayerToggleState {
  const [layers, setLayers] = useState<LayerToggles>({
    heatmap: true,
    dfci: true,
    myTraces: true,
    myCells: false,
    hillshade: false,
    photos: false,
  });

  const [waymarked, setWaymarked] = useState<WaymarkedToggles>({ hiking: false, cycling: false, mtb: false });

  const [highContrast, setHighContrast] = useState(false);
  const [showLayersMenu, setShowLayersMenu] = useState(false);
  const [showMoreLayers, setShowMoreLayers] = useState(false);
  const [showBasemapMenu, setShowBasemapMenu] = useState(false);
  const [basemapKey, setBasemapKey] = useState('ign');

  const layersMenuRef = useRef<HTMLDivElement>(null);
  const basemapMenuRef = useRef<HTMLDivElement>(null);

  // Hydration restore: waymarked + basemapKey defaults match the SSG HTML so
  // the static /map HTML matches the first client render. Swap to localStorage
  // values after mount — avoids React error #418 for returning users.
  useEffect(() => {
    try {
      const storedWaymarked = localStorage.getItem('cc_waymarked');
      if (storedWaymarked) setWaymarked(JSON.parse(storedWaymarked));
    } catch { /* ignore */ }
    const storedBasemap = localStorage.getItem('cc_basemap');
    if (storedBasemap) setBasemapKey(storedBasemap);
  }, []);

  // Persist basemap to localStorage
  useEffect(() => {
    try { localStorage.setItem('cc_basemap', basemapKey); } catch { /* ignore */ }
  }, [basemapKey]);

  // Close menus on click outside
  useEffect(() => {
    if (!showLayersMenu && !showBasemapMenu) return;
    const handleClick = (e: MouseEvent) => {
      if (showLayersMenu && layersMenuRef.current && !layersMenuRef.current.contains(e.target as Node)) {
        setShowLayersMenu(false);
      }
      if (showBasemapMenu && basemapMenuRef.current && !basemapMenuRef.current.contains(e.target as Node)) {
        setShowBasemapMenu(false);
      }
    };
    setTimeout(() => document.addEventListener('click', handleClick), 10);
    return () => document.removeEventListener('click', handleClick);
  }, [showLayersMenu, showBasemapMenu]);

  // ── Heatmap H key toggle with fade (Crouzet discovery technique) ────────
  // "if there are minimal differences when toggling, all cyclists use roads.
  //  If the map colors, you have assurance of many possibilities off-asphalt"
  const [heatmapFading, setHeatmapFading] = useState(false);

  const toggleHeatmapWithFade = useCallback(() => {
    setHeatmapFading(true);
    // Wait one frame so CSS transition starts before flipping state
    requestAnimationFrame(() => {
      setLayers(prev => ({ ...prev, heatmap: !prev.heatmap }));
      // Clear fading flag after CSS transition completes (300ms)
      setTimeout(() => setHeatmapFading(false), 300);
    });
  }, []);

  // H keyboard shortcut — works anywhere except input/textarea/contenteditable
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'h' && e.key !== 'H') return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (!target) return;
      const tag = target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || target.isContentEditable) return;
      e.preventDefault();
      toggleHeatmapWithFade();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [toggleHeatmapWithFade]);

  return {
    layers, setLayers,
    waymarked, setWaymarked,
    highContrast, setHighContrast,
    showLayersMenu, setShowLayersMenu,
    showMoreLayers, setShowMoreLayers,
    showBasemapMenu, setShowBasemapMenu,
    basemapKey, setBasemapKey,
    layersMenuRef, basemapMenuRef,
    heatmapFading, toggleHeatmapWithFade,
  };
}
