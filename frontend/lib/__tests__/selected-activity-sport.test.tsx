// @vitest-environment jsdom
/**
 * Sport correction on the /map selected-activity card, driven through the REAL
 * SelectedActivityCard + I18nProvider + lib/activity-sport; only `fetch` (the
 * network boundary) and next/link are faked.
 */
import React from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) =>
    React.createElement('a', { href, ...rest }, children),
}));

import SelectedActivityCard from '@/components/map/SelectedActivityCard';
import { I18nProvider } from '@/lib/i18n';
import { withActivitySport, EDITABLE_SPORTS } from '@/lib/activity-sport';
import type { ActivityItem } from '@/lib/map-utils';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const activity: ActivityItem = {
  id: 'a1', name: 'Sortie', sport: 'road', distance_m: 12000, elevation_gain_m: null,
  center: [3, 43], bbox: [3, 43, 3.1, 43.1], activity_date: null, created_at: null,
  coords: [[3, 43], [3.1, 43.1]], provider: 'file', provider_activity_id: null,
};

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

describe('SelectedActivityCard — sport correction', () => {
  let container: HTMLDivElement;
  let root: Root | null = null;
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    container = document.createElement('div');
    document.body.appendChild(container);
  });
  afterEach(() => {
    act(() => root?.unmount());
    root = null;
    container.remove();
    vi.unstubAllGlobals();
  });

  const render = (onSportChange = vi.fn(), act_ = activity) => {
    root = createRoot(container);
    act(() => root!.render(
      <I18nProvider>
        <SelectedActivityCard activity={act_} dateLocale="fr-FR" onClose={() => {}} onSportChange={onSportChange} />
      </I18nProvider>,
    ));
    return onSportChange;
  };
  const select = () => container.querySelector('[data-testid="activity-sport-select"]') as HTMLSelectElement;
  const pick = async (value: string) => {
    await act(async () => {
      select().value = value;
      select().dispatchEvent(new Event('change', { bubbles: true }));
    });
  };

  it('offers only the concrete sports and shows the current one', () => {
    render();
    const opts = Array.from(select().options).map((o) => o.value);
    expect(opts).toEqual([...EDITABLE_SPORTS]);
    expect(select().value).toBe('road');
    expect(select().getAttribute('aria-label')).toBeTruthy();
  });

  it('updates optimistically, PATCHes the owner endpoint, keeps the new sport on success', async () => {
    const d = deferred<Response>();
    fetchMock.mockReturnValue(d.promise);
    const onSportChange = render();

    await pick('mtb');
    // Optimistic: UI + parent updated BEFORE the request resolves.
    expect(select().value).toBe('mtb');
    expect(onSportChange).toHaveBeenCalledWith('a1', 'mtb');
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/me\/activities\/a1$/);
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(init.body)).toEqual({ sport: 'mtb' });

    await act(async () => {
      d.resolve(new Response(JSON.stringify({ id: 'a1', sport: 'mtb', changed: true }), { status: 200 }));
    });
    expect(select().value).toBe('mtb');
    expect(onSportChange).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="activity-sport-error"]')).toBeNull();
  });

  it('rolls back the UI and the parent on error, and shows a message', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: 'nope' }), { status: 500 }));
    const onSportChange = render();

    await pick('gravel');
    expect(onSportChange.mock.calls).toEqual([['a1', 'gravel'], ['a1', 'road']]);
    expect(select().value).toBe('road');
    expect(container.querySelector('[data-testid="activity-sport-error"]')).not.toBeNull();
  });

  it('a legacy "offroad" activity shows it as current but cannot re-pick it', () => {
    render(vi.fn(), { ...activity, sport: 'offroad' });
    expect(select().value).toBe('offroad');
    const off = Array.from(select().options).find((o) => o.value === 'offroad')!;
    expect(off.disabled).toBe(true);
  });
});

describe('withActivitySport', () => {
  it('patches only the matching feature, returns a new object', () => {
    const fc = { type: 'FeatureCollection', features: [
      { properties: { id: 'a1', sport: 'road' } }, { properties: { id: 'a2', sport: 'road' } },
    ] };
    const out = withActivitySport(fc, 'a1', 'mtb');
    expect(out).not.toBe(fc);
    expect(out.features.map((f) => f.properties.sport)).toEqual(['mtb', 'road']);
    expect(fc.features[0].properties.sport).toBe('road');
  });
  it('unknown id or null input is returned unchanged', () => {
    const fc = { features: [{ properties: { id: 'a1', sport: 'road' } }] };
    expect(withActivitySport(fc, 'zz', 'mtb')).toBe(fc);
    expect(withActivitySport(null, 'a1', 'mtb')).toBeNull();
  });
});
