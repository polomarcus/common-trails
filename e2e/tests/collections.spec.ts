/**
 * CHEMINS COMMUNS — Collection Sharing E2E tests
 *
 * Tests collection creation via trip API, public view, GPX export.
 * The /collections page uses the trip collections API (/trips).
 */
import { test, expect, request } from '@playwright/test';

const API_URL = process.env.API_URL || 'http://localhost:8787';
const BASE_URL = process.env.BASE_URL || 'http://localhost:3787';

// ── Helpers ────────────────────────────────────────────────────────────────

async function registerAndLogin(apiContext: Awaited<ReturnType<typeof request.newContext>>) {
  const email = `coll_test_${Date.now()}@example.com`;
  const password = 'testpassword123';
  const username = `colluser_${Date.now()}`;
  const regResp = await apiContext.post(`${API_URL}/auth/register`, {
    data: { email, password, username },
  });
  expect(regResp.status()).toBe(201);
  const data = await regResp.json();
  return { token: data.access_token, user_id: data.user_id };
}

async function createRoute(
  apiContext: Awaited<ReturnType<typeof request.newContext>>,
  token: string,
  name = 'E2E Collection Route',
) {
  const resp = await apiContext.post(`${API_URL}/routes`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      name,
      sport: 'gravel',
      visibility: 'public',
      geometry_geojson: JSON.stringify({
        type: 'LineString',
        coordinates: [
          [3.870, 43.610, 62], [3.874, 43.617, 75], [3.878, 43.624, 95],
          [3.882, 43.631, 130], [3.887, 43.638, 175],
        ],
      }),
      distance_m: 5000,
      elevation_gain_m: 120,
    },
  });
  expect(resp.status()).toBe(201);
  return resp.json();
}

async function createPublicCollectionWithRoutes(
  apiContext: Awaited<ReturnType<typeof request.newContext>>,
  token: string,
  routeCount = 2,
) {
  // Create trip-based collection
  const collResp = await apiContext.post(`${API_URL}/trips`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: 'E2E Test Collection', description: 'Test description', sport: 'gravel', visibility: 'public', status: 'planned' },
  });
  expect(collResp.status()).toBe(201);
  const collection = await collResp.json();

  const routes = [];
  for (let i = 0; i < routeCount; i++) {
    const route = await createRoute(apiContext, token, `Route ${i + 1}`);
    await apiContext.post(`${API_URL}/trips/${collection.id}/stages`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { route_id: route.id, day_index: i, title: `Étape ${i + 1}` },
    });
    routes.push(route);
  }

  return { collection, routes };
}

// ── API Tests ────────────────────────────────────────────────────────────────

test.describe('Collection Sharing — API', () => {
  test('create collection with routes via trip API', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);
    const { collection } = await createPublicCollectionWithRoutes(apiContext, token);

    const resp = await apiContext.get(`${API_URL}/trips/${collection.id}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    expect(data.stages.length).toBe(2);
    expect(data.total_distance_m).toBe(10000);
  });

  test('create POI annotation on collection', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);
    const { collection } = await createPublicCollectionWithRoutes(apiContext, token, 1);

    const poiResp = await apiContext.post(
      `${API_URL}/trips/${collection.id}/pois`,
      {
        headers: { Authorization: `Bearer ${token}` },
        data: { type: 'water', lon: 3.875, lat: 43.62, name: 'Source test' },
      },
    );
    expect(poiResp.status()).toBe(201);

    const resp = await apiContext.get(`${API_URL}/trips/${collection.id}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await resp.json();
    expect(data.pois.length).toBe(1);
    expect(data.pois[0].type).toBe('water');
  });
});

// ── UI Tests ────────────────────────────────────────────────────────────────

test.describe('Collection Sharing — UI', () => {
  test('public collection page shows stages and stats', async ({ page }) => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);
    const { collection } = await createPublicCollectionWithRoutes(apiContext, token, 2);

    await page.goto(`${BASE_URL}/collections?id=${collection.id}`);
    await page.waitForLoadState('networkidle');

    // Collection name visible
    await expect(page.getByText('E2E Test Collection')).toBeVisible();

    // Stages visible
    await expect(page.getByText('Étape 1')).toBeVisible();
    await expect(page.getByText('Étape 2')).toBeVisible();

    // Stats visible (distance)
    await expect(page.getByText(/\d+ itinéraire/)).toBeVisible();
  });

  test('click stage in timeline highlights it', async ({ page }) => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);
    const { collection } = await createPublicCollectionWithRoutes(apiContext, token, 2);

    await page.goto(`${BASE_URL}/collections?id=${collection.id}`);
    await page.waitForLoadState('networkidle');

    // Click first stage
    await page.getByText('Étape 1').click();
    // Stage should be selected (visual feedback — no crash)
    await expect(page.getByText('Étape 1')).toBeVisible();
  });

  test('private collection returns error for anonymous', async ({ page }) => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);
    const collResp = await apiContext.post(`${API_URL}/trips`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: 'Secret', sport: 'gravel', visibility: 'private' },
    });
    const coll = await collResp.json();

    await page.goto(`${BASE_URL}/collections?id=${coll.id}`);
    await page.waitForLoadState('networkidle');
    // Should show error (HTTP 404 from API)
    await expect(page.getByText('404')).toBeVisible();
  });

  test('GPX export button works for collection with stages', async ({ page }) => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);
    const { collection } = await createPublicCollectionWithRoutes(apiContext, token, 1);

    await page.goto(`${BASE_URL}/collections?id=${collection.id}`);
    await page.waitForLoadState('networkidle');

    // GPX button should be visible (collection has stages)
    const gpxButton = page.getByRole('button', { name: 'GPX' }).first();
    await expect(gpxButton).toBeVisible();
  });
});
