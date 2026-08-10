/**
 * Community-map posture (2026-07, Paul's ratified decision).
 *
 * The community MAP is PUBLICLY VIEWABLE — anonymous visitors can browse the
 * heatmap on /map "pour donner envie" (privacy for raw K=1 traces rests on
 * endpoint masking + the HEATMAP_MIN_USERS policy, NOT on gating the map).
 *
 * The ONE members-only affordance is the community DATA EXPORT (the ODbL
 * bulk download in ExportHeatmapModal): view freely → log in to take the data
 * → contribute back. This spec pins all three branches:
 *  - anonymous → /map renders the interactive map (NO login gate),
 *  - anonymous → opening the community export shows the login CTA (no form),
 *  - logged-in → the export form is reachable.
 *
 * It FAILS on the #509 members-only-map code (which rendered a login gate for
 * anonymous visitors on /map → the map canvas would never appear).
 *
 * CI-safe: no WASM router boot, no heatmap data, no backend auth round-trip.
 */
import { test, expect, type Page } from '@playwright/test';

async function openExportEntry(page: Page) {
  const layersBtn = page.getByTestId('map-layers-btn');
  await expect(layersBtn).toBeVisible({ timeout: 15000 });
  await layersBtn.click();
  await page.getByTestId('open-export-heatmap-modal').click();
}

test.describe('Community map — public map, members-only export', () => {
  test.setTimeout(60000);

  test('an anonymous visitor to /map sees the map, not a login gate', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('cc_beta_dismissed', '1'));
    await page.goto('/map/');
    await page.waitForLoadState('domcontentloaded');

    // The interactive map mounts for everyone — no members-only page gate.
    await expect(page.locator('.maplibregl-map, canvas.maplibregl-canvas').first())
      .toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId('map-login-gate')).toHaveCount(0);
    // The toolbar (with the export entry) is available to anonymous users.
    await expect(page.getByTestId('map-layers-btn')).toBeVisible({ timeout: 15000 });
  });

  test('an anonymous visitor opening the community export gets the login CTA', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('cc_beta_dismissed', '1'));
    await page.goto('/map?lat=43.612&lon=3.875&zoom=13');
    await page.waitForLoadState('domcontentloaded');

    await openExportEntry(page);

    // The export is members-only → the login gate replaces the export form.
    await expect(page.getByTestId('export-login-gate')).toBeVisible();
    await expect(page.getByTestId('email-login-form')).toBeVisible();
    // The download form controls must NOT be present for anonymous users.
    await expect(page.getByTestId('export-download-pmtiles')).toHaveCount(0);
    await expect(page.getByTestId('export-download-geojsonl')).toHaveCount(0);
  });

  test('a logged-in visitor reaches the export form', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('cc_beta_dismissed', '1');
      localStorage.setItem('user_id', 'e2e-export-member');
    });
    await page.goto('/map?lat=43.612&lon=3.875&zoom=13');
    await page.waitForLoadState('domcontentloaded');

    await openExportEntry(page);

    // Members see the real export form, not the gate.
    await expect(page.getByTestId('export-login-gate')).toHaveCount(0);
    await expect(page.getByTestId('export-download-pmtiles')).toBeVisible();
    await expect(page.getByTestId('export-download-geojsonl')).toBeVisible();
  });
});
