/**
 * Regression: hydration mismatches on /strava (React error #418).
 *
 * Same class of bug as `/map` (fixed in PR #310): `useState(() => …)` /
 * `useRef(...)` initialisers reading browser-only state during the first
 * render. With Next.js `output: 'export'`, /strava/index.html is built
 * at SSG time when `typeof window === 'undefined'` so those initialisers
 * return their SSR fallback. On the client they return user-specific
 * values → static HTML doesn't match first client render → React #418
 * → the entire tree regenerates and the import page silently fails.
 *
 * This test pre-seeds the browser state a returning user would carry,
 * opens /strava, and asserts no `pageerror` event fires during hydration.
 */
import { expect, test } from '@playwright/test';

test.describe('regression: /strava hydration', () => {
  test('returning user with auth state does not emit React #418', async ({
    page,
    context,
  }) => {
    // Seed the localStorage values a logged-in returning user carries.
    // If `useState(() => getToken())` reads localStorage during init,
    // SSG returns null, client returns the token → mismatch → React #418.
    await context.addInitScript(() => {
      try {
        localStorage.setItem('user_id', 'test-user-id');
        localStorage.setItem('cc_strava_name', 'Test User');
      } catch { /* ignore */ }
    });

    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await page.goto('/strava');

    // Wait long enough for React to mount, hydrate, run effects.
    await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
    await page.waitForTimeout(1500);

    const hydrationErrors = errors.filter(
      (m) => /Minified React error #418|Hydration failed|did not match/i.test(m),
    );
    expect(
      hydrationErrors,
      `Hydration errors on /strava: ${hydrationErrors.join('\n')}`,
    ).toHaveLength(0);
  });

  test('/strava with ?user_id query param does not emit React #418', async ({
    page,
    context,
  }) => {
    // The original Strava-OAuth redirect lands on `/strava?user_id=<uuid>`,
    // and the previous useState initialiser parsed that URL during init
    // (only on the client, not on SSG). The fix moves URL parsing into
    // useEffect — this test asserts the new path stays clean.
    await context.addInitScript(() => {
      try { localStorage.setItem('user_id', 'pre-existing-user'); } catch { /* ignore */ }
    });

    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await page.goto('/strava?user_id=test-callback-user');
    await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
    await page.waitForTimeout(1500);

    const hydrationErrors = errors.filter(
      (m) => /Minified React error #418|Hydration failed|did not match/i.test(m),
    );
    expect(
      hydrationErrors,
      `Hydration errors on /strava?user_id=...: ${hydrationErrors.join('\n')}`,
    ).toHaveLength(0);
  });
});
