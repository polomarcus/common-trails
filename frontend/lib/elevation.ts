/**
 * Pure elevation utilities.
 *
 * NO process.env, NO side effects. Take explicit options.
 */

/** Colour by slope % — luminance-ordered ramp (light→dark = easy→hard), accessible for color blindness. */
export function slopeColor(pct: number): string {
  const abs = Math.abs(pct);
  if (abs < 2)  return '#94d2bd'; // flat — light teal
  if (abs < 5)  return '#e9d8a6'; // gentle — warm sand
  if (abs < 9)  return '#ee9b00'; // moderate — amber
  if (abs < 14) return '#ca6702'; // steep — burnt orange
  return '#9b2226';               // extreme — dark red
}

/**
 * Haversine distance in metres between two [lon, lat] points.
 * Pure — no external dependencies.
 */
export function computeDistance(
  lon1: number, lat1: number,
  lon2: number, lat2: number,
): number {
  const R = 6_371_000;
  const rlat1 = (lat1 * Math.PI) / 180;
  const rlat2 = (lat2 * Math.PI) / 180;
  const dlat = ((lat2 - lat1) * Math.PI) / 180;
  const dlon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dlat / 2) ** 2 +
    Math.cos(rlat1) * Math.cos(rlat2) * Math.sin(dlon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/**
 * 3-point moving-average smoothing.
 * Returns a new array; does not mutate input.
 */
export function smoothElevation(
  elevations: number[],
  windowSize: number = 3,
): number[] {
  const n = elevations.length;
  if (n < 3 || windowSize < 2) return [...elevations];
  const half = Math.floor(windowSize / 2);
  const result = [...elevations];
  for (let i = half; i < n - half; i++) {
    const lo = Math.max(0, i - half);
    const hi = Math.min(n, i + half + 1);
    let sum = 0;
    for (let j = lo; j < hi; j++) sum += elevations[j];
    result[i] = sum / (hi - lo);
  }
  return result;
}

/**
 * Compute D+ and D- using threshold-based filtering.
 * Ignores micro noise below `thresholdM` (default 1.5m).
 *
 * @returns [ascent_m, descent_m]
 */
export function computeDPlus(
  elevations: number[],
  thresholdM: number = 1.5,
): [number, number] {
  let ascent = 0;
  let descent = 0;
  for (let i = 1; i < elevations.length; i++) {
    const delta = elevations[i] - elevations[i - 1];
    if (delta > thresholdM) ascent += delta;
    else if (delta < -thresholdM) descent += Math.abs(delta);
  }
  return [Math.round(ascent * 10) / 10, Math.round(descent * 10) / 10];
}
