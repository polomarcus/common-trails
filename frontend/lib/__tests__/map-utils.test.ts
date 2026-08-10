/**
 * Unit tests for pure utility functions in map-utils.ts.
 * No React, no map — pure math/geometry assertions.
 */
import { describe, it, expect } from 'vitest';
import {
  haversineKmScalar,
  computeKmMarkers,
  distanceAlongAt,
  getTrackCenter,
  getTrackBbox,
  totalDistanceKm,
  computeTraceMarkerPositions,
  projectOnSegment,
} from '@/lib/map-utils';

describe('haversineKmScalar', () => {
  it('returns 0 for identical points', () => {
    expect(haversineKmScalar(2.35, 48.85, 2.35, 48.85)).toBe(0);
  });

  it('computes Paris → Lyon distance (~393km)', () => {
    const km = haversineKmScalar(2.3522, 48.8566, 4.8357, 45.7640);
    expect(km).toBeGreaterThan(390);
    expect(km).toBeLessThan(400);
  });
});

describe('totalDistanceKm', () => {
  it('returns 0 for empty or single-point line', () => {
    expect(totalDistanceKm([])).toBe(0);
    expect(totalDistanceKm([[0, 0]])).toBe(0);
  });

  it('sums consecutive segment distances', () => {
    // 3 collinear points, each ~1km apart at the equator
    const pts: [number, number][] = [[0, 0], [0.009, 0], [0.018, 0]];
    const km = totalDistanceKm(pts);
    expect(km).toBeGreaterThan(1.9);
    expect(km).toBeLessThan(2.1);
  });
});

describe('getTrackCenter', () => {
  it('returns [0,0] for empty', () => {
    expect(getTrackCenter([])).toEqual([0, 0]);
  });

  it('returns midpoint of array', () => {
    const pts: [number, number][] = [[0, 0], [1, 1], [2, 2], [3, 3], [4, 4]];
    expect(getTrackCenter(pts)).toEqual([2, 2]);
  });
});

describe('getTrackBbox', () => {
  it('returns zeros for empty', () => {
    expect(getTrackBbox([])).toEqual([0, 0, 0, 0]);
  });

  it('returns [minLon, minLat, maxLon, maxLat]', () => {
    const pts: [number, number][] = [[2, 48], [3, 49], [1, 50], [4, 47]];
    expect(getTrackBbox(pts)).toEqual([1, 47, 4, 50]);
  });
});

describe('computeKmMarkers', () => {
  it('returns no features for short line (< 10km)', () => {
    const result = computeKmMarkers([[0, 0], [0.01, 0]]);
    expect(result.features).toHaveLength(0);
  });

  it('places markers every 10km by default', () => {
    // ~30km line at equator (1° lon ≈ 111km, so 0.27° ≈ 30km)
    const result = computeKmMarkers([[0, 0], [0.27, 0]]);
    expect(result.features.length).toBeGreaterThanOrEqual(2);
    expect(result.features[0].properties.km).toBe(10);
  });
});

describe('distanceAlongAt', () => {
  it('returns nearest point along line', () => {
    const coords = [[0, 0], [0.01, 0], [0.02, 0], [0.03, 0]];
    const { km, nearestIdx } = distanceAlongAt(coords, 0.02, 0);
    expect(nearestIdx).toBe(2);
    expect(km).toBeGreaterThan(2);
    expect(km).toBeLessThan(2.5);
  });
});

describe('computeTraceMarkerPositions', () => {
  it('returns empty for line with < 2 points', () => {
    expect(computeTraceMarkerPositions([])).toEqual([]);
    expect(computeTraceMarkerPositions([[0, 0]])).toEqual([]);
  });

  it('always includes start and end markers', () => {
    const markers = computeTraceMarkerPositions([[0, 0], [0.05, 0]]); // ~5km
    expect(markers[0].type).toBe('start');
    expect(markers[markers.length - 1].type).toBe('end');
  });

  it('adds km markers every 10km', () => {
    const markers = computeTraceMarkerPositions([[0, 0], [0.27, 0]]); // ~30km
    const kmMarkers = markers.filter(m => m.type === 'km');
    expect(kmMarkers.length).toBeGreaterThanOrEqual(2);
    expect(kmMarkers[0].label).toBe('10');
  });
});

describe('projectOnSegment', () => {
  it('projects to midpoint when t=0.5', () => {
    const { t, proj } = projectOnSegment([0.5, 0.5], [0, 0], [1, 0]);
    expect(t).toBeCloseTo(0.5, 5);
    expect(proj[0]).toBeCloseTo(0.5, 5);
    expect(proj[1]).toBeCloseTo(0, 5);
  });

  it('clamps t to [0, 1]', () => {
    const r1 = projectOnSegment([-1, 0], [0, 0], [1, 0]);
    expect(r1.t).toBe(0);
    const r2 = projectOnSegment([2, 0], [0, 0], [1, 0]);
    expect(r2.t).toBe(1);
  });
});
