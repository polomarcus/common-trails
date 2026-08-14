/**
 * Non-regression: /map is a STATIC export (built logged-out). The account
 * button in components/map/MapToolbar rendered its label from getToken()
 * (reads localStorage) DURING render — so a logged-in visitor's first client
 * render ("account") mismatched the build's HTML ("create account"), a React
 * hydration text mismatch (#418) that crashed /map for every logged-in user
 * (reported on Firefox 2026-08-14). The fix reads auth in a mount effect, so
 * first paint matches the build. This asserts /map hydrates with no #418 when a
 * user_id is present in localStorage (the exact trigger).
 */
import { test, expect } from '@playwright/test';

test('/map hydrates cleanly for a logged-in visitor (no React #418)', async ({ page }) => {
  const hydrationErrors: string[] = [];
  page.on('pageerror', (e) => {
    if (/#41[0-9]|#42[0-9]|hydrat|Minified React/i.test(e.message)) {
      hydrationErrors.push(e.message);
    }
  });
  // The trigger: a stored user_id makes the app render the logged-in account UI.
  await page.addInitScript(() => {
    localStorage.setItem('user_id', 'e2e-hydration');
    localStorage.setItem('cc_beta_dismissed', '1');
  });
  await page.goto('/map?lat=43.62&lon=3.877&zoom=12', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);
  expect(hydrationErrors, `hydration errors: ${hydrationErrors.join(' | ')}`).toEqual([]);
});
