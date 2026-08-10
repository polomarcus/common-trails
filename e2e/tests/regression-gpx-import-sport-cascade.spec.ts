/**
 * Regression: GPX import end-to-end + sport-from-track-type cascade.
 *
 * Two coverage gaps closed by this file:
 *
 * 1. **Sport cascade** — PR #302 added the `<trk><type>` → sport mapping
 *    so Garmin Connect exports auto-classify (mountain_biking → mtb,
 *    gravel_cycling → gravel). Unit tests in
 *    `backend/tests/test_gpx_sport_classification.py` pin the parser
 *    behaviour, but no E2E test confirmed the cascade reaches the stored
 *    Activity row when the form-supplied sport disagrees.
 *
 * 2. **UI-level import** — every existing E2E test in `core-gpx.spec.ts`
 *    uploads via `apiContext.post(/imports/files)`. The actual user-facing
 *    flow (open the modal, drop a file, submit, see success) had zero
 *    coverage until this file.
 */
import path from 'node:path';
import fs from 'node:fs';

import { expect, request, test } from '@playwright/test';

const API_URL = process.env.API_URL || 'http://localhost:8787';

/** Register a fresh user via API; return JWT + user_id. */
async function registerAndLogin(
  apiContext: Awaited<ReturnType<typeof request.newContext>>,
): Promise<{ token: string; userId: string; email: string }> {
  const uid = `${Date.now()}_${Math.floor(Math.random() * 100000)}`;
  const email = `gpx_import_${uid}@example.com`;
  const password = 'testpassword123';
  const username = `gpxuser_${uid}`;

  const regResp = await apiContext.post(`${API_URL}/auth/register`, {
    data: { email, password, username },
  });
  expect(regResp.status()).toBe(201);
  const data = await regResp.json();
  return { token: data.access_token, userId: data.user_id, email };
}

// ── Part 1 — API-level sport cascade ───────────────────────────────────

test.describe('GPX sport cascade — Garmin <trk><type> overrides form sport', () => {
  test('Garmin MTB fixture (<type>mountain_biking</type>) classified as mtb even when form=road', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);

    // mtb_pic_st_loup.gpx carries `<type>mountain_biking</type>` —
    // verified at fixture import time. The form deliberately submits
    // sport=road; the GPX type should win.
    const gpxPath = path.join(__dirname, '../fixtures/gpx/mtb_pic_st_loup.gpx');
    const gpxContent = fs.readFileSync(gpxPath);
    expect(gpxContent.toString()).toContain('<type>mountain_biking</type>');

    const upload = await apiContext.post(`${API_URL}/imports/files`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: { name: 'mtb_pic_st_loup.gpx', mimeType: 'application/gpx+xml', buffer: gpxContent },
        sport: 'road', // intentionally wrong
        contribute_heatmap: 'false',
      },
    });
    expect(upload.status()).toBe(202);
    const upBody = await upload.json();
    expect(upBody.imported).toBe(1);
    expect(upBody.activity_ids.length).toBe(1);
    const activityId = upBody.activity_ids[0];

    // Verify the stored activity has sport=mtb (cascade won, form lost)
    const meActs = await apiContext.get(`${API_URL}/me/activities?limit=100`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(meActs.status()).toBe(200);
    const geo = await meActs.json();
    const ours = (geo.features || []).find(
      (f: { properties: { id: string } }) => f.properties.id === activityId,
    );
    expect(ours, `activity ${activityId} not in /me/activities`).toBeTruthy();
    expect(ours.properties.sport).toBe('mtb');

    await apiContext.dispose();
  });

  test('Garmin gravel fixture (<type>gravel_cycling</type>) classified as gravel even when form=mtb', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);

    const gpxPath = path.join(__dirname, '../fixtures/gpx/gravel_garrigue.gpx');
    const gpxContent = fs.readFileSync(gpxPath);
    expect(gpxContent.toString()).toContain('<type>gravel_cycling</type>');

    const upload = await apiContext.post(`${API_URL}/imports/files`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: { name: 'gravel_garrigue.gpx', mimeType: 'application/gpx+xml', buffer: gpxContent },
        sport: 'mtb', // intentionally wrong
        contribute_heatmap: 'false',
      },
    });
    expect(upload.status()).toBe(202);
    const body = await upload.json();
    expect(body.imported).toBe(1);
    const activityId = body.activity_ids[0];

    const meActs = await apiContext.get(`${API_URL}/me/activities?limit=100`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const geo = await meActs.json();
    const ours = (geo.features || []).find(
      (f: { properties: { id: string } }) => f.properties.id === activityId,
    );
    expect(ours.properties.sport).toBe('gravel');

    await apiContext.dispose();
  });

  test('Strava generic <type>cycling</type> falls back to form-supplied sport', async () => {
    // Strava bulk export collapses every cycling variant to plain "cycling".
    // The cascade must defer to the form-supplied sport in that case.
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);

    const generic = Buffer.from(
      `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="strava.com" xmlns="http://www.topografix.com/GPX/1/1">
  <trk>
    <name>Strava generic cycling export</name>
    <type>cycling</type>
    <trkseg>
      <trkpt lat="43.61" lon="3.87"><ele>100</ele></trkpt>
      <trkpt lat="43.62" lon="3.88"><ele>105</ele></trkpt>
      <trkpt lat="43.63" lon="3.89"><ele>110</ele></trkpt>
    </trkseg>
  </trk>
</gpx>`,
      'utf-8',
    );

    const upload = await apiContext.post(`${API_URL}/imports/files`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: { name: 'strava_generic.gpx', mimeType: 'application/gpx+xml', buffer: generic },
        sport: 'gravel', // form value should win for generic GPX
        contribute_heatmap: 'false',
      },
    });
    expect(upload.status()).toBe(202);
    const body = await upload.json();
    expect(body.imported).toBe(1);
    const activityId = body.activity_ids[0];

    const meActs = await apiContext.get(`${API_URL}/me/activities?limit=100`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const geo = await meActs.json();
    const ours = (geo.features || []).find(
      (f: { properties: { id: string } }) => f.properties.id === activityId,
    );
    expect(ours.properties.sport).toBe('gravel'); // form sport won

    await apiContext.dispose();
  });
});

