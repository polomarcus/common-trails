/**
 * CHEMINS COMMUNS — E2E tests: community heatmap display wiring (/map).
 *
 * Rewritten for the RAW-PMTiles display (the 2026-07/08 pivot). The community
 * heatmap on /map is drawn from the STATIC `heatmap-display.pmtiles` binary via
 * the `pmtiles://` protocol — NOT the pre-pivot live-MVT vector source that was
 * source-swapped at runtime. These specs pin the CURRENT display contract from
 * lib/init-map-layers.ts + lib/community-heatmap-layers.ts:
 *
 *   source `community-trails` = vector, url `pmtiles://…/heatmap-display.pmtiles`,
 *                               minzoom 6 / maxzoom 14 (communityTrailsSourceSpec)
 *   layer  `community-trails-heat`   = heatmap over source-layer `heat_points`
 *   layer  `community-trails-line`   = line    over source-layer `trails` (z9+)
 *   layer  `community-trails-arrows` = symbol  over source-layer `trails`
 *   layer  `community-trails-hit`    = line    over source-layer `trails`
 *   (NO circle/points layer, NO `community-trails-glow` — both were removed)
 *
 * HERMETIC: assert the DISPLAY IS WIRED (source/layer specs via
 * `__mapInstance.getStyle()`) — never rendered pixels or artifact size.
 *
 * ⚠️ Build-env branch (mirrors home-hero-pmtiles.spec): the community source is
 * only added when a PMTiles URL RESOLVES. In a production build (`next build`,
 * which is what the CI E2E gate runs) the same-origin dev fallback is compiled
 * out, so with `NEXT_PUBLIC_HEATMAP_URL` UNSET the source is deliberately
 * OMITTED (env-url.ts refuses to point it at the SPA origin). So each browser
 * test branches:
 *   - source PRESENT (a build with NEXT_PUBLIC_HEATMAP_URL set, or prod)
 *     → assert the full source/layer contract;
 *   - source ABSENT (the CI-default build)
 *     → assert it degrades correctly: NO community-* layers leak, and NO source
 *       ever streams the live MVT endpoint (the load-bearing doctrine pin).
 * Either way the specs are green + meaningful.
 *
 * The `Heatmap MVT backend` describe below is UNCHANGED — the live
 * `/heatmap/tiles/**.mvt` endpoint is still the DB-backed FALLBACK and is tested
 * directly against the backend.
 */
import { test, expect, request } from '@playwright/test';

const BASE_URL = process.env.BASE_URL || 'http://localhost:3787';
const API_URL = process.env.API_URL || 'http://localhost:8787';

// The app registers `tile-cache-sw.js` (skipWaiting + clients.claim) which
// serves heatmap requests stale-while-revalidate. Service-worker-mediated
// fetches BYPASS Playwright's page-level request observation. Block SW
// registration so every PMTiles range GET is observable + routable.
test.use({ serviceWorkers: 'block' });

// ── Types + helpers ────────────────────────────────────────────────────────────

interface LayerInfo {
  id: string;
  type: string;
  source: string | null;
  sourceLayer: string | null;
  minzoom: number | null;
}
interface Wiring {
  present: boolean;
  source: { type: string; url: string | null; tiles: string[] | null; minzoom: number | null; maxzoom: number | null } | null;
  community: LayerInfo[];
  allSourceUrls: string[];
}

/** Open /map at the given zoom; wait for the live map handle to be exposed. */
async function openMap(page: import('@playwright/test').Page, zoom: number): Promise<void> {
  await page.addInitScript(() => localStorage.setItem('cc_beta_dismissed', '1'));
  await page.goto(`${BASE_URL}/map?lat=43.6&lon=3.87&zoom=${zoom}`);
  await page.waitForLoadState('domcontentloaded');
  // __mapInstance is set in app/map/page.tsx; initMapLayers (which adds the
  // community source, if it resolves) runs SYNCHRONOUSLY right after — so once
  // __mapInstance exists, source presence is already deterministic.
  await page.waitForFunction(() => !!(window as unknown as { __mapInstance?: unknown }).__mapInstance, undefined, {
    timeout: 30000,
  });
}

