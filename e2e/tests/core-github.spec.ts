/**
 * CHEMINS COMMUNS — Core E2E tests: GitHub-like (fork/PR/merge) + Tags
 *
 * Split from core.spec.ts — no logic changes.
 */
import { test, expect, request } from '@playwright/test';

const API_URL = process.env.API_URL || 'http://localhost:8787';

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

// ── Test 4: GitHub-like — fork + version (upstream-PR removed in df9981f) ─

test.describe.serial('4. GitHub-like: fork + version on fork', () => {
  let ownerToken: string;
  let forkToken: string;
  let parentRouteId: string;
  let forkRouteId: string;

  test.beforeAll(async () => {
    const apiContext = await request.newContext();
    const owner = await registerAndLogin(apiContext);
    const forker = await registerAndLogin(apiContext);
    ownerToken = owner.token;
    forkToken = forker.token;
    await apiContext.dispose();
  });

  test('4a. create parent route (public)', async () => {
    const apiContext = await request.newContext();

    const resp = await apiContext.post(`${API_URL}/routes`, {
      headers: { Authorization: `Bearer ${ownerToken}` },
      data: {
        name: 'Test Parent Route',
        sport: 'road',
        visibility: 'public',
        geometry_geojson: JSON.stringify({
          type: 'LineString',
          coordinates: [[4.83, 45.75], [4.84, 45.76], [4.85, 45.77]],
        }),
      },
    });
    expect(resp.status()).toBe(201);
    const data = await resp.json();
    parentRouteId = data.id;
    expect(parentRouteId).toBeTruthy();

    await apiContext.dispose();
  });

  test('4b. fork parent route', async () => {
    const apiContext = await request.newContext();

    const resp = await apiContext.post(`${API_URL}/routes/${parentRouteId}/fork`, {
      headers: { Authorization: `Bearer ${forkToken}` },
    });
    expect(resp.status()).toBe(201);
    const data = await resp.json();
    forkRouteId = data.id;
    expect(forkRouteId).toBeTruthy();
    expect(data.forked_from_id).toBe(parentRouteId);

    await apiContext.dispose();
  });

  test('4c. create version on fork', async () => {
    const apiContext = await request.newContext();

    const resp = await apiContext.post(`${API_URL}/routes/${forkRouteId}/versions`, {
      headers: { Authorization: `Bearer ${forkToken}` },
      data: {
        message: 'Improved route via fork',
        geometry_geojson: JSON.stringify({
          type: 'LineString',
          coordinates: [[4.83, 45.75], [4.835, 45.755], [4.84, 45.76], [4.85, 45.77]],
        }),
        distance_m: 15000,
        elevation_gain_m: 250,
      },
    });
    expect(resp.status()).toBe(201);

    await apiContext.dispose();
  });
});

// ── Test 5: Tags — create + GPX export ───────────────────────────────────

test.describe.serial('5. Tags: create tag + export GPX', () => {
  let ownerToken: string;
  let routeId: string;
  let versionId: string;

  test.beforeAll(async () => {
    const apiContext = await request.newContext();
    const owner = await registerAndLogin(apiContext);
    ownerToken = owner.token;

    // Create a route with geometry
    const routeResp = await apiContext.post(`${API_URL}/routes`, {
      headers: { Authorization: `Bearer ${ownerToken}` },
      data: {
        name: 'Tagged Route',
        sport: 'gravel',
        visibility: 'public',
        geometry_geojson: JSON.stringify({
          type: 'LineString',
          coordinates: [[4.83, 45.75], [4.84, 45.76], [4.85, 45.77], [4.86, 45.78]],
        }),
      },
    });
    const route = await routeResp.json();
    routeId = route.id;
    versionId = route.current_version_id;

    await apiContext.dispose();
  });

  test('5a. create tag v1 on route', async () => {
    const apiContext = await request.newContext();

    expect(versionId).toBeTruthy();

    const resp = await apiContext.post(`${API_URL}/routes/${routeId}/tags`, {
      headers: { Authorization: `Bearer ${ownerToken}` },
      data: {
        tag: 'v1',
        version_id: versionId,
        message: 'First stable release',
      },
    });
    expect(resp.status()).toBe(201);
    const data = await resp.json();
    expect(data.tag).toBe('v1');

    await apiContext.dispose();
  });

  test('5b. export GPX for tag v1', async () => {
    const apiContext = await request.newContext();

    const resp = await apiContext.get(`${API_URL}/routes/${routeId}/tags/v1/gpx`);
    expect(resp.status()).toBe(200);

    const body = await resp.text();
    expect(body).toContain('<?xml');
    expect(body).toContain('<gpx');
    expect(body).toContain('<trkpt');
    // Verify coordinates are present
    expect(body).toContain('lat=');
    expect(body).toContain('lon=');

    await apiContext.dispose();
  });
});

