/**
 * Community heatmap PMTiles WIRING (raw-PMTiles display pivot).
 *
 * Run: cd e2e && npx playwright test tests/heatmap-pmtiles.spec.ts
 *
 * The community heatmap is drawn from the STATIC `heatmap-display.pmtiles`
 * binary, loaded via the `pmtiles://` protocol (registered in components/Map.tsx
 * before the map is constructed). The PRE-pivot version of this spec asserted a
 * real >1MB prod artifact, which never exists locally/CI — so it was quarantined.
 *
 * Rewritten to be HERMETIC + meaningful in every env: assert the WIRING, not the
 * artifact size —
 *   1. if the frontend serves the file, it is a valid PMTiles v3 binary (magic
 *      bytes) — the size check is GUARDED (logged, never asserted >1MB), and the
 *      test skips gracefully when the file is absent;
 *   2. on /map the app resolves a `pmtiles://…` URL AND issues real HTTP range
 *      GETs for it — which only succeeds if the `pmtiles://` protocol is
 *      registered (otherwise maplibre errors on the unknown scheme and never
 *      fetches), so the request firing is a strong proxy for "protocol wired";
 *   3. the live MVT endpoint still works as the DB-backed FALLBACK.
 */
import { test, expect, request as pwRequest } from '@playwright/test';

const API_URL = process.env.API_URL || 'http://localhost:8787';
const FRONTEND_URL = process.env.FRONTEND_URL || process.env.BASE_URL || 'http://localhost:3787';

// PMTiles v3 header magic: bytes 0-6 = ASCII "PMTiles", byte 7 = version (0x03).
const PMTILES_MAGIC = 'PMTiles';

test.use({ serviceWorkers: 'block' });

test.describe('Community heatmap PMTiles wiring', () => {
  test.setTimeout(60000);

  test('served heatmap-display.pmtiles is a valid PMTiles v3 binary (size logged, not asserted)', async () => {
    const ctx = await pwRequest.newContext();
    const resp = await ctx.get(`${FRONTEND_URL}/heatmap-display.pmtiles`, {
      headers: { Range: 'bytes=0-6' }, // just the magic string
    });
    const ct = resp.headers()['content-type'] || '';
    const magic = (await resp.body()).slice(0, 7).toString('ascii');

    // The binary is a build artifact (gitignored, produced by build_pmtiles) and
    // is ABSENT in a fresh checkout / CI. serve.py (and the backend static host)
    // answer an unmatched path with the SPA catch-all — HTTP 200 `text/html`, NOT
    // a 404 — so guard on BOTH the status and the HTML content-type / magic
    // bytes, then skip gracefully. When a real binary IS served, assert it.
    if (resp.status() === 404 || ct.includes('text/html') || magic !== PMTILES_MAGIC) {
      console.log(`[pmtiles] not served here (status=${resp.status()} content-type=${ct}) — build_pmtiles not run; skipping`);
      await ctx.dispose();
      test.skip(true, 'heatmap-display.pmtiles absent (SPA catch-all) — build_pmtiles not run in this env');
      return;
    }
    // A Range-capable server answers 206; a plain one answers 200 with the body.
    expect([200, 206]).toContain(resp.status());
    expect(magic, 'PMTiles v3 files start with the ASCII magic "PMTiles"').toBe(PMTILES_MAGIC);

    // Size is env-dependent (tiny fixture locally, big in prod) — LOG only, do
    // NOT assert a floor (the pre-pivot >1MB assertion was the quarantine cause).
    const head = await ctx.head(`${FRONTEND_URL}/heatmap-display.pmtiles`);
    if (head.status() === 200) {
      const size = parseInt(head.headers()['content-length'] || '0', 10);
      console.log(`[pmtiles] served heatmap-display.pmtiles = ${(size / 1024 / 1024).toFixed(3)}MB`);
      expect(size, 'a served PMTiles binary must be non-empty').toBeGreaterThan(0);
    }
    await ctx.dispose();
  });

  test('/map resolves a pmtiles:// URL and fetches the binary (protocol wired)', async ({ page }) => {
    const pmtilesRequests: string[] = [];
    page.on('request', (req) => {
      if (req.url().includes('heatmap-display.pmtiles')) pmtilesRequests.push(req.url());
    });

    await page.addInitScript(() => localStorage.setItem('cc_beta_dismissed', '1'));
    await page.goto(`${FRONTEND_URL}/map?lat=43.6&lon=3.87&zoom=12`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForFunction(() => !!(window as unknown as { __mapInstance?: unknown }).__mapInstance, undefined, {
      timeout: 30000,
    });

    // The community source is added SYNCHRONOUSLY with __mapInstance, so its
    // presence is deterministic now. In a prod build with NEXT_PUBLIC_HEATMAP_URL
    // UNSET (the CI-default) the source is deliberately omitted — nothing to
    // fetch — so skip the protocol-fetch assertion in that build.
    const sourceUrl = await page.evaluate(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const m = (window as any).__mapInstance;
      const s = m.getStyle().sources['community-trails'];
      return s?.url ?? null;
    });
    test.skip(sourceUrl === null, 'community source not built (NEXT_PUBLIC_HEATMAP_URL unset)');

    // The community source URL must be resolved through the pmtiles:// protocol.
    expect(sourceUrl, 'community-trails must be a pmtiles:// source').toMatch(/^pmtiles:\/\//);
    expect(sourceUrl).toContain('/heatmap-display.pmtiles');

    // And the protocol must actually fetch it (nudge to force covering tiles).
    for (let i = 0; i < 20 && pmtilesRequests.length === 0; i++) {
      await page.evaluate((s) => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const m = (window as any).__mapInstance;
        m.setZoom(m.getZoom() + 0.01 * s);
        m.triggerRepaint();
      }, i % 2 === 0 ? 1 : -1);
      await page.waitForTimeout(300);
    }
    expect(
      pmtilesRequests.length,
      'the pmtiles:// protocol must issue HTTP GETs for heatmap-display.pmtiles',
    ).toBeGreaterThan(0);
  });

  test('MVT fallback endpoint still serves (DB-backed fallback)', async () => {
    const ctx = await pwRequest.newContext();
    // In-range zoom → 200 protobuf even with an empty DB (geometry may be empty).
    const resp = await ctx.get(`${API_URL}/heatmap/tiles/offroad/14/8369/5980.mvt`);
    expect(resp.status()).toBe(200);
    expect(resp.headers()['content-type']).toContain('protobuf');
    const body = await resp.body();
    expect(body).toBeDefined();
    await ctx.dispose();
  });
});
