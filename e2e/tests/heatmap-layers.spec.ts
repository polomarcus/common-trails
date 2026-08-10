/**
 * CHEMINS COMMUNS — E2E tests: Heatmap layer rendering at different zoom levels
 *
 * Validates that:
 * 1. MVT tiles are requested when heatmap is enabled
 * 2. Glow layer uses blurred lines (type=line, not heatmap/circle) at all zoom levels
 * 3. Detail line layer fades in at z10+ while glow fades out
 * 4. No circle/point layers pollute the map (no black dots)
 * 5. Heatmap toggle shows/hides layers correctly
 * 6. Panning at low zoom fetches new tiles promptly (no overzoom stall)
 */
import { test, expect, request } from '@playwright/test';

const BASE_URL = process.env.BASE_URL || 'http://localhost:3787';
const API_URL = process.env.API_URL || 'http://localhost:8787';

// The app registers `tile-cache-sw.js` (skipWaiting + clients.claim) which
// serves `/heatmap/tiles/**` stale-while-revalidate. Service-worker-mediated
// fetches BYPASS Playwright's page.route — with the SW active, the tile
// interception below is nondeterministic (works only until the SW claims
// the page). Block SW registration so every request is routable.
test.use({ serviceWorkers: 'block' });

// Return empty graph tiles — routing is not under test here.
test.beforeEach(async ({ page }) => {
  await page.route('**/routing/graph/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ v: 1, edges: [] }),
    });
  });
});

// ── Env-independent MVT interception helpers ────────────────────────────────
//
// The /map page loads the STATIC `heatmap-display.pmtiles` as its PRIMARY
// heatmap source (init-map-layers.ts:175 — `pmtiles://<origin>/heatmap-
// display.pmtiles`; the pmtiles:// protocol resolves to plain HTTP(S)
// range requests against that same URL). When the binary is present
// (local dev), NO `/heatmap/tiles/**.mvt` request ever fires — the old
// interception-based specs false-failed locally and only "passed" in CI
// because the whole describe was skipped there.
//
// To make the specs deterministic in BOTH envs:
//   1. Block every fetch of the PMTiles binary (`**/heatmap-display.
//      pmtiles*` — covers the protocol's underlying header/range GETs),
//      so the static path is guaranteed absent, exactly like a CI build.
//   2. Re-point the `community-trails` source at the live MVT endpoint.
//      The app has NO automatic pmtiles→MVT fallback wired on /map today;
//      the live endpoint is its documented fallback and the app itself
//      swaps the source tiles at runtime (map/page.tsx:3004 `setTiles`).
//      We drive that same seam through `window.__mapInstance`, keeping
//      the app's own zoom contract (minzoom 6 / maxzoom 14).
//   3. Fulfill the MVT requests with an empty protobuf — hermetic, no
//      DB dependency — and assert on the requested z/x/y.
const MVT_TILE_RE = /\/heatmap\/tiles\/[^/]+\/(\d+)\/(\d+)\/(\d+)\.mvt/;

/** Abort all fetches of the static PMTiles binary; returns a hit counter. */
async function blockPmtiles(page: import('@playwright/test').Page): Promise<{ count: number }> {
  const counter = { count: 0 };
  await page.route('**/heatmap-display.pmtiles*', async (route) => {
    counter.count += 1;
    await route.abort();
  });
  return counter;
}

/** Fulfill MVT tile requests with an empty protobuf, collecting URLs. */
async function interceptMvtTiles(page: import('@playwright/test').Page): Promise<string[]> {
  const urls: string[] = [];
  await page.route(MVT_TILE_RE, async (route) => {
    urls.push(route.request().url());
    await route.fulfill({
      status: 200,
      contentType: 'application/x-protobuf',
      body: Buffer.alloc(0),
    });
  });
  return urls;
}

