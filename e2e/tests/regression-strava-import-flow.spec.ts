/**
 * End-to-end: /strava is a source-neutral SINGLE-JOB contribution page.
 *
 * The compliance pivot CANCELLED the personal "connect Strava = see your own
 * rides" flow at the UI level (the backend OAuth/webhook/sync code stays but
 * dormant — no UI entry point). De-branding (2026-07): Strava is one source
 * among GPX / FIT, so the HERO is a source-neutral deposit dropzone and all the
 * Strava-specific material (bulk-archive wizard + how-to) is folded into a
 * collapsed "Using Strava? Get your archive" section.
 *
 * This spec pins:
 *   1. logged-in → the generic deposit dropzone is the hero, the Strava archive
 *      wizard is folded away, and NONE of the removed personal-connect UI is
 *      present (no "Connecter Strava", no connected card, no personal refresh).
 *   2. the folded archive wizard is guided + consent-gated, and step 2 of the
 *      how-to embeds the real Strava archive-request link.
 *   3. no React #418 hydration error along the way.
 *
 * CI-safe via TEST_MODE stubs. The actual archive upload is manual-smoke only:
 * it needs a real .zip through the signed-URL PUT, which a synthetic Playwright
 * dataTransfer cannot carry.
 */
import { expect, test } from '@playwright/test';

const API_URL = process.env.API_URL || 'http://localhost:8787';

async function openStravaHelp(page: import('@playwright/test').Page) {
  await page.locator('[data-testid="archive-strava-help"]').evaluate(
    (d: HTMLDetailsElement) => { d.open = true; },
  );
}

test.describe('Strava single-job contribution page (TEST_MODE stubs)', () => {
  test.setTimeout(180_000);

  let _backendReady = false;
  test.beforeAll(async ({ request }) => {
    test.setTimeout(180_000);
    const deadline = Date.now() + 150_000;
    while (Date.now() < deadline) {
      try {
        const resp = await request.get(`${API_URL}/startup-status`);
        if (resp.ok()) {
          const body = await resp.json();
          if (body.done === true) { _backendReady = true; return; }
        }
      } catch { /* transient — keep polling */ }
      await new Promise((r) => setTimeout(r, 2_000));
    }
    // eslint-disable-next-line no-console
    console.warn('[#325] /startup-status did not reach done=true within 150s — skipping');
  });

  test.beforeEach(() => {
    test.skip(!_backendReady, 'Backend background-load did not finish in time');
  });

  async function registerAndSeed(page: import('@playwright/test').Page, prefix: string) {
    const uid = `${Date.now()}_${Math.floor(Math.random() * 100000)}`;
    const regResp = await page.request.post(`${API_URL}/auth/register`, {
      data: { email: `${prefix}_${uid}@example.com`, password: 'testpassword123', username: `${prefix}user_${uid}` },
    });
    expect(regResp.status()).toBe(201);
    const reg = await regResp.json();
    await page.addInitScript(([t, u]) => {
      try {
        localStorage.setItem('ct_token', t);
        localStorage.setItem('user_id', u);
      } catch { /* ignore */ }
    }, [reg.access_token as string, reg.user_id as string]);
  }

  test('logged-in: generic deposit is the hero, Strava is folded, NO personal-connect UI', async ({ page }) => {
    await registerAndSeed(page, 'strava_e2e');

    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await page.goto('/strava');
    await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {});

    // The source-neutral deposit dropzone is the hero action.
    await expect(page.getByTestId('strava-contribute-section')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('strava-gpx-upload')).toBeVisible();

    // The Strava archive wizard is present but folded into the collapsed section.
    await expect(page.getByTestId('archive-strava-help')).toBeVisible();
    await openStravaHelp(page);
    await expect(page.getByTestId('strava-archive-import')).toBeVisible();

    // The removed personal-Strava affordances must be GONE.
    await expect(page.getByRole('button', { name: /Connecter Strava|Connect Strava/i })).toHaveCount(0);
    await expect(page.getByTestId('strava-connected-card')).toHaveCount(0);
    await expect(page.getByTestId('strava-personal-refresh')).toHaveCount(0);
    await expect(page.getByText(/Préparation de l'import/i)).toHaveCount(0);

    const hydrationErrors = errors.filter(
      (m) => /Minified React error #418|Hydration failed|did not match/i.test(m),
    );
    expect(hydrationErrors, `Hydration errors: ${hydrationErrors.join('\n')}`).toHaveLength(0);
  });

  // The folded Strava help is now HELP-ONLY (unified-dropzone refactor): it
  // explains WHY + HOW to get the archive out of Strava and links to the two
  // Strava pages — the deposit itself (picker + ODbL consent) lives ONCE, in the
  // hero ContributionDropzone. This pins that the help carries no competing
  // upload UI (no dropzone / consent / picker) but keeps the guided how-to.
  test('folded Strava help is instructional-only, step 2 links out', async ({ page }) => {
    await registerAndSeed(page, 'arch_e2e');

    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await page.goto('/strava');
    await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {});

    await openStravaHelp(page);
    const help = page.getByTestId('strava-archive-import');
    await expect(help).toBeVisible({ timeout: 15_000 });

    // The motivating "why" + the visual, numbered how-to (4 steps).
    await expect(page.getByTestId('archive-why')).toBeVisible();
    await expect(page.getByTestId('archive-steps').locator('li')).toHaveCount(4);

    // Step 2 embeds the real Strava archive-request link (the copy-fix).
    await expect(
      page.getByTestId('archive-steps').locator('a[href="https://www.strava.com/athlete/delete_your_account"]'),
    ).toBeVisible();

    // The two Strava links to GET the archive.
    await expect(page.getByTestId('archive-help-link')).toHaveAttribute('href', /support\.strava\.com/);
    await expect(page.getByTestId('archive-export-link')).toHaveAttribute('href', /strava\.com/);

    // HELP-ONLY: no competing upload UI lives here anymore (the hero owns it).
    await expect(page.getByTestId('archive-dropzone')).toHaveCount(0);
    await expect(page.getByTestId('archive-consent-checkbox')).toHaveCount(0);
    await expect(page.getByTestId('archive-zip-label')).toHaveCount(0);

    // The picker lives ONCE, on the hero dropzone above. The consent GATE was
    // removed (2026-08-07); only a passive ODbL note remains.
    await expect(page.getByTestId('hero-consent-note')).toBeVisible();

    const hydrationErrors = errors.filter(
      (m) => /Minified React error #418|Hydration failed|did not match/i.test(m),
    );
    expect(hydrationErrors, `Hydration errors: ${hydrationErrors.join('\n')}`).toHaveLength(0);
  });
});
