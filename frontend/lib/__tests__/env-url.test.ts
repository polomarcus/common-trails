import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  resolveApiUrl, resolveStablePmtilesUrl, pointerUrlFor, immutableUrlFromPointer,
  DEV_API_URL,
} from '@/lib/env-url';

/**
 * Fail-loud env resolution + freshness-pointer helpers. The 2026-08-07 trap: an
 * UNSET NEXT_PUBLIC_API_URL in a prod build silently baked localhost (CORS-fail),
 * and an unset NEXT_PUBLIC_HEATMAP_URL pointed the tile source at the SPA origin
 * (index.html/200 → blank map). These are the pure, deterministic guards.
 */
describe('resolveApiUrl', () => {
  afterEach(() => vi.restoreAllMocks());

  it('honours an explicit "" (same-origin) in prod — the INTENDED prod value', () => {
    expect(resolveApiUrl('', true)).toBe('');
  });

  it('returns a real URL verbatim in prod', () => {
    expect(resolveApiUrl('https://api.example.com', true)).toBe('https://api.example.com');
  });

  it('UNSET in prod does NOT return localhost — falls back to same-origin + logs', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    const out = resolveApiUrl(undefined, true);
    expect(out).toBe('');
    expect(out).not.toBe(DEV_API_URL);
    expect(err).toHaveBeenCalledOnce();
  });

  it('UNSET in dev keeps the localhost:8787 convenience default', () => {
    expect(resolveApiUrl(undefined, false)).toBe(DEV_API_URL);
  });
});

describe('resolveStablePmtilesUrl', () => {
  afterEach(() => vi.restoreAllMocks());

  it('returns the configured heatmap URL when set', () => {
    const gcs = 'https://storage.googleapis.com/bkt/heatmap-display.pmtiles';
    expect(resolveStablePmtilesUrl(gcs, 'https://chemins-communs.fr', true)).toBe(gcs);
  });

  it('UNSET in prod returns null (refuse SPA origin) + logs', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    const out = resolveStablePmtilesUrl(undefined, 'https://chemins-communs.fr', true);
    expect(out).toBeNull();
    expect(err).toHaveBeenCalledOnce();
  });

  it('UNSET in dev falls back to same-origin', () => {
    expect(resolveStablePmtilesUrl(undefined, 'http://localhost:3787', false))
      .toBe('http://localhost:3787/heatmap-display.pmtiles');
  });
});

describe('pointerUrlFor', () => {
  it('resolves the sibling pointer JSON next to the pmtiles binary', () => {
    expect(pointerUrlFor('https://storage.googleapis.com/bkt/heatmap-display.pmtiles'))
      .toBe('https://storage.googleapis.com/bkt/heatmap-display.json');
  });
  it('works for a same-origin path', () => {
    expect(pointerUrlFor('http://localhost:3787/heatmap-display.pmtiles'))
      .toBe('http://localhost:3787/heatmap-display.json');
  });
});

describe('immutableUrlFromPointer', () => {
  it('extracts latest_url (the immutable snapshot URL)', () => {
    const pointer = {
      latest: 'v20606-ab12cd34',
      latest_url: 'https://storage.googleapis.com/bkt/heatmap-display-v20606-ab12cd34.pmtiles',
      mutable_url: 'https://storage.googleapis.com/bkt/heatmap-display.pmtiles',
    };
    expect(immutableUrlFromPointer(pointer)).toBe(pointer.latest_url);
  });
  it('returns null on a malformed / missing-field payload', () => {
    expect(immutableUrlFromPointer(null)).toBeNull();
    expect(immutableUrlFromPointer({})).toBeNull();
    expect(immutableUrlFromPointer({ latest_url: 42 })).toBeNull();
    expect(immutableUrlFromPointer({ latest_url: '' })).toBeNull();
    expect(immutableUrlFromPointer('nope')).toBeNull();
  });
});