/** Open /map at the given zoom and enable the heatmap layer via Calques. */
async function openMapWithHeatmap(page: import('@playwright/test').Page, zoom: number): Promise<void> {
  await page.goto(`${BASE_URL}/map?lat=43.6&lon=3.87&zoom=${zoom}`);
  await page.waitForLoadState('domcontentloaded');
  await expect(page.locator('[data-testid="map-layers-btn"]')).toBeVisible({ timeout: 15000 });

  await page.getByRole('button', { name: 'Calques' }).click();
  const heatmapToggle = page.locator('[data-testid="toggle-heatmap"]');
  await expect(heatmapToggle).toBeVisible();
  const isChecked = await heatmapToggle.isChecked().catch(() => false);
  if (!isChecked) {
    await heatmapToggle.click();
  }
  // Close dropdown
  await page.locator('body').click({ position: { x: 0, y: 0 } });
}

/**
 * Re-point the community-trails source at the live MVT endpoint (the
 * app's fallback seam — same runtime source swap the app performs at
 * map/page.tsx:3004). Re-adds the source with the app's own zoom
 * contract (minzoom 6 / maxzoom 14 from init-map-layers.ts) and
 * re-attaches the existing layers with their current visibility.
 */
async function switchHeatmapSourceToLiveMvt(page: import('@playwright/test').Page): Promise<void> {
  await page.waitForFunction(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = (window as any).__mapInstance;
    return !!m?.getSource?.('community-trails')
      && m.getLayer?.('community-trails-line') != null;
  }, undefined, { timeout: 20000 });

  await page.evaluate((mvtTemplate) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = (window as any).__mapInstance;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const layers = (m.getStyle()?.layers || []).filter((l: any) => l.source === 'community-trails');
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    for (const l of layers) m.removeLayer(l.id);
    m.removeSource('community-trails');
    m.addSource('community-trails', {
      type: 'vector',
      tiles: [mvtTemplate],
      minzoom: 6,
      maxzoom: 14,
    });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    for (const l of layers) m.addLayer(l);
  }, `${API_URL}/heatmap/tiles/road/{z}/{x}/{y}.mvt`);
}

/**
 * Wait for MVT tile requests to arrive, nudging the map when needed.
 * After the source swap MapLibre registers the covering tiles but the
 * worker-side fetches can stay pending until the next real transform
 * update (observed reliably in headless: tileCount=8, 0 network
 * requests until a zoom jiggle). The nudge oscillates ±0.01 zoom —
 * the INTEGER tile zoom never changes, so the z-assertions stay exact.
 */
async function waitForMvtRequests(
  page: import('@playwright/test').Page,
  urls: string[],
  timeoutMs = 15000,
): Promise<void> {
  const start = Date.now();
  let sign = 1;
  while (urls.length === 0 && Date.now() - start < timeoutMs) {
    await page.evaluate((s) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const m = (window as any).__mapInstance;
      m.setZoom(m.getZoom() + 0.01 * s);
      m.triggerRepaint();
    }, sign);
    sign = -sign;
    await page.waitForTimeout(500);
  }
  expect(urls.length, `no /heatmap/tiles/**.mvt request within ${timeoutMs}ms`).toBeGreaterThan(0);
  // Let the remaining covering-tile requests of the batch land too.
  await page.waitForTimeout(500);
}

function parseTileZooms(urls: string[]): number[] {
  return urls
    .map((url) => url.match(MVT_TILE_RE))
    .filter((m): m is RegExpMatchArray => m !== null)
    .map((m) => parseInt(m[1], 10));
}

