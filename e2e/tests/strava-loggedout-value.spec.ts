/**
 * /strava — LOGGED-OUT contribution journey (source-neutral single-job page).
 *
 * A first-time, signed-out visitor must SEE the contribution value + how-to and
 * be able to sign up inline — not hit a bare wall, and not a Strava-centric one
 * (Strava is one source among GPX / FIT). After the unified-dropzone refactor
 * the logged-out state:
 *   - shows the SAME hero ContributionDropzone as logged-in (its dispatch gates
 *     on getToken(): a deposit attempt surfaces the "log in to import" message,
 *     so signing up unlocks it — the deposit is reachable right after signup),
 *   - shows the compact 3-step flow strip,
 *   - offers exactly ONE account-creation affordance (the EmailLoginForm signup
 *     gate) — no duplicate "create account" CTA,
 *   - folds the Strava-specific material (why / how-to / links) into a collapsed,
 *     HELP-ONLY "Using Strava? Get your archive" section (no picker/consent —
 *     those live once, on the hero).
 *
 * No auth state is seeded → getToken() returns null → logged-out branch.
 */
import { expect, test } from '@playwright/test';

async function openStravaHelp(page: import('@playwright/test').Page) {
  await page.locator('[data-testid="archive-strava-help"]').evaluate(
    (d: HTMLDetailsElement) => { d.open = true; },
  );
}

test.describe('/strava logged-out contribution journey', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      try { localStorage.clear(); } catch { /* ignore */ }
    });
    await page.goto('/strava');
    await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
  });

  test('hero dropzone + flow strip + single signup gate + collapsible Strava how-to', async ({ page }) => {
    // The compact 3-step flow strip is always present.
    await expect(page.getByTestId('strava-flow-strip')).toBeVisible();
    await expect(page.getByTestId('strava-contribute-section')).toBeVisible();

    // The hero deposit dropzone is shown even logged-out (deposit reachable
    // after signup; a logged-out attempt just surfaces the login message).
    await expect(page.getByTestId('strava-gpx-upload')).toBeVisible();

    // Exactly ONE account-creation affordance: the inline email signup gate.
    await expect(page.getByTestId('strava-signup-gate')).toBeVisible();
    await expect(page.getByTestId('email-login-form')).toBeVisible();

    // The Strava material lives in a collapsed HELP-ONLY section; open it and
    // check the contribution story is intact.
    await expect(page.getByTestId('archive-strava-help')).toBeVisible();
    await openStravaHelp(page);
    await expect(page.getByTestId('strava-archive-import')).toBeVisible();
    await expect(page.getByTestId('archive-why')).toBeVisible();
    await expect(page.getByTestId('archive-steps')).toBeVisible();
    await expect(page.getByTestId('archive-help-link')).toBeVisible();
    await expect(page.getByTestId('archive-export-link')).toBeVisible();
  });

  // (The subtitle "your Strava export" inline link was removed 2026-08-08 when
  // the /strava copy was made source-neutral for Strava/Garmin parity — the
  // how-to is reached via the dropzone help link + the collapsed Strava/Garmin
  // expanders instead. The dropzone-help path is covered above.)

  test('single signup gate + help carries no competing upload UI', async ({ page }) => {
    // The single account affordance is the EmailLoginForm; the old wizard
    // "create account" CTA is gone (no double CTA).
    await expect(page.getByTestId('email-login-form')).toBeVisible();
    await expect(page.getByTestId('archive-login-cta')).toHaveCount(0);

    await openStravaHelp(page);
    // HELP-ONLY: the collapsed Strava section has no picker / consent / dropzone
    // of its own (those live once, on the hero above).
    await expect(page.getByTestId('archive-consent-checkbox')).toHaveCount(0);
    await expect(page.getByTestId('archive-dropzone')).toHaveCount(0);
    await expect(page.getByTestId('archive-dropzone-preview')).toHaveCount(0);

    // The old personal-Strava "connect" affordance is gone entirely.
    await expect(page.getByRole('button', { name: /Connecter Strava|Connect Strava/i })).toHaveCount(0);
  });
});
