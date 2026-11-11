/**
 * "Ma heatmap" from the per-user PRIVATE PMTiles file
 * (docs/ma-heatmap-fichier-prive.md).
 *
 * Flow on /map (page.tsx loadActivities):
 *  1. GET /me/heatmap/manifest. Anything but `ready` (flag off, file missing or
 *     stale after a change) → the caller falls back to GET /me/activities.
 *  2. GET the version's summary (sidebar list, bbox/center/start — no trace
 *     coordinates) and register an authenticated PMTiles source for its tiles.
 *  3. Swap the `user-activities` source from `geojson` to `vector` (same three
 *     layer ids → handlers, toggles and dimming unchanged). The browser only
 *     fetches the tiles of the visible area; versions are immutable, so a
 *     reload is served from the HTTP cache.
 *
 * The selected activity's full coordinates are fetched on demand
 * (GET /me/activities/{id}) — the list carries none.
 */
import type { RangeResponse, Source } from 'pmtiles';

import type { ActivityItem } from '@/lib/map-utils';
import { addPMTilesSource } from '@/lib/pmtiles-protocol';
import {
  USER_TRACE_HIT_LAYER, USER_TRACE_SOURCE, USER_TRACE_TILE_LAYER,
  USER_TRACE_GLOW_LAYER, USER_TRACE_LINE_LAYER, userTraceLayerSpecs,
} from '@/lib/user-traces-layers';

export interface MyHeatmapManifest {
  status: 'ready' | 'stale' | 'missing' | 'disabled';
  version?: string;
  activity_count?: number;
  built_at?: string | null;
  tiles_url?: string | null;
  summary_url?: string;
}

export interface MyHeatmapSummaryEntry {
  id: string;
  name: string;
  sport: string;
  /** Absent from files built before the flag existed → reads as false. */
  sport_is_default?: boolean;
  distance_m: number | null;
  elevation_gain_m: number | null;
  moving_time?: number | null;
  activity_date: string | null;
  created_at: string | null;
  undated: boolean;
  provider: string;
  provider_activity_id: string | null;
  bbox: [number, number, number, number];
  center: [number, number];
  start: [number, number];
}

export interface MyHeatmapSummary {
  total: number;
  activities: MyHeatmapSummaryEntry[];
}

/**
 * PMTiles Source that range-fetches through the backend's authenticated proxy:
 * same-origin in prod, `credentials: 'include'` so the httpOnly auth cookie is
 * sent cross-origin in dev too (the stock FetchSource omits it).
 */
export class CredentialedRangeSource implements Source {
  constructor(private readonly url: string) {}

  getKey(): string {
    return this.url;
  }

  async getBytes(offset: number, length: number, signal?: AbortSignal): Promise<RangeResponse> {
    const resp = await fetch(this.url, {
      credentials: 'include',
      signal,
      headers: { Range: `bytes=${offset}-${offset + length - 1}` },
    });
    if (resp.status !== 206 && resp.status !== 200) {
      throw new Error(`my-heatmap tiles: HTTP ${resp.status}`);
    }
    let data = await resp.arrayBuffer();
    // A 200 is the whole object (a proxy that ignored Range) — slice it.
    if (resp.status === 200 && data.byteLength > length) data = data.slice(offset, offset + length);
    return {
      data,
      etag: resp.headers.get('ETag') ?? undefined,
      cacheControl: resp.headers.get('Cache-Control') ?? undefined,
    };
  }
}

function absolute(apiUrl: string, path: string): string {
  const base = apiUrl || (typeof window !== 'undefined' ? window.location.origin : '');
  return new URL(path, base.endsWith('/') ? base : `${base}/`).href;
}

export async function fetchMyHeatmapManifest(apiUrl: string): Promise<MyHeatmapManifest | null> {
  try {
    const resp = await fetch(`${apiUrl}/me/heatmap/manifest`, { credentials: 'include' });
    if (!resp.ok) return null;
    return (await resp.json()) as MyHeatmapManifest;
  } catch {
    return null; // older backend / network: the JSON path takes over
  }
}

export interface LoadedMyHeatmap {
  summary: MyHeatmapSummary;
  /** MapLibre vector source URL (`pmtiles://…`), null when the user has no traces. */
  tilesSourceUrl: string | null;
}

