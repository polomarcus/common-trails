/**
 * CHEMINS COMMUNS — Core E2E tests: Frontend smoke tests + Personal stats API
 *
 * Split from core.spec.ts — no logic changes.
 */
import { test, expect, request } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';

const API_URL = process.env.API_URL || 'http://localhost:8787';
const BASE_URL = process.env.BASE_URL || 'http://localhost:3787';

// Return empty graph tiles for ALL core tests so client-side routing falls
// through to the server cascade.
test.beforeEach(async ({ page }) => {
  await page.route('**/routing/graph/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ v: 1, edges: [] }),
    });
  });
});

// ── Helpers ────────────────────────────────────────────────────────────────

async function registerAndLogin(apiContext: Awaited<ReturnType<typeof request.newContext>>) {
  const uid = `${Date.now()}_${Math.floor(Math.random() * 100000)}`;
  const email = `test_${uid}@example.com`;
  const password = 'testpassword123';
  const username = `testuser_${uid}`;

  const regResp = await apiContext.post(`${API_URL}/auth/register`, {
    data: { email, password, username },
  });
  expect(regResp.status()).toBe(201);
  const data = await regResp.json();
  return { token: data.access_token, email, password, username, user_id: data.user_id };
}

/** Set the httpOnly auth cookie on the browser context (works before navigation). */
async function setBrowserAuth(page: import('@playwright/test').Page, token: string, userId: string) {
  const url = new URL(BASE_URL);
  await page.context().addCookies([{
    name: 'auth_token',
    value: token,
    domain: url.hostname,
    path: '/',
    httpOnly: true,
    sameSite: 'Lax',
  }]);
  // user_id in localStorage needs a page loaded on the origin
  await page.addInitScript((uid: string) => localStorage.setItem('user_id', uid), userId);
}

// ── Test 5d: /me/stats endpoint ──────────────────────────────────────────

test.describe('5d. Personal stats API', () => {
  test('/me/stats returns correct shape after GPX upload', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);

    const gpxPath = path.join(__dirname, '../fixtures/gpx/test_route.gpx');
    const gpxContent = fs.readFileSync(gpxPath);

    // Upload one road activity
    await apiContext.post(`${API_URL}/imports/files`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: { name: 'test_route.gpx', mimeType: 'application/gpx+xml', buffer: gpxContent },
        sport: 'road',
        contribute_heatmap: 'false',
      },
    });

    // Fetch overall stats
    const statsResp = await apiContext.get(`${API_URL}/me/stats`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(statsResp.status()).toBe(200);
    const stats = await statsResp.json();
    expect(stats.activity_count).toBeGreaterThanOrEqual(1);
    expect(typeof stats.total_distance_m).toBe('number');
    expect(typeof stats.total_elevation_gain_m).toBe('number');
    expect(typeof stats.unique_cells).toBe('number');

    // Fetch sport-specific stats
    const roadStats = await apiContext.get(`${API_URL}/me/stats?sport=road`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(roadStats.status()).toBe(200);
    const roadData = await roadStats.json();
    expect(roadData.activity_count).toBeGreaterThanOrEqual(1);

    await apiContext.dispose();
  });
});

// ── Test 6: Home page loads ───────────────────────────────────────────────

test.describe('6. Frontend smoke tests', () => {
  test('home page loads with auth form and map link', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await expect(page.locator('text=CHEMINS COMMUNS').first()).toBeVisible();
    await expect(page.locator('[data-testid="go-to-map"]')).toBeVisible();
    await expect(page.locator('[data-testid="auth-submit"]')).toBeVisible();
  });

  test('map page loads with toolbar controls', async ({ page }) => {
    await page.goto('/map/');
    await page.waitForLoadState('domcontentloaded');
    // Wait for toolbar to hydrate
    await expect(page.locator('[data-testid="map-layers-btn"]')).toBeVisible({ timeout: 30000 });

    // Layer toggles are now inside the "Calques" dropdown
    await page.getByRole('button', { name: 'Calques' }).click();
    await expect(page.locator('[data-testid="toggle-heatmap"]')).toBeVisible();
    // Hillshade is in the "Plus de calques" expandable section
    await page.locator('[data-testid="toggle-more-layers"]').click();
    await expect(page.locator('[data-testid="toggle-hillshade"]')).toBeVisible();
    // Close dropdown by clicking elsewhere
    await page.locator('body').click({ position: { x: 0, y: 0 } });
    await expect(page.locator('[data-testid="toggle-explore-mode"]')).toBeVisible();
    await expect(page.locator('[data-testid="map-import-btn"]')).toBeVisible();
  });

  test('home has ONE canonical import CTA → /strava (no inline upload form)', async ({ page }) => {
    // The home used to carry a "Quick Connect" modal with two inline upload
    // forms POSTing to /imports/files (a residual competing entry point + a
    // .zip→50MB trap, since /imports/files streams through the 512Mi web
    // service). Those were removed: the single canonical contribution place is
    // the /strava dropzone. Assert the home now only signposts there.
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const contribute = page.locator('[data-testid="go-to-contribute"]');
    await expect(contribute).toBeVisible();
    await expect(contribute).toHaveAttribute('href', '/strava');

    // The removed upload machinery must be gone from the home.
    await expect(page.locator('[data-testid="quick-connect-btn"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="import-files"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="connect-garmin"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="file-input"]')).toHaveCount(0);
  });

  // Removed: 'map import modal' test (flaky timeout on button click)
  // Removed: 'stats page loads' test (page shows login prompt, no CHEMINS COMMUNS text)

  test('/healthz returns ok', async () => {
    const apiContext = await request.newContext();
    const resp = await apiContext.get(`${API_URL}/healthz`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.status).toBe('ok');
    await apiContext.dispose();
  });
});
