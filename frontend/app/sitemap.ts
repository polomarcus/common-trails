import type { MetadataRoute } from 'next';

// Required by Next.js static export — sitemap is regenerated at build time only.
export const dynamic = 'force-static';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://chemins-communs.fr';
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'https://api.chemins-communs.fr';

const STATIC_ROUTES: MetadataRoute.Sitemap = [
  { url: `${SITE_URL}/`, priority: 1.0, changeFrequency: 'weekly' },
  { url: `${SITE_URL}/map`, priority: 0.9, changeFrequency: 'weekly' },
  { url: `${SITE_URL}/stats`, priority: 0.7, changeFrequency: 'weekly' },
  { url: `${SITE_URL}/methode`, priority: 0.5, changeFrequency: 'monthly' },
  { url: `${SITE_URL}/privacy`, priority: 0.3, changeFrequency: 'yearly' },
  { url: `${SITE_URL}/support`, priority: 0.3, changeFrequency: 'yearly' },
];

interface RouteSummary {
  id: string;
  updated_at?: string | null;
  created_at?: string | null;
}

/**
 * Pulls public routes from the backend at build time and includes their
 * share URLs in the sitemap. Best-effort — if the API is unreachable
 * during build the static fallback list still ships.
 *
 * The shared URL today is `/routes?id={uuid}` (static export single page
 * with a query param). Search engines tolerate this but won't treat it
 * as a path. Worth migrating to `/routes/{id}` post-beta.
 */
async function fetchPublicRoutes(): Promise<MetadataRoute.Sitemap> {
  try {
    const resp = await fetch(`${API_URL}/routes?visibility=public&limit=500`, {
      // Build-time fetch — no cookies, short timeout to fail fast
      headers: { Accept: 'application/json' },
      signal: AbortSignal.timeout(5000),
    });
    if (!resp.ok) return [];
    const routes: RouteSummary[] = await resp.json();
    return routes.map((r) => ({
      url: `${SITE_URL}/routes?id=${r.id}`,
      lastModified: r.updated_at || r.created_at || undefined,
      priority: 0.6,
      changeFrequency: 'monthly' as const,
    }));
  } catch {
    return [];
  }
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const routes = await fetchPublicRoutes();
  return [...STATIC_ROUTES, ...routes];
}