// ── Part 2 — dedup happy-path (the "1 ignorée" case Paul hit) ─────────

test.describe('GPX import dedup', () => {
  test('uploading the same GPX twice → second upload reports skipped not imported', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);

    const gpxPath = path.join(__dirname, '../fixtures/gpx/test_route.gpx');
    const gpxContent = fs.readFileSync(gpxPath);

    const first = await apiContext.post(`${API_URL}/imports/files`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: { name: 'test_route.gpx', mimeType: 'application/gpx+xml', buffer: gpxContent },
        sport: 'road',
        contribute_heatmap: 'false',
      },
    });
    expect(first.status()).toBe(202);
    const firstBody = await first.json();
    expect(firstBody.imported).toBe(1);
    expect(firstBody.skipped).toBe(0);

    const second = await apiContext.post(`${API_URL}/imports/files`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: { name: 'test_route.gpx', mimeType: 'application/gpx+xml', buffer: gpxContent },
        sport: 'road',
        contribute_heatmap: 'false',
      },
    });
    expect(second.status()).toBe(202);
    const secondBody = await second.json();
    expect(secondBody.imported).toBe(0);
    expect(secondBody.skipped).toBe(1);

    await apiContext.dispose();
  });
});

// ── Part 3 — UI-level: the map "import" is a thin signpost → /strava ─────

test.describe('Map import launcher → /strava', () => {
  // Unified-dropzone refactor (2026-07): there is ONE canonical import place —
  // /strava, with a single smart dropzone (GPX / FIT / archive .zip) + one ODbL
  // consent. The map no longer carries a second, competing upload form. The
  // in-menu "Import / Contribute" entry now opens a thin launcher modal whose
  // only action is a link to /strava. The actual GPX→success upload is covered
  // by the Part 1/2 API tests + unified-dropzone-routing.spec.ts.
  test('logged-in user opens the import launcher and is sent to /strava', async ({ page, context }) => {
    // Register via API, then seed the httpOnly cookie + user_id localStorage
    // flag so /map loads authenticated. Mirrors setBrowserAuth().
    const apiContext = await request.newContext();
    const { token, userId } = await registerAndLogin(apiContext);
    await apiContext.dispose();

    const url = new URL(process.env.BASE_URL || 'http://localhost:3787');
    await context.addCookies([{
      name: 'auth_token',
      value: token,
      domain: url.hostname,
      path: '/',
      httpOnly: true,
      sameSite: 'Lax',
    }]);
    await context.addInitScript((uid: string) => {
      localStorage.setItem('user_id', uid);
    }, userId);

    await page.goto('/map');

    // Step 1 — open the user menu (`map-import-btn` is the account button).
    const userMenuBtn = page.getByTestId('map-import-btn');
    await expect(userMenuBtn).toBeVisible({ timeout: 15_000 });
    await userMenuBtn.click();

    // Step 2 — click "Import / Contribute" inside the menu → thin launcher.
    const openImport = page.getByTestId('map-open-import');
    await expect(openImport).toBeVisible({ timeout: 5_000 });
    await openImport.click();

    // Step 3 — the launcher is a signpost, NOT a second upload form: no file
    // picker / sport select / consent live here anymore.
    await expect(page.getByTestId('import-launcher')).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId('map-file-input')).toHaveCount(0);
    await expect(page.getByTestId('map-sport-select')).toHaveCount(0);

    // Step 4 — its one action navigates to the canonical import place, /strava,
    // where the single hero dropzone lives.
    await page.getByTestId('import-goto-strava').click();
    await expect(page).toHaveURL(/\/strava/, { timeout: 10_000 });
    await expect(page.getByTestId('strava-gpx-upload')).toBeVisible({ timeout: 15_000 });
  });
});
