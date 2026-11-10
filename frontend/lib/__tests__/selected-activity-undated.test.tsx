// @vitest-environment jsdom
/**
 * An UNDATED activity (in practice a planned itinerary) is kept in "Ma heatmap"
 * but never published on the public heatmap (backend
 * provenance.community_eligible_conditions). The /map card says so, and does
 * not pass the import date off as the ride date. Drives the REAL
 * SelectedActivityCard + I18nProvider; only next/link is faked.
 */
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) =>
    React.createElement('a', { href, ...rest }, children),
}));

import SelectedActivityCard from '@/components/map/SelectedActivityCard';
import { I18nProvider } from '@/lib/i18n';
import type { ActivityItem } from '@/lib/map-utils';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const base: ActivityItem = {
  id: 'a1', name: 'Sortie', sport: 'road', distance_m: 12000, elevation_gain_m: null,
  // As /me/activities sends an undated row: activity_date falls back to the
  // import date, ``undated`` carries the truth.
  center: [3, 43], bbox: [3, 43, 3.1, 43.1],
  activity_date: '2026-11-01T10:00:00Z', created_at: '2026-11-01T10:00:00Z', undated: true,
  coords: [[3, 43], [3.1, 43.1]], provider: 'file', provider_activity_id: null,
};

describe('SelectedActivityCard — undated activity', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;

  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
  });

  const render = (activity: ActivityItem) => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root!.render(
      <I18nProvider>
        <SelectedActivityCard activity={activity} dateLocale="fr-FR" onClose={() => {}} />
      </I18nProvider>,
    ));
  };
  const note = () => container.querySelector('[data-testid="activity-undated-note"]');

  it('says an undated trace is not on the public heatmap, without faking a date', () => {
    render(base);
    expect(note()?.textContent).toBe('Sans date — non publiée sur la heatmap publique');
    // The import date (created_at) must not be shown as the ride date.
    expect(container.textContent).not.toContain('2026');
  });

  it('shows the date and no note for a dated trace', () => {
    render({ ...base, activity_date: '2024-03-02T07:30:00Z', undated: false });
    expect(note()).toBeNull();
    expect(container.textContent).toContain('2024');
  });

  it('renders before its coordinates are fetched (per-user PMTiles path: list has none)', () => {
    render({ ...base, coords: [], center: [3.5, 43.5] });
    const link = container.querySelector('a[href^="/map?"]');
    expect(link?.getAttribute('href')).toBe('/map?lat=43.50000&lon=3.50000&zoom=12');
  });
});
