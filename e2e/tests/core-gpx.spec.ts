/**
 * CHEMINS COMMUNS — Core E2E tests: GPX upload → the activity is stored and
 * readable by its owner.
 *
 * The community heatmap is the STATIC raw PMTiles (built offline by
 * `build_pmtiles`), so nothing here reads a live heatmap endpoint: the
 * matched-era `/heatmap/stats` (K-anonymity cells) and `/heatmap/trails`
 * (edges, forward/backward counts) assertions were removed with that pipeline.
 */
import { test, expect, request } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';

const API_URL = process.env.API_URL || 'http://localhost:8787';

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

// ── Test 3: Upload GPX fixture ────────────────────────────────────────────

test.describe('3. GPX Upload', () => {
  test('upload GPX fixture with contribute_heatmap=true → visible in /me/activities', async () => {
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

    // The upload is stored with its geometry and readable by its owner — the
    // rows `build_pmtiles` (raw display) and "Ma heatmap" are built from.
    const actResp = await apiContext.get(`${API_URL}/me/activities?sport=road`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(actResp.status()).toBe(200);
    const geojson = await actResp.json();
    expect(geojson.type).toBe('FeatureCollection');
    expect(geojson.total).toBeGreaterThanOrEqual(1);
    const feat = geojson.features[geojson.features.length - 1];
    expect(feat.geometry.type).toBe('LineString');
    expect(feat.properties.sport).toBe('road');
    // test_route.gpx is in Lyon (lon≈4.83, lat≈45.75)
    const [lon, lat] = feat.geometry.coordinates[0];
    expect(lon).toBeGreaterThan(4.7);
    expect(lon).toBeLessThan(5.0);
    expect(lat).toBeGreaterThan(45.6);
    expect(lat).toBeLessThan(45.9);

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
