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
// The share panel fetches its status: never hit a real API from a unit test.
vi.mock('@/lib/my-heatmap-share', () => ({
  fetchShareStatus: () => Promise.resolve({ available: false, active: false }),
  createOrRegenerateShare: vi.fn(),
  revokeShare: vi.fn(),
  formatShareDate: () => '',
}));

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

function Harness({ menuOpen = false, isAdmin, activitiesLoading }: { menuOpen?: boolean; isAdmin?: boolean; activitiesLoading?: boolean } = {}) {
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
    showUserMenu: menuOpen, setShowUserMenu: noop, isAdmin,
    userEmail: null, stravaName: null, stravaSyncing: false, stravaSyncMsg: null, lastSyncedAt: null,
    handleLogout: noop, setShowImportModal: noop, setShowExportModal: noop,
    activitiesCount: 0, mapInstance: null, dateLocale: 'fr-FR', activitiesLoading,
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

describe('MapToolbar — "Ma heatmap" loading hint while /me/activities is in flight', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;

  beforeEach(() => {
    auth.token = null;
    container = document.createElement('div');
    document.body.appendChild(container);
  });
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
  });

  const hint = () => container.querySelector('[data-testid="my-traces-loading"]');

  it('signed in + loading: "Chargement de tes traces…" sits on the "Ma heatmap" row', async () => {
    auth.token = 'cookie-auth';
    await act(async () => { root = createRoot(container); root.render(<Harness activitiesLoading />); });
    const row = container.querySelector('[data-testid="toggle-my-traces"]')!;
    expect(hint()).not.toBeNull();
    expect(row.contains(hint())).toBe(true);
    expect(hint()!.textContent).toBe('Chargement de tes traces…');
    expect(hint()!.getAttribute('role')).toBe('status');
  });

  it('signed in + loaded: no hint', async () => {
    auth.token = 'cookie-auth';
    await act(async () => { root = createRoot(container); root.render(<Harness activitiesLoading={false} />); });
    expect(container.querySelector('[data-testid="toggle-my-traces"]')).not.toBeNull();
    expect(hint()).toBeNull();
  });

  it('flips off when the load finishes (re-render with loading=false)', async () => {
    auth.token = 'cookie-auth';
    await act(async () => { root = createRoot(container); root.render(<Harness activitiesLoading />); });
    expect(hint()).not.toBeNull();
    await act(async () => { root!.render(<Harness activitiesLoading={false} />); });
    expect(hint()).toBeNull();
  });

  it('logged out: never shown, and the static render never contains it (#418)', async () => {
    auth.token = 'cookie-auth';
    expect(renderToString(<Harness activitiesLoading />)).not.toContain('my-traces-loading');
    auth.token = null;
    await act(async () => { root = createRoot(container); root.render(<Harness activitiesLoading />); });
    expect(hint()).toBeNull();
  });

  it('FR and EN strings exist', async () => {
    const fr = (await import('@/lib/translations/fr')).default;
    const en = (await import('@/lib/translations/en')).default;
    expect(fr['map.toolbar.myHeatmapLoading']).toBe('Chargement de tes traces…');
    expect(en['map.toolbar.myHeatmapLoading']).toBe('Loading your traces…');
  });
});

describe('MapToolbar — account menu "Admin" entry is admin-only', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;

  beforeEach(() => {
    auth.token = 'cookie-auth';
    container = document.createElement('div');
    document.body.appendChild(container);
  });
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
  });

  const adminLink = () => Array.from(container.querySelectorAll('a'))
    .find((a) => a.getAttribute('href') === '/admin');

  it('hidden for a regular signed-in rider (is_admin false / unknown)', async () => {
    await act(async () => { root = createRoot(container); root.render(<Harness menuOpen />); });
    // The menu itself is open (other entries render)…
    expect(container.querySelector('[data-testid="map-open-import"]')).not.toBeNull();
    // …but no Admin entry.
    expect(adminLink()).toBeUndefined();
    expect(container.textContent).not.toContain('Admin');
  });

  it('shown for an admin', async () => {
    await act(async () => { root = createRoot(container); root.render(<Harness menuOpen isAdmin />); });
    expect(adminLink()).toBeDefined();
  });
});

