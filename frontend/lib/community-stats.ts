/**
 * Community stats for the homepage hero banner (contributeurs / traces /
 * km de chemins).
 *
 * The numbers are computed at heatmap-BUILD time and published as a tiny
 * static `stats.json` next to `heatmap-display.pmtiles` (see
 * `backend/app/jobs/build_pmtiles.py::publish_stats_json`). The homepage
 * fetches that static object — NEVER the DB at request time — so a cold
 * db-f1-micro (min-instances=0) can never blank the hero. This module holds
 * the PURE parsing + formatting helpers (unit-tested); the impure fetch with
 * its GCS → dev-static → API fallback chain lives in `cdn-cache.ts`.
 */

export interface CommunityStats {
  contributors: number;
  traces: number;
  km: number;
}

/**
 * Canonical public prod heatmap base. The authoritative `stats.json` (and
 * `heatmap-display.pmtiles`) are served here, CORS-enabled
 * (`access-control-allow-origin:*`), via the first-party CDN domain fronting
 * the `common-trails-heatmap-prod` bucket (fixes ad-blocker/Firefox blocking of
 * `storage.googleapis.com` + a clean share URL). Used as a deploy-resilient
 * fallback: see {@link buildStatsCandidates}.
 */
export const PROD_HEATMAP_BUCKET = 'https://tiles.chemins-communs.fr';

/** Hostname(s) on which the prod-bucket fallback is allowed to fire. */
const PROD_HOST_RE = /(^|\.)chemins-communs\.fr$/;

/**
 * Build the ordered list of `stats.json` URLs to try before the API fallback.
 *
 * Kept pure (unit-tested) because the flagship hero numbers regressed once: on
 * prod the build shipped WITHOUT `NEXT_PUBLIC_HEATMAP_URL` baked in, so the
 * only remaining candidate was the same-origin `${origin}/stats.json` — which
 * the backend answers with the SPA fallback HTML (200), not JSON. The fetch
 * then fell through to the live `/heatmap/summary`, whose SUM-over-sub-edges
 * inflates km (35 106 vs the authoritative 16 986) and under-counts
 * contributors. This helper adds the authoritative public-bucket `stats.json`
 * as an explicit candidate on the prod host, so the correct numbers show even
 * when the env var isn't set. It is GATED to the prod host so dev/local never
 * surfaces prod numbers.
 *
 * Order: (1) sibling of `NEXT_PUBLIC_HEATMAP_URL`, (2) same-origin/CDN
 * `stats.json`, (3) the public prod bucket (prod host only). De-duplicated,
 * order-preserving.
 */
export function buildStatsCandidates(opts: {
  /** `NEXT_PUBLIC_HEATMAP_URL` — the full `.../heatmap-display.pmtiles` URL. */
  heatmapUrl?: string;
  /** `NEXT_PUBLIC_CDN_URL`. */
  cdnBase?: string;
  /** `window.location.origin` (undefined under SSR/build). */
  origin?: string;
  /** `window.location.hostname` (undefined under SSR/build). */
  hostname?: string;
}): string[] {
  const { heatmapUrl, cdnBase, origin, hostname } = opts;
  const out: string[] = [];
  // (1) Sibling stats.json next to the configured PMTiles binary.
  if (heatmapUrl) out.push(heatmapUrl.replace(/[^/]*$/, 'stats.json'));
  // (2) Same-origin (dev static export) or CDN base.
  const base = cdnBase || origin || '';
  if (base) out.push(`${base}/stats.json`);
  // (3) Deploy-resilient authoritative fallback — prod host only.
  if (hostname && PROD_HOST_RE.test(hostname)) {
    out.push(`${PROD_HEATMAP_BUCKET}/stats.json`);
  }
  return out.filter((u, i) => out.indexOf(u) === i);
}

/**
 * Normalise a stats payload into {@link CommunityStats}.
 *
 * Accepts BOTH shapes so a single home-page code path works whether the data
 * came from the static `stats.json` (`contributors` / `traces` / `km`) or the
 * legacy `/heatmap/summary` API fallback (`total_contributors` /
 * `total_activities` / `total_km`).
 *
 * Returns `null` when the payload carries none of the recognised numeric
 * fields, so the hero keeps its "—" placeholder rather than rendering zeros.
 */
export function parseCommunityStats(data: unknown): CommunityStats | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  const pick = (...keys: string[]): number | null => {
    for (const k of keys) {
      const v = d[k];
      if (typeof v === 'number' && Number.isFinite(v)) return v;
    }
    return null;
  };
  const contributors = pick('contributors', 'total_contributors');
  const traces = pick('traces', 'total_activities');
  const km = pick('km', 'total_km');
  if (contributors === null && traces === null && km === null) return null;
  return {
    contributors: contributors ?? 0,
    traces: traces ?? 0,
    km: km ?? 0,
  };
}

/**
 * Locale-aware integer formatting for a single banner counter.
 *
 * - `null` / `undefined` / non-finite → "—" (loading or error — never a
 *   layout-shifting blank or a misleading 0).
 * - fr → grouped with the French thousands separator (narrow no-break space,
 *   e.g. "12 400"); en → "12,400".
 */
export function formatStatValue(
  value: number | null | undefined,
  locale: string,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '—';
  }
  return value.toLocaleString(locale === 'en' ? 'en-GB' : 'fr-FR');
}
