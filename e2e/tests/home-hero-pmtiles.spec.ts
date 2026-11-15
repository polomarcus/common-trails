/**
 * Regression pin (2026-07, Paul): the home hero map drew a huge striped
 * PURPLE FOG — a `type:'heatmap'` DENSITY layer over `heat_points` centroids
 * fed by the LIVE `/heatmap/tiles/...` MVT endpoint. Double bug:
 *   (a) visual: the density layer saturates into fog with tile-grid banding;
 *   (b) doctrine: the HOME (most-visited page) hit the live MVT endpoint →
 *       per-visitor DB queries on the db-f1-micro. The static PMTiles is the
 *       PRIMARY display artefact; live MVT is a fallback only.
 *
 * RASTER-HEATMAP REDESIGN (2026-08): the density visual is BACK — but now a
 * maplibre `heatmap` layer over the STATIC PMTiles `heat_points` source-layer
 * (built offline by build_pmtiles), NOT the live MVT endpoint. So a
 * `type:'heatmap'` layer is now EXPECTED and correct; the doctrine that
 * survives is only that no source may stream the LIVE MVT endpoint. The thin
 * crisp `line` overlay (z14+, over `trails`) is layered on top for street-zoom
 * crispness. This spec drives the real home page and asserts the hero's style.
 *
 * 2026-08 re-pin: the old assertions were stale (they predated the raster
 * redesign — they forbade ANY heatmap layer and required every community layer
 * to be a `line` over `trails`). Re-pinned to the current contract, this spec
 * also caught a REAL regression: the hero passes opacityMult 0.9, and the old
 * `withMult` wrapped the zoom-driven heatmap-opacity ramp as `['*', 0.9, …]`,
 * which maplibre rejects → the density layer was SILENTLY dropped and the hero
 * rendered no community heatmap. Fixed in lib/community-heatmap-layers.ts.
 *
 * THIN LINES ONLY (2026-11-15): the density layer is gone again, on purpose —
 * its blurred kernel drew a pink halo around every trace and merged into a
 * solid mat at regional zoom. The hero (like /map) now draws only the crisp
 * `line` layer over `trails`; a `heatmap` layer is once more a regression.
 */
import { test, expect } from '@playwright/test';


test.describe('Home hero — static PMTiles thin lines, no blur, no live-MVT fog', () => {
  test('hero renders the static-PMTiles heatmap as thin lines, never the live MVT endpoint', async ({ page }) => {
    await page.goto('/');
    // Wait for the MapLibre instance the hero exposes for E2E.
    await page.waitForFunction(() => !!(window as any).__heroMapInstance, null, { timeout: 30000 });
    // Let the async PMTiles setup (HEAD probe + addSource) settle.
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(1500);

    const style = await page.evaluate(() => {
      const m = (window as any).__heroMapInstance;
      const style = m.getStyle();
      return {
        sources: Object.entries(style.sources || {}).map(([id, s]: [string, any]) => ({
          id, type: s.type, url: s.url ?? null, tiles: s.tiles ?? null,
        })),
        layers: (style.layers || []).map((l: any) => ({
          id: l.id, type: l.type, sourceLayer: l['source-layer'] ?? null,
        })),
      };
    });

    // (b) Doctrine pin (unchanged): NO source streams the live MVT endpoint
    // from the home. This is the load-bearing invariant — the density visual
    // must come from the static PMTiles file, never per-visitor DB queries.
    for (const s of style.sources) {
      const urls = [s.url, ...(s.tiles || [])].filter(Boolean) as string[];
      for (const u of urls) {
        expect(u, `source ${s.id} must not hit the live MVT endpoint`).not.toContain('/heatmap/tiles/');
      }
    }

    // If the PMTiles file is served (local dev / prod), the community source
    // must be the pmtiles:// one, and the hero must render the crisp `line`
    // layer over `trails` — and NO blurred `heatmap` density layer. (In CI
    // without the binary the hero legitimately stays a plain basemap — the
    // doctrine pin above is the load-bearing one.)
    const community = style.sources.find((s: { id: string }) => s.id === 'community-trails');
    if (community) {
      expect(community.url).toMatch(/^pmtiles:\/\//);
      const communityLayers = style.layers.filter(
        (l: { id: string }) => l.id.startsWith('community-trails-'),
      );
      // No density layer: its halos turned the hero into a pink mat.
      expect(communityLayers.filter((l: { type: string }) => l.type === 'heatmap')).toEqual([]);
      expect(communityLayers.some((l: { sourceLayer: string | null }) => l.sourceLayer === 'heat_points')).toBe(false);
      // The heatmap itself: the `line` layer over `trails`. It must have
      // survived style validation (an invalid paint — e.g. a multiplier wrapped
      // around a zoom ramp — drops the layer silently), so look it up by id.
      const lines = communityLayers.filter((l: { type: string }) => l.type === 'line');
      expect(lines.map((l: { id: string }) => l.id)).toContain('community-trails-line');
      for (const l of lines) expect(l.sourceLayer).toBe('trails');
    }
  });

  test('sport chips switch layer filters without re-fetching tiles', async ({ page }) => {
    const mvtRequests: string[] = [];
    page.on('request', (req) => {
      if (req.url().includes('/heatmap/tiles/')) mvtRequests.push(req.url());
    });

    await page.goto('/');
    await page.waitForFunction(() => !!(window as any).__heroMapInstance, null, { timeout: 30000 });
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(1000);

    const hasCommunitySource = await page.evaluate(
      () => !!(window as any).__heroMapInstance.getSource('community-trails'),
    );

    // Click through the sport chips (Gravel then Tous). The chips are the
    // hero pills; match by class to stay locale-proof.
    const pills = page.locator('.hero-pill');
    await expect(pills.first()).toBeVisible();
    await pills.nth(2).click(); // gravel
    await page.waitForTimeout(300);

    if (hasCommunitySource) {
      const filterAfterGravel = await page.evaluate(
        () => (window as any).__heroMapInstance.getFilter('community-trails-line') ?? null,
      );
      expect(JSON.stringify(filterAfterGravel)).toContain('gravel');

      await pills.nth(0).click(); // tous → filter cleared
      await page.waitForTimeout(300);
      const filterAfterAll = await page.evaluate(
        () => (window as any).__heroMapInstance.getFilter('community-trails-line') ?? null,
      );
      expect(filterAfterAll).toBeNull();
    }

    // The doctrine pin holds through chip interaction: zero live-MVT fetches.
    expect(mvtRequests).toEqual([]);
  });
});
