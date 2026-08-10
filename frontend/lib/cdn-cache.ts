/**
 * CDN cache client — fetches pre-computed heatmap/graph data from Cloud Storage.
 *
 * Flow: manifest.json (60s cache) → versioned files (7d cache, immutable paths).
 * Falls back to API if CDN is unavailable or not configured.
 */

import { type CommunityStats, parseCommunityStats, buildStatsCandidates } from './community-stats';
import {
  resolveApiUrl, resolveStablePmtilesUrl, pointerUrlFor, immutableUrlFromPointer,
} from './env-url';

const IS_PROD = process.env.NODE_ENV === 'production';
const CDN_BASE = process.env.NEXT_PUBLIC_CDN_URL || '';
// Shared fail-loud semantics with api-client.ts (see env-url.ts): unset-in-prod
// is loud, not a silent localhost default.
const API_URL = resolveApiUrl(process.env.NEXT_PUBLIC_API_URL, IS_PROD);
// Override for the heatmap PMTiles binary (PRD #391, Phase 1). When set,
// points at the public heatmap base URL — e.g. the first-party CDN domain
// https://tiles.chemins-communs.fr/heatmap-display.pmtiles
// Leave empty in local dev so the relative `/heatmap-display.pmtiles`
// path served from the frontend container's static export keeps
// working.
const HEATMAP_URL = process.env.NEXT_PUBLIC_HEATMAP_URL || '';

/**
 * Resolve the pmtiles URL THROUGH the freshness pointer (`heatmap-display.json`,
 * 5-min cached). The build publishes an immutable per-build snapshot
 * (`heatmap-display-<id>.pmtiles`, 1 y-immutable) and points the JSON at it via
 * `latest_url`; the STABLE `heatmap-display.pmtiles` is 24 h-cached, so a
 * returning browser keeps a stale heatmap for up to a day after a rebuild unless
 * we hop to the immutable snapshot.
 *
 * On ANY failure (fetch error, non-200, missing/malformed `latest_url`) this
 * falls back to the stable URL so the map is never worse than before the pointer
 * existed. Kept fast — the pointer is ~300 B and already CDN-cached.
 *
 * @param stableUrl the canonical `heatmap-display.pmtiles` URL (no scheme prefix)
 * @returns the immutable snapshot URL if resolvable, else `stableUrl`
 */
async function resolvePmtilesViaPointer(stableUrl: string): Promise<string> {
  try {
    // Align the cache-buster to the pointer's own 5-min cache window so we don't
    // defeat its caching while still picking up new versions promptly.
    const bucket = Math.floor(Date.now() / 300_000);
    const res = await fetch(`${pointerUrlFor(stableUrl)}?t=${bucket}`);
    if (res.ok) {
      const immutable = immutableUrlFromPointer(await res.json());
      if (immutable) return immutable;
    }
  } catch {
    /* fall through to the stable name */
  }
  return stableUrl;
}

interface CacheManifest {
  version: string;
  published_at: number;
  rollback?: boolean;
  files: {
    summary: string;
    dfci?: string;
    trails: Record<string, string>;
    mvt_tiles?: string;
  };
}

let _manifest: CacheManifest | null = null;
let _manifestFetchedAt = 0;
const MANIFEST_TTL_MS = 60_000;

async function getManifest(): Promise<CacheManifest | null> {
  // F6: always re-fetch if TTL expired (don't serve stale on error)
  if (_manifest && Date.now() - _manifestFetchedAt < MANIFEST_TTL_MS) {
    return _manifest;
  }
  if (!CDN_BASE) return null;
  try {
    const cacheBuster = Math.floor(Date.now() / 60000);
    const res = await fetch(`${CDN_BASE}/api-cache/manifest.json?t=${cacheBuster}`);
    if (res.ok) {
      _manifest = await res.json();
      _manifestFetchedAt = Date.now();
    } else {
      // F6: clear stale manifest if CDN returns error (files may be cleaned up)
      _manifest = null;
    }
  } catch (e) {
    console.warn('CDN manifest fetch failed, falling back to API:', e);
    _manifest = null;
  }
  return _manifest;
}

