/**
 * Community heatmap display-pipeline INITIALIZATION time (/map).
 *
 * The heatmap defaults ON (useLayerToggles), so on a plain page load the display
 * pipeline must stand itself up with no user interaction: the map is
 * constructed, the STATIC `heatmap-display.pmtiles` source is added, and the
 * crisp-line layer is attached and made visible.
 *
 * PRE-pivot this measured a "first-paint" console-log signal that the
 * raw-PMTiles pivot removed (quarantine cause). Rewritten to be HERMETIC: poll
 * the live map handle (`__mapInstance`) until the `community-trails` source and
 * its `community-trails-line` layer are present AND visible, within a sane time
 * budget. This asserts the pipeline initializes — no rendered pixels, no
 * artifact size, no DB heatmap data required.
 *
 * Readiness used to be measured on the blurred `community-trails-heat` density
 * layer too; that layer was removed (2026-11-15) — the thin line layer is now
 * the whole community heatmap, so readiness is measured on it alone.
 */
import { test, expect } from '@playwright/test';

const FRONTEND_URL = process.env.FRONTEND_URL || process.env.BASE_URL || 'http://localhost:3787';

// CI runners are shared + slow, so keep the budget generous; the point is that
// the pipeline initializes at all (regression: a broken source/layer wiring
// would never satisfy the predicate and the test would time out).
const INIT_BUDGET_MS = process.env.CI ? 45000 : 25000;

test.describe('Community heatmap — display pipeline init', () => {
  test.setTimeout(90000);

  test('community-trails source + line layer stand up (no interaction)', async ({ page }) => {
    await page.addInitScript(() => { localStorage.setItem('cc_beta_dismissed', '1'); localStorage.setItem('cc_beta_footer_dismissed', '1'); });

    const tGoto = Date.now();
    await page.goto(`${FRONTEND_URL}/map?lat=43.612&lon=3.875&zoom=13`);
    await page.waitForLoadState('domcontentloaded');
    const tDom = Date.now() - tGoto;

    // Stage 1: the map handle is exposed. initMapLayers (which adds the community
    // source, if a PMTiles URL resolves) runs synchronously right after, so once
    // __mapInstance exists the source presence is deterministic.
    await page.waitForFunction(() => !!(window as unknown as { __mapInstance?: unknown }).__mapInstance, undefined, {
      timeout: INIT_BUDGET_MS,
    });
    const tMap = Date.now() - tGoto;

    const hasSource = await page.evaluate(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const m = (window as any).__mapInstance;
      return !!m?.getSource?.('community-trails');
    });

    // Build-env branch (see heatmap-layers.spec): in a prod build with
    // NEXT_PUBLIC_HEATMAP_URL UNSET (the CI-default) the source is deliberately
    // omitted, so there is no display pipeline to stand up. Assert the graceful
    // end state (map up, source correctly skipped) and log timings.
    if (!hasSource) {
      const tReady = Date.now() - tGoto;
      console.log('\n=== HEATMAP DISPLAY-PIPELINE INIT (source OMITTED build) ===');
      console.log(`  t_dom    ${String(tDom).padStart(6)} ms  (DOMContentLoaded)`);
      console.log(`  t_map    ${String(tMap).padStart(6)} ms  (__mapInstance exposed; community source skipped)`);
      console.log('  note     NEXT_PUBLIC_HEATMAP_URL unset → community heatmap intentionally not wired');
      console.log('============================================================\n');
      const communityLayers = await page.evaluate(() => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const m = (window as any).__mapInstance;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        return (m.getStyle().layers || []).filter((l: any) => String(l.id).startsWith('community-trails-')).length;
      });
      expect(communityLayers, 'no community layers when the source is skipped').toBe(0);
      expect(tMap, `map must come up within ${INIT_BUDGET_MS}ms`).toBeLessThan(INIT_BUDGET_MS);
      return;
    }

    // Stage 2: the static-PMTiles source + its line layer are added AND the
    // heatmap default-ON state has made the line layer visible.
    await page.waitForFunction(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const m = (window as any).__mapInstance;
      if (!m?.getSource?.('community-trails')) return false;
      if (m.getLayer?.('community-trails-line') == null) return false;
      return m.getLayoutProperty('community-trails-line', 'visibility') === 'visible';
    }, undefined, { timeout: INIT_BUDGET_MS });
    const tReady = Date.now() - tGoto;

    console.log('\n=== HEATMAP DISPLAY-PIPELINE INIT (ms from page.goto) ===');
    console.log(`  t_dom    ${String(tDom).padStart(6)} ms  (DOMContentLoaded)`);
    console.log(`  t_map    ${String(tMap).padStart(6)} ms  (__mapInstance exposed)`);
    console.log(`  t_ready  ${String(tReady).padStart(6)} ms  (community source + visible layers)`);
    console.log('=========================================================\n');

    // Final explicit assertion (the waitForFunction above already guarantees it,
    // but keep a readable expect on the end state + budget).
    const ready = await page.evaluate(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const m = (window as any).__mapInstance;
      return {
        hasSource: !!m.getSource('community-trails'),
        line: m.getLayoutProperty('community-trails-line', 'visibility'),
        densityLayer: m.getLayer('community-trails-heat') != null,
      };
    });
    expect(ready.hasSource).toBe(true);
    expect(ready.line).toBe('visible');
    expect(ready.densityLayer, 'the blurred density layer was removed').toBe(false);
    expect(tReady, `pipeline must initialize within ${INIT_BUDGET_MS}ms`).toBeLessThan(INIT_BUDGET_MS);
  });
});
