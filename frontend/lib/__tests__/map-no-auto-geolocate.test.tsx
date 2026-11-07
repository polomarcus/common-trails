// @vitest-environment jsdom
/**
 * The home hero and /map share components/Map. It used to call
 * `geolocate.trigger()` on map `load`, so EVERY visitor of the landing page got
 * the browser's location-permission prompt (frontend audit 2026-11-07, P0 #1).
 * The locate BUTTON stays; it is never triggered programmatically.
 * Drives the REAL Map component over a mocked maplibre-gl.
 */
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';

const ml = vi.hoisted(() => {
  const state = {
    trigger: vi.fn(),
    controls: [] as unknown[],
    handlers: {} as Record<string, Array<(e?: unknown) => void>>,
  };
  class GeolocateControl {
    trigger = state.trigger;
    constructor(public opts: unknown) {}
  }
  class FakeMap {
    constructor() { state.handlers = {}; }
    on(type: string, fn: (e?: unknown) => void) {
      (state.handlers[type] ??= []).push(fn);
      return this;
    }
    addControl(c: unknown) { state.controls.push(c); return this; }
    remove() {}
  }
  return { state, GeolocateControl, FakeMap };
});

vi.mock('maplibre-gl', () => ({
  default: { Map: ml.FakeMap, GeolocateControl: ml.GeolocateControl },
}));
vi.mock('maplibre-gl/dist/maplibre-gl.css', () => ({}));
vi.mock('@/lib/pmtiles-protocol', () => ({ registerPMTiles: () => {} }));

import Map from '@/components/Map';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe('Map — no geolocation prompt on load', () => {
  let root: Root | null = null;
  let container: HTMLDivElement | null = null;
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container?.remove();
    ml.state.trigger.mockClear();
    ml.state.controls = [];
  });

  it('adds the locate button but never triggers it when the map loads', async () => {
    container = document.createElement('div');
    document.body.appendChild(container);
    const onMapReady = vi.fn();
    await act(async () => {
      root = createRoot(container!);
      root.render(<Map offlineMode={false} styleUrl="https://example.test/style.json" onMapReady={onMapReady} />);
    });
    // Let the dynamic-import promise resolve and the map be constructed.
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });

    expect(ml.state.controls.some((c) => c instanceof ml.GeolocateControl)).toBe(true);

    await act(async () => {
      for (const fn of ml.state.handlers.load ?? []) fn();
    });
    expect(onMapReady).toHaveBeenCalledTimes(1);
    expect(ml.state.trigger).not.toHaveBeenCalled();
  });
});
