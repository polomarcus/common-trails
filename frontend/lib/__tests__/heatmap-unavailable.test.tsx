// @vitest-environment jsdom
/**
 * Tiles host down → /map showed the basemap alone, no word of explanation
 * (frontend audit 2026-11-07, P0 #4). Drives the REAL watcher over a fake
 * evented map, and the REAL notice through the I18nProvider (FR + EN).
 */
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { watchHeatmapAvailability, COMMUNITY_HEATMAP_SOURCE_ID } from '@/lib/heatmap-availability';
import HeatmapUnavailableNotice from '@/components/map/HeatmapUnavailableNotice';
import { I18nProvider } from '@/lib/i18n';
import fr from '@/lib/translations/fr';
import en from '@/lib/translations/en';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

type Ev = { sourceId?: string; tile?: unknown; error?: unknown };
function fakeMap() {
  const handlers: Record<string, Array<(e: Ev) => void>> = {};
  return {
    on: (t: string, fn: (e: Ev) => void) => { (handlers[t] ??= []).push(fn); },
    off: (t: string, fn: (e: Ev) => void) => {
      handlers[t] = (handlers[t] ?? []).filter((h) => h !== fn);
    },
    fire: (t: string, e: Ev) => { for (const fn of handlers[t] ?? []) fn(e); },
  };
}

describe('watchHeatmapAvailability', () => {
  it('reports unavailable when the community source errors before any tile', () => {
    const m = fakeMap();
    const onChange = vi.fn();
    watchHeatmapAvailability(m, onChange);
    m.fire('error', { sourceId: COMMUNITY_HEATMAP_SOURCE_ID, error: new Error('Failed to fetch') });
    m.fire('error', { sourceId: COMMUNITY_HEATMAP_SOURCE_ID, error: new Error('again') });
    expect(onChange.mock.calls).toEqual([[true]]);
  });

  it('ignores errors from other sources (basemap, DFCI…)', () => {
    const m = fakeMap();
    const onChange = vi.fn();
    watchHeatmapAvailability(m, onChange);
    m.fire('error', { sourceId: 'openmaptiles', error: new Error('x') });
    m.fire('error', { error: new Error('style') });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('a tile error AFTER tiles loaded is not an outage', () => {
    const m = fakeMap();
    const onChange = vi.fn();
    watchHeatmapAvailability(m, onChange);
    m.fire('sourcedata', { sourceId: COMMUNITY_HEATMAP_SOURCE_ID, tile: {} });
    m.fire('error', { sourceId: COMMUNITY_HEATMAP_SOURCE_ID, error: new Error('one tile') });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('recovers when a tile finally loads, and unsubscribes cleanly', () => {
    const m = fakeMap();
    const onChange = vi.fn();
    const stop = watchHeatmapAvailability(m, onChange);
    m.fire('error', { sourceId: COMMUNITY_HEATMAP_SOURCE_ID });
    m.fire('sourcedata', { sourceId: COMMUNITY_HEATMAP_SOURCE_ID }); // metadata, no tile
    m.fire('sourcedata', { sourceId: COMMUNITY_HEATMAP_SOURCE_ID, tile: {} });
    expect(onChange.mock.calls).toEqual([[true], [false]]);
    stop();
    m.fire('error', { sourceId: COMMUNITY_HEATMAP_SOURCE_ID });
    expect(onChange).toHaveBeenCalledTimes(2);
  });
});

describe('HeatmapUnavailableNotice', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;
  beforeEach(() => {
    localStorage.clear();
    container = document.createElement('div');
    document.body.appendChild(container);
  });
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
  });

  const render = async (unavailable: boolean) => {
    await act(async () => {
      root ??= createRoot(container);
      root.render(<I18nProvider><HeatmapUnavailableNotice unavailable={unavailable} /></I18nProvider>);
    });
  };
  const notice = () => container.querySelector('[data-testid="heatmap-unavailable"]');

  it('renders nothing while the heatmap is fine', async () => {
    await render(false);
    expect(notice()).toBeNull();
  });

  it('shows the translated, non-blocking notice and can be dismissed', async () => {
    await render(true);
    expect(notice()?.getAttribute('role')).toBe('status');
    expect(notice()?.textContent).toContain(fr['map.heatmapUnavailable']);
    expect(fr['map.heatmapUnavailable']).toBe('La heatmap est momentanément indisponible.');
    await act(async () => {
      (container.querySelector('[data-testid="heatmap-unavailable-dismiss"]') as HTMLButtonElement).click();
    });
    expect(notice()).toBeNull();
  });

  it('is translated in English', async () => {
    localStorage.setItem('ct_locale', 'en');
    await render(true);
    expect(notice()?.textContent).toContain(en['map.heatmapUnavailable']);
  });
});
