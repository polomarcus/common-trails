/**
 * "Ma heatmap" served from the PER-USER PRIVATE PMTiles file — the path prod
 * actually runs (docs/ma-heatmap-fichier-prive.md), end to end in a browser.
 *
 * Until 2026-11-11 the E2E suite only exercised the legacy JSON path
 * (`GET /me/activities?limit=5000`): the file path — manifest → summary →
 * authenticated Range reads of the .pmtiles → a `vector` MapLibre source —
 * was covered by backend unit tests only, while every prod account uses it.
 *
 *   1. register, import one GPX → the manifest says `missing` (no file yet);
 *   2. build the user's file with the REAL CLI (`E2E_MY_HEATMAP_BUILD_CMD`,
 *      set in CI to a `docker compose exec` of `app.cli.my_heatmap_files
 *      backfill`; in TEST_MODE nothing schedules the build on its own);
 *   3. the manifest is `ready`; /map loads the summary and Range-reads the
 *      tiles, the `user-activities` source is `vector`, and the legacy
 *      `/me/activities?limit=` call is NOT made;
 *   4. the tiles are private: no session → no bytes.
 *
 * Skipped when `E2E_MY_HEATMAP_BUILD_CMD` is unset (a local run against a
 * shared stack, where the flag is off and nothing may be built).
 */
import { execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

import { expect, request, test } from '@playwright/test';

const API_URL = process.env.API_URL || 'http://localhost:8787';
const BUILD_CMD = process.env.E2E_MY_HEATMAP_BUILD_CMD || '';

test.describe('Ma heatmap — per-user private PMTiles file, in the browser', () => {
  test.skip(!BUILD_CMD, 'E2E_MY_HEATMAP_BUILD_CMD unset — the private-file path is only built in CI');
  test.setTimeout(120_000);

  test('import → build → /map reads the private file (no legacy JSON), tiles need the session', async ({ page, context }) => {
    // ── 1. A fresh rider with one imported trace ──
    const uid = `${Date.now()}_${Math.floor(Math.random() * 100000)}`;
    const reg = await page.request.post(`${API_URL}/auth/register`, {
      data: { email: `mhf_${uid}@example.com`, password: 'testpassword123', username: `mhf_${uid}` },
    });
    expect(reg.status()).toBe(201);
    const { access_token: token, user_id: userId } = await reg.json();
    const auth = { Authorization: `Bearer ${token}` };

    const gpx = fs.readFileSync(path.join(__dirname, '../fixtures/gpx/test_route.gpx'));
    const up = await page.request.post(`${API_URL}/imports/files`, {
      headers: auth,
      multipart: {
        file: { name: 'test_route.gpx', mimeType: 'application/gpx+xml', buffer: gpx },
        sport: 'gravel',
        contribute_heatmap: 'true',
      },
    });
    expect(up.ok(), await up.text()).toBe(true);

    const before = await page.request.get(`${API_URL}/me/heatmap/manifest`, { headers: auth });
    expect(before.status()).toBe(200);
    expect((await before.json()).status).toBe('missing');

    // ── 2. Build this user's file with the real CLI ──
    execSync(`${BUILD_CMD} --user-id ${userId}`, { stdio: 'inherit', timeout: 90_000 });

    const after = await page.request.get(`${API_URL}/me/heatmap/manifest`, { headers: auth });
    const manifest = await after.json();
    expect(manifest.status).toBe('ready');
    expect(manifest.activity_count).toBe(1);
    expect(manifest.tiles_url).toMatch(/^\/me\/heatmap\/r\d+-[0-9a-f]{16}\.pmtiles$/);

    // ── 3. /map uses the file ──
    // The file requests authenticate with the session COOKIE (credentials:
    // 'include', no Authorization header) — make sure the browser has it.
    await context.addCookies([{ name: 'auth_token', value: token, url: API_URL }]);
    await page.addInitScript(([t, u]) => {
      try {
        localStorage.setItem('ct_token', t);
        localStorage.setItem('user_id', u);
      } catch { /* ignore */ }
    }, [token as string, userId as string]);

    const legacyCalls: string[] = [];
    page.on('request', (r) => {
      if (/\/me\/activities\?/.test(r.url())) legacyCalls.push(r.url());
    });
    const summary = page.waitForResponse(
      (r) => /\/me\/heatmap\/[^/]+\.summary\.json$/.test(r.url()) && r.status() === 200,
      { timeout: 60_000 },
    );
    const tiles = page.waitForResponse(
      (r) => /\/me\/heatmap\/[^/]+\.pmtiles$/.test(r.url()) && r.status() === 206,
      { timeout: 60_000 },
    );
    await page.goto('/map');
    const summaryBody = await (await summary).json();
    expect(summaryBody.total).toBe(1);
    expect(summaryBody.activities[0].sport).toBe('gravel');
    const tileResp = await tiles;
    expect(tileResp.headers()['cache-control']).toContain('private');
    expect(tileResp.request().headers()['range']).toMatch(/^bytes=\d+-\d+$/);

    await page.waitForFunction(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const m = (window as any).__mapInstance;
      return m?.getSource?.('user-activities')?.type === 'vector';
    }, undefined, { timeout: 30_000 });
    const layer = await page.evaluate(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const m = (window as any).__mapInstance;
      const l = m.getStyle().layers.find((x: { id: string }) => x.id === 'user-activities-line');
      return { sourceLayer: l?.['source-layer'], visibility: m.getLayoutProperty('user-activities-line', 'visibility') };
    });
    expect(layer.sourceLayer).toBe('activities');
    expect(layer.visibility).not.toBe('none');
    // The legacy JSON fallback (every trace's geometry in one response) is not used.
    expect(legacyCalls).toEqual([]);

    // ── 4. Private: the same URL without a session returns no bytes ──
    const anon = await request.newContext();
    const leaked = await anon.get(`${API_URL}${manifest.tiles_url}`, { headers: { Range: 'bytes=0-511' } });
    expect([401, 403, 404]).toContain(leaked.status());
    await anon.dispose();
  });
});
