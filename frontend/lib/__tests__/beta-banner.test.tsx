// @vitest-environment jsdom
/**
 * Global beta footer banner: it is fixed at the bottom (zIndex 9998) and used
 * to cover the /map legend and the selected-activity card on phones.
 *  - it publishes its height as `--beta-banner-h`, and the bottom-anchored map
 *    panels (MapLegend, SelectedActivityCard) offset themselves by it;
 *  - it is dismissible, the choice is remembered (localStorage, read only in
 *    an effect), and dismissing drops the offset to 0px.
 * Drives the REAL components.
 */
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('@/lib/auth', () => ({ getToken: () => null }));
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) =>
    React.createElement('a', { href, ...rest }, children),
}));
vi.mock('@/components/ElevationProfile', () => ({ default: () => null }));

import BetaBanner, { BETA_BANNER_DISMISSED_KEY } from '@/components/BetaBanner';
import MapLegend from '@/components/map/MapLegend';
import SelectedActivityCard from '@/components/map/SelectedActivityCard';
import { I18nProvider } from '@/lib/i18n';
import type { ActivityItem } from '@/lib/map-utils';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const bannerVar = () => document.documentElement.style.getPropertyValue('--beta-banner-h');

describe('BetaBanner — dismissible + --beta-banner-h', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;

  beforeEach(() => {
    localStorage.clear();
    document.documentElement.style.removeProperty('--beta-banner-h');
    // jsdom has no layout: give the banner a real-looking height.
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(37);
    container = document.createElement('div');
    document.body.appendChild(container);
  });
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
    vi.restoreAllMocks();
  });

  const mount = async () => {
    await act(async () => {
      root = createRoot(container);
      root.render(<I18nProvider><BetaBanner /></I18nProvider>);
    });
  };

  it('publishes its height as --beta-banner-h', async () => {
    await mount();
    expect(container.querySelector('[data-testid="beta-banner"]')).not.toBeNull();
    expect(bannerVar()).toBe('37px');
  });

  // Non-régression (2026-11-11) : sur téléphone la phrase complète tenait sur
  // trois lignes ; avec les barres d'outils il restait deux tiers d'écran à la carte.
  it('phone width: one short line (Bêta · Signaler un bug) instead of the full sentence', async () => {
    window.matchMedia = vi.fn().mockReturnValue({
      matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }) as unknown as typeof window.matchMedia;
    await mount();
    const banner = container.querySelector('[data-testid="beta-banner"]')!;
    expect(banner.textContent).toContain('Bêta');
    expect(banner.textContent).not.toContain('maximum de détails');
    const report = container.querySelector('[data-testid="beta-banner-report"]') as HTMLAnchorElement;
    expect(report.getAttribute('href')).toBe('mailto:paul@epauler.fr');
    expect(report.textContent).toBe('Signaler un bug');
    // @ts-expect-error — restore jsdom's absence of matchMedia for the other tests
    delete window.matchMedia;
  });

  it('desktop width: the full sentence stays', async () => {
    await mount();
    expect(container.querySelector('[data-testid="beta-banner"]')!.textContent).toContain('maximum de détails');
    expect(container.querySelector('[data-testid="beta-banner-report"]')).toBeNull();
  });

  it('dismiss hides it, zeroes the offset and is remembered across mounts', async () => {
    await mount();
    await act(async () => {
      (container.querySelector('[data-testid="beta-banner-dismiss"]') as HTMLButtonElement).click();
    });
    expect(container.querySelector('[data-testid="beta-banner"]')).toBeNull();
    expect(bannerVar()).toBe('0px');
    expect(localStorage.getItem(BETA_BANNER_DISMISSED_KEY)).toBe('1');

    act(() => root?.unmount());
    root = null;
    await mount();
    expect(container.querySelector('[data-testid="beta-banner"]')).toBeNull();
    expect(bannerVar()).toBe('0px');
  });

  it('still renders when localStorage throws (private mode)', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied'); });
    await mount();
    expect(container.querySelector('[data-testid="beta-banner"]')).not.toBeNull();
    await act(async () => {
      (container.querySelector('[data-testid="beta-banner-dismiss"]') as HTMLButtonElement).click();
    });
    expect(container.querySelector('[data-testid="beta-banner"]')).toBeNull();
  });
});

describe('bottom-anchored /map panels sit above the banner', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;
  beforeEach(() => { container = document.createElement('div'); document.body.appendChild(container); });
  afterEach(() => { act(() => root?.unmount()); root = null; container.remove(); });

  it('MapLegend bottom offset uses var(--beta-banner-h)', async () => {
    await act(async () => {
      root = createRoot(container);
      root.render(
        <I18nProvider>
          <MapLegend
            layers={{ heatmap: true, dfci: false, myTraces: false, myCells: false, hillshade: false }}
            waymarked={{ hiking: false, cycling: false, mtb: false }}
          />
        </I18nProvider>,
      );
    });
    const legend = container.querySelector('[data-testid="legend-community"]')!.parentElement as HTMLElement;
    expect(legend.getAttribute('style')).toContain('var(--beta-banner-h');
  });

  it('SelectedActivityCard bottom offset uses var(--beta-banner-h)', async () => {
    const activity = {
      id: 'a1', name: 'Sortie', sport: 'road', distance_m: 1000, elevation_gain_m: 10,
      center: [3, 43], bbox: [3, 43, 3.1, 43.1], activity_date: null, created_at: null,
      coords: [[3, 43], [3.1, 43.1]], provider: 'file', provider_activity_id: null,
    } as unknown as ActivityItem;
    await act(async () => {
      root = createRoot(container);
      root.render(
        <I18nProvider>
          <SelectedActivityCard activity={activity} dateLocale="fr-FR" onClose={() => {}} />
        </I18nProvider>,
      );
    });
    const card = container.firstElementChild as HTMLElement;
    expect(card.getAttribute('style')).toContain('var(--beta-banner-h');
  });
});
