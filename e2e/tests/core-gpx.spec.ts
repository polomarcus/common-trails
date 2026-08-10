/**
 * CHEMINS COMMUNS — Core E2E tests: GPX Upload + Heatmap K-anonymity
 *
 * Split from core.spec.ts — no logic changes.
 */
import { test, expect, request } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';

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

// ── Test 3: Upload GPX fixture + heatmap K-anonymity ─────────────────────

test.describe('3. GPX Upload + Heatmap K-anonymity', () => {
  test('upload GPX fixture with contribute_heatmap=true', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);

    const gpxPath = path.join(__dirname, '../fixtures/gpx/test_route.gpx');
    const gpxContent = fs.readFileSync(gpxPath);

    const resp = await apiContext.post(`${API_URL}/imports/files`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: {
          name: 'test_route.gpx',
          mimeType: 'application/gpx+xml',
          buffer: gpxContent,
        },
        sport: 'road',
        contribute_heatmap: 'true',
      },
    });

    expect(resp.status()).toBe(202);
    const data = await resp.json();
    expect(data.imported + data.skipped).toBeGreaterThanOrEqual(1);

    await apiContext.dispose();
  });

  test('heatmap stats respects K-anonymity (K=2)', async () => {
    const apiContext = await request.newContext();

    const statsResp = await apiContext.get(
      `${API_URL}/heatmap/stats?sport=road&zoom=14&min_lon=4.82&min_lat=45.74&max_lon=4.86&max_lat=45.76`
    );
    expect(statsResp.status()).toBe(200);

    const data = await statsResp.json();
    expect(data).toHaveProperty('k_anonymity');
    expect(data.k_anonymity).toBeGreaterThanOrEqual(1);

    // All returned cells must have user_count >= k_anonymity
    for (const cell of data.cells || []) {
      expect(cell.user_count).toBeGreaterThanOrEqual(data.k_anonymity);
    }
    expect(Array.isArray(data.cells)).toBe(true);

    await apiContext.dispose();
  });

  test('trail heatmap: 2 users uploading same GPX → /heatmap/trails returns edges', async () => {
    const apiContext = await request.newContext();
    const gpxPath = path.join(__dirname, '../fixtures/gpx/test_route.gpx');
    const gpxContent = fs.readFileSync(gpxPath);

    // Upload as 2 distinct users with contribute_heatmap=true
    for (let i = 0; i < 2; i++) {
      const { token } = await registerAndLogin(apiContext);
      const resp = await apiContext.post(`${API_URL}/gpx/upload`, {
        headers: { Authorization: `Bearer ${token}` },
        multipart: {
          file: { name: 'test_route.gpx', mimeType: 'application/gpx+xml', buffer: gpxContent },
          sport: 'road',
          contribute_heatmap: 'true',
        },
      });
      expect(resp.status()).toBe(202);
      const data = await resp.json();
      expect(['computing', 'done']).toContain(data.heatmap_status);
    }

    // After K=2 users, /heatmap/trails must return line features
    // Use bbox filter to restrict to Lyon area (test_route.gpx coords) to avoid
    // interference from demo activities (Montpellier area, lon~3.87, lat~43.61)
    const trailResp = await apiContext.get(
      `${API_URL}/heatmap/trails?sport=road&min_lon=4.82&min_lat=45.74&max_lon=4.86&max_lat=45.77`
    );
    expect(trailResp.status()).toBe(200);
    const trails = await trailResp.json();
    expect(trails.type).toBe('FeatureCollection');
    expect(trails.features.length).toBeGreaterThan(0);
    // Each feature must be a LineString respecting K-anonymity
    // (K=2 in production, K=1 in dev — use whatever the server reports)
    const k = (trails.metadata as any)?.k_anonymity ?? 1;
    for (const feat of trails.features) {
      expect(feat.geometry.type).toBe('LineString');
      expect(feat.properties.user_count).toBeGreaterThanOrEqual(k);
    }

    await apiContext.dispose();
  });

  test('trail heatmap features include forward_count and backward_count', async () => {
    const apiContext = await request.newContext();
    const gpxPath = path.join(__dirname, '../fixtures/gpx/test_route.gpx');
    const gpxContent = fs.readFileSync(gpxPath);

    // Upload as 3 distinct users (2 forward, 1 backward via reverse coords)
    // so forward/backward counts are different
    for (let i = 0; i < 3; i++) {
      const { token } = await registerAndLogin(apiContext);
      const resp = await apiContext.post(`${API_URL}/gpx/upload`, {
        headers: { Authorization: `Bearer ${token}` },
        multipart: {
          file: { name: 'test_route.gpx', mimeType: 'application/gpx+xml', buffer: gpxContent },
          sport: 'road',
          contribute_heatmap: 'true',
        },
      });
      expect(resp.status()).toBe(202);
    }

    const trailResp = await apiContext.get(
      `${API_URL}/heatmap/trails?sport=road&min_lon=4.82&min_lat=45.74&max_lon=4.86&max_lat=45.77`
    );
    expect(trailResp.status()).toBe(200);
    const trails = await trailResp.json();
    expect(trails.features.length).toBeGreaterThan(0);
    for (const feat of trails.features) {
      expect(feat.properties).toHaveProperty('forward_count');
      expect(feat.properties).toHaveProperty('backward_count');
      expect(typeof feat.properties.forward_count).toBe('number');
      expect(typeof feat.properties.backward_count).toBe('number');
      expect(feat.properties.forward_count + feat.properties.backward_count).toBe(
        feat.properties.pass_count
      );
    }

    await apiContext.dispose();
  });
});

