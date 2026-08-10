/**
 * Unit tests for the homepage community-stats pure helpers.
 *
 * parseCommunityStats + formatStatValue back the hero banner (contributeurs /
 * traces / km de chemins). They drive the REAL exported functions — no inline
 * mirror. The impure fetch (GCS → dev-static → API fallback) lives in
 * cdn-cache.ts and is exercised end-to-end by the app; here we pin the two
 * behaviours a unit test can own: payload normalisation (both key shapes) and
 * locale-aware / graceful formatting.
 */
import { describe, it, expect } from 'vitest';
import {
  parseCommunityStats,
  formatStatValue,
  buildStatsCandidates,
  PROD_HEATMAP_BUCKET,
} from '../community-stats';

describe('parseCommunityStats', () => {
  it('parses the static stats.json shape', () => {
    expect(parseCommunityStats({ contributors: 12, traces: 2676, km: 12400 }))
      .toEqual({ contributors: 12, traces: 2676, km: 12400 });
  });

  it('parses the legacy /heatmap/summary API shape', () => {
    expect(parseCommunityStats({
      total_contributors: 3, total_activities: 1404, total_km: 987, extra: 'ignored',
    })).toEqual({ contributors: 3, traces: 1404, km: 987 });
  });

  it('accepts zero as a real value', () => {
    expect(parseCommunityStats({ contributors: 0, traces: 0, km: 0 }))
      .toEqual({ contributors: 0, traces: 0, km: 0 });
  });

  it('fills missing fields with 0 when at least one is present', () => {
    expect(parseCommunityStats({ traces: 5 }))
      .toEqual({ contributors: 0, traces: 5, km: 0 });
  });

  it('returns null when no recognised numeric field is present', () => {
    expect(parseCommunityStats({ foo: 'bar' })).toBeNull();
    expect(parseCommunityStats({ contributors: 'nope' })).toBeNull();
    expect(parseCommunityStats(null)).toBeNull();
    expect(parseCommunityStats(undefined)).toBeNull();
    expect(parseCommunityStats('string')).toBeNull();
  });

  it('ignores non-finite numbers', () => {
    expect(parseCommunityStats({ contributors: NaN, traces: Infinity })).toBeNull();
  });
});

describe('formatStatValue', () => {
  it('returns the em-dash placeholder for loading / error / non-finite', () => {
    expect(formatStatValue(null, 'fr')).toBe('—');
    expect(formatStatValue(undefined, 'fr')).toBe('—');
    expect(formatStatValue(NaN, 'fr')).toBe('—');
    expect(formatStatValue(Infinity, 'en')).toBe('—');
  });

  it('formats 0 as "0", not the placeholder', () => {
    expect(formatStatValue(0, 'fr')).toBe('0');
  });

  it('groups thousands per locale', () => {
    // en uses a comma; fr uses a (narrow) space — assert structurally so the
    // exact Intl separator codepoint is not hard-coded.
    expect(formatStatValue(12400, 'en')).toBe('12,400');
    const fr = formatStatValue(12400, 'fr');
    expect(fr).not.toContain(',');
    expect(fr.replace(/\s| | /g, '')).toBe('12400');
  });

  it('defaults non-en locales to the fr grouping', () => {
    const de = formatStatValue(12400, 'de');
    expect(de.replace(/\s| | /g, '')).toBe('12400');
  });
});

describe('PROD_HEATMAP_BUCKET', () => {
  // The public heatmap base moved off storage.googleapis.com to the first-party
  // CDN domain (fixes ad-blocker/Firefox blocking + a clean share URL). Pin it so
  // a revert to the raw GCS host fails loudly. The stats fallback candidate + the
  // buildStatsCandidates prod-host pin below both derive from this.
  it('is the first-party tiles.* CDN domain, not the raw GCS host', () => {
    expect(PROD_HEATMAP_BUCKET).toBe('https://tiles.chemins-communs.fr');
    expect(PROD_HEATMAP_BUCKET).not.toContain('storage.googleapis.com');
  });
});

describe('buildStatsCandidates', () => {
  const PROD = `${PROD_HEATMAP_BUCKET}/stats.json`;

  it('derives the sibling stats.json from NEXT_PUBLIC_HEATMAP_URL first', () => {
    const c = buildStatsCandidates({
      heatmapUrl: 'https://storage.googleapis.com/common-trails-heatmap-prod/heatmap-display.pmtiles',
      origin: 'https://chemins-communs.fr',
      hostname: 'chemins-communs.fr',
    });
    expect(c[0]).toBe('https://storage.googleapis.com/common-trails-heatmap-prod/stats.json');
  });

  it('adds the same-origin stats.json when no CDN base', () => {
    const c = buildStatsCandidates({ origin: 'http://localhost:3787', hostname: 'localhost' });
    expect(c).toEqual(['http://localhost:3787/stats.json']);
  });

  it('prefers the CDN base over the origin', () => {
    const c = buildStatsCandidates({ cdnBase: 'https://cdn.example', origin: 'https://ignored', hostname: 'x' });
    expect(c[0]).toBe('https://cdn.example/stats.json');
  });

  // NON-REGRESSION: the flagship-stats bug. On the prod host with NO
  // NEXT_PUBLIC_HEATMAP_URL baked in, the same-origin /stats.json answers with
  // SPA HTML -> the authoritative public bucket MUST be an explicit candidate,
  // else the hero falls through to the SUM-inflated /heatmap/summary.
  it('appends the authoritative prod bucket on the prod host when the env var is missing', () => {
    const c = buildStatsCandidates({ origin: 'https://chemins-communs.fr', hostname: 'chemins-communs.fr' });
    expect(c).toEqual(['https://chemins-communs.fr/stats.json', PROD]);
  });

  it('also fires the prod fallback on a subdomain of the prod host', () => {
    const c = buildStatsCandidates({ origin: 'https://www.chemins-communs.fr', hostname: 'www.chemins-communs.fr' });
    expect(c).toContain(PROD);
  });

  it('NEVER adds the prod bucket on dev/local hosts', () => {
    expect(buildStatsCandidates({ origin: 'http://localhost:3787', hostname: 'localhost' })).not.toContain(PROD);
    expect(buildStatsCandidates({ origin: 'https://staging.example.com', hostname: 'staging.example.com' })).not.toContain(PROD);
  });

  it('de-duplicates while preserving order', () => {
    const c = buildStatsCandidates({
      heatmapUrl: `${PROD_HEATMAP_BUCKET}/heatmap-display.pmtiles`,
      origin: 'https://chemins-communs.fr',
      hostname: 'chemins-communs.fr',
    });
    expect(c.filter((u) => u === PROD)).toHaveLength(1);
  });

  it('returns an empty list under SSR (no origin, no hostname)', () => {
    expect(buildStatsCandidates({})).toEqual([]);
  });
});
