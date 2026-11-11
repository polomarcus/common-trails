// @vitest-environment jsdom
/**
 * Réglages d'apparence des heatmaps (Calques → Apparence) : store mémorisé par
 * navigateur + panneau + effet sur les couches « Ma heatmap ».
 */
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';

import HeatmapAppearancePanel from '@/components/map/HeatmapAppearanceControls';
import { I18nProvider } from '@/lib/i18n';
import { MY_TRACES_COLOR } from '@/lib/constants';
import {
  DEFAULT_APPEARANCE, MY_COLOR_CHOICES, STORAGE_KEY, __resetHeatmapAppearanceForTests,
  getHeatmapAppearance, sanitizeAppearance, setHeatmapAppearance,
} from '@/lib/heatmap-appearance';
import {
  USER_TRACE_GLOW_LAYER, USER_TRACE_LINE_LAYER, USER_TRACE_LINE_OPACITY,
  applyUserTraceAppearance, applyUserTraceOpacity, userTraceOpacity,
} from '@/lib/user-traces-layers';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

beforeEach(() => {
  localStorage.clear();
  __resetHeatmapAppearanceForTests();
});

describe('heatmap appearance store', () => {
  it('defaults: the SSOT colour, full opacity — and the default colour is offered', () => {
    expect(getHeatmapAppearance()).toEqual(DEFAULT_APPEARANCE);
    expect(DEFAULT_APPEARANCE.myColor).toBe(MY_TRACES_COLOR);
    expect(MY_COLOR_CHOICES[0]).toBe(MY_TRACES_COLOR);
  });

  it('persists a change and reloads it in a later visit', () => {
    setHeatmapAppearance({ myColor: MY_COLOR_CHOICES[2], myOpacity: 0.6 });
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toMatchObject({ myColor: MY_COLOR_CHOICES[2], myOpacity: 0.6 });
    __resetHeatmapAppearanceForTests(); // "new page load"
    expect(getHeatmapAppearance()).toMatchObject({ myColor: MY_COLOR_CHOICES[2], myOpacity: 0.6, communityOpacity: 1 });
  });

  it('never trusts localStorage: unknown colour, junk and out-of-range values fall back / clamp', () => {
    expect(sanitizeAppearance({ myColor: 'url(javascript:alert(1))', myOpacity: 99, communityOpacity: -3 }))
      .toEqual({ myColor: MY_TRACES_COLOR, myOpacity: 1, communityOpacity: 0.3 });
    expect(sanitizeAppearance('nope')).toEqual(DEFAULT_APPEARANCE);
    localStorage.setItem(STORAGE_KEY, '{not json');
    expect(getHeatmapAppearance()).toEqual(DEFAULT_APPEARANCE);
  });
});

describe('effect on the "Ma heatmap" layers', () => {
  it('default setting leaves the zoom ramps byte-identical; a lower opacity scales them (still zoom ramps)', () => {
    expect(userTraceOpacity(false).line).toBe(USER_TRACE_LINE_OPACITY);
    setHeatmapAppearance({ myOpacity: 0.5 });
    const line = userTraceOpacity(false).line as unknown[];
    expect(line.slice(0, 3)).toEqual(['interpolate', ['linear'], ['zoom']]);
    expect(line[4]).toBeCloseTo((USER_TRACE_LINE_OPACITY as number[])[4] * 0.5, 6);
  });

  it('applyUserTraceAppearance repaints colour + opacity and keeps the selection dimming', () => {
    const set: Record<string, unknown> = {};
    const map = {
      getLayer: () => ({}),
      setPaintProperty: (layer: string, prop: string, v: unknown) => { set[`${layer}:${prop}`] = v; },
    };
    applyUserTraceOpacity(map, true); // an activity is selected → others dimmed
    const dimmedDefault = (set[`${USER_TRACE_LINE_LAYER}:line-opacity`] as number[])[4];
    setHeatmapAppearance({ myColor: MY_COLOR_CHOICES[1], myOpacity: 0.5 });
    applyUserTraceAppearance(map);
    expect(set[`${USER_TRACE_LINE_LAYER}:line-color`]).toBe(MY_COLOR_CHOICES[1]);
    expect(set[`${USER_TRACE_GLOW_LAYER}:line-color`]).toBe(MY_COLOR_CHOICES[1]);
    expect((set[`${USER_TRACE_LINE_LAYER}:line-opacity`] as number[])[4]).toBeCloseTo(dimmedDefault * 0.5, 6);
    applyUserTraceOpacity(map, false); // leave the module state clean
  });
});

describe('HeatmapAppearancePanel (Calques → Apparence)', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;
  const q = (id: string) => container.querySelector(`[data-testid="${id}"]`) as HTMLElement | null;

  async function mount(props: { showMine: boolean; showCommunity: boolean }) {
    await act(async () => {
      root = createRoot(container);
      root.render(<I18nProvider><HeatmapAppearancePanel {...props} /></I18nProvider>);
    });
  }
  beforeEach(() => { container = document.createElement('div'); document.body.appendChild(container); });
  afterEach(() => { act(() => root?.unmount()); root = null; container.remove(); });

  it('collapsed to one line by default; nothing at all when no heatmap layer is shown', async () => {
    await mount({ showMine: true, showCommunity: true });
    expect(q('heatmap-appearance-toggle')?.textContent).toContain('Apparence');
    expect(q('my-heatmap-opacity')).toBeNull();
    act(() => root?.unmount());
    await mount({ showMine: false, showCommunity: false });
    expect(q('heatmap-appearance')).toBeNull();
  });

  it('picking a colour and moving the sliders updates the stored setting', async () => {
    await mount({ showMine: true, showCommunity: true });
    await act(async () => { q('heatmap-appearance-toggle')!.click(); });
    const target = MY_COLOR_CHOICES[3];
    await act(async () => { q(`my-heatmap-color-${target.slice(1)}`)!.click(); });
    expect(getHeatmapAppearance().myColor).toBe(target);
    expect(q(`my-heatmap-color-${target.slice(1)}`)!.getAttribute('aria-checked')).toBe('true');

    const slide = async (id: string, v: string) => {
      const input = q(id) as HTMLInputElement;
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, v);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
    };
    await slide('my-heatmap-opacity', '0.5');
    await slide('community-heatmap-opacity', '1.2');
    expect(getHeatmapAppearance()).toMatchObject({ myOpacity: 0.5, communityOpacity: 1.2 });
    // Reset link brings the community opacity back to the default.
    await act(async () => { q('community-heatmap-opacity-reset')!.click(); });
    expect(getHeatmapAppearance().communityOpacity).toBe(1);
  });

  it('only the shown layers get controls', async () => {
    await mount({ showMine: false, showCommunity: true });
    await act(async () => { q('heatmap-appearance-toggle')!.click(); });
    expect(q('my-heatmap-appearance')).toBeNull();
    expect(q('community-heatmap-opacity')).not.toBeNull();
  });
});
