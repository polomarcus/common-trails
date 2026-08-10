/**
 * Service Worker for heatmap MVT tile caching.
 *
 * Caches heatmap tiles in a dedicated Cache Storage bucket with
 * stale-while-revalidate strategy. Tiles are served from cache instantly
 * while a background fetch updates the cache for next time.
 *
 * Only intercepts /heatmap/tiles/ requests — all other requests pass through.
 */

const CACHE_NAME = 'ct-heatmap-tiles-v1';
const MAX_CACHE_ENTRIES = 3000;
const TILE_URL_PATTERN = /\/heatmap\/tiles\//;

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  // Clean up old cache versions
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k.startsWith('ct-heatmap-tiles-') && k !== CACHE_NAME)
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = event.request.url;

  // Only cache heatmap tile requests
  if (!TILE_URL_PATTERN.test(url)) return;

  event.respondWith(
    caches.open(CACHE_NAME).then(async (cache) => {
      const cached = await cache.match(event.request);

      // Stale-while-revalidate: serve cached response immediately,
      // then update cache in background
      const fetchPromise = fetch(event.request).then((response) => {
        if (response.ok) {
          cache.put(event.request, response.clone());
          // Evict oldest entries if cache is too large
          evictOldEntries(cache);
        }
        return response;
      }).catch(() => {
        // Network failure — return cached version if available
        return cached || new Response('', { status: 504 });
      });

      // Return cached response instantly, or wait for network
      return cached || fetchPromise;
    })
  );
});

async function evictOldEntries(cache) {
  const keys = await cache.keys();
  if (keys.length > MAX_CACHE_ENTRIES) {
    // Delete oldest entries (first in list = oldest)
    const toDelete = keys.length - MAX_CACHE_ENTRIES;
    for (let i = 0; i < toDelete; i++) {
      await cache.delete(keys[i]);
    }
  }
}