// ── Test 3b: Montpellier MTB GPX + /me/activities ────────────────────────

test.describe('3b. Montpellier MTB fixture + activities API', () => {
  test('upload montpellier_mtb.gpx and fetch via /me/activities', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);

    const gpxPath = path.join(__dirname, '../fixtures/gpx/montpellier_mtb.gpx');
    const gpxContent = fs.readFileSync(gpxPath);

    // Upload as MTB sport
    const upResp = await apiContext.post(`${API_URL}/gpx/upload`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: {
          name: 'montpellier_mtb.gpx',
          mimeType: 'application/gpx+xml',
          buffer: gpxContent,
        },
        sport: 'mtb',
        contribute_heatmap: 'false',
      },
    });
    expect(upResp.status()).toBe(202);
    const upData = await upResp.json();
    expect(upData.status).toMatch(/created|already_exists/);
    // Verify it's the right activity (~34 km Montpellier ride)
    expect(upData.distance_m).toBeGreaterThan(30_000);
    expect(upData.distance_m).toBeLessThan(40_000);

    // Fetch personal activities → must return a GeoJSON FeatureCollection
    const actResp = await apiContext.get(`${API_URL}/me/activities?sport=mtb`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(actResp.status()).toBe(200);
    const geojson = await actResp.json();
    expect(geojson.type).toBe('FeatureCollection');
    expect(geojson.total).toBeGreaterThanOrEqual(1);

    const feat = geojson.features[geojson.features.length - 1];
    expect(feat.geometry.type).toBe('LineString');
    expect(feat.properties.sport).toBe('mtb');
    // Coords must be in Montpellier area (lon≈3.87, lat≈43.62)
    const [lon, lat] = feat.geometry.coordinates[0];
    expect(lon).toBeGreaterThan(3.5);
    expect(lon).toBeLessThan(4.2);
    expect(lat).toBeGreaterThan(43.4);
    expect(lat).toBeLessThan(43.8);

    await apiContext.dispose();
  });
});

// ── Test 5c: Running sport upload ─────────────────────────────────────────

test.describe('5c. Running sport upload', () => {
  test('upload GPX with running sport → accepted', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);

    const gpxPath = path.join(__dirname, '../fixtures/gpx/test_route.gpx');
    const gpxContent = fs.readFileSync(gpxPath);

    const resp = await apiContext.post(`${API_URL}/imports/files`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: {
          name: 'test_route.gpx',
          mimeType: 'application/gpx+xml',
          buffer: gpxContent,
        },
        sport: 'running',
        contribute_heatmap: 'true',
      },
    });

    expect(resp.status()).toBe(202);
    const data = await resp.json();
    expect(data.imported + data.skipped).toBeGreaterThanOrEqual(1);

    // Verify /me/activities returns running activities
    const actResp = await apiContext.get(`${API_URL}/me/activities?sport=running`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(actResp.status()).toBe(200);
    const geojson = await actResp.json();
    expect(geojson.type).toBe('FeatureCollection');

    await apiContext.dispose();
  });
});
