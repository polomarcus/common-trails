/**
 * CHEMINS COMMUNS — Trip Collections E2E tests
 *
 * Tests trip CRUD, stages, POIs, GPX export, fork, and UI rendering.
 */
import { test, expect, request } from '@playwright/test';

const API_URL = process.env.API_URL || 'http://localhost:8787';
const BASE_URL = process.env.BASE_URL || 'http://localhost:3787';

// ── Helpers ────────────────────────────────────────────────────────────────

async function registerAndLogin(apiContext: Awaited<ReturnType<typeof request.newContext>>) {
  const email = `trip_test_${Date.now()}@example.com`;
  const password = 'testpassword123';
  const username = `tripuser_${Date.now()}`;
  const regResp = await apiContext.post(`${API_URL}/auth/register`, {
    data: { email, password, username },
  });
  expect(regResp.status()).toBe(201);
  const data = await regResp.json();
  return { token: data.access_token, user_id: data.user_id };
}

async function createRoute(apiContext: Awaited<ReturnType<typeof request.newContext>>, token: string) {
  const resp = await apiContext.post(`${API_URL}/routes`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      name: 'E2E Route for Trip',
      sport: 'gravel',
      visibility: 'public',
      geometry_geojson: JSON.stringify({
        type: 'LineString',
        coordinates: [[3.870, 43.610, 62], [3.874, 43.617, 75], [3.878, 43.624, 95]],
      }),
      distance_m: 5000,
      elevation_gain_m: 120,
    },
  });
  expect(resp.status()).toBe(201);
  return resp.json();
}

// ── API Tests ────────────────────────────────────────────────────────────────

test.describe('Trip Collections — API', () => {
  // CI: bump timeout for slow shared runners (API tests usually fast locally).
  test.setTimeout(process.env.CI ? 90_000 : 30_000);

  test('create trip via API', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);

    const resp = await apiContext.post(`${API_URL}/trips`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: 'E2E Trip', sport: 'gravel' },
    });
    expect(resp.status()).toBe(201);
    const trip = await resp.json();
    expect(trip.name).toBe('E2E Trip');
    expect(trip.sport).toBe('gravel');
    expect(trip.status).toBe('draft');
  });

  test('add stage to trip', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);
    const route = await createRoute(apiContext, token);

    const tripResp = await apiContext.post(`${API_URL}/trips`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: 'Stage Trip', sport: 'gravel' },
    });
    const trip = await tripResp.json();

    const stageResp = await apiContext.post(`${API_URL}/trips/${trip.id}/stages`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { route_id: route.id, day_index: 0, title: 'Day 1' },
    });
    expect(stageResp.status()).toBe(201);
    const stage = await stageResp.json();
    expect(stage.title).toBe('Day 1');
    expect(stage.route_name).toBe('E2E Route for Trip');
  });

  test('reorder stages', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);

    const tripResp = await apiContext.post(`${API_URL}/trips`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: 'Reorder Trip', sport: 'road' },
    });
    const trip = await tripResp.json();

    const s1 = await (await apiContext.post(`${API_URL}/trips/${trip.id}/stages`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { day_index: 0, title: 'First' },
    })).json();

    const s2 = await (await apiContext.post(`${API_URL}/trips/${trip.id}/stages`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { day_index: 1, title: 'Second' },
    })).json();

    const reorderResp = await apiContext.put(`${API_URL}/trips/${trip.id}/stages/reorder`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { stage_ids: [s2.id, s1.id] },
    });
    expect(reorderResp.status()).toBe(200);
    const stages = await reorderResp.json();
    expect(stages[0].id).toBe(s2.id);
    expect(stages[1].id).toBe(s1.id);
  });

  test('add POI to trip', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);

    const tripResp = await apiContext.post(`${API_URL}/trips`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: 'POI Trip', sport: 'gravel' },
    });
    const trip = await tripResp.json();

    const poiResp = await apiContext.post(`${API_URL}/trips/${trip.id}/pois`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { type: 'water', lon: 3.87, lat: 43.61, name: 'Source E2E' },
    });
    expect(poiResp.status()).toBe(201);
    const poi = await poiResp.json();
    expect(poi.type).toBe('water');
    expect(poi.name).toBe('Source E2E');
  });

  test('GPX export returns valid XML', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);
    const route = await createRoute(apiContext, token);

    const tripResp = await apiContext.post(`${API_URL}/trips`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: 'GPX Trip', sport: 'gravel' },
    });
    const trip = await tripResp.json();

    await apiContext.post(`${API_URL}/trips/${trip.id}/stages`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { route_id: route.id, day_index: 0 },
    });

    const gpxResp = await apiContext.get(`${API_URL}/trips/${trip.id}/gpx`);
    expect(gpxResp.status()).toBe(200);
    const body = await gpxResp.text();
    expect(body).toContain('<gpx');
    expect(body).toContain('<trk>');
    expect(body).toContain('<trkpt');
  });

  test('ZIP export contains files', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);
    const route = await createRoute(apiContext, token);

    const tripResp = await apiContext.post(`${API_URL}/trips`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: 'ZIP Trip', sport: 'gravel' },
    });
    const trip = await tripResp.json();

    await apiContext.post(`${API_URL}/trips/${trip.id}/stages`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { route_id: route.id, day_index: 0, title: 'Stage 1' },
    });

    const zipResp = await apiContext.get(`${API_URL}/trips/${trip.id}/gpx/zip`);
    expect(zipResp.status()).toBe(200);
    const ct = zipResp.headers()['content-type'] ?? '';
    expect(ct).toContain('application/zip');
  });

  test('fork trip creates independent copy', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);
    const route = await createRoute(apiContext, token);

    const tripResp = await apiContext.post(`${API_URL}/trips`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: 'Fork Source', sport: 'gravel', visibility: 'public', status: 'planned' },
    });
    const trip = await tripResp.json();

    await apiContext.post(`${API_URL}/trips/${trip.id}/stages`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { route_id: route.id, day_index: 0, title: 'Day 1' },
    });

    await apiContext.post(`${API_URL}/trips/${trip.id}/pois`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { type: 'water', lon: 3.87, lat: 43.61, name: 'Source' },
    });

    const forkResp = await apiContext.post(`${API_URL}/trips/${trip.id}/fork`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(forkResp.status()).toBe(201);
    const fork = await forkResp.json();
    expect(fork.id).not.toBe(trip.id);
    expect(fork.visibility).toBe('private');
    expect(fork.forked_from_id).toBe(trip.id);
    expect(fork.stages.length).toBe(1);
    expect(fork.pois.length).toBe(1);
  });
});