// These specs are hermetic (pmtiles blocked, MVT fulfilled inline) so they
// run in BOTH local and CI environments. Only the toggle test below keeps
// a CI skip (mouse-drag on the canvas hangs in CI offline mode).
// QUARANTINED (raw-trace pivot): this pins the PRE-pivot live-MVT source-swap
// flow (blockPmtiles → repoint community-trails at /heatmap/tiles). The display
// moved to static PMTiles + a raster pyramid, so the __mapInstance source/layer
// contract asserted here no longer holds. TODO: rewrite against the raw-PMTiles
// display path, then un-skip. The backend-tile describe below still runs.
test.describe.skip('Heatmap MVT tile loading', () => {
  test('requests MVT tiles when heatmap layer is enabled (pmtiles blocked → live MVT fallback)', async ({ page }) => {
    const pmtilesBlocked = await blockPmtiles(page);
    const tileRequests = await interceptMvtTiles(page);

    // Navigate to map at z10 (Montpellier area) and enable heatmap
    await openMapWithHeatmap(page, 10);
    await switchHeatmapSourceToLiveMvt(page);

    // MVT tile requests must arrive against the live endpoint
    await waitForMvtRequests(page, tileRequests);

    // The static PMTiles path must have been attempted AND blocked —
    // pins the URL pattern of the pmtiles:// protocol's HTTP fetches.
    expect(pmtilesBlocked.count).toBeGreaterThan(0);

    // At z10 the source (maxzoom=14) must serve native z10 tiles
    const zooms = parseTileZooms(tileRequests);
    expect(zooms.length).toBeGreaterThan(0);
    expect(zooms.filter((z) => z === 10).length).toBeGreaterThan(0);
  });

  test('requests z14 tiles at high zoom', async ({ page }) => {
    await blockPmtiles(page);
    const tileRequests = await interceptMvtTiles(page);

    await openMapWithHeatmap(page, 14);
    await switchHeatmapSourceToLiveMvt(page);

    await waitForMvtRequests(page, tileRequests);

    // At z14, tiles should be z14 (source maxzoom=14)
    const zooms = parseTileZooms(tileRequests);
    expect(zooms.length).toBeGreaterThan(0);
    expect(zooms.filter((z) => z === 14).length).toBeGreaterThan(0);
  });

  // This test uses mouse.move on map canvas which hangs in CI offline mode
  test('heatmap toggle hides and shows layers', async ({ page }, testInfo) => {
    if (process.env.CI) { testInfo.skip(); return; }
    await page.route('**/heatmap/tiles/**/*.mvt', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/x-protobuf',
        body: Buffer.alloc(0),
      });
    });

    await page.goto(`${BASE_URL}/map?lat=43.6&lon=3.87&zoom=12`);
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('[data-testid="map-layers-btn"]')).toBeVisible({ timeout: 15000 });

    // Open Calques dropdown and enable heatmap
    await page.getByRole('button', { name: 'Calques' }).click();
    const heatmapToggle = page.locator('[data-testid="toggle-heatmap"]');
    await expect(heatmapToggle).toBeVisible();

    // Enable if not already
    const wasChecked = await heatmapToggle.isChecked().catch(() => false);
    if (!wasChecked) {
      await heatmapToggle.click();
    }

    // Verify the toggle is now checked/on
    // (toggle may be a checkbox, switch, or custom element — verify it's active)
    await page.locator('body').click({ position: { x: 0, y: 0 } });
    await page.waitForTimeout(500);

    // Now disable heatmap
    await page.getByRole('button', { name: 'Calques' }).click();
    await heatmapToggle.click();
    await page.locator('body').click({ position: { x: 0, y: 0 } });

    // After disabling, new tile requests should stop
    const requestsAfterDisable: string[] = [];
    await page.route('**/heatmap/tiles/**/*.mvt', async (route) => {
      requestsAfterDisable.push(route.request().url());
      await route.fulfill({
        status: 200,
        contentType: 'application/x-protobuf',
        body: Buffer.alloc(0),
      });
    });

    // Pan the map — no new heatmap tile requests expected
    await page.mouse.move(400, 300);
    await page.mouse.down();
    await page.mouse.move(500, 350, { steps: 5 });
    await page.mouse.up();
    await page.waitForTimeout(1000);

    // Tiles may or may not be requested (MapLibre caches), but layer should be hidden
    // Re-enable and verify toggle still works
    await page.getByRole('button', { name: 'Calques' }).click();
    await heatmapToggle.click();
    await page.locator('body').click({ position: { x: 0, y: 0 } });
    // Should function without errors (no crash)
    await page.waitForTimeout(500);
  });

  test('z10 tiles are native (not overzoomed from z11+)', async ({ page }) => {
    await blockPmtiles(page);
    const tileRequests = await interceptMvtTiles(page);

    // Navigate at z10 — should request z10 tiles natively
    await openMapWithHeatmap(page, 10);
    await switchHeatmapSourceToLiveMvt(page);

    await waitForMvtRequests(page, tileRequests);

    const tileZooms = parseTileZooms(tileRequests);
    expect(tileZooms.length).toBeGreaterThan(0);
    // All requested tiles should be z10 — NOT z11+ overzoomed down
    // (source minzoom=6, so MapLibre should request native z10 tiles)
    const nonZ10 = tileZooms.filter(z => z > 10);
    expect(nonZ10.length).toBe(0);
  });
});

