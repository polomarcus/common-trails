/**
 * Regression: hydration mismatches on /map (React error #418).
 *
 * PR #310 fixed six `useState(() => ...)` lazy-initialisers that read
 * browser-only state (localStorage, window.innerWidth, Math.random)
 * during the first render. With Next.js `output: 'export'`, the static
 * /map/index.html is built at SSG time when `typeof window === 'undefined'`
 * so those initialisers returned their SSR fallback. On the client they
 * returned user-specific values → static HTML didn't match first client
 * render → React #418 → entire tree regenerated, activity panel never
 * resolved.
 *
 * This test pre-seeds the localStorage values a returning user would
 * have, then asserts no `pageerror` fires during hydration. React #418
 * is a synchronous throw during hydration and surfaces as a `pageerror`
 * event in Playwright.
 */
import { expect, test } from '@playwright/test';

test.describe('regression: /map hydration', () => {
  test('returning user with all localStorage flags does not emit React #418', async ({
    page,
    context,
  }) => {
    // Seed every flag the original bug touched. If any of the six
    // useState lazy-initialisers regresses, the SSG HTML won't match
    // the post-hydration state and React #418 fires synchronously.
    await context.addInitScript(() => {
      try {
        localStorage.setItem('cc_beta_dismissed', '1');
        localStorage.setItem('ct_preferred_sport', 'gravel'); // not the SSG default 'road'
        localStorage.setItem('cc_basemap', 'satellite'); // not the SSG default 'ign'
        localStorage.setItem('cc_waymarked', JSON.stringify({ hiking: true, cycling: false, mtb: true }));
      } catch { /* ignore */ }
    });

    const errors: string[] = [];
    page.on('pageerror', (err) => {
      // React error #418 is a hydration mismatch — the most common signal
      // is the minified message "Minified React error #418" or the legacy
      // "Hydration failed because…". Capture all uncaught errors and
      // filter in the assertion so we get useful diagnostics on failure.
      errors.push(err.message);
    });

    await page.goto('/map');

    // Wait long enough for React to mount, hydrate, and run effects.
    // The bug surfaces immediately on hydration; a 1.5 s grace is generous.
    await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
    await page.waitForTimeout(1500);

    const hydrationErrors = errors.filter(
      (m) => /Minified React error #418|Hydration failed|did not match/i.test(m),
    );
    expect(hydrationErrors, `Hydration errors: ${hydrationErrors.join('\n')}`).toHaveLength(0);
  });

  test('mobile viewport does not emit React #418 on /map', async ({ browser }) => {
    // The viewedRouteExpanded useState used to read window.innerWidth
    // during init — desktop SSG built `true`, mobile client returned
    // `false` → mismatch. Test the mobile path specifically.
    const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
    const page = await context.newPage();

    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await page.goto('/map');
    await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});
    await page.waitForTimeout(1500);

    const hydrationErrors = errors.filter(
      (m) => /Minified React error #418|Hydration failed|did not match/i.test(m),
    );
    expect(hydrationErrors, `Mobile hydration errors: ${hydrationErrors.join('\n')}`).toHaveLength(0);

    await context.close();
  });
});
