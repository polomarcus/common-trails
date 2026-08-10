import { slopeColor, smoothElevation, computeDistance, computeDPlus } from '@/lib/elevation';

export { slopeColor };

export interface ProfilePoint {
  idx: number;
  lon: number;
  lat: number;
  dist_m: number;   // cumulative from start
  ele_m: number;     // smoothed (3-point moving average)
  grade: number;     // slope %
  surface?: string;
}

export interface ProfileData {
  points: ProfilePoint[];
  distArr: Float64Array; // parallel array for binary search
  totalDist: number;
  minEle: number;
  maxEle: number;
  ascentM: number;
  descentM: number;
}

/**
 * Build profile data from coords [lon, lat, ele?].
 * Uses pure utility functions from elevation.ts (no env globals).
 * O(n) — haversine distances, smoothing, grade + D+ calc.
 */
export function buildProfileData(
  coords: number[][],
  surfaceSegs?: { from_idx: number; to_idx: number; surface: string }[],
): ProfileData | null {
  if (!coords || coords.length < 2) return null;

  // Check for 3D data
  const hasEle = coords.some((c) => c[2] != null && c[2] !== 0);
  if (!hasEle) return null;

  const n = coords.length;

  // Raw elevations — interpolate missing/zero values from nearest valid neighbors
  const rawEle: number[] = new Array(n);
  for (let i = 0; i < n; i++) {
    rawEle[i] = (coords[i][2] != null && coords[i][2] !== 0) ? coords[i][2] : NaN;
  }
  // Forward/backward interpolation for NaN gaps
  for (let i = 0; i < n; i++) {
    if (!isNaN(rawEle[i])) continue;
    // Find previous valid and next valid
    let prevIdx = -1;
    for (let j = i - 1; j >= 0; j--) { if (!isNaN(rawEle[j])) { prevIdx = j; break; } }
    let nextIdx = -1;
    for (let j = i + 1; j < n; j++) { if (!isNaN(rawEle[j])) { nextIdx = j; break; } }
    if (prevIdx >= 0 && nextIdx >= 0) {
      // Linear interpolation
      const t = (i - prevIdx) / (nextIdx - prevIdx);
      rawEle[i] = rawEle[prevIdx] + t * (rawEle[nextIdx] - rawEle[prevIdx]);
    } else if (prevIdx >= 0) {
      rawEle[i] = rawEle[prevIdx];
    } else if (nextIdx >= 0) {
      rawEle[i] = rawEle[nextIdx];
    } else {
      rawEle[i] = 0;
    }
  }

  // Smooth using shared pure function
  const smoothEle = smoothElevation(rawEle, 3);

  // Cumulative distances using pure computeDistance
  const distArr = new Float64Array(n);
  distArr[0] = 0;
  for (let i = 1; i < n; i++) {
    const d = computeDistance(
      coords[i - 1][0], coords[i - 1][1],
      coords[i][0], coords[i][1],
    );
    distArr[i] = distArr[i - 1] + d;
  }
  const totalDist = distArr[n - 1];

  // D+ / D- using pure function (threshold-based)
  const [ascentM, descentM] = computeDPlus(smoothEle, 1.5);

  // Build surface lookup if available (idx → surface)
  let surfaceLookup: Map<number, string> | undefined;
  if (surfaceSegs && surfaceSegs.length > 0) {
    surfaceLookup = new Map();
    for (const seg of surfaceSegs) {
      for (let i = seg.from_idx; i <= Math.min(seg.to_idx, n - 1); i++) {
        surfaceLookup.set(i, seg.surface);
      }
    }
  }

  // Build points with grade
  let minEle = Infinity;
  let maxEle = -Infinity;
  const points: ProfilePoint[] = new Array(n);

  for (let i = 0; i < n; i++) {
    const ele = smoothEle[i];
    if (ele < minEle) minEle = ele;
    if (ele > maxEle) maxEle = ele;

    let grade = 0;
    if (i > 0) {
      const dx = distArr[i] - distArr[i - 1];
      if (dx > 0) {
        grade = ((smoothEle[i] - smoothEle[i - 1]) / dx) * 100;
      }
    }

    points[i] = {
      idx: i,
      lon: coords[i][0],
      lat: coords[i][1],
      dist_m: distArr[i],
      ele_m: ele,
      grade,
      surface: surfaceLookup?.get(i),
    };
  }

  return { points, distArr, totalDist, minEle, maxEle, ascentM, descentM };
}

/**
 * Binary search: find index of nearest point by cumulative distance.
 * O(log n).
 */
export function findNearestByDistance(data: ProfileData, dist_m: number): number {
  const arr = data.distArr;
  let lo = 0;
  let hi = arr.length - 1;

  if (dist_m <= arr[0]) return 0;
  if (dist_m >= arr[hi]) return hi;

  while (lo < hi - 1) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] <= dist_m) lo = mid;
    else hi = mid;
  }

  // Return closest of lo, hi
  return (dist_m - arr[lo]) <= (arr[hi] - dist_m) ? lo : hi;
}

/**
 * Linear scan: find index of nearest point by lon/lat.
 * For 10k points this is <1ms.
 */
export function findNearestByLngLat(data: ProfileData, lon: number, lat: number): number {
  const pts = data.points;
  let bestIdx = 0;
  let bestDist = Infinity;

  for (let i = 0; i < pts.length; i++) {
    // Approximate squared distance (no sqrt needed for comparison)
    const dLon = pts[i].lon - lon;
    const dLat = pts[i].lat - lat;
    const d2 = dLon * dLon + dLat * dLat;
    if (d2 < bestDist) {
      bestDist = d2;
      bestIdx = i;
    }
  }

  return bestIdx;
}

/**
 * Extract sub-linestring around idx within ±radiusM.
 */
export function getHighlightCoords(
  data: ProfileData,
  idx: number,
  radiusM: number,
): [number, number][] {
  const pts = data.points;
  const centerDist = data.distArr[idx];
  const lo = centerDist - radiusM;
  const hi = centerDist + radiusM;

  const coords: [number, number][] = [];
  for (let i = 0; i < pts.length; i++) {
    const d = data.distArr[i];
    if (d >= lo && d <= hi) {
      coords.push([pts[i].lon, pts[i].lat]);
    }
  }

  return coords;
}
