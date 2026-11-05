// @vitest-environment jsdom
/**
 * Calques menu — "Ma heatmap" + "Heatmap commune" are two INDEPENDENT toggles
 * shown side by side (beta request, Thierry Crouzet), driven through the REAL
 * MapToolbar + I18nProvider.
 *
 * Hydration rule (#418): /map is a static export built LOGGED OUT. The
 * signed-in-only "Ma heatmap" toggle must therefore be absent from the
 * server/first render and appear only after the mount effect reads auth —
 * reading getToken() during render crashed /map for logged-in visitors.
 */
import React, { useState } from 'react';
import { renderToString } from 'react-dom/server';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const auth = vi.hoisted(() => ({ token: null as string | null }));
vi.mock('@/lib/auth', () => ({ getToken: () => auth.token }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: () => {}, replace: () => {} }) }));
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) =>
    React.createElement('a', { href, ...rest }, children),
}));
vi.mock('@/components/PlaceSearch', () => ({ default: () => null }));
vi.mock('@/components/LanguageToggle', () => ({ default: () => null }));

import MapToolbar, { type MapToolbarProps } from '@/components/map/MapToolbar';
import MapLegend from '@/components/map/MapLegend';
import { I18nProvider } from '@/lib/i18n';
import { MY_TRACES_COLOR } from '@/lib/constants';
import type { LayerToggles } from '@/hooks/useLayerToggles';

// IS_REACT_ACT_ENVIRONMENT silences React's "not wrapped in act" warning.
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const INITIAL_LAYERS: LayerToggles = {
  heatmap: true, dfci: true, myTraces: true, myCells: false, hillshade: false, photos: false,
};

let latestLayers: LayerToggles = INITIAL_LAYERS;

function Harness() {
  const [layers, setLayers] = useState<LayerToggles>(INITIAL_LAYERS);
  latestLayers = layers;
  const noop = () => {};
  const props: MapToolbarProps = {
    layers, setLayers,
    waymarked: { hiking: false, cycling: false, mtb: false }, setWaymarked: noop,
    highContrast: false, setHighContrast: noop,
    showLayersMenu: true, setShowLayersMenu: noop,
    showMoreLayers: false, setShowMoreLayers: noop,
    showBasemapMenu: false, setShowBasemapMenu: noop,
    basemapKey: 'ign', setBasemapKey: noop,
    layersMenuRef: React.createRef<HTMLDivElement>() as React.RefObject<HTMLDivElement>,
    basemapMenuRef: React.createRef<HTMLDivElement>() as React.RefObject<HTMLDivElement>,
    heatmapSport: 'road', setHeatmapSport: noop,
    heatmapDays: null, setHeatmapDays: noop,
    dfciCount: null,
    exploreMode: false, setExploreMode: noop,
    showUserMenu: false, setShowUserMenu: noop,
    userEmail: null, stravaName: null, stravaSyncing: false, stravaSyncMsg: null, lastSyncedAt: null,
    handleLogout: noop, setShowImportModal: noop, setShowExportModal: noop,
    activitiesCount: 0, mapInstance: null, dateLocale: 'fr-FR',
  };
  return (
    <I18nProvider>
      <MapToolbar {...props} />
    </I18nProvider>
  );
}

function checkbox(container: HTMLElement, testId: string): HTMLInputElement {
  const el = container.querySelector(`[data-testid="${testId}"] input[type="checkbox"]`);
  if (!el) throw new Error(`no checkbox under ${testId}`);
  return el as HTMLInputElement;
}

