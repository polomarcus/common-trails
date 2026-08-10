/**
 * Unit tests for format.ts — formatting + sport speed estimates.
 */
import { describe, it, expect } from 'vitest';
import {
  formatDist, fmtKm, fmtElev, fmtElevFull,
  fmtDuration, estimateDurationMin,
} from '@/lib/format';

describe('formatDist', () => {
  it('returns "–" for falsy', () => {
    expect(formatDist(undefined)).toBe('–');
    expect(formatDist(0)).toBe('–');
  });

  it('formats < 1km as meters', () => {
    expect(formatDist(500)).toBe('500 m');
    expect(formatDist(999)).toBe('999 m');
  });

  it('formats >= 1km with one decimal', () => {
    expect(formatDist(1000)).toBe('1.0 km');
    expect(formatDist(15234)).toBe('15.2 km');
  });
});

describe('fmtKm', () => {
  it('returns "–" for falsy', () => {
    expect(fmtKm(0)).toBe('–');
  });
  it('always uses km regardless of magnitude', () => {
    expect(fmtKm(500)).toBe('0.5 km');
    expect(fmtKm(15234)).toBe('15.2 km');
  });
});

describe('fmtElev', () => {
  it('returns null for falsy (so callers can hide the element)', () => {
    expect(fmtElev(undefined)).toBeNull();
    expect(fmtElev(0)).toBeNull();
  });
  it('rounds and prefixes with arrow', () => {
    expect(fmtElev(123.7)).toBe('↑124 m');
  });
});

describe('fmtElevFull', () => {
  it('returns "—" for falsy', () => {
    expect(fmtElevFull(0)).toBe('—');
  });
  it('uses thousand separator from locale', () => {
    expect(fmtElevFull(1234, 'fr-FR')).toMatch(/1.234 m/);
  });
});

describe('fmtDuration', () => {
  it('formats < 60 min as minutes', () => {
    expect(fmtDuration(5)).toBe('5 min');
    expect(fmtDuration(59)).toBe('59 min');
  });

  it('formats >= 60 min as Xh Ym', () => {
    expect(fmtDuration(60)).toBe('1h');
    expect(fmtDuration(125)).toBe('2h 5m');
    expect(fmtDuration(180)).toBe('3h');
  });

  it('rounds minutes to nearest', () => {
    expect(fmtDuration(0.4)).toBe('0 min');
    expect(fmtDuration(0.6)).toBe('1 min');
  });
});

describe('estimateDurationMin', () => {
  it('uses sport-specific speed (gravel @ 16km/h)', () => {
    // 16km gravel → 60 min
    expect(estimateDurationMin(16, 'gravel')).toBe(60);
  });

  it('road is faster than gravel', () => {
    expect(estimateDurationMin(20, 'road')).toBeLessThan(estimateDurationMin(20, 'gravel'));
  });

  it('mtb is slower than gravel', () => {
    expect(estimateDurationMin(20, 'mtb')).toBeGreaterThan(estimateDurationMin(20, 'gravel'));
  });

  it('falls back to gravel speed for unknown sport', () => {
    const unknown = estimateDurationMin(10, 'kayak');
    const gravel = estimateDurationMin(10, 'gravel');
    expect(unknown).toBe(gravel);
  });

  it('hiking is the slowest (4km/h)', () => {
    // 4km hiking → 60 min
    expect(estimateDurationMin(4, 'hiking')).toBe(60);
  });
});
