/**
 * The ONE way the app loads maplibre-gl (every map: /map, the home hero, the
 * trace/trip maps, the pmtiles:// protocol registration).
 *
 * maplibre-gl 6 ships as ES modules only, which changed two things:
 *   - NO default export any more. `(await import('maplibre-gl')).default` is
 *     `undefined`, so `new maplibregl.default.Map(…)` threw and no map was ever
 *     built (Dependabot PR #105: every map e2e spec timed out waiting for
 *     `__mapInstance`). Use the module namespace: `ml.Map`, `ml.Marker`…
 *   - The worker is no longer inlined as a blob: maplibre loads
 *     `maplibre-gl-worker.mjs` as a module worker, and under a bundler its own
 *     guess (a file next to the bundled chunk) does not exist. The bundler must
 *     emit the worker — `new URL('<package path>', import.meta.url)` is the
 *     pattern Turbopack (and webpack) turn into a hashed static asset — and we
 *     hand maplibre a URL for it with `setWorkerUrl()` before the first map.
 *
 * Why a blob: URL and not the asset URL itself: a worker started from an
 * http(s) URL runs under the Content-Security-Policy of THAT response, and the
 * backend serving the export in prod gives every non-HTML response the API
 * policy `default-src 'none'` (app/main.py). Under it the worker cannot fetch
 * anything: the basemap's vector tiles (fetched inside the worker) never load.
 * A blob: worker inherits the PAGE's policy instead — exactly how the 4.x
 * worker (an inlined blob) ran. The blob only `import`s the emitted asset,
 * which is what maplibre itself does for a cross-origin worker URL.
 *
 * The import is memoised: one module instance, one `setWorkerUrl` call. The
 * blob URL is never revoked — the shared worker pool is torn down when the last
 * map is removed and rebuilt (from this URL) by the next one.
 */
export type MapLibreModule = typeof import('maplibre-gl');

let loading: Promise<MapLibreModule> | null = null;

/** Worker URL for `setWorkerUrl`: a page-CSP blob: module importing the asset. */
export function maplibreWorkerUrl(): string {
  const asset = new URL('maplibre-gl/dist/maplibre-gl-worker.mjs', import.meta.url).href;
  if (typeof Blob === 'undefined' || typeof URL.createObjectURL !== 'function') return asset;
  // A blob: module has no base URL for a relative specifier: import it absolute.
  const absolute = new URL(asset, globalThis.location?.href).href;
  return URL.createObjectURL(
    new Blob([`import ${JSON.stringify(absolute)};`], { type: 'text/javascript' }),
  );
}

export function loadMaplibre(): Promise<MapLibreModule> {
  if (!loading) {
    loading = import('maplibre-gl').then((ml) => {
      ml.setWorkerUrl(maplibreWorkerUrl());
      return ml;
    });
    // A failed chunk load must not be cached forever: the next caller retries.
    loading.catch(() => { loading = null; });
  }
  return loading;
}