describe('MapToolbar — Ma heatmap / Heatmap commune', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;

  beforeEach(() => {
    auth.token = null;
    latestLayers = INITIAL_LAYERS;
    container = document.createElement('div');
    document.body.appendChild(container);
  });
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
  });

  it('server/static render has NO "Ma heatmap" toggle even for a signed-in rider (no #418 mismatch)', () => {
    auth.token = 'cookie-auth'; // logged in on the client…
    const html = renderToString(<Harness />);
    // …but the render path never reads auth: identical to the logged-out build.
    expect(html).not.toContain('toggle-my-traces');
    expect(html).toContain('toggle-heatmap');
    expect(html).toContain('Heatmap commune');
  });

  it('hydrating the logged-out static HTML as a signed-in rider does not mismatch, then shows the toggle', async () => {
    auth.token = null;
    container.innerHTML = renderToString(<Harness />); // the static export
    auth.token = 'cookie-auth';
    const { hydrateRoot } = await import('react-dom/client');
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    await act(async () => { root = hydrateRoot(container, <Harness />); });
    const hydrationErrors = errors.mock.calls.filter((c) =>
      /hydrat|did not match|#418/i.test(String(c[0])));
    errors.mockRestore();
    expect(hydrationErrors).toEqual([]);
    expect(container.querySelector('[data-testid="toggle-my-traces"]')).not.toBeNull();
  });

  it('logged out after mount: still no "Ma heatmap" toggle, community toggle present', async () => {
    await act(async () => { root = createRoot(container); root.render(<Harness />); });
    expect(container.querySelector('[data-testid="toggle-my-traces"]')).toBeNull();
    expect(container.querySelector('[data-testid="toggle-heatmap"]')).not.toBeNull();
  });

  it('signed in: both toggles render side by side, with their swatches and French labels', async () => {
    auth.token = 'cookie-auth';
    await act(async () => { root = createRoot(container); root.render(<Harness />); });

    const mine = container.querySelector('[data-testid="toggle-my-traces"]')!;
    const community = container.querySelector('[data-testid="toggle-heatmap"]')!;
    expect(mine.textContent).toContain('Ma heatmap');
    expect(community.textContent).toContain('Heatmap commune');
    // Adjacent: "Ma heatmap" sits immediately before the community block.
    expect(mine.nextElementSibling?.contains(community)).toBe(true);

    const swatch = container.querySelector('[data-testid="swatch-my-traces"]') as HTMLElement;
    // jsdom normalises hex → rgb(); #06b6d4 = rgb(6, 182, 212).
    expect(swatch.style.background).toMatch(/rgb\(6, 182, 212\)|#06b6d4/i);
    expect(MY_TRACES_COLOR).toBe('#06b6d4');
    const ramp = container.querySelector('[data-testid="swatch-community"]') as HTMLElement;
    expect(ramp.getAttribute('style')).toContain('linear-gradient');
  });

  it('the two toggles are independent: each flips only its own layer, both can be on', async () => {
    auth.token = 'cookie-auth';
    await act(async () => { root = createRoot(container); root.render(<Harness />); });

    // Both on by default → superposed.
    expect(checkbox(container, 'toggle-my-traces').checked).toBe(true);
    expect(checkbox(container, 'toggle-heatmap').checked).toBe(true);

    await act(async () => { checkbox(container, 'toggle-heatmap').click(); });
    expect(latestLayers.heatmap).toBe(false);
    expect(latestLayers.myTraces).toBe(true);

    await act(async () => { checkbox(container, 'toggle-my-traces').click(); });
    expect(latestLayers.heatmap).toBe(false);
    expect(latestLayers.myTraces).toBe(false);

    await act(async () => { checkbox(container, 'toggle-heatmap').click(); });
    expect(latestLayers.heatmap).toBe(true);
    expect(latestLayers.myTraces).toBe(false);

    await act(async () => { checkbox(container, 'toggle-my-traces').click(); });
    expect(latestLayers).toMatchObject({ heatmap: true, myTraces: true });
  });
});

describe('MapLegend — one cyan "Mes sorties" entry next to the community ramp', () => {
  const waymarked = { hiking: false, cycling: false, mtb: false };

  it('renders a single "Mes sorties" swatch (no per-sport list) after the community ramp', () => {
    const html = renderToString(
      <I18nProvider><MapLegend layers={INITIAL_LAYERS} waymarked={waymarked} /></I18nProvider>,
    );
    expect(html).toContain('Mes sorties');
    expect(html).toContain(MY_TRACES_COLOR);
    expect(html).not.toContain('Gravel');
    expect(html.indexOf('legend-community')).toBeLessThan(html.indexOf('legend-my-activities'));
  });

  it('hides "Mes sorties" when "Ma heatmap" is off', () => {
    const html = renderToString(
      <I18nProvider>
        <MapLegend layers={{ ...INITIAL_LAYERS, myTraces: false }} waymarked={waymarked} />
      </I18nProvider>,
    );
    expect(html).not.toContain('Mes sorties');
    expect(html).toContain('legend-community');
  });
});
