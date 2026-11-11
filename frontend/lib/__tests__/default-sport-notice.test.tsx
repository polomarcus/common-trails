// @vitest-environment jsdom
/**
 * "Sorties classées Route par défaut" on /map, driven through the REAL
 * DefaultSportNotice + I18nProvider + lib/default-sport; only auth and `fetch`
 * (the network boundary) are faked — the fake keeps a tiny in-memory backend
 * so the count the notice shows is the one the "server" answers.
 */
import React from 'react';
import { renderToString } from 'react-dom/server';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const auth = vi.hoisted(() => ({ token: null as string | null }));
vi.mock('@/lib/auth', () => ({ getToken: () => auth.token }));

import DefaultSportNotice from '@/components/map/DefaultSportNotice';
import { I18nProvider } from '@/lib/i18n';
import { applyDefaultSportChange, type DefaultSportChange } from '@/lib/default-sport';

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

interface Row { id: string; name: string; sport: string; distance_m: number; activity_date: string; flagged: boolean }

/** In-memory stand-in for the two endpoints. `extra` = flagged rides beyond the listed ones. */
function fakeBackend(rows: Row[], extra = 0) {
  const state = { rows, extra, patches: [] as Record<string, unknown>[], failPatch: false };
  const count = () => state.rows.filter((r) => r.flagged).length + state.extra;
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes('/me/activities/default-sport')) {
      return json({
        count: count(),
        activities: state.rows.filter((r) => r.flagged).map(({ flagged: _f, ...a }) => a),
      });
    }
    if (String(url).endsWith('/me/activities/sport') && init?.method === 'PATCH') {
      if (state.failPatch) return json({ detail: 'boom' }, 500);
      const body = JSON.parse(String(init.body));
      state.patches.push(body);
      const targets: Row[] = body.from_default
        ? state.rows.filter((r) => r.flagged)
        : (body.activity_ids as string[]).flatMap((id) => state.rows.filter((r) => r.id === id));
      const updated = targets.filter((r) => r.sport !== body.sport).map((r) => r.id);
      targets.forEach((r) => { r.sport = body.sport; r.flagged = false; });
      if (body.from_default) state.extra = 0;
      return json({ sport: body.sport, matched: targets.length, updated: updated.length, updated_ids: updated, default_sport_count: count() });
    }
    return json({ detail: 'not found' }, 404);
  });
  return { state, fetchMock };
}

const rides = (): Row[] => [
  { id: 'r3', name: 'Tour du lac', sport: 'road', distance_m: 42000, activity_date: '2025-06-03T08:00:00+00:00', flagged: true },
  { id: 'r2', name: 'Piste des crêtes', sport: 'road', distance_m: 31000, activity_date: '2025-06-02T08:00:00+00:00', flagged: true },
  { id: 'r1', name: 'Singles du bois', sport: 'road', distance_m: 18000, activity_date: '2025-06-01T08:00:00+00:00', flagged: true },
];

