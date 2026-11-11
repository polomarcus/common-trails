// @vitest-environment jsdom
/**
 * Closing a selected ride on /map restores the layer toggles saved when it was
 * opened. Regression: the restore used a lazy updater `() => ref.current!` and
 * cleared the ref right after, so React ran the updater on a null ref, set the
 * toggles to null and /map crashed ("can't access property heatmap") on the
 * card's close button. Drives the REAL useMapLayerSync with a stub map.
 */
import React, { useRef, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';

import { useMapLayerSync } from '@/hooks/useMapLayerSync';
import type { LayerToggles, WaymarkedToggles } from '@/hooks/useLayerToggles';
import type { ActivityItem } from '@/lib/map-utils';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
globalThis.fetch = vi.fn(() => new Promise(() => {})) as unknown as typeof fetch;

// Every map method exists and does nothing; getSource returns a stub source.
const stub: any = new Proxy(() => stub, { get: () => stub, apply: () => stub }); // eslint-disable-line @typescript-eslint/no-explicit-any
const map: any = new Proxy({}, { // eslint-disable-line @typescript-eslint/no-explicit-any
  get: (_t, k) => (k === 'getLayer' || k === 'getStyle' ? () => undefined : () => stub),
});

const INITIAL: LayerToggles = {
  heatmap: true, dfci: true, myTraces: true, myCells: false, hillshade: false, photos: false,
};

const ride: ActivityItem = {
  id: 'a1', name: 'Sortie', sport: 'gravel', distance_m: 12000, elevation_gain_m: 100,
  center: [3, 43], bbox: [3, 43, 3.1, 43.1],
  activity_date: '2026-11-01T10:00:00Z', created_at: '2026-11-01T10:00:00Z',
  coords: [[3, 43], [3.05, 43.05], [3.1, 43.1]], provider: 'file', provider_activity_id: null,
};

let seen: LayerToggles | null = null;
let select: (a: ActivityItem | null) => void = () => {};
let view: (r: unknown) => void = () => {};
const route = { coords: [[3, 43], [3.2, 43.2]] };

function Harness() {
  const [layers, setLayers] = useState<LayerToggles>(INITIAL);
  const [waymarked, setWaymarked] = useState<WaymarkedToggles>({ hiking: false, cycling: false, mtb: false });
  const [selected, setSelected] = useState<ActivityItem | null>(null);
  const [viewed, setViewed] = useState<unknown>(null);
  select = setSelected;
  view = setViewed;
  seen = layers;
  const layersReady = useRef(true);
  const nullRef = <T,>() => ({ current: null as T | null });
  useMapLayerSync({
    mapInstance: map, layersReady, layers, setLayers, waymarked, setWaymarked,
    highContrast: false, rv: {}, heatmapSport: 'all', heatmapDays: null,
    loadCommunityHeatmap: () => {}, loadDfciTrails: () => {},
    selectedActivity: selected, viewedRoute: viewed, editParentRoute: null,
    routeVariants: [], visibleVariantIds: new Set(),
    photoData: null, setPhotoData: () => {}, setLightboxPhotos: () => {}, setLightboxPhoto: () => {},
    photoMarkersRef: useRef([]), maplibreModuleRef: useRef(null),
    layersBeforeActivityRef: useRef(nullRef<LayerToggles>().current),
    layersBeforeRouteRef: useRef(nullRef<LayerToggles>().current),
    waymarkedBeforeRouteRef: useRef(nullRef<WaymarkedToggles>().current),
    updateTraceMarkers: () => {}, clearTraceMarkers: () => {},
    viewedSurface: null, viewedRouteProfileDataRef: useRef(null), routeTooltipRef: useRef(null),
  });
  return null;
}

describe('/map — closing a selected ride', () => {
  let root: Root | null = null;
  let container: HTMLDivElement;
  afterEach(() => { act(() => root?.unmount()); root = null; container.remove(); });

  it('restores the saved layer toggles (never null)', () => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root!.render(<Harness />));

    act(() => select(ride));
    expect(seen).toMatchObject({ heatmap: false, dfci: false, myTraces: false });

    act(() => select(null));
    expect(seen).not.toBeNull();
    expect(seen).toEqual(INITIAL);
  });

  it('ride + route open together: overlays stay hidden until both are closed', () => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root!.render(<Harness />));
    const hidden = { heatmap: false, dfci: false, myTraces: false };

    act(() => select(ride));
    act(() => view(route));
    act(() => select(null));
    expect(seen).toMatchObject(hidden);       // route still open
    act(() => view(null));
    expect(seen).toEqual(INITIAL);            // not the hidden snapshot

    act(() => view(route));
    act(() => select(ride));
    act(() => view(null));
    expect(seen).toMatchObject(hidden);       // ride still open
    act(() => select(null));
    expect(seen).toEqual(INITIAL);
  });
});
