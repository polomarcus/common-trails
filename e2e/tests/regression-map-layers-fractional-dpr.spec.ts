/**
 * CHEMINS COMMUNS — E2E regression: /map must build its layers at ANY
 * devicePixelRatio, including fractional ones.
 *
 * Prod bug (2026-10-12): `/map` showed the basemap and NOTHING else — no
 * community heatmap, no DFCI, no user traces — and the "Calques" checkbox plus
 * the `H` shortcut were inert, with no error in the console.
 *
 * Cause: `registerDirectionArrow()` (the FIRST statement of
 * `addMapSourcesAndLayers`) sized its canvas at `16 * devicePixelRatio`.
 * Firefox reports a FRACTIONAL dpr at browser zoom (1.1 / 2.2 / 2.4), as does OS
 * display scaling (1.25, 1.5). The canvas truncates `16 * 1.1 = 17.6` to a 17 px
 * bitmap while `addImage()` was told 17.6 px, so MapLibre's
 * `width * height * 4 === data.length` check threw "RGBAImage size mismatch" —
 * and that throw aborted the whole straight-line setup.
 *
 * The unit suite (frontend/lib/__tests__/init-map-layers-resilience.test.ts)
 * pins the logic against a FAKE map that re-implements MapLibre's size check.
 * This spec is the integration counterpart: a REAL MapLibre, at a real
 * fractional deviceScaleFactor, in the build CI actually ships.
 *
 * HERMETIC + env-independent on purpose: it asserts the layers that do NOT
 * depend on `NEXT_PUBLIC_HEATMAP_URL` (hillshade / DFCI / user traces, plus the
 * arrow sprite itself). The community-* layers are deliberately omitted from a
 * CI-default build (see heatmap-layers.spec.ts), so asserting them here would
 * make the spec pass for the wrong reason. Every layer below was `false` during
 * the incident, so this spec fails on the bug and passes on the fix.
 */
import { test, expect } from '@playwright/test';

const FRONTEND_URL = process.env.BASE_URL || 'http://localhost:3787';

// Firefox at 110 % zoom on a 1x display. Any non-integer value reproduces it;
// 1.1 is the one the incident was reproduced with headless.
test.use({ deviceScaleFactor: 1.1 });

// Layers added by blocks that need no build-time env var.
const ENV_INDEPENDENT_LAYERS = [
  'hillshade-layer',
  'dfci-trails-line',
  'user-activities-line',
];

test.describe('/map layer setup @ fractional devicePixelRatio', () => {
  test('builds every env-independent layer and registers the arrow sprite', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('cc_beta_dismissed', '1'));
    await page.goto(`${FRONTEND_URL}/map?lat=43.612&lon=3.875&zoom=13`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForFunction(() => !!(window as unknown as Record<string, unknown>).__mapInstance, { timeout: 30000 });

    const report = await page.evaluate((layerIds) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const m = (window as any).__mapInstance;
      return {
        dpr: window.devicePixelRatio,
        present: layerIds.filter((id: string) => !!m.getLayer(id)),
        hasArrowSprite: m.hasImage('direction-arrow'),
      };
    }, ENV_INDEPENDENT_LAYERS);

    // Guard against a vacuous pass: the browser must really be at a fractional dpr.
    expect(Number.isInteger(report.dpr)).toBe(false);
    // The whole point: ONE sprite failure used to take all of these down.
    expect(report.present).toEqual(ENV_INDEPENDENT_LAYERS);
    // And the sprite now WORKS at a fractional dpr, not merely survives.
    expect(report.hasArrowSprite).toBe(true);
  });

  test('the heatmap toggle stays responsive (layersReady was reached)', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('cc_beta_dismissed', '1'));
    await page.goto(`${FRONTEND_URL}/map?lat=43.612&lon=3.875&zoom=13`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForFunction(() => !!(window as unknown as Record<string, unknown>).__mapInstance, { timeout: 30000 });

    // During the incident the init threw BEFORE `layersReady = true`, so every
    // layer stayed on its initial `visibility: none` and `H` did nothing at all.
    // DFCI is toggled by the same effect family and needs no env var.
    const visibilityOf = () => page.evaluate(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const m = (window as any).__mapInstance;
      return m.getLayer('dfci-trails-line')
        ? (m.getLayoutProperty('dfci-trails-line', 'visibility') ?? 'visible')
        : null;
    });

    await expect.poll(visibilityOf, { timeout: 15000 }).toBe('visible');
  });
});
