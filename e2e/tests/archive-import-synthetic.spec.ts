/**
 * Full archive import through the REAL dropzone, with a SYNTHETIC Strava
 * bulk export (the repo is public — never a real one).
 *
 * The zip is built at test time by `backend/tests/synthetic_exports.py`
 * (stdlib-only, deterministic): nested `export_123/` root + `__MACOSX/` junk,
 * real activities.csv headers, out-of-scope types, GPX / FIT / FIT.gz incl. the
 * mis-sized-field FIT, Strava `routes/`, a duplicate, an empty track, an
 * unlisted file… The backend test `test_archive_import_e2e.py` asserts every
 * per-member outcome; this spec pins the browser half:
 *
 *   1. /strava → pick the .zip on the archive block → the pre-send recap
 *      (no sport picked → "files without a sport will be skipped" + the
 *      activities.csv note) → Envoyer → init / PUT / complete all succeed →
 *      the "Archive reçue" card.
 *   2. When the harness can run the drain job (`E2E_ARCHIVE_DRAIN_CMD`, set in
 *      CI to a `docker compose exec` of `app.jobs.ingest_pending_archives`),
 *      run it and check the rides reach "Ma heatmap" (its data source,
 *      `/me/activities`) with the right sports and no planned route. Without
 *      it (local run against a shared stack) the spec stops at upload
 *      completion — the drain must never be fired at someone's dev queue.
 */
import { execFileSync, execSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { expect, test } from '@playwright/test';

const API_URL = process.env.API_URL || 'http://localhost:8787';
const DRAIN_CMD = process.env.E2E_ARCHIVE_DRAIN_CMD || '';
const GENERATOR = path.join(__dirname, '../../backend/tests/synthetic_exports.py');

interface Expected {
  members_total: number;
  imported: number;
  skipped: number;
  failed: number;
  sports: string[];
}

function buildSyntheticStravaZip(): { zipPath: string; expected: Expected } {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ct-synth-export-'));
  tmpDirs.push(dir);
  const zipPath = path.join(dir, 'export_123.zip');
  const out = execFileSync('python3', [GENERATOR, 'strava', zipPath], { encoding: 'utf-8' });
  return { zipPath, expected: JSON.parse(out.trim().split('\n').pop() as string) };
}

const tmpDirs: string[] = [];

test.describe('Archive import — synthetic Strava export through the dropzone', () => {
  test.setTimeout(120_000);

  test.afterAll(() => {
    for (const d of tmpDirs) fs.rmSync(d, { recursive: true, force: true });
  });

  test('recap → upload → "archive reçue" → (drain) → rides in Ma heatmap', async ({ page }) => {
    const { zipPath, expected } = buildSyntheticStravaZip();

    const uid = `${Date.now()}_${Math.floor(Math.random() * 100000)}`;
    const reg = await page.request.post(`${API_URL}/auth/register`, {
      data: { email: `synth_${uid}@example.com`, password: 'testpassword123', username: `synth_${uid}` },
    });
    expect(reg.status()).toBe(201);
    const { access_token: token, user_id: userId } = await reg.json();
    await page.addInitScript(([t, u]) => {
      try {
        localStorage.setItem('ct_token', t);
        localStorage.setItem('user_id', u);
      } catch { /* ignore */ }
    }, [token as string, userId as string]);

    await page.goto('/strava');
    await expect(page.getByTestId('archive-block')).toBeVisible({ timeout: 15_000 });

    // ── Pick the .zip on the archive block → pre-send recap ──
    await page.getByTestId('archive-block').locator('input[type="file"]').setInputFiles(zipPath);
    const recap = page.getByTestId('upload-recap');
    await expect(recap).toBeVisible();
    await expect(page.getByTestId('upload-recap-text')).toHaveText(
      /sans sport de cette archive seront ignorés|with no sport will be skipped/,
    );
    await expect(recap).toContainText(/activities\.csv/);

    // ── Envoyer → init / PUT / complete, then the "received" card ──
    const completed = page.waitForResponse(
      (r) => r.url().includes('/imports/strava-archive/complete') && r.request().method() === 'POST',
    );
    const stored = page.waitForResponse(
      (r) => r.url().includes('/imports/strava-archive/upload/') && r.request().method() === 'PUT',
    );
    await page.getByTestId('upload-recap-confirm').click();
    const put = await stored;
    expect(put.status()).toBe(200);
    const complete = await completed;
    expect(complete.status()).toBe(202);
    expect((await complete.json()).status).toBe('uploaded');
    await expect(page.getByTestId('strava-gpx-msg')).toHaveText(/Archive reçue|Archive received/, {
      timeout: 30_000,
    });
    await expect(page.getByTestId('archive-see-map')).toBeVisible();

    if (!DRAIN_CMD) {
      test.info().annotations.push({
        type: 'note',
        description: 'E2E_ARCHIVE_DRAIN_CMD unset — stopped at upload completion (drain not run).',
      });
      return;
    }

    // ── Run the real drain job, then check "Ma heatmap"'s data source ──
    execSync(DRAIN_CMD, { stdio: 'inherit', timeout: 90_000 });

    const me = await page.request.get(`${API_URL}/me/activities?limit=5000`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(me.status()).toBe(200);
    const fc = await me.json();
    expect(fc.total).toBe(expected.imported);
    const features = fc.features as { properties: { sport: string; name: string } }[];
    expect(features.map((f) => f.properties.sport).sort()).toEqual(expected.sports);
    // Strava planned routes (routes/*.gpx) never become activities.
    for (const f of features) {
      expect(['Boucle prevue', 'Itineraire']).not.toContain(f.properties.name);
    }

    const status = await page.request.get(`${API_URL}/me/contribution-status`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(status.status()).toBe(200);
    expect((await status.json()).count).toBe(expected.imported);
  });
});
