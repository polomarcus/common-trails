// @vitest-environment jsdom
/**
 * /map empty state + 5000 cap note (MapActivitiesNotice), driven through the
 * REAL component + I18nProvider; only auth and next/link are faked.
 *
 * Hydration rule (#418): /map is a static export built LOGGED OUT, so the card
 * must be absent from the server render and appear only after the mount
 * effect reads auth — hydrating as a signed-in rider must not mismatch.
 */
import React from 'react';
import { renderToString } from 'react-dom/server';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const auth = vi.hoisted(() => ({ token: null as string | null }));
vi.mock('@/lib/auth', () => ({ getToken: () => auth.token }));
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) =>
    React.createElement('a', { href, ...rest }, children),
}));

import MapActivitiesNotice, { MAP_ACTIVITIES_LIMIT } from '@/components/map/MapActivitiesNotice';
import { I18nProvider } from '@/lib/i18n';
import { markArchivePending, ARCHIVE_PENDING_WINDOW_MS } from '@/lib/archive-pending';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const el = (count: number | null) => <I18nProvider><MapActivitiesNotice count={count} /></I18nProvider>;

describe('MapActivitiesNotice', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;

  beforeEach(() => {
    auth.token = null;
    localStorage.clear();
    container = document.createElement('div');
    document.body.appendChild(container);
  });
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
  });

  it('static render never shows the card, even for a signed-in rider (render never reads auth)', () => {
    auth.token = 'cookie-auth';
    expect(renderToString(el(0))).not.toContain('map-empty-state');
  });

  it('hydrating the logged-out HTML as a signed-in rider with 0 activities: no mismatch, then the card + import link', async () => {
    container.innerHTML = renderToString(el(0));
    auth.token = 'cookie-auth';
    const { hydrateRoot } = await import('react-dom/client');
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    await act(async () => { root = hydrateRoot(container, el(0)); });
    const hydrationErrors = errors.mock.calls.filter((c) =>
      /hydrat|did not match|#418/i.test(String(c[0])));
    errors.mockRestore();
    expect(hydrationErrors).toEqual([]);

    const card = container.querySelector('[data-testid="map-empty-state"]');
    expect(card).not.toBeNull();
    expect(card!.textContent).toContain('Importez vos traces pour voir votre heatmap');
    expect(container.querySelector('[data-testid="map-empty-state-cta"]')!.getAttribute('href')).toBe('/strava');
  });

  it('is dismissible', async () => {
    auth.token = 'cookie-auth';
    await act(async () => { root = createRoot(container); root.render(el(0)); });
    await act(async () => {
      (container.querySelector('[data-testid="map-empty-state-dismiss"]') as HTMLButtonElement).click();
    });
    expect(container.querySelector('[data-testid="map-empty-state"]')).toBeNull();
  });

  it('not shown when logged out, while loading, or when the rider has activities', async () => {
    await act(async () => { root = createRoot(container); root.render(el(0)); });
    expect(container.querySelector('[data-testid="map-empty-state"]')).toBeNull(); // logged out
    act(() => root?.unmount());

    auth.token = 'cookie-auth';
    await act(async () => { root = createRoot(container); root.render(el(null)); });
    expect(container.querySelector('[data-testid="map-empty-state"]')).toBeNull(); // not loaded
    await act(async () => { root!.render(el(12)); });
    expect(container.querySelector('[data-testid="map-empty-state"]')).toBeNull();
    expect(container.querySelector('[data-testid="map-activities-capped"]')).toBeNull();
  });

  it('archive accepted < 2 h ago: "processing" variant instead of the import CTA', async () => {
    markArchivePending(Date.now() - 10 * 60 * 1000);
    auth.token = 'cookie-auth';
    await act(async () => { root = createRoot(container); root.render(el(0)); });
    expect(container.querySelector('[data-testid="map-empty-state-processing"]')!.textContent)
      .toContain('Traitement en cours — vos sorties apparaîtront ici, un email vous préviendra');
    expect(container.querySelector('[data-testid="map-empty-state-cta"]')).toBeNull();
    expect(container.textContent).not.toContain('Importez vos traces');
  });

  it('marker older than 2 h: back to the import CTA', async () => {
    markArchivePending(Date.now() - ARCHIVE_PENDING_WINDOW_MS - 1000);
    auth.token = 'cookie-auth';
    await act(async () => { root = createRoot(container); root.render(el(0)); });
    expect(container.querySelector('[data-testid="map-empty-state-processing"]')).toBeNull();
    expect(container.querySelector('[data-testid="map-empty-state-cta"]')).not.toBeNull();
  });

  it('storage throwing (private mode) → plain import CTA, no crash', async () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied'); });
    auth.token = 'cookie-auth';
    await act(async () => { root = createRoot(container); root.render(el(0)); });
    expect(container.querySelector('[data-testid="map-empty-state-cta"]')).not.toBeNull();
    spy.mockRestore();
  });

  it('static render with a fresh marker still renders nothing (marker read post-mount only)', () => {
    markArchivePending();
    auth.token = 'cookie-auth';
    expect(renderToString(el(0))).not.toContain('map-empty-state');
  });

  it(`exactly ${MAP_ACTIVITIES_LIMIT} activities → discreet "most recent shown" note`, async () => {
    expect(MAP_ACTIVITIES_LIMIT).toBe(5000);
    auth.token = 'cookie-auth';
    await act(async () => { root = createRoot(container); root.render(el(MAP_ACTIVITIES_LIMIT)); });
    expect(container.querySelector('[data-testid="map-activities-capped"]')!.textContent)
      .toBe('5000 sorties les plus récentes affichées');
  });
});