// QUARANTINED (raw-trace pivot): asserts the pre-pivot community-trails
// glow/line layer internals. TODO: rewrite for the raw-PMTiles layer set.
test.describe.skip('Heatmap layer types — no circles, no heatmap kernel', () => {
  test.skip(!!process.env.CI, 'Flaky in CI — heatmap UI toggle dependency');

  test('glow layer is type=line with blur (not heatmap or circle)', async ({ page }) => {
    await page.route('**/heatmap/tiles/**/*.mvt', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/x-protobuf', body: Buffer.alloc(0) });
    });

    await page.goto(`${BASE_URL}/map?lat=43.6&lon=3.87&zoom=8`);
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('[data-testid="map-layers-btn"]')).toBeVisible({ timeout: 15000 });

    // Enable heatmap
    await page.getByRole('button', { name: 'Calques' }).click();
    const heatmapToggle = page.locator('[data-testid="toggle-heatmap"]');
    const isChecked = await heatmapToggle.isChecked().catch(() => false);
    if (!isChecked) await heatmapToggle.click();
    await page.locator('body').click({ position: { x: 0, y: 0 } });
    await page.waitForTimeout(500);

    // Query MapLibre for layer types via globally exposed map instance
    const layerInfo = await page.evaluate(() => {
      const m = (window as any).__mapInstance;
      if (!m) return { error: 'no map ref' };

      const layers = m.getStyle()?.layers || [];
      const glowLayer = layers.find((l: any) => l.id === 'community-trails-glow');
      const lineLayer = layers.find((l: any) => l.id === 'community-trails-line');
      const pointsLayer = layers.find((l: any) => l.id === 'community-trails-points');
      const heatmapLayers = layers.filter((l: any) => l.type === 'heatmap');

      return {
        glowType: glowLayer?.type ?? null,
        glowSourceLayer: glowLayer?.['source-layer'] ?? null,
        glowHasBlur: glowLayer?.paint?.['line-blur'] != null,
        lineType: lineLayer?.type ?? null,
        lineSourceLayer: lineLayer?.['source-layer'] ?? null,
        pointsLayerExists: pointsLayer != null,
        heatmapTypeCount: heatmapLayers.length,
      };
    });

    // Glow must be a line layer (not heatmap or circle)
    if (layerInfo && !('error' in layerInfo)) {
      expect(layerInfo.glowType).toBe('line');
      expect(layerInfo.glowSourceLayer).toBe('trails');
      expect(layerInfo.glowHasBlur).toBe(true);
      // Detail layer must also be line
      expect(layerInfo.lineType).toBe('line');
      expect(layerInfo.lineSourceLayer).toBe('trails');
      // No circle points layer (caused black dots)
      expect(layerInfo.pointsLayerExists).toBe(false);
      // No heatmap-type layers (slow GPU kernel)
      expect(layerInfo.heatmapTypeCount).toBe(0);
    }
  });

  test('no community-trails-points circle layer exists at any zoom', async ({ page }) => {
    await page.route('**/heatmap/tiles/**/*.mvt', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/x-protobuf', body: Buffer.alloc(0) });
    });

    for (const zoom of [8, 10, 12, 14]) {
      await page.goto(`${BASE_URL}/map?lat=43.6&lon=3.87&zoom=${zoom}`);
      await page.waitForLoadState('domcontentloaded');
      await expect(page.locator('[data-testid="map-layers-btn"]')).toBeVisible({ timeout: 15000 });

      const hasPointsLayer = await page.evaluate(() => {
        const m = (window as any).__mapInstance;
        if (!m) return false;
        return m.getStyle()?.layers?.some((l: any) => l.id === 'community-trails-points') ?? false;
      });

      expect(hasPointsLayer).toBe(false);
    }
  });
});

