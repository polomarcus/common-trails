/**
 * GPX parsing and export utilities.
 * Extracted from map/page.tsx for reuse and testability.
 */
import { haversineKmScalar } from '@/lib/map-utils';

// ── Types ──────────────────────────────────────────────────────────────────

export interface GpxPreviewData {
  coords: [number, number, number?][];
  name: string;
  distanceKm: number;
  elevGain: number;
  elevLoss: number;
  bbox: [number, number, number, number]; // [minLon, minLat, maxLon, maxLat]
}

// ── Annotation icon presets ────────────────────────────────────────────────

export const ANNOTATION_ICONS: { key: string; emoji: string; labelKey: string }[] = [
  { key: 'water', emoji: '\u{1F4A7}', labelKey: 'annotation.water' },
  { key: 'food', emoji: '\u{1F37D}\uFE0F', labelKey: 'annotation.food' },
  { key: 'viewpoint', emoji: '\u{1F441}\uFE0F', labelKey: 'annotation.viewpoint' },
  { key: 'danger', emoji: '\u26A0\uFE0F', labelKey: 'annotation.danger' },
  { key: 'info', emoji: '\u2139\uFE0F', labelKey: 'annotation.info' },
  { key: 'photo', emoji: '\u{1F4F8}', labelKey: 'annotation.photo' },
  { key: 'shelter', emoji: '\u26FA', labelKey: 'annotation.shelter' },
  { key: 'bike-shop', emoji: '\u{1F527}', labelKey: 'annotation.repair' },
];

export const ANNOTATION_EMOJI_MAP: Record<string, string> = Object.fromEntries(
  ANNOTATION_ICONS.map(i => [i.key, i.emoji]),
);

// ── GPX export ─────────────────────────────────────────────────────────────

/** Build a minimal GPX string from an ordered list of [lon, lat] waypoints.
 *  Optionally includes annotations as <wpt> elements. */
export function waypointsToGpx(
  pts: [number, number][],
  name = 'Mon itinéraire',
  gpxAnnotations: { lon: number; lat: number; icon: string; text: string | null }[] = [],
): string {
  const trkpts = pts
    .map(([lon, lat]) => `      <trkpt lat="${lat.toFixed(6)}" lon="${lon.toFixed(6)}"></trkpt>`)
    .join('\n');
  const wpts = gpxAnnotations.map(a => {
    const emoji = ANNOTATION_EMOJI_MAP[a.icon] || '';
    const wptName = `${emoji} ${a.text || a.icon}`.trim().replace(/&/g, '&amp;').replace(/</g, '&lt;');
    return `  <wpt lat="${a.lat.toFixed(6)}" lon="${a.lon.toFixed(6)}"><name>${wptName}</name><type>${a.icon}</type></wpt>`;
  }).join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="CHEMINS COMMUNS" xmlns="http://www.topografix.com/GPX/1/1">
${wpts}
  <trk>
    <name>${name}</name>
    <trkseg>
${trkpts}
    </trkseg>
  </trk>
</gpx>`;
}

// ── GPX parsing ────────────────────────────────────────────────────────────

export function parseGpxFile(xml: string): GpxPreviewData | null {
  try {
    const doc = new DOMParser().parseFromString(xml, 'application/xml');
    const trkpts = doc.querySelectorAll('trkpt');
    if (trkpts.length < 2) return null;

    const coords: [number, number, number?][] = [];
    let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
    trkpts.forEach((pt) => {
      const lat = parseFloat(pt.getAttribute('lat') || '');
      const lon = parseFloat(pt.getAttribute('lon') || '');
      if (isNaN(lat) || isNaN(lon)) return;
      const eleNode = pt.querySelector('ele');
      const ele = eleNode ? parseFloat(eleNode.textContent || '') : undefined;
      coords.push(isNaN(ele as number) ? [lon, lat] : [lon, lat, ele]);
      if (lon < minLon) minLon = lon;
      if (lat < minLat) minLat = lat;
      if (lon > maxLon) maxLon = lon;
      if (lat > maxLat) maxLat = lat;
    });
    if (coords.length < 2) return null;

    let distanceKm = 0, elevGain = 0, elevLoss = 0;
    for (let i = 1; i < coords.length; i++) {
      distanceKm += haversineKmScalar(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1]);
      const e1 = coords[i - 1][2], e2 = coords[i][2];
      if (e1 != null && e2 != null) {
        const diff = e2 - e1;
        if (diff > 0) elevGain += diff; else elevLoss -= diff;
      }
    }

    const nameEl = doc.querySelector('trk > name') || doc.querySelector('name');
    const name = nameEl?.textContent?.trim() || 'GPX Preview';

    return { coords, name, distanceKm, elevGain, elevLoss, bbox: [minLon, minLat, maxLon, maxLat] };
  } catch {
    return null;
  }
}
