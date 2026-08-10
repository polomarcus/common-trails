import { describe, it, expect } from 'vitest';
import {
  ageMs,
  freshnessLevel,
  relativeParts,
  resyncLevel,
  stravaLevel,
  sparklinePoints,
  sparklinePath,
  FRESHNESS_THRESHOLDS,
} from '../admin-metrics';

const NOW = Date.parse('2026-07-12T12:00:00Z');

describe('ageMs', () => {
  it('returns null for missing/invalid timestamps', () => {
    expect(ageMs(null, NOW)).toBeNull();
    expect(ageMs(undefined, NOW)).toBeNull();
    expect(ageMs('not-a-date', NOW)).toBeNull();
  });
  it('computes elapsed ms and never goes negative', () => {
    expect(ageMs('2026-07-12T09:00:00Z', NOW)).toBe(3 * 3600_000);
    expect(ageMs('2026-07-12T15:00:00Z', NOW)).toBe(0); // future clamps to 0
  });
});

describe('freshnessLevel', () => {
  const { amber, red } = FRESHNESS_THRESHOLDS.rebuild;
  it('unknown for null age', () => {
    expect(freshnessLevel(null, amber, red)).toBe('unknown');
  });
  it('green below amber, amber between, red at/above red', () => {
    expect(freshnessLevel(3 * 3600_000, amber, red)).toBe('green'); // 3h
    expect(freshnessLevel(2 * 24 * 3600_000, amber, red)).toBe('amber'); // 2d
    expect(freshnessLevel(8 * 24 * 3600_000, amber, red)).toBe('red'); // 8d
    expect(freshnessLevel(red, amber, red)).toBe('red'); // boundary inclusive
  });
});

describe('relativeParts', () => {
  it('buckets by magnitude', () => {
    expect(relativeParts(null)).toEqual({ unit: 'never', value: 0 });
    expect(relativeParts(30_000)).toEqual({ unit: 'now', value: 0 });
    expect(relativeParts(5 * 60_000)).toEqual({ unit: 'min', value: 5 });
    expect(relativeParts(3 * 3600_000)).toEqual({ unit: 'h', value: 3 });
    expect(relativeParts(2 * 24 * 3600_000)).toEqual({ unit: 'd', value: 2 });
  });
});

describe('resyncLevel', () => {
  it('maps job status to chip level', () => {
    expect(resyncLevel(null)).toBe('unknown');
    expect(resyncLevel('COMPLETED')).toBe('green');
    expect(resyncLevel('running')).toBe('amber');
    expect(resyncLevel('PENDING')).toBe('amber');
    expect(resyncLevel('FAILED')).toBe('red');
  });
});

describe('stravaLevel', () => {
  it('unknown when nobody connected', () => {
    expect(stravaLevel({ strava_connected: 0, strava_sync_failures: 0, strava_reconnect_required: false })).toBe('unknown');
  });
  it('green when connected + clean', () => {
    expect(stravaLevel({ strava_connected: 1, strava_sync_failures: 0, strava_reconnect_required: false })).toBe('green');
  });
  it('amber on failures, red on reconnect required', () => {
    expect(stravaLevel({ strava_connected: 1, strava_sync_failures: 2, strava_reconnect_required: false })).toBe('amber');
    expect(stravaLevel({ strava_connected: 1, strava_sync_failures: 5, strava_reconnect_required: true })).toBe('red');
  });
});

describe('sparklinePoints / sparklinePath', () => {
  it('empty series → no points / empty path', () => {
    expect(sparklinePoints([], 100, 20)).toEqual([]);
    expect(sparklinePath([], 100, 20)).toBe('');
  });
  it('single point sits centred horizontally', () => {
    const pts = sparklinePoints([5], 100, 20, 2);
    expect(pts).toHaveLength(1);
    expect(pts[0].x).toBeCloseTo(50, 1);
  });
  it('flat series maps to a centred horizontal line', () => {
    const pts = sparklinePoints([7, 7, 7], 100, 20, 2);
    const ys = pts.map((p) => p.y);
    expect(new Set(ys.map((y) => y.toFixed(3))).size).toBe(1); // all equal
    expect(ys[0]).toBeCloseTo(10, 1); // centre of a 20-high box
  });
  it('increasing series → x increases, larger value sits higher (smaller y)', () => {
    const pts = sparklinePoints([1, 2, 3], 100, 20, 2);
    expect(pts[0].x).toBeLessThan(pts[1].x);
    expect(pts[1].x).toBeLessThan(pts[2].x);
    expect(pts[2].y).toBeLessThan(pts[0].y); // max value → top
  });
  it('all points stay within the padded box', () => {
    const w = 120, h = 30, pad = 3;
    const pts = sparklinePoints([3, 9, 1, 7, 5], w, h, pad);
    for (const p of pts) {
      expect(p.x).toBeGreaterThanOrEqual(pad);
      expect(p.x).toBeLessThanOrEqual(w - pad + 0.01);
      expect(p.y).toBeGreaterThanOrEqual(pad - 0.01);
      expect(p.y).toBeLessThanOrEqual(h - pad + 0.01);
    }
  });
  it('path starts with a move command', () => {
    expect(sparklinePath([1, 2], 100, 20)).toMatch(/^M[\d.]+,[\d.]+ L/);
  });
});
