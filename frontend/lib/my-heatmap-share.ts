/**
 * Sharing "Ma heatmap" through a secret link (docs/ma-heatmap-fichier-prive.md §10).
 *
 * - GET    /me/heatmap/share → status (never the link: only its hash is stored);
 * - POST   /me/heatmap/share → create, or regenerate (the old link dies); the
 *   response carries the URLs — the ONLY time the secret is shown;
 * - DELETE /me/heatmap/share → disable (every URL of the link → 404 at once).
 *
 * The link has no automatic expiry: it is pasted as a layer in gpx.studio /
 * OsmAnd / Locus, where an expiring URL would silently break the layer. The UI
 * shows its creation date; the owner revokes or regenerates it at will.
 */
import { apiFetch } from '@/lib/api-client';

export interface MyHeatmapShareStatus {
  /** False when "Ma heatmap" files are off for this account (flag / allow-list). */
  available: boolean;
  active: boolean;
  created_at?: string;
  /** The masked shared file is built (false right after creation, ~1 min). */
  ready?: boolean;
  activity_count?: number | null;
}

export interface MyHeatmapShareUrls {
  /** MapLibre style JSON — gpx.studio "vector style" custom layer. */
  style: string;
  /** Raster XYZ template — gpx.studio overlay, OsmAnd, Locus. */
  raster: string;
  /** Vector XYZ template (MVT). */
  vector: string;
  /** PMTiles archive (Range) for pmtiles-aware clients. */
  pmtiles: string;
}

export interface MyHeatmapShareCreated {
  active: true;
  created_at: string;
  urls: MyHeatmapShareUrls;
}

export function fetchShareStatus(): Promise<MyHeatmapShareStatus> {
  return apiFetch<MyHeatmapShareStatus>('/me/heatmap/share', { cache: 'no-store' });
}

/** Create the link, or regenerate it (the previous one stops working). */
export function createOrRegenerateShare(): Promise<MyHeatmapShareCreated> {
  return apiFetch<MyHeatmapShareCreated>('/me/heatmap/share', { method: 'POST' });
}

export function revokeShare(): Promise<void> {
  return apiFetch<void>('/me/heatmap/share', { method: 'DELETE' });
}

/** "12 novembre 2026" — the date shown next to an active link. */
export function formatShareDate(iso: string | undefined, locale: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString(locale, { day: 'numeric', month: 'long', year: 'numeric' });
}
