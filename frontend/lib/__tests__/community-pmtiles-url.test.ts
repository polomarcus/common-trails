import { describe, it, expect, afterEach } from 'vitest';
import { communityPmtilesUrl } from '@/lib/cdn-cache';

/**
 * Non-regression for the /map community-heatmap "renders nothing" bug:
 * init-map-layers.ts used to hardcode `pmtiles://${origin}/heatmap-display.pmtiles`.
 * In prod that same-origin path returns the SPA index.html (200), so the pmtiles
 * source loaded HTML and the whole community heatmap was invisible. The source URL
 * MUST prefer the build-baked NEXT_PUBLIC_HEATMAP_URL (the public GCS bucket).
 */
describe('communityPmtilesUrl', () => {
  const orig = process.env.NEXT_PUBLIC_HEATMAP_URL;
  afterEach(() => {
    if (orig === undefined) delete process.env.NEXT_PUBLIC_HEATMAP_URL;
    else process.env.NEXT_PUBLIC_HEATMAP_URL = orig;
  });

  it('uses the baked GCS URL when NEXT_PUBLIC_HEATMAP_URL is set (prod)', () => {
    const gcs = 'https://storage.googleapis.com/common-trails-heatmap-prod/heatmap-display.pmtiles';
    process.env.NEXT_PUBLIC_HEATMAP_URL = gcs;
    // Must NOT fall back to the same-origin SPA path — that was the bug.
    expect(communityPmtilesUrl('https://chemins-communs.fr')).toBe(`pmtiles://${gcs}`);
  });

  it('falls back to same-origin when the env var is empty (dev)', () => {
    delete process.env.NEXT_PUBLIC_HEATMAP_URL;
    expect(communityPmtilesUrl('http://localhost:3787'))
      .toBe('pmtiles://http://localhost:3787/heatmap-display.pmtiles');
  });
});
