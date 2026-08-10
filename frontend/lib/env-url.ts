/**
 * env-url.ts — shared, unit-testable resolution of the `NEXT_PUBLIC_*` URL env
 * vars, plus the heatmap freshness-pointer helpers.
 *
 * The trap this exists to prevent (happened in prod 2026-08-07, curl-invisible):
 *  - `NEXT_PUBLIC_API_URL` UNSET in a prod build silently baked
 *    `http://localhost:8787`, so every API call CORS-failed.
 *  - `NEXT_PUBLIC_HEATMAP_URL` UNSET pointed the pmtiles/MVT tile source at the
 *    SPA origin, which returns `index.html` with HTTP 200 → a blank heatmap.
 *
 * Rule: distinguish `''` (deliberately same-origin — the INTENDED prod value for
 * the API) from `undefined` (never set — the trap). Unset-in-prod is made LOUD,
 * never a silent dev default.
 *
 * `api-client.ts` and `cdn-cache.ts` both resolve through here so the two agree.
 */

export const DEV_API_URL = 'http://localhost:8787';

/**
 * Resolve `NEXT_PUBLIC_API_URL`.
 *  - a real value (incl. an explicit `''` = same-origin, the intended prod value)
 *    → returned as-is.
 *  - `undefined` + dev → `http://localhost:8787` (dev convenience).
 *  - `undefined` + prod → LOUD `console.error`, then `''` (same-origin) so the app
 *    is no worse than a correct prod deploy — NEVER `localhost`, which CORS-fails.
 */
export function resolveApiUrl(raw: string | undefined, isProd: boolean): string {
  if (raw !== undefined) return raw; // '' means same-origin — intended in prod
  if (isProd) {
    console.error(
      '[env] NEXT_PUBLIC_API_URL is UNSET in a production build. Falling back to ' +
        'same-origin (""). Set it to "" (same-origin) or the API URL at build time — ' +
        'a dev localhost default would CORS-fail every API call.',
    );
    return '';
  }
  return DEV_API_URL;
}

/**
 * Resolve the STABLE community-heatmap PMTiles URL (the 24 h-cached canonical
 * `heatmap-display.pmtiles`). This is the FALLBACK target; the freshness pointer
 * upgrades it to the immutable snapshot at runtime.
 *  - `heatmapUrl` set → that URL (prod public GCS bucket).
 *  - unset + prod → `null` + LOUD `console.error`: refuse to point the source at
 *    the SPA origin (returns `index.html`/200 → a silently blank map). A visible
 *    skip beats a broken-but-200 tile source.
 *  - unset + dev → `${origin}/heatmap-display.pmtiles` (the frontend container
 *    serves the file same-origin in dev).
 */
export function resolveStablePmtilesUrl(
  heatmapUrl: string | undefined,
  origin: string | undefined,
  isProd: boolean,
): string | null {
  if (heatmapUrl) return heatmapUrl;
  if (isProd) {
    console.error(
      '[env] NEXT_PUBLIC_HEATMAP_URL is UNSET in a production build. Refusing to ' +
        'point the heatmap tile source at the SPA origin (returns index.html with ' +
        'HTTP 200 → a silently blank map). Set it to the public PMTiles URL.',
    );
    return null;
  }
  if (!origin) return null;
  return `${origin}/heatmap-display.pmtiles`;
}

/**
 * The freshness pointer (`heatmap-display.json`) written by
 * `backend/app/jobs/build_pmtiles.py` lives NEXT TO the pmtiles binary. Given the
 * stable pmtiles URL, compute the sibling pointer URL (same directory).
 */
export function pointerUrlFor(pmtilesUrl: string): string {
  const cut = pmtilesUrl.lastIndexOf('/');
  const dir = cut >= 0 ? pmtilesUrl.slice(0, cut) : '';
  return `${dir}/heatmap-display.json`;
}

/**
 * Extract the immutable snapshot URL from a pointer payload. The pointer shape
 * (see build_pmtiles.py step 3) is `{ latest, latest_url, mutable_url, ... }`;
 * `latest_url` is an ABSOLUTE URL to the 1 y-immutable
 * `heatmap-display-<id>.pmtiles`. Returns `null` on any malformed payload so the
 * caller falls back to the stable name.
 */
export function immutableUrlFromPointer(json: unknown): string | null {
  if (!json || typeof json !== 'object') return null;
  const u = (json as { latest_url?: unknown }).latest_url;
  return typeof u === 'string' && u.length > 0 ? u : null;
}