describe('DefaultSportNotice', () => {
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
    vi.unstubAllGlobals();
  });

  const q = (id: string) => container.querySelector(`[data-testid="${id}"]`) as HTMLElement | null;
  const el = (onChanged?: (c: DefaultSportChange) => void, refreshKey = 0) => (
    <I18nProvider><DefaultSportNotice dateLocale="fr-FR" onChanged={onChanged} refreshKey={refreshKey} /></I18nProvider>
  );
  const mount = async (onChanged?: (c: DefaultSportChange) => void) => {
    root = createRoot(container);
    await act(async () => { root!.render(el(onChanged)); });
  };
  const click = async (id: string) => { await act(async () => { q(id)!.click(); }); };

  it('static render and logged-out visitors: nothing, and no request', async () => {
    const { fetchMock } = fakeBackend(rides());
    vi.stubGlobal('fetch', fetchMock);
    expect(renderToString(el())).toBe('');
    auth.token = null;
    await mount();
    expect(q('default-sport-notice')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows the notice with the count the API returns, and is dismissible', async () => {
    const { fetchMock } = fakeBackend(rides(), 209);
    vi.stubGlobal('fetch', fetchMock);
    await mount();
    expect(q('default-sport-notice')!.textContent).toContain(
      '212 sorties vélo sont classées « Route » par défaut : Strava ne précise pas le type.',
    );
    expect(q('default-sport-open')!.textContent).toBe('Les reclasser');
    await click('default-sport-dismiss');
    expect(q('default-sport-notice')).toBeNull();
  });

  it('no default-sport activity (or an older backend answering an error): no notice', async () => {
    vi.stubGlobal('fetch', fakeBackend([]).fetchMock);
    await mount();
    expect(q('default-sport-notice')).toBeNull();
    act(() => root?.unmount());
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"x"}', { status: 500 })));
    await mount();
    expect(q('default-sport-notice')).toBeNull();
  });

  it('the panel lists the rides newest first with "et N autres"', async () => {
    vi.stubGlobal('fetch', fakeBackend(rides(), 7).fetchMock);
    await mount();
    await click('default-sport-open');
    const items = Array.from(q('default-sport-list')!.querySelectorAll('li')).map((li) => li.textContent);
    expect(items[0]).toContain('Tour du lac');
    expect(items[0]).toContain('42');
    expect(items[2]).toContain('Singles du bois');
    expect(q('default-sport-others')!.textContent).toBe('et 7 autres');
    expect((q('default-sport-apply') as HTMLButtonElement).disabled).toBe(true); // nothing ticked
  });

  it('re-classifies the ticked rides, then « Ce sont bien des sorties route » confirms the rest: the notice disappears', async () => {
    const { state, fetchMock } = fakeBackend(rides());
    vi.stubGlobal('fetch', fetchMock);
    const onChanged = vi.fn();
    await mount(onChanged);
    await click('default-sport-open');

    await click('default-sport-item-r1');
    await click('default-sport-item-r2');
    await act(async () => {
      const sel = q('default-sport-sport') as HTMLSelectElement;
      sel.value = 'mtb';
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(q('default-sport-apply')!.textContent).toBe('Appliquer (2)');
    await click('default-sport-apply');

    expect(state.patches).toEqual([{ activity_ids: ['r1', 'r2'], sport: 'mtb' }]);
    expect(onChanged).toHaveBeenLastCalledWith({ sport: 'mtb', updatedIds: ['r1', 'r2'], cleared: ['r1', 'r2'] });
    // One ride left, the panel stays open on it.
    expect(q('default-sport-list')!.querySelectorAll('li').length).toBe(1);
    expect(q('default-sport-list')!.textContent).toContain('Tour du lac');

    expect(q('default-sport-confirm-road')!.textContent).toBe('Ce sont bien des sorties route');
    await click('default-sport-confirm-road');
    expect(state.patches[1]).toEqual({ from_default: true, sport: 'road' });
    expect(onChanged).toHaveBeenLastCalledWith({ sport: 'road', updatedIds: [], cleared: 'all' });
    expect(q('default-sport-panel')).toBeNull();
    expect(q('default-sport-notice')).toBeNull();
  });

  it('« Tout sélectionner » targets every default-sport ride server-side, not only the listed ones', async () => {
    const { state, fetchMock } = fakeBackend(rides(), 400);
    vi.stubGlobal('fetch', fetchMock);
    await mount();
    await click('default-sport-open');
    await click('default-sport-select-all');
    expect(q('default-sport-apply')!.textContent).toBe('Appliquer (403)');
    await click('default-sport-apply');
    expect(state.patches).toEqual([{ from_default: true, sport: 'gravel' }]);
    expect(q('default-sport-notice')).toBeNull();
  });

  it('a failed request keeps the panel and says so', async () => {
    const { state, fetchMock } = fakeBackend(rides());
    state.failPatch = true;
    vi.stubGlobal('fetch', fetchMock);
    await mount();
    await click('default-sport-open');
    await click('default-sport-item-r1');
    await click('default-sport-apply');
    expect(q('default-sport-error')).not.toBeNull();
    expect(q('default-sport-list')!.querySelectorAll('li').length).toBe(3);
  });

  it('after a failed request the count and the list are re-read from the server', async () => {
    const { state, fetchMock } = fakeBackend(rides());
    state.failPatch = true;
    vi.stubGlobal('fetch', fetchMock);
    await mount();
    await click('default-sport-open');
    await click('default-sport-item-r1');
    // Meanwhile the server-side set changed (e.g. the request was partly applied).
    state.rows[0].flagged = false;
    await click('default-sport-apply');
    expect(q('default-sport-error')).not.toBeNull();
    expect(q('default-sport-list')!.querySelectorAll('li').length).toBe(2);
    await click('default-sport-close');
    expect(q('default-sport-notice')!.textContent).toContain('2 sorties');
  });

  // Non-régression : si la relecture échoue aussi (réseau coupé, 5xx), elle ne
  // doit pas se lire comme « plus rien à classer » — le panneau, l'encart et
  // le message d'erreur disparaissaient, comme si tout avait été classé.
  it('a failed request followed by a failed re-read keeps the panel, the list and the error', async () => {
    const { state, fetchMock } = fakeBackend(rides());
    vi.stubGlobal('fetch', fetchMock);
    await mount();
    await click('default-sport-open');
    await click('default-sport-item-r1');
    state.failPatch = true;
    const real = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url).includes('/me/activities/default-sport')) throw new TypeError('network down');
      return real(url, init);
    });
    await click('default-sport-apply');
    expect(q('default-sport-error')).not.toBeNull();
    expect(q('default-sport-list')!.querySelectorAll('li').length).toBe(3);
  });

  it('the panel takes focus when it opens, Escape closes it and focus returns to the opener', async () => {
    vi.stubGlobal('fetch', fakeBackend(rides()).fetchMock);
    await mount();
    q('default-sport-open')!.focus();
    await click('default-sport-open');
    const panel = q('default-sport-panel')!;
    expect(panel.getAttribute('role')).toBe('dialog');
    expect(panel.contains(document.activeElement)).toBe(true);
    await act(async () => {
      document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(q('default-sport-panel')).toBeNull();
    expect(q('default-sport-notice')).not.toBeNull();
    expect(document.activeElement).toBe(q('default-sport-open'));
  });

  it('re-reads the count when refreshKey changes (sport fixed from the activity card)', async () => {
    const { state, fetchMock } = fakeBackend(rides());
    vi.stubGlobal('fetch', fetchMock);
    await mount();
    expect(q('default-sport-notice')!.textContent).toContain('3 sorties');
    state.rows[0].flagged = false;
    state.rows[1].flagged = false;
    await act(async () => { root!.render(el(undefined, 1)); });
    expect(q('default-sport-notice')!.textContent).toContain('1 sortie vélo est classée');
  });
});

describe('applyDefaultSportChange', () => {
  const items = [
    { id: 'a', sport: 'road', sport_is_default: true },
    { id: 'b', sport: 'road', sport_is_default: true },
    { id: 'c', sport: 'road', sport_is_default: false },
  ];
  it('sets the sport of the updated ids and clears their flag', () => {
    const out = applyDefaultSportChange(items, { sport: 'mtb', updatedIds: ['a'], cleared: ['a'] });
    expect(out).toEqual([
      { id: 'a', sport: 'mtb', sport_is_default: false }, items[1], items[2],
    ]);
    expect(out[1]).toBe(items[1]);
  });
  it('"all" clears every flag (confirmation), sport untouched', () => {
    const out = applyDefaultSportChange(items, { sport: 'road', updatedIds: [], cleared: 'all' });
    expect(out.map((a) => [a.sport, a.sport_is_default])).toEqual([['road', false], ['road', false], ['road', false]]);
  });
  it('returns the same array when nothing changes', () => {
    expect(applyDefaultSportChange(items, { sport: 'mtb', updatedIds: [], cleared: [] })).toBe(items);
  });
});