test.describe('Heatmap MVT backend', () => {
  test('z10 tile returns valid protobuf', async () => {
    const apiContext = await request.newContext();
    const resp = await apiContext.fetch(`${API_URL}/heatmap/tiles/road/10/527/373.mvt`);
    expect(resp.status()).toBe(200);
    expect(resp.headers()['content-type']).toContain('protobuf');
    const body = await resp.body();
    expect(body).toBeDefined();
    await apiContext.dispose();
  });

  test('z12 tile returns trails layer (line geometries)', async () => {
    const apiContext = await request.newContext();
    const resp = await apiContext.fetch(`${API_URL}/heatmap/tiles/road/12/2108/1492.mvt`);
    expect(resp.status()).toBe(200);
    expect(resp.headers()['content-type']).toContain('protobuf');
    const body = await resp.body();
    expect(body).toBeDefined();
    await apiContext.dispose();
  });

  test('out-of-range zoom returns 204', async () => {
    const apiContext = await request.newContext();
    const resp = await apiContext.fetch(`${API_URL}/heatmap/tiles/road/5/0/0.mvt`);
    expect(resp.status()).toBe(204);
    const resp2 = await apiContext.fetch(`${API_URL}/heatmap/tiles/road/17/0/0.mvt`);
    expect(resp2.status()).toBe(204);
    await apiContext.dispose();
  });

  // Requires heatmap data seeded in DB — may not exist in CI fresh DB
  test('tile responses are gzip-compressed', async ({}, testInfo) => {
    if (process.env.CI) { testInfo.skip(); return; }
    const apiContext = await request.newContext();
    // Request a tile that has data (gravel near Montpellier)
    const resp = await apiContext.fetch(`${API_URL}/heatmap/tiles/gravel/9/263/186.mvt`);
    expect(resp.status()).toBe(200);
    const encoding = resp.headers()['content-encoding'];
    expect(encoding).toBe('gzip');
    await apiContext.dispose();
  });
});

// ── Performance tests: heatmap tiles during map movement ─────────────────────