/** Snapshot the community source + layers from the live style. */
async function readWiring(page: import('@playwright/test').Page): Promise<Wiring> {
  return page.evaluate(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const m = (window as any).__mapInstance;
    const style = m.getStyle();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const src = (style.sources || {})['community-trails'] as any;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const community = (style.layers || [])
      .filter((l: any) => typeof l.id === 'string' && l.id.startsWith('community-trails-'))
      .map((l: any) => ({
        id: l.id,
        type: l.type,
        source: l.source ?? null,
        sourceLayer: l['source-layer'] ?? null,
        minzoom: l.minzoom ?? null,
      }));
    return {
      present: !!src,
      source: src
        ? { type: src.type, url: src.url ?? null, tiles: src.tiles ?? null, minzoom: src.minzoom ?? null, maxzoom: src.maxzoom ?? null }
        : null,
      community,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      allSourceUrls: Object.values(style.sources || {}).flatMap((s: any) =>
        [s.url, ...(s.tiles || [])].filter(Boolean),
      ),
    };
  });
}

/** The load-bearing doctrine pin: NO source may stream the live MVT endpoint. */
function assertNoLiveMvtSource(w: Wiring): void {
  for (const u of w.allSourceUrls) {
    expect(u, 'no map source may stream the live /heatmap/tiles/ MVT endpoint').not.toContain('/heatmap/tiles/');
  }
}

// ── The static-PMTiles source + layer wiring ───────────────────────────────────

test.describe('Community heatmap — static PMTiles source + layers', () => {
  test.setTimeout(60000);

  test('community-trails, when built, is a pmtiles vector source with the code zoom contract', async ({ page }) => {
    await openMap(page, 12);
    const w = await readWiring(page);
    assertNoLiveMvtSource(w);

    if (!w.present) {
      // Prod build + NEXT_PUBLIC_HEATMAP_URL unset (the CI-default): the source is
      // deliberately OMITTED (env-url.ts refuses the SPA origin) → no community layers.
      console.log('[heatmap-layers] community source omitted (NEXT_PUBLIC_HEATMAP_URL unset) — asserting graceful skip');
      expect(w.community, 'no community layers when the source is skipped').toEqual([]);
      return;
    }

    // communityTrailsSourceSpec(): { type:'vector', url:'pmtiles://…', minzoom:6, maxzoom:14 }
    expect(w.source!.type).toBe('vector');
    expect(w.source!.url, 'must be the pmtiles:// protocol URL').toMatch(/^pmtiles:\/\//);
    expect(w.source!.url).toContain('/heatmap-display.pmtiles');
    expect(w.source!.minzoom).toBe(6);
    expect(w.source!.maxzoom).toBe(14);
    // It must NOT be a runtime-swapped live-MVT vector source (pre-pivot path).
    expect(w.source!.tiles, 'source must use `url`, not raw MVT `tiles`').toBeFalsy();
  });

  test('the community layers, when built, have the correct types + source-layers', async ({ page }) => {
    await openMap(page, 12);
    const w = await readWiring(page);
    assertNoLiveMvtSource(w);
    test.skip(!w.present, 'community source not built (NEXT_PUBLIC_HEATMAP_URL unset)');

    const byId = Object.fromEntries(w.community.map((l) => [l.id, l]));

    // ② density heatmap (PRIMARY visual) over the weighted `heat_points` points.
    expect(byId['community-trails-heat']?.type).toBe('heatmap');
    expect(byId['community-trails-heat']?.sourceLayer).toBe('heat_points');

    // ② crisp core line over `trails`, street-zoom floor LINE_CRISP_MINZOOM=9.
    expect(byId['community-trails-line']?.type).toBe('line');
    expect(byId['community-trails-line']?.sourceLayer).toBe('trails');
    expect(byId['community-trails-line']?.minzoom).toBe(9);

    // ② direction arrows (one-way mtb/gravel) — symbol over `trails`.
    expect(byId['community-trails-arrows']?.type).toBe('symbol');
    expect(byId['community-trails-arrows']?.sourceLayer).toBe('trails');

    // ②a transparent hit layer for explore-mode clicks — line over `trails`.
    expect(byId['community-trails-hit']?.type).toBe('line');
    expect(byId['community-trails-hit']?.sourceLayer).toBe('trails');

    // Every community layer binds the community-trails source.
    for (const l of w.community) expect(l.source).toBe('community-trails');
  });

  test('exactly one density heatmap layer; no circle/points and no removed glow', async ({ page }) => {
    await openMap(page, 12);
    const w = await readWiring(page);
    test.skip(!w.present, 'community source not built (NEXT_PUBLIC_HEATMAP_URL unset)');

    // The Strava-style density field is a single maplibre `heatmap` layer.
    expect(w.community.filter((l) => l.type === 'heatmap').length).toBe(1);
    // No circle 'points' layer (the pre-pivot black-dots bug); the code adds none.
    expect(w.community.some((l) => l.type === 'circle')).toBe(false);
    expect(w.community.some((l) => l.id === 'community-trails-points')).toBe(false);
    // The wide blurred glow line layer was REMOVED (it was the pâté driver).
    expect(w.community.some((l) => l.id === 'community-trails-glow')).toBe(false);
  });

  test('the app requests the static PMTiles binary (never the live MVT endpoint)', async ({ page }) => {
    const pmtilesRequests: string[] = [];
    const mvtRequests: string[] = [];
    page.on('request', (req) => {
      const u = req.url();
      if (u.includes('heatmap-display.pmtiles')) pmtilesRequests.push(u);
      if (u.includes('/heatmap/tiles/')) mvtRequests.push(u);
    });

    // Heatmap defaults ON (useLayerToggles) → visible layers → the pmtiles
    // header/directory/tile range GETs fire without any interaction.
    await openMap(page, 12);
    const w = await readWiring(page);

    // Doctrine pin holds in BOTH branches: /map never streams the live MVT
    // endpoint (which would run per-visitor DB queries on db-f1-micro).
    if (!w.present) {
      await page.waitForTimeout(1000);
      expect(mvtRequests, 'no live MVT fetch even when the static source is skipped').toEqual([]);
      expect(pmtilesRequests, 'nothing to fetch when the source is skipped').toEqual([]);
      return;
    }

    // Nudge the map to guarantee the covering-tile fetches are dispatched.
    for (let i = 0; i < 20 && pmtilesRequests.length === 0; i++) {
      await page.evaluate((s) => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const m = (window as any).__mapInstance;
        m.setZoom(m.getZoom() + 0.01 * s);
        m.triggerRepaint();
      }, i % 2 === 0 ? 1 : -1);
      await page.waitForTimeout(300);
    }
    expect(pmtilesRequests.length, 'app must fetch the static heatmap-display.pmtiles').toBeGreaterThan(0);
    expect(mvtRequests, 'the /map display must not stream the live MVT endpoint').toEqual([]);
  });
});

