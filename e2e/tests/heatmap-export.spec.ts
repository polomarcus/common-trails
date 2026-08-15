/**
 * Heatmap export modal — PRE-COMPUTED artifacts ONLY.
 *
 * The "⬇️ Exporter" entry (inside the "Calques" layers dropdown) opens a modal
 * offering the STATIC, pre-built community-heatmap artifacts. There is NO
 * on-demand build, NO bbox editor, NO format radios, NO async pipeline anymore:
 *
 * - **Calque** — the raster XYZ tile-template URL (`raster/{z}/{x}/{y}.png`) to
 *   copy into gpx.studio / VisuGPX (per-sport tutorial at /calque).
 * - **PMTiles** — `/export/heatmap.pmtiles` → 302 to the canonical GCS URL.
 * - **GeoJSONL** — `/export/heatmap.geojsonl` → 302 to the static GCS object.
 *
 * Both download links stay disabled until the ODbL checkbox is checked.
 */
import { test, expect, request, type Page } from '@playwright/test';

const FRONTEND_URL = process.env.FRONTEND_URL || 'http://localhost:3787';
const API_URL = process.env.API_URL || 'http://localhost:8787';

// The export action was folded into the single "Calques" (layers) dropdown
// (declutter, 2026-07). The `open-export-heatmap-modal` entry now lives inside
// that dropdown, so it must be opened first. Testid + click target preserved.
async function openExportModal(page: Page) {
  const layersBtn = page.getByTestId('map-layers-btn');
  await expect(layersBtn).toBeVisible({ timeout: 15000 });
  await layersBtn.click();
  await page.getByTestId('open-export-heatmap-modal').click();
}

test.describe('Heatmap export modal (pre-computed only)', () => {
  test.setTimeout(60000);

  // The community MAP is public, but the community EXPORT is members-only
  // (2026-07 posture). Seed logged-in state so these tests exercise the export
  // FORM, not the login gate. (The gate branch itself is pinned in
  // map-login-gate.spec.ts.)
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('user_id', 'e2e-export-member'));
    await page.addInitScript(() => localStorage.setItem('cc_beta_dismissed', '1'));
  });

  test('opens the export modal with the two static downloads', async ({ page }) => {
    await page.goto(`${FRONTEND_URL}/map?lat=43.612&lon=3.875&zoom=13`);
    await page.waitForLoadState('domcontentloaded');

    await openExportModal(page);

    const modal = page.getByTestId('export-heatmap-modal');
    await expect(modal).toBeVisible();
    await expect(modal.getByTestId('calque-copy-url')).toBeVisible();
    await expect(modal.getByTestId('export-odbl-checkbox')).toBeVisible();
    await expect(modal.getByTestId('export-download-pmtiles')).toBeVisible();
    await expect(modal.getByTestId('export-download-geojsonl')).toBeVisible();
  });

  test('downloads are gated on the ODbL checkbox', async ({ page }) => {
    await page.goto(`${FRONTEND_URL}/map?lat=43.612&lon=3.875&zoom=13`);
    await page.waitForLoadState('domcontentloaded');

    await openExportModal(page);

    const pmtiles = page.getByTestId('export-download-pmtiles');
    const geojsonl = page.getByTestId('export-download-geojsonl');
    const checkbox = page.getByTestId('export-odbl-checkbox');

    // Pre-acknowledgement: links have aria-disabled, no href.
    await expect(pmtiles).toHaveAttribute('aria-disabled', 'true');
    await expect(geojsonl).toHaveAttribute('aria-disabled', 'true');
    expect(await pmtiles.getAttribute('href')).toBeFalsy();

    // Check the box; both downloads become active with the right URLs.
    await checkbox.check();
    await expect(pmtiles).toHaveAttribute('aria-disabled', 'false');
    await expect(geojsonl).toHaveAttribute('aria-disabled', 'false');
    expect(await pmtiles.getAttribute('href')).toContain('/export/heatmap.pmtiles');
    expect(await geojsonl.getAttribute('href')).toContain('/export/heatmap.geojsonl');
  });

  test('clicking the PMTiles download hits /export/heatmap.pmtiles', async ({ page }) => {
    let interceptedUrl: string | null = null;
    await page.route('**/export/heatmap.pmtiles', async (route) => {
      interceptedUrl = route.request().url();
      await route.fulfill({
        status: 302,
        headers: { Location: '/heatmap-display.pmtiles' },
        body: '',
      });
    });

    await page.goto(`${FRONTEND_URL}/map?lat=43.612&lon=3.875&zoom=13`);
    await page.waitForLoadState('domcontentloaded');

    await openExportModal(page);
    await page.getByTestId('export-odbl-checkbox').check();

    const href = await page.getByTestId('export-download-pmtiles').getAttribute('href');
    expect(href).toBeTruthy();

    // Fire the request manually instead of letting the browser navigate —
    // anchor downloads in Playwright are flaky across CI envs.
    await page.evaluate((u) => fetch(u as string, { method: 'GET' }).catch(() => null), href);
    await page.waitForTimeout(200);
    expect(interceptedUrl).not.toBeNull();
    expect(interceptedUrl).toContain('/export/heatmap.pmtiles');
  });
});

/**
 * API-level contract for the pre-computed export surface: the discovery JSON
 * advertises ONLY the three static artifacts, and the two redirect endpoints
 * 302 to the public GCS objects.
 */
test.describe('Heatmap export API contract (pre-computed)', () => {
  test('discovery advertises only pmtiles + raster + geojsonl', async () => {
    const apiContext = await request.newContext();
    const resp = await apiContext.get(`${API_URL}/export/heatmap`);
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.license).toBe('ODbL-1.0');
    expect(body).not.toHaveProperty('async');
    const formats = new Set((body.formats as Array<{ format: string }>).map((f) => f.format));
    expect(formats).toEqual(new Set(['pmtiles', 'raster', 'geojsonl']));
    await apiContext.dispose();
  });

  test('geojsonl endpoint 302s to the static GCS artifact', async () => {
    const apiContext = await request.newContext();
    const resp = await apiContext.get(`${API_URL}/export/heatmap.geojsonl`, {
      maxRedirects: 0,
    });
    expect(resp.status()).toBe(302);
    expect(resp.headers()['location']).toContain('heatmap-display.geojsonl.gz');
    await apiContext.dispose();
  });

  test('the removed on-demand endpoints are gone (404)', async () => {
    // Current export router (backend/app/api/export.py) exposes only /heatmap,
    // /heatmap.pmtiles and /heatmap.geojsonl. The per-bbox on-demand formats
    // were removed in favour of the static artifacts — assert they 404.
    const apiContext = await request.newContext();
    for (const path of [
      '/export/heatmap.geojson?bbox=4.80,45.72,4.90,45.78',
      '/export/heatmap.mbtiles?bbox=4.80,45.72,4.90,45.78',
      '/export/heatmap.gpx?bbox=4.80,45.72,4.90,45.78',
      '/export/heatmap.kml?bbox=4.80,45.72,4.90,45.78',
    ]) {
      const resp = await apiContext.get(`${API_URL}${path}`, { maxRedirects: 0 });
      expect(resp.status(), `${path} should be gone`).toBe(404);
    }
    await apiContext.dispose();
  });
});
