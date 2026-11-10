/**
 * "Ma heatmap" from the per-user private PMTiles file
 * (docs/ma-heatmap-fichier-prive.md) — client side:
 *   - the tiles are range-fetched WITH credentials (auth cookie) — the stock
 *     pmtiles FetchSource omits them;
 *   - anything but a `ready` manifest, or a failing file, yields null → the
 *     caller falls back to GET /me/activities;
 *   - the source swap keeps the three layer ids, their stack position,
 *     visibility and (dimmed) opacity, and adds the vector `source-layer`.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import {
  CredentialedRangeSource, loadMyHeatmapFile, setUserActivitiesSource, summaryToActivityItems,
  type MyHeatmapSummary,
} from '@/lib/my-heatmap-file';

vi.mock('@/lib/pmtiles-protocol', () => ({
  addPMTilesSource: vi.fn(async (src: { getKey: () => string }) => `pmtiles://${src.getKey()}`),
}));

const g = globalThis as Record<string, unknown>;
afterEach(() => { delete g.fetch; vi.restoreAllMocks(); });

function okRange(bytes = 128) {
  return new Response(new Uint8Array(bytes), {
    status: 206,
    headers: { 'Content-Range': `bytes 0-${bytes - 1}/999`, ETag: '"r1-x"' },
  });
}

const SUMMARY: MyHeatmapSummary = {
  total: 1,
  activities: [{
    id: 'a1', name: '', sport: 'gravel', distance_m: 1234, elevation_gain_m: 50, activity_date: '2025-05-04T08:00:00+00:00',
    created_at: null, undated: false, provider: 'strava', provider_activity_id: '42', total_photo_count: 2,
    bbox: [3, 43, 3.1, 43.1], center: [3.05, 43.05], start: [3, 43],
  }],
};

describe('CredentialedRangeSource', () => {
  it('sends the Range and the auth cookie', async () => {
    const fetchMock = vi.fn(async () => okRange(10));
    g.fetch = fetchMock;
    const src = new CredentialedRangeSource('http://api/me/heatmap/r1-x.pmtiles');
    const res = await src.getBytes(100, 10);
    expect(res.data.byteLength).toBe(10);
    expect(res.etag).toBe('"r1-x"');
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.credentials).toBe('include');
    expect((init.headers as Record<string, string>).Range).toBe('bytes=100-109');
  });

  it('throws on 404 (version gone) so the map does not silently draw nothing', async () => {
    g.fetch = vi.fn(async () => new Response('nope', { status: 404 }));
    await expect(new CredentialedRangeSource('u').getBytes(0, 10)).rejects.toThrow(/404/);
  });
});

describe('loadMyHeatmapFile', () => {
  it.each(['disabled', 'missing', 'stale'] as const)('%s → null (JSON fallback)', async (status) => {
    g.fetch = vi.fn();
    expect(await loadMyHeatmapFile('', { status })).toBeNull();
    expect(g.fetch).not.toHaveBeenCalled();
  });

  it('ready → summary + pmtiles source URL', async () => {
    g.fetch = vi.fn(async (url: string) => (
      url.endsWith('.summary.json') ? new Response(JSON.stringify(SUMMARY)) : okRange()
    ));
    const out = await loadMyHeatmapFile('http://api', {
      status: 'ready', version: 'r1-x', tiles_url: '/me/heatmap/r1-x.pmtiles', summary_url: '/me/heatmap/r1-x.summary.json',
    });
    expect(out?.summary.total).toBe(1);
    expect(out?.tilesSourceUrl).toBe('pmtiles://http://api/me/heatmap/r1-x.pmtiles');
  });

  it('unreachable tiles → null (JSON fallback), not a blank layer', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    g.fetch = vi.fn(async (url: string) => (
      url.endsWith('.summary.json') ? new Response(JSON.stringify(SUMMARY)) : new Response('', { status: 404 })
    ));
    expect(await loadMyHeatmapFile('http://api', {
      status: 'ready', tiles_url: '/me/heatmap/r1-x.pmtiles', summary_url: '/me/heatmap/r1-x.summary.json',
    })).toBeNull();
  });
});

describe('summaryToActivityItems', () => {
  it('maps the list without coordinates (fetched on selection)', () => {
    const [a] = summaryToActivityItems(SUMMARY, 'Sortie');
    expect(a).toMatchObject({
      id: 'a1', name: 'Sortie', sport: 'gravel', bbox: [3, 43, 3.1, 43.1], center: [3.05, 43.05],
      provider: 'strava', provider_activity_id: '42', total_photo_count: 2, undated: false, coords: [],
    });
  });
});

describe('setUserActivitiesSource', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function fakeMap() {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const layers: any[] = [
      { id: 'community' },
      { id: 'user-activities-glow', layout: { visibility: 'none' }, paint: { 'line-opacity': 0.1 } },
      { id: 'user-activities-line', layout: { visibility: 'none' }, paint: { 'line-opacity': ['dimmed'] } },
      { id: 'user-activities-hit', layout: { visibility: 'none' }, paint: {} },
      { id: 'user-cells-fill' },
    ];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let source: any = { type: 'geojson' };
    const m = {
      layers,
      get source() { return source; },
      getSource: () => source,
      removeSource: () => { source = undefined; },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      addSource: (_id: string, spec: any) => { source = spec; },
      getStyle: () => ({ layers }),
      getLayer: (id: string) => layers.find((l) => l.id === id),
      getLayoutProperty: (id: string, k: string) => layers.find((l) => l.id === id)?.layout?.[k],
      getPaintProperty: (id: string, k: string) => layers.find((l) => l.id === id)?.paint?.[k],
      removeLayer: (id: string) => { layers.splice(layers.findIndex((l) => l.id === id), 1); },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      addLayer: (spec: any, before?: string) => {
        const i = before ? layers.findIndex((l) => l.id === before) : layers.length;
        layers.splice(i, 0, spec);
      },
    };
    return m;
  }

  it('swaps to vector in place, keeping ids, order, visibility and dimming', () => {
    const m = fakeMap();
    setUserActivitiesSource(m, '#0ff', 'pmtiles://k');
    expect(m.source).toEqual({ type: 'vector', url: 'pmtiles://k' });
    expect(m.layers.map((l) => l.id)).toEqual([
      'community', 'user-activities-glow', 'user-activities-line', 'user-activities-hit', 'user-cells-fill',
    ]);
    const line = m.layers[2];
    expect(line['source-layer']).toBe('activities');
    expect(line.layout.visibility).toBe('none');
    expect(line.paint['line-opacity']).toEqual(['dimmed']);
    expect(line.paint['line-color']).toBe('#0ff');
  });

  it('swaps back to an empty geojson source for the JSON fallback', () => {
    const m = fakeMap();
    setUserActivitiesSource(m, '#0ff', 'pmtiles://k');
    setUserActivitiesSource(m, '#0ff', null);
    expect(m.source.type).toBe('geojson');
    expect(m.layers[2]['source-layer']).toBeUndefined();
  });
});
