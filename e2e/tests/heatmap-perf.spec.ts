/**
 * Heatmap performance tests — verify tile loading matches Garmin Connect targets.
 *
 * Run: cd e2e && npx playwright test tests/heatmap-perf.spec.ts
 */
import { test, expect, request as pwRequest } from '@playwright/test';

const API_URL = process.env.API_URL || 'http://localhost:8787';
const FRONTEND_URL = process.env.FRONTEND_URL || 'http://localhost:3787';

test.describe('Heatmap tile performance', () => {
  test.setTimeout(30000);
  // Skip in CI: requires populated heat_edges + PMTiles file (prod-only).
  test.skip(!!process.env.CI, 'Requires prod-like data + PMTiles file');

  test('z14 tile loads in <1s', async () => {
    const ctx = await pwRequest.newContext();
    // Warm
    await ctx.get(`${API_URL}/heatmap/tiles/offroad/14/8369/5980.mvt`);
    // Test
    const t0 = Date.now();
    const resp = await ctx.get(`${API_URL}/heatmap/tiles/offroad/14/8370/5981.mvt`);
    const ms = Date.now() - t0;
    console.log(`[perf] z14: ${ms}ms`);
    expect(resp.status()).toBe(200);
    expect(ms, `z14 <1s (got ${ms}ms)`).toBeLessThan(1000);
    await ctx.dispose();
  });

  test('z13 tile loads in <1s', async () => {
    const ctx = await pwRequest.newContext();
    const t0 = Date.now();
    const resp = await ctx.get(`${API_URL}/heatmap/tiles/offroad/13/4184/2990.mvt`);
    const ms = Date.now() - t0;
    console.log(`[perf] z13: ${ms}ms`);
    expect(resp.status()).toBe(200);
    expect(ms, `z13 <3s (got ${ms}ms)`).toBeLessThan(3000);
    await ctx.dispose();
  });

  test('z12 tile loads in <2s', async () => {
    const ctx = await pwRequest.newContext();
    const t0 = Date.now();
    const resp = await ctx.get(`${API_URL}/heatmap/tiles/offroad/12/2092/1495.mvt`);
    const ms = Date.now() - t0;
    console.log(`[perf] z12: ${ms}ms`);
    expect(resp.status()).toBe(200);
    expect(ms, `z12 <2s (got ${ms}ms)`).toBeLessThan(2000);
    await ctx.dispose();
  });

  test('cached tile loads in <50ms', async () => {
    const ctx = await pwRequest.newContext();
    await ctx.get(`${API_URL}/heatmap/tiles/offroad/14/8369/5980.mvt`);
    const t0 = Date.now();
    const resp = await ctx.get(`${API_URL}/heatmap/tiles/offroad/14/8369/5980.mvt`);
    const ms = Date.now() - t0;
    console.log(`[perf] cached: ${ms}ms`);
    expect(resp.status()).toBe(200);
    expect(ms, `cached <50ms (got ${ms}ms)`).toBeLessThan(50);
    await ctx.dispose();
  });

  test('pan: 4 z13 tiles in <3s parallel', async () => {
    const ctx = await pwRequest.newContext();
    const tiles = [[4183, 2990], [4185, 2990], [4183, 2991], [4185, 2991]];
    const t0 = Date.now();
    await Promise.all(tiles.map(([x, y]) => ctx.get(`${API_URL}/heatmap/tiles/offroad/13/${x}/${y}.mvt`)));
    const ms = Date.now() - t0;
    console.log(`[perf] pan 4×z13: ${ms}ms`);
    expect(ms, `4 tiles <3s (got ${ms}ms)`).toBeLessThan(3000);
    await ctx.dispose();
  });

  // REMOVED: the '.fgraph loads' perf test. Client-side routing (the WASM
  // router + regional .fgraph shards) was decommissioned in the raw-trace
  // pivot — there is no .fgraph artifact to serve any more.
});