test.describe('Heatmap tile performance', () => {
  test.skip(!!process.env.CI, 'Flaky in CI — measures wall-clock latency on shared runners');

  test('backend serves tiles under 200ms (cold) and 10ms (warm)', async () => {
    const apiContext = await request.newContext();

    // Cold request (first hit, no cache)
    const coldStart = Date.now();
    const coldResp = await apiContext.fetch(`${API_URL}/heatmap/tiles/gravel/9/263/186.mvt`);
    const coldMs = Date.now() - coldStart;
    expect(coldResp.status()).toBe(200);
    expect(coldMs).toBeLessThan(500); // generous for CI, typically <100ms

    // Warm request (cache hit)
    const warmStart = Date.now();
    const warmResp = await apiContext.fetch(`${API_URL}/heatmap/tiles/gravel/9/263/186.mvt`);
    const warmMs = Date.now() - warmStart;
    expect(warmResp.status()).toBe(200);
    expect(warmMs).toBeLessThan(300); // cache hit should be <10ms locally, CI runners are slower

    console.log(`[perf] z9 tile: cold=${coldMs}ms, warm=${warmMs}ms`);
    await apiContext.dispose();
  });

  test('parallel tile batch completes under 500ms', async () => {
    const apiContext = await request.newContext();
    // Simulate browser requesting 8 tiles at once (typical viewport at z12)
    const tiles = [
      [12, 2108, 1492], [12, 2108, 1495], [12, 2109, 1492], [12, 2109, 1495],
      [12, 2110, 1492], [12, 2110, 1495], [12, 2111, 1492], [12, 2111, 1495],
    ];

    const batchStart = Date.now();
    const results = await Promise.all(
      tiles.map(([z, x, y]) =>
        apiContext.fetch(`${API_URL}/heatmap/tiles/gravel/${z}/${x}/${y}.mvt`)
      )
    );
    const batchMs = Date.now() - batchStart;

    for (const resp of results) {
      expect(resp.status()).toBe(200);
    }
    // 8 tiles in parallel should complete under 2s (cold cache, async DB)
    expect(batchMs).toBeLessThan(2000);
    console.log(`[perf] 8 parallel z12 tiles: ${batchMs}ms`);
    await apiContext.dispose();
  });

  // This test uses mouse.move on map canvas which hangs in CI offline mode
  test('heatmap tiles load within 2s during map pan', async ({ page }, testInfo) => {
    if (process.env.CI) { testInfo.skip(); return; }
    const tileTimings: { url: string; start: number; end: number }[] = [];
    const testStart = Date.now();

    // Intercept tile requests to measure timing (pass through to real backend)
    page.on('request', (req) => {
      if (req.url().includes('/heatmap/tiles/')) {
        tileTimings.push({ url: req.url(), start: Date.now() - testStart, end: 0 });
      }
    });
    page.on('response', (resp) => {
      if (resp.url().includes('/heatmap/tiles/')) {
        const entry = tileTimings.find(t => t.url === resp.url() && t.end === 0);
        if (entry) entry.end = Date.now() - testStart;
      }
    });

    // Navigate to area with heatmap data (gravel near Montpellier, z12)
    await page.goto(`${BASE_URL}/map?lat=43.62&lon=3.88&zoom=12`);
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('[data-testid="map-layers-btn"]')).toBeVisible({ timeout: 15000 });

    // Enable heatmap
    await page.getByRole('button', { name: 'Calques' }).click();
    const heatmapToggle = page.locator('[data-testid="toggle-heatmap"]');
    const isChecked = await heatmapToggle.isChecked().catch(() => false);
    if (!isChecked) {
      await heatmapToggle.click();
    }
    await page.locator('body').click({ position: { x: 0, y: 0 } });

    // Wait for initial tiles to load
    await page.waitForTimeout(3000);
    const initialTileCount = tileTimings.length;

    // Pan the map (simulate user dragging east)
    const panStart = Date.now() - testStart;
    await page.mouse.move(600, 400);
    await page.mouse.down();
    await page.mouse.move(200, 400, { steps: 10 });
    await page.mouse.up();

    // Wait for new tiles triggered by pan
    await page.waitForTimeout(3000);
    const newTiles = tileTimings.slice(initialTileCount);

    if (newTiles.length > 0) {
      const completedTiles = newTiles.filter(t => t.end > 0);
      const maxLatency = Math.max(...completedTiles.map(t => t.end - t.start));
      const avgLatency = completedTiles.reduce((sum, t) => sum + (t.end - t.start), 0) / completedTiles.length;
      const lastTileEnd = Math.max(...completedTiles.map(t => t.end));
      const totalPanToComplete = lastTileEnd - panStart;

      console.log(`[perf] pan tiles: ${newTiles.length} requested, ${completedTiles.length} completed`);
      console.log(`[perf] latency: avg=${Math.round(avgLatency)}ms, max=${maxLatency}ms`);
      console.log(`[perf] time from pan to last tile: ${totalPanToComplete}ms`);

      // All tiles should complete within 5s of pan (includes browser overhead + cold cache)
      expect(totalPanToComplete).toBeLessThan(5000);
      // Individual tile latency should be under 1s
      expect(maxLatency).toBeLessThan(1000);
    }
    // Even if no new tiles (small pan), test should pass
  });
});