// ── UI Tests ─────────────────────────────────────────────────────────────────

test.describe('Trip Collections — UI', () => {
  test('trip listing page shows trips', async ({ page }) => {
    await page.goto(`${BASE_URL}/collections`);
    await page.waitForLoadState('networkidle');
    // Should show the page header
    await expect(page.getByRole('heading', { name: 'Collections' })).toBeVisible();
  });

  test('trip detail page shows stages and POIs', async ({ page }) => {
    // First create a trip via API
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);
    const route = await createRoute(apiContext, token);

    const tripResp = await apiContext.post(`${API_URL}/trips`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: 'UI Detail Trip', sport: 'gravel', visibility: 'public', status: 'planned' },
    });
    const trip = await tripResp.json();

    await apiContext.post(`${API_URL}/trips/${trip.id}/stages`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { route_id: route.id, day_index: 0, title: 'Etape Visible' },
    });

    await apiContext.post(`${API_URL}/trips/${trip.id}/pois`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { type: 'water', lon: 3.87, lat: 43.61, name: 'Source Visible' },
    });

    await page.goto(`${BASE_URL}/collections?id=${trip.id}`);
    await page.waitForLoadState('networkidle');
    await expect(page.getByText('UI Detail Trip')).toBeVisible();
    await expect(page.getByText('Etape Visible')).toBeVisible();
    await expect(page.getByText('Source Visible')).toBeVisible();
  });

  test('collections page has search input and hero cards', async ({ page }) => {
    await page.goto(`${BASE_URL}/collections`);
    await page.waitForLoadState('networkidle');
    await expect(page.getByPlaceholder('Rechercher...')).toBeVisible();
    // EXACT match: the seed data also contains a "Grandes Traversées VTT"
    // collection, so a substring 'Grandes Traversées' matches 2 elements
    // (strict-mode violation). Pin the exact hero-card title.
    await expect(page.getByText('Grandes Traversées', { exact: true }).first()).toBeVisible();
  });

  test('collection detail: editable description for owner', async ({ page }) => {
    const apiContext = await request.newContext();
    const { token, user_id } = await registerAndLogin(apiContext);

    const tripResp = await apiContext.post(`${API_URL}/trips`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: 'Desc Edit Trip', sport: 'gravel', visibility: 'public', status: 'planned' },
    });
    const trip = await tripResp.json();

    // Set auth in browser
    await page.goto(`${BASE_URL}/collections`);
    await page.evaluate(({ token, user_id }) => {
      localStorage.setItem('access_token', token);
      localStorage.setItem('user_id', user_id);
      document.cookie = `access_token=${token}; path=/`;
    }, { token, user_id });

    await page.goto(`${BASE_URL}/collections?id=${trip.id}`);
    await page.waitForLoadState('networkidle');

    // Owner should see "+ Ajouter une description"
    await expect(page.getByText('Ajouter une description')).toBeVisible();
  });

  test('collection detail: owner can change visibility via API and see dropdown', async ({ page }) => {
    const apiContext = await request.newContext();
    const { token, user_id } = await registerAndLogin(apiContext);

    const tripResp = await apiContext.post(`${API_URL}/trips`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: 'Vis Trip', sport: 'gravel', visibility: 'private', status: 'draft' },
    });
    const trip = await tripResp.json();

    // Update visibility via API
    const updateResp = await apiContext.put(`${API_URL}/trips/${trip.id}`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { visibility: 'public' },
    });
    expect(updateResp.status()).toBe(200);
    const updated = await updateResp.json();
    expect(updated.visibility).toBe('public');

    // Update status via API
    const statusResp = await apiContext.put(`${API_URL}/trips/${trip.id}`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { status: 'completed' },
    });
    expect(statusResp.status()).toBe(200);
    expect((await statusResp.json()).status).toBe('completed');

    // Verify owner sees dropdowns on the page
    await page.goto(`${BASE_URL}/collections`);
    await page.evaluate(({ token, user_id }) => {
      localStorage.setItem('access_token', token);
      localStorage.setItem('user_id', user_id);
      document.cookie = `access_token=${token}; path=/`;
    }, { token, user_id });

    await page.goto(`${BASE_URL}/collections?id=${trip.id}`);
    await page.waitForLoadState('networkidle');
    await expect(page.getByText('Vis Trip')).toBeVisible();
    // Owner should see select elements (dropdowns for visibility/status)
    await expect(page.locator('select').first()).toBeVisible();
  });

  test('collection detail: GPX buttons hidden when no stages', async ({ page }) => {
    const apiContext = await request.newContext();
    const { token, user_id } = await registerAndLogin(apiContext);

    const tripResp = await apiContext.post(`${API_URL}/trips`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: 'Empty Trip', sport: 'gravel', visibility: 'public', status: 'planned' },
    });
    const trip = await tripResp.json();

    await page.goto(`${BASE_URL}/collections?id=${trip.id}`);
    await page.waitForLoadState('networkidle');

    await expect(page.getByText('Empty Trip')).toBeVisible();
    // GPX/ZIP buttons should NOT be visible for empty collection
    await expect(page.getByRole('button', { name: 'GPX' })).not.toBeVisible();
    await expect(page.getByRole('button', { name: 'ZIP' })).not.toBeVisible();
  });

  test('collection detail: POI section visible for owner with hint', async ({ page }) => {
    const apiContext = await request.newContext();
    const { token, user_id } = await registerAndLogin(apiContext);

    const route = await createRoute(apiContext, token);
    const tripResp = await apiContext.post(`${API_URL}/trips`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { name: 'POI Section Trip', sport: 'gravel', visibility: 'public', status: 'planned' },
    });
    const trip = await tripResp.json();
    await apiContext.post(`${API_URL}/trips/${trip.id}/stages`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { route_id: route.id, day_index: 0, title: 'Stage 1' },
    });

    await page.goto(`${BASE_URL}/collections`);
    await page.evaluate(({ token, user_id }) => {
      localStorage.setItem('access_token', token);
      localStorage.setItem('user_id', user_id);
      document.cookie = `access_token=${token}; path=/`;
    }, { token, user_id });

    await page.goto(`${BASE_URL}/collections?id=${trip.id}`);
    await page.waitForLoadState('networkidle');

    // POI section visible even with 0 POIs (owner sees hint)
    await expect(page.getByText("Points d'intérêt")).toBeVisible();
    await expect(page.getByRole('button', { name: /Ajouter un point/ })).toBeVisible();
  });
});