/** F7: safe API fallback — checks r.ok before parsing JSON */
async function apiFallback(url: string): Promise<unknown> {
  try {
    const res = await fetch(url);
    if (!res.ok) {
      console.warn(`API fallback failed: ${res.status} ${url}`);
      return null;
    }
    return res.json();
  } catch (e) {
    console.warn(`API fallback error: ${url}`, e);
    return null;
  }
}

/** Fetch trails GeoJSON from CDN, fallback to API. */
export async function fetchTrailsCDN(sport: string): Promise<unknown> {
  const manifest = await getManifest();
  if (manifest?.files?.trails?.[sport]) {
    const url = `${CDN_BASE}/api-cache/${manifest.files.trails[sport]}`;
    try {
      const res = await fetch(url);
      if (res.ok) return res.json();
    } catch (e) {
      console.warn(`CDN fetch failed for trails/${sport}, falling back to API:`, e);
    }
  }
  return apiFallback(`${API_URL}/heatmap/trails?sport=${sport}`);
}

/**
 * Synchronous PMTiles URL for the community-heatmap source.
 *
 * Prefers the build-baked `NEXT_PUBLIC_HEATMAP_URL` (the public GCS bucket in
 * prod), else falls back to same-origin `/heatmap-display.pmtiles` (dev, where
 * the frontend container serves the file). Unlike `getPmtilesUrl()` this does
 * NO HEAD probe, so it is safe to call synchronously at map init (the source
 * add must not block — see PR #711b97b4 which made init sync for the 1.9s chain).
 *
 * ⚠️ Why this exists: `/map` (init-map-layers.ts) used to hardcode
 * `${origin}/heatmap-display.pmtiles`. In prod that path is NOT a real file —
 * the SPA catch-all returns index.html (200) — so the pmtiles source loaded
 * HTML and the whole community heatmap silently rendered NOTHING. The home hero
 * already resolved correctly via the async `getPmtilesUrl()`; this gives `/map`
 * the same GCS URL without reintroducing the blocking HEAD probe.
 */
export function communityPmtilesUrl(origin: string): string | null {
  // Fail-loud resolution (env-url.ts): prod + unset NEXT_PUBLIC_HEATMAP_URL →
  // null (skip the source) rather than pointing at the SPA origin, which returns
  // index.html/200 → a silently blank heatmap. Dev falls back to same-origin.
  // Read process.env at call time (Next inlines it either way) so the value is
  // never staler than the build.
  const base = resolveStablePmtilesUrl(
    process.env.NEXT_PUBLIC_HEATMAP_URL || undefined, origin, IS_PROD,
  );
  return base ? `pmtiles://${base}` : null;
}

/**
 * Async companion to `communityPmtilesUrl` that resolves the pmtiles URL THROUGH
 * the freshness pointer (immutable snapshot), so a returning `/map` visitor
 * escapes the 24 h-cached stable name after a rebuild. Falls back to the stable
 * `pmtiles://` URL on any pointer failure, and returns null in the same
 * prod-unset case as `communityPmtilesUrl`.
 *
 * `/map` init adds the source SYNCHRONOUSLY with `communityPmtilesUrl` (never
 * blocking), then calls this and swaps the source URL only if a newer immutable
 * snapshot is available — see lib/init-map-layers.ts.
 */
export async function resolveCommunityPmtilesUrl(origin: string): Promise<string | null> {
  const base = resolveStablePmtilesUrl(
    process.env.NEXT_PUBLIC_HEATMAP_URL || undefined, origin, IS_PROD,
  );
  if (!base) return null;
  const resolved = await resolvePmtilesViaPointer(base);
  return `pmtiles://${resolved}`;
}

/**
 * Get the PMTiles URL for the heatmap display file. Returns null if not available.
 * Display file (~10MB) contains: user_count, sport, heat_score (display only).
 * Routing uses a separate CTGB tile system (tippecanoe simplifies geometry,
 * which would break routing accuracy).
 */
// Session-memoized — the PMTiles file location doesn't change at runtime, and the HEAD
// probe was being repeated on every loadCommunityHeatmap call (sport switch, route mode, etc.)
let _pmtilesUrlPromise: Promise<string | null> | null = null;