/** Load the summary + register the tiles of a `ready` manifest. Null on any failure. */
export async function loadMyHeatmapFile(
  apiUrl: string,
  manifest: MyHeatmapManifest,
): Promise<LoadedMyHeatmap | null> {
  if (manifest.status !== 'ready' || !manifest.summary_url) return null;
  try {
    const resp = await fetch(`${apiUrl}${manifest.summary_url}`, { credentials: 'include' });
    if (!resp.ok) return null;
    const summary = (await resp.json()) as MyHeatmapSummary;
    if (!summary || !Array.isArray(summary.activities)) return null;
    let tilesSourceUrl: string | null = null;
    if (manifest.tiles_url) {
      const source = new CredentialedRangeSource(absolute(apiUrl, manifest.tiles_url));
      // Fail fast on an unreachable file (header fetch) → JSON fallback.
      await source.getBytes(0, 127);
      tilesSourceUrl = await addPMTilesSource(source);
    }
    return { summary, tilesSourceUrl };
  } catch (e) {
    console.error('[map] my-heatmap file load failed — falling back to /me/activities:', e);
    return null;
  }
}

/** Sidebar items from the summary. `coords` stays empty until the activity is selected. */
export function summaryToActivityItems(summary: MyHeatmapSummary, defaultName: string): ActivityItem[] {
  return summary.activities.map((a) => ({
    id: a.id,
    name: a.name || defaultName,
    sport: a.sport || 'road',
    distance_m: a.distance_m || 0,
    elevation_gain_m: a.elevation_gain_m ?? null,
    center: a.center,
    bbox: a.bbox,
    activity_date: a.activity_date ?? null,
    created_at: a.created_at ?? null,
    coords: [],
    provider: a.provider ?? 'file',
    provider_activity_id: a.provider_activity_id ?? null,
    undated: a.undated === true,
    sport_is_default: a.sport_is_default === true,
  }));
}

const USER_LAYERS = [USER_TRACE_GLOW_LAYER, USER_TRACE_LINE_LAYER, USER_TRACE_HIT_LAYER];

/**
 * Re-create the `user-activities` source as `vector` (tilesSourceUrl) or as the
 * legacy empty `geojson` (null), keeping the three layer ids, their position in
 * the stack, their visibility and their current (possibly dimmed) opacity.
 * No-op when the source already has the requested flavour/URL.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function setUserActivitiesSource(m: any, color: string, tilesSourceUrl: string | null): void {
  const current = m.getSource(USER_TRACE_SOURCE);
  if (tilesSourceUrl === null && current?.type === 'geojson') return;
  if (tilesSourceUrl !== null && current?.type === 'vector' && current?.url === tilesSourceUrl) return;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const layers: any[] = m.getStyle()?.layers ?? [];
  const lastIdx = Math.max(...USER_LAYERS.map((id) => layers.findIndex((l) => l.id === id)));
  const beforeId = lastIdx >= 0 ? layers[lastIdx + 1]?.id : undefined;
  const saved = new Map<string, { visibility: unknown; opacity: unknown }>();
  for (const id of USER_LAYERS) {
    if (!m.getLayer(id)) continue;
    saved.set(id, {
      visibility: m.getLayoutProperty(id, 'visibility'),
      opacity: m.getPaintProperty(id, 'line-opacity'),
    });
    m.removeLayer(id);
  }
  if (current) m.removeSource(USER_TRACE_SOURCE);

  if (tilesSourceUrl) {
    m.addSource(USER_TRACE_SOURCE, { type: 'vector', url: tilesSourceUrl });
  } else {
    m.addSource(USER_TRACE_SOURCE, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  }
  const specs = userTraceLayerSpecs(color, tilesSourceUrl ? USER_TRACE_TILE_LAYER : undefined);
  for (const spec of specs) {
    const prev = saved.get(spec.id);
    if (prev?.visibility !== undefined) spec.layout = { ...spec.layout, visibility: prev.visibility };
    if (prev?.opacity !== undefined) spec.paint = { ...spec.paint, 'line-opacity': prev.opacity };
    m.addLayer(spec, beforeId && m.getLayer(beforeId) ? beforeId : undefined);
  }
}
