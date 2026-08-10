/**
 * Regression pin (2026-07-14, Paul): logged-in users who ARRIVE on the home `/`
 * must STAY on the home — they must NOT be auto-bounced to `/map`.
 *
 * Since the compliance pivot the home `/` is the pivot showcase for EVERYONE
 * (hero heatmap-first, "Contribuer mes traces" CTA, "how it works"). The old
 * code had a useEffect: "redirect to /map if the user was ALREADY logged in
 * when visiting /" (guarded by `wasAlreadyLoggedInRef`). That defeated the
 * showcase — Paul, permanently logged in, never saw his own home page.
 *
 * The fix REMOVES that auto-redirect. This spec seeds a logged-in state
 * (a `user_id` in localStorage — the sole signal `lib/auth.isAuthenticated()`
 * reads; the JWT itself lives in an httpOnly cookie) BEFORE the page boots
 * (via addInitScript, so `wasAlreadyLoggedInRef` would have seen it true),
 * navigates to `/`, and asserts the page STAYS on `/` with the home showcase
 * visible. It FAILS on the old auto-redirect code (URL would become /map).
 *
 * CI-safe: no WASM router, no heatmap data, no backend auth round-trip — the
 * `user_id` localStorage key is all the client reads for logged-in UI state.
 */
import { test, expect } from '@playwright/test';

test.describe('Home — logged-in arrival stays on / (no auto-redirect)', () => {
  test('a logged-in visitor landing on / is NOT bounced to /map', async ({ page }) => {
    // Seed logged-in state BEFORE any page script runs, so the component sees
    // it as "already logged in" at mount (this is exactly what used to trigger
    // the auto-redirect).
    await page.addInitScript(() => {
      localStorage.setItem('user_id', 'e2e-logged-in-user');
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Give any (now-removed) redirect effect ample time to fire.
    await page.waitForTimeout(1500);

    // 1. We STAYED on the home, not /map.
    expect(new URL(page.url()).pathname).toBe('/');

    // 2. The home showcase is actually rendered (the "Contribuer mes traces"
    //    CTA — proof this is the home, not a blank pre-redirect frame).
    await expect(page.locator('[data-testid="go-to-contribute"]')).toBeVisible();
    await expect(page.locator('[data-testid="go-to-map"]')).toBeVisible();

    // 3. The contribute CTA is the ONE canonical import entry — it navigates to
    //    /strava. The home no longer hosts any inline upload form (the two
    //    `/imports/files` forms — a residual competing entry point + a .zip→50MB
    //    trap — were removed; all contribution flows through /strava's GB-safe
    //    dropzone). Pin: no home upload input, CTA points to /strava.
    await expect(page.locator('[data-testid="go-to-contribute"]')).toHaveAttribute('href', '/strava');
    await expect(page.locator('[data-testid="quick-connect-btn"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="file-input"]')).toHaveCount(0);
  });

  test('the ?disconnected=1 banner path also stays on /', async ({ page }) => {
    // A logged-out arrival with the logout banner must also stay on the home
    // (this path was already exempt from the old redirect; pin it too).
    await page.goto('/?disconnected=1');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(500);

    expect(new URL(page.url()).pathname).toBe('/');
    await expect(page.locator('[data-testid="go-to-contribute"]')).toBeVisible();
  });
});