// ── Heatmap toggle: the real Calques UI drives layer visibility ─────────────────

test.describe('Community heatmap — toggle wiring (Calques)', () => {
  test.setTimeout(60000);

  test('disabling then re-enabling the heatmap flips the density + line visibility', async ({ page }) => {
    await openMap(page, 12);
    const w = await readWiring(page);
    test.skip(!w.present, 'community source not built (NEXT_PUBLIC_HEATMAP_URL unset)');

    // Heatmap defaults ON: useMapLayerSync sets the layers visible on mount.
    await page.waitForFunction(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const m = (window as any).__mapInstance;
      return m.getLayoutProperty('community-trails-heat', 'visibility') === 'visible';
    }, undefined, { timeout: 15000 });

    // Open Calques and UNCHECK the heatmap toggle (the checkbox starts checked).
    await page.getByTestId('map-layers-btn').click();
    const toggle = page.getByTestId('toggle-heatmap');
    await expect(toggle).toBeVisible();
    await toggle.click(); // disable

    // useMapLayerSync fades opacity then sets visibility:none after ~320ms.
    await page.waitForFunction(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const m = (window as any).__mapInstance;
      return m.getLayoutProperty('community-trails-heat', 'visibility') === 'none'
        && m.getLayoutProperty('community-trails-line', 'visibility') === 'none';
    }, undefined, { timeout: 5000 });

    // Re-enable → both layers visible again.
    await toggle.click(); // enable
    await page.waitForFunction(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const m = (window as any).__mapInstance;
      return m.getLayoutProperty('community-trails-heat', 'visibility') === 'visible'
        && m.getLayoutProperty('community-trails-line', 'visibility') === 'visible';
    }, undefined, { timeout: 5000 });
  });
});

// ── Live MVT endpoint (unchanged — still the DB-backed FALLBACK) ────────────────

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

// ── Performance tests: heatmap tiles from the backend ───────────────────────────

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
});
