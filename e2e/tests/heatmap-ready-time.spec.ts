/**
 * Heatmap initial-load timing benchmark.
 *
 * Measures the AUTOMATIC initial heatmap load — heatmap defaults to ON in
 * useLayerToggles, so MapLibre should fetch + paint the first PMTiles tile
 * without any user interaction.
 *
 * Captures the lifecycle [init] and [heatmap] logs the app emits in any
 * browser session — see:
 *   - frontend/app/map/page.tsx → [init] map-page-module-eval / map-ready / init-map-layers done
 *   - frontend/hooks/useHeatmapControl.ts → [heatmap] loadCommunityHeatmap chain
 */
import { test, expect } from '@playwright/test';

const FRONTEND_URL = process.env.FRONTEND_URL || 'http://localhost:3787';

test.describe('Heatmap initial load', () => {
  test.setTimeout(60000);
  // Skip in CI: requires heatmap-display.pmtiles (~10-200MB) which is built
  // from prod data via `python -m app.jobs.build_pmtiles` and not in the repo.
  test.skip(!!process.env.CI, 'Requires PMTiles file (prod-only)');

  test('measure goto → first heatmap tile painted (auto)', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('cc_beta_dismissed', '1');
      localStorage.setItem('cc_perf_trace', '1'); // opt in to [init]/[heatmap] logs
    });

    const logs: string[] = [];
    page.on('console', (msg) => {
      const text = msg.text();
      if (text.includes('[init]') || text.includes('[heatmap]')) logs.push(text);
    });

    const tGoto = Date.now();
    await page.goto(`${FRONTEND_URL}/map?lat=43.612&lon=3.875&zoom=13`);
    await page.waitForLoadState('domcontentloaded');
    const tDom = Date.now() - tGoto;

    // Wait up to 30s for first-paint (or timeout) — purely passive
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline && !logs.some((l) => l.includes('first-paint'))) {
      await page.waitForTimeout(100);
    }
    const tTotal = Date.now() - tGoto;

    console.log('\n=== HEATMAP INITIAL LOAD (ms from page.goto) ===');
    console.log(`  t_dom         ${String(tDom).padStart(6)} ms  (DOMContentLoaded)`);
    console.log(`  t_total       ${String(tTotal).padStart(6)} ms  (first-paint observed or timeout)`);
    console.log('\n--- Captured lifecycle logs ---');
    for (const l of logs) console.log('  ' + l);
    console.log('==========================================\n');

    expect(logs.some((l) => l.includes('first-paint'))).toBe(true);
  });
});