describe('wording — legend and toolbar both say "Ma heatmap" / "My heatmap"', () => {
  it('same label in FR and EN', async () => {
    const fr = (await import('@/lib/translations/fr')).default;
    const en = (await import('@/lib/translations/en')).default;
    expect(fr['map.legend.myActivities']).toBe(fr['map.toolbar.myHeatmap']);
    expect(fr['map.legend.myActivities']).toBe('Ma heatmap');
    expect(en['map.legend.myActivities']).toBe(en['map.toolbar.myHeatmap']);
    expect(en['map.legend.myActivities']).toBe('My heatmap');
  });
});

describe('MapLegend — one cyan "Ma heatmap" entry next to the community ramp, signed-in only', () => {
  const waymarked = { hiking: false, cycling: false, mtb: false };
  const legend = (layers: LayerToggles = INITIAL_LAYERS) => (
    <I18nProvider><MapLegend layers={layers} waymarked={waymarked} /></I18nProvider>
  );
  let container: HTMLDivElement;
  let root: Root | null = null;

  beforeEach(() => {
    auth.token = null;
    container = document.createElement('div');
    document.body.appendChild(container);
  });
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
  });

  it('static render has NO "Ma heatmap" even for a signed-in rider (render never reads auth)', () => {
    auth.token = 'cookie-auth';
    const html = renderToString(legend());
    expect(html).not.toContain('Ma heatmap');
    expect(html).not.toContain('legend-my-activities');
    expect(html).toContain('legend-community');
  });

  it('logged out after mount: no "Ma heatmap" (a visitor has no traces and no toggle to hide it)', async () => {
    await act(async () => { root = createRoot(container); root.render(legend()); });
    expect(container.querySelector('[data-testid="legend-my-activities"]')).toBeNull();
    expect(container.textContent).not.toContain('Ma heatmap');
    expect(container.querySelector('[data-testid="legend-community"]')).not.toBeNull();
  });

  it('signed in: hydrating the logged-out HTML does not mismatch, then ONE cyan "Ma heatmap" appears right after the community ramp', async () => {
    container.innerHTML = renderToString(legend()); // the static export (logged out)
    auth.token = 'cookie-auth';
    const { hydrateRoot } = await import('react-dom/client');
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    await act(async () => { root = hydrateRoot(container, legend()); });
    const hydrationErrors = errors.mock.calls.filter((c) =>
      /hydrat|did not match|#418/i.test(String(c[0])));
    errors.mockRestore();
    expect(hydrationErrors).toEqual([]);

    const community = container.querySelector('[data-testid="legend-community"]');
    const mine = container.querySelector('[data-testid="legend-my-activities"]') as HTMLElement | null;
    expect(community).not.toBeNull();
    expect(mine).not.toBeNull();
    expect(mine!.textContent).toContain('Ma heatmap');
    // Exactly one entry for my traces — no per-sport list.
    expect(container.querySelectorAll('[data-testid="legend-my-activities"]').length).toBe(1);
    expect(container.textContent).not.toContain('Gravel');
    // ORDER: "Ma heatmap" is the very next legend row after the community ramp.
    expect(community!.nextElementSibling).toBe(mine);
    const swatch = mine!.firstElementChild as HTMLElement;
    expect(swatch.style.background).toMatch(/rgb\(6, 182, 212\)|#06b6d4/i);
    expect(MY_TRACES_COLOR).toBe('#06b6d4');
  });

  it('signed in but "Ma heatmap" off: no "Ma heatmap"', async () => {
    auth.token = 'cookie-auth';
    await act(async () => {
      root = createRoot(container);
      root.render(legend({ ...INITIAL_LAYERS, myTraces: false }));
    });
    expect(container.querySelector('[data-testid="legend-my-activities"]')).toBeNull();
    expect(container.querySelector('[data-testid="legend-community"]')).not.toBeNull();
  });
});
