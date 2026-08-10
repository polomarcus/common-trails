/**
 * End-to-end: the ContributionDropzone has NO consent GATE (Paul 2026-08-07:
 * "le consentement on l'oublie"). The mandatory tick killed contributions, so:
 *   - there is NO consent checkbox / gate any more;
 *   - the ODbL wording stays VISIBLE as a passive note (transparency);
 *   - the upload file inputs are ENABLED immediately (uploading is the
 *     affirmative act; consent is recorded server-side on every upload).
 *
 * No backend needed: auth is faked with the `user_id` localStorage flag that
 * getToken() reads (lib/auth.ts). We never pick a file, so no upload runs.
 */
import { expect, test } from '@playwright/test';

test.describe('ContributionDropzone — no consent gate, uploads enabled', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      try { localStorage.setItem('user_id', 'e2e-user'); } catch { /* ignore */ }
    });
  });

  test('no consent checkbox; ODbL note visible; upload inputs enabled with no interaction', async ({ page }) => {
    await page.goto('/strava');

    const hero = page.getByTestId('strava-gpx-upload');
    await expect(hero).toBeVisible({ timeout: 15_000 });

    // The gate + checkbox are GONE.
    await expect(page.getByTestId('hero-consent-gate')).toHaveCount(0);
    await expect(page.getByTestId('hero-consent-checkbox')).toHaveCount(0);

    // The passive ODbL note stays (transparency).
    await expect(page.getByTestId('hero-consent-note')).toBeVisible();

    // Uploads are enabled immediately — no tick required.
    await expect(page.getByTestId('archive-block').locator('input[type="file"]')).toBeEnabled();
    await expect(page.getByTestId('files-block').locator('input[type="file"]')).toBeEnabled();
  });
});