export function getPmtilesUrl(): Promise<string | null> {
  if (_pmtilesUrlPromise) return _pmtilesUrlPromise;
  _pmtilesUrlPromise = (async () => {
    // Resolve the STABLE pmtiles URL, then hop to the immutable snapshot via the
    // freshness pointer so returning visitors aren't stuck on the 24 h-cached
    // stable name after a rebuild.
    //   1. NEXT_PUBLIC_HEATMAP_URL (PRD #391 — the public GCS bucket in prod).
    //   2. else, in DEV: NEXT_PUBLIC_CDN_URL || window.location.origin, joined
    //      to /heatmap-display.pmtiles (served from the frontend container).
    //   3. prod + unset → null + loud error (env-url.ts), never the SPA origin.
    // Falling back to API_URL would 404 (the backend doesn't serve this file).
    const devOrigin = CDN_BASE || (typeof window !== 'undefined' ? window.location.origin : undefined);
    const stable = resolveStablePmtilesUrl(HEATMAP_URL || undefined, devOrigin, IS_PROD);
    if (!stable) return null;
    const resolved = await resolvePmtilesViaPointer(stable);
    return `pmtiles://${resolved}`;
  })();
  // Don't cache failures — let next call retry
  _pmtilesUrlPromise.then((url) => { if (url === null) _pmtilesUrlPromise = null; });
  return _pmtilesUrlPromise;
}

/**
 * Get the MVT tile URL template for a given sport.
 * Falls back from PMTiles → CDN → API tiles.
 */
export async function getMvtTileUrl(sport: string, days?: number | null): Promise<string> {
  const manifest = await getManifest();
  const version = manifest?.version || Date.now().toString();
  const base = API_URL || (typeof window !== 'undefined' ? window.location.origin : '');
  const safeSport = sport === 'all' ? 'road' : sport;
  const params = new URLSearchParams({ v: version });
  if (days && days > 0) params.set('days', String(days));
  return `${base}/heatmap/tiles/${safeSport}/{z}/{x}/{y}.mvt?${params.toString()}`;
}

/** Fetch heatmap summary from CDN, fallback to API. */
export async function fetchSummaryCDN(): Promise<unknown> {
  const manifest = await getManifest();
  if (manifest?.files?.summary) {
    const url = `${CDN_BASE}/api-cache/${manifest.files.summary}`;
    try {
      const res = await fetch(url);
      if (res.ok) return res.json();
    } catch (e) {
      console.warn('CDN summary fetch failed, falling back to API:', e);
    }
  }
  return apiFallback(`${API_URL}/heatmap/summary`);
}

/** Check if CDN is configured and available. */
export function isCDNEnabled(): boolean {
  return CDN_BASE !== '';
}

/**
 * Fetch the homepage community stats (contributeurs / traces / km de chemins).
 *
 * Robustness is the whole point: the frontend is a static export and the API
 * runs on db-f1-micro with min-instances=0, so a per-visit DB call would
 * cold-start-timeout and blank the hero. We therefore read a pre-built static
 * `stats.json` (emitted alongside `heatmap-display.pmtiles` on every heatmap
 * rebuild) — DB-free, CDN-cached — and only fall back to the live API summary
 * if the static object is unreachable.
 *
 * Order: (1) `stats.json` next to the GCS PMTiles (prod), (2) `stats.json` on
 * the dev static origin, (3) the authoritative public prod bucket (prod host
 * only — deploy-resilient when `NEXT_PUBLIC_HEATMAP_URL` isn't baked in), then
 * (4) the `/heatmap/summary` API (last resort). Returns `null` on total
 * failure so the caller keeps its "—" placeholders. The candidate list is
 * built by the pure, unit-tested `buildStatsCandidates`.
 */
export async function fetchCommunityStats(): Promise<CommunityStats | null> {
  const candidates = buildStatsCandidates({
    heatmapUrl: HEATMAP_URL,
    cdnBase: CDN_BASE,
    origin: typeof window !== 'undefined' ? window.location.origin : undefined,
    hostname: typeof window !== 'undefined' ? window.location.hostname : undefined,
  });

  for (const url of candidates) {
    try {
      const res = await fetch(url);
      if (!res.ok) continue;
      const parsed = parseCommunityStats(await res.json());
      if (parsed) return parsed;
    } catch {
      /* try next candidate */
    }
  }
  // Last resort: the live API summary (older key names — parseCommunityStats
  // handles both shapes).
  return parseCommunityStats(await apiFallback(`${API_URL}/heatmap/summary`));
}
