/**
 * Pure utility functions for polyline geometry and trace markers.
 * Extracted from map/page.tsx for reuse and testability.
 *
 * The drag-edit / route-editor segment functions were removed with the
 * in-app WASM router decommission (chore/decommission-wasm-routing).
 */
import { haversineKm } from '@/lib/geo';

// ── ActivityItem interface ──────────────────────────────────────────────────

export interface ActivityItem {
  id: string;
  name: string;
  sport: string;
  distance_m: number;
  elevation_gain_m: number | null;
  center: [number, number]; // midpoint of the track
  bbox: [number, number, number, number]; // [minLon, minLat, maxLon, maxLat]
  activity_date: string | null; // actual activity date (from Strava start_date_local)
  created_at: string | null;
  coords: number[][]; // [lon, lat] or [lon, lat, elevation_m] per point
  provider: string; // "strava" | "file"
  provider_activity_id: string | null; // Strava activity ID when provider=strava
  total_photo_count?: number; // count of associated ActivityPhoto rows (Strava import phase 4)
}

// ── Scalar haversine (used by functions that receive raw lon/lat numbers) ───

export function haversineKmScalar(lon1: number, lat1: number, lon2: number, lat2: number): number {
  const R = 6371;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// ── Polyline utilities ─────────────────────────────────────────────────────

/** Return GeoJSON points at every `intervalKm` along a polyline. */
export function computeKmMarkers(
  coords: number[][], intervalKm: number = 10,
): { type: 'FeatureCollection'; features: { type: 'Feature'; geometry: { type: 'Point'; coordinates: number[] }; properties: { km: number } }[] } {
  const features: { type: 'Feature'; geometry: { type: 'Point'; coordinates: number[] }; properties: { km: number } }[] = [];
  let cumKm = 0;
  let nextMark = intervalKm;
  for (let i = 1; i < coords.length; i++) {
    const segKm = haversineKmScalar(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1]);
    const prevCum = cumKm;
    cumKm += segKm;
    // Interpolate marker positions within this segment
    while (cumKm >= nextMark && segKm > 0) {
      const t = (nextMark - prevCum) / segKm;
      const lon = coords[i - 1][0] + t * (coords[i][0] - coords[i - 1][0]);
      const lat = coords[i - 1][1] + t * (coords[i][1] - coords[i - 1][1]);
      features.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [lon, lat] },
        properties: { km: nextMark },
      });
      nextMark += intervalKm;
    }
  }
  return { type: 'FeatureCollection', features };
}

/** Find the distance along a polyline closest to a given point. Returns km. */
export function distanceAlongAt(coords: number[][], lon: number, lat: number): { km: number; nearestIdx: number } {
  let cumKm = 0;
  let bestDist = Infinity;
  let bestKm = 0;
  let bestIdx = 0;
  for (let i = 0; i < coords.length; i++) {
    if (i > 0) cumKm += haversineKmScalar(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1]);
    const d = haversineKmScalar(coords[i][0], coords[i][1], lon, lat);
    if (d < bestDist) {
      bestDist = d;
      bestKm = cumKm;
      bestIdx = i;
    }
  }
  return { km: bestKm, nearestIdx: bestIdx };
}

/** Return the midpoint coordinate of a LineString. */
export function getTrackCenter(coords: [number, number][]): [number, number] {
  if (coords.length === 0) return [0, 0];
  return coords[Math.floor(coords.length / 2)];
}

/** Return [minLon, minLat, maxLon, maxLat] bounding box of a LineString. */
export function getTrackBbox(coords: [number, number][]): [number, number, number, number] {
  if (coords.length === 0) return [0, 0, 0, 0];
  let minLon = coords[0][0], minLat = coords[0][1];
  let maxLon = minLon, maxLat = minLat;
  for (const [lon, lat] of coords) {
    if (lon < minLon) minLon = lon;
    if (lat < minLat) minLat = lat;
    if (lon > maxLon) maxLon = lon;
    if (lat > maxLat) maxLat = lat;
  }
  return [minLon, minLat, maxLon, maxLat];
}

export function totalDistanceKm(pts: [number, number][]): number {
  let d = 0;
  for (let i = 1; i < pts.length; i++) d += haversineKm(pts[i - 1], pts[i]);
  return d;
}

/**
 * Compute marker positions for a trace:
 * - Start point (type='start')
 * - End point (type='end')
 * - Every 10 km (type='km', label='10', '20', ...)
 */
export function computeTraceMarkerPositions(coords: number[][]): { type: string; lon: number; lat: number; label: string }[] {
  const markers: { type: string; lon: number; lat: number; label: string }[] = [];
  if (coords.length < 2) return markers;

  // Start marker
  markers.push({ type: 'start', lon: coords[0][0], lat: coords[0][1], label: '' });

  // Walk through the trace computing cumulative distance
  let cumKm = 0;
  let nextMark = 10;
  for (let i = 1; i < coords.length; i++) {
    const segKm = haversineKm(
      [coords[i - 1][0], coords[i - 1][1]],
      [coords[i][0], coords[i][1]],
    );
    const prevCum = cumKm;
    cumKm += segKm;

    while (cumKm >= nextMark) {
      const ratio = segKm > 0 ? (nextMark - prevCum) / segKm : 0;
      const t = Math.max(0, Math.min(1, ratio));
      const lon = coords[i - 1][0] + t * (coords[i][0] - coords[i - 1][0]);
      const lat = coords[i - 1][1] + t * (coords[i][1] - coords[i - 1][1]);
      markers.push({ type: 'km', lon, lat, label: `${nextMark}` });
      nextMark += 10;
    }
  }

  // End marker
  const last = coords[coords.length - 1];
  markers.push({ type: 'end', lon: last[0], lat: last[1], label: '' });

  return markers;
}

// ── Point-to-segment geometry ──────────────────────────────────────────────

/**
 * Project point p onto segment [a, b], returning the parameter t and the projected point.
 */
export function projectOnSegment(
  p: [number, number],
  a: [number, number],
  b: [number, number],
): { t: number; proj: [number, number]; distKm: number } {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return { t: 0, proj: a, distKm: haversineKm(p, a) };
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / lenSq));
  const proj: [number, number] = [a[0] + t * dx, a[1] + t * dy];
  return { t, proj, distKm: haversineKm(p, proj) };
}
