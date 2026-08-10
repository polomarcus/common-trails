import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { resolveCommunityPmtilesUrl } from '@/lib/cdn-cache';

/**
 * Freshness-pointer resolution for the community heatmap. The build publishes a
 * STABLE `heatmap-display.pmtiles` (24 h cache) + an IMMUTABLE per-build snapshot
 * pointed to by `heatmap-display.json`'s `latest_url`. Returning visitors must
 * hop to the immutable snapshot (escape the 24 h stale window), and fall back to
 * the stable name on ANY pointer failure.
 */
const GCS = 'https://storage.googleapis.com/common-trails-heatmap-prod/heatmap-display.pmtiles';
const IMMUTABLE = 'https://storage.googleapis.com/common-trails-heatmap-prod/heatmap-display-v20606-ab12cd34.pmtiles';

describe('resolveCommunityPmtilesUrl (pointer resolution)', () => {
  const orig = process.env.NEXT_PUBLIC_HEATMAP_URL;
  beforeEach(() => { process.env.NEXT_PUBLIC_HEATMAP_URL = GCS; });
  afterEach(() => {
    vi.restoreAllMocks();
    if (orig === undefined) delete process.env.NEXT_PUBLIC_HEATMAP_URL;
    else process.env.NEXT_PUBLIC_HEATMAP_URL = orig;
  });

  it('returns the immutable snapshot URL from the pointer', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({ latest: 'v20606-ab12cd34', latest_url: IMMUTABLE, mutable_url: GCS }),
    })));
    expect(await resolveCommunityPmtilesUrl('https://chemins-communs.fr'))
      .toBe(`pmtiles://${IMMUTABLE}`);
  });

  it('fetches the pointer JSON sitting next to the pmtiles binary', async () => {
    const fetchMock = vi.fn(async (_url: string) => ({
      ok: true, json: async () => ({ latest_url: IMMUTABLE }),
    }));
    vi.stubGlobal('fetch', fetchMock);
    await resolveCommunityPmtilesUrl('https://chemins-communs.fr');
    const calledUrl = String(fetchMock.mock.calls[0][0]);
    expect(calledUrl).toContain(
      'https://storage.googleapis.com/common-trails-heatmap-prod/heatmap-display.json',
    );
  });

  it('falls back to the STABLE url on a non-200 pointer', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })));
    expect(await resolveCommunityPmtilesUrl('https://chemins-communs.fr'))
      .toBe(`pmtiles://${GCS}`);
  });

  it('falls back to the STABLE url on a fetch error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('network'); }));
    expect(await resolveCommunityPmtilesUrl('https://chemins-communs.fr'))
      .toBe(`pmtiles://${GCS}`);
  });

  it('falls back to the STABLE url when latest_url is missing', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ latest: 'x' }) })));
    expect(await resolveCommunityPmtilesUrl('https://chemins-communs.fr'))
      .toBe(`pmtiles://${GCS}`);
  });
});
