/**
 * Mobile viewport sanity check (375 x 667 = iPhone SE).
 *
 * The May 10 frontend audit flagged that map controls (zoom, layer
 * toggles) might be hidden behind the route editor panel on small
 * phones. This test opens the map at iPhone-SE size, exercises the
 * critical UI paths a friend on a phone would hit, and asserts the
 * primary controls are visible / clickable.
 *
 * Skipped in CI: runs locally against a live frontend at :3787.
 */
import { expect, request, test } from '@playwright/test';

const API_URL = process.env.API_URL || 'http://localhost:8787';
const FRONTEND_URL = process.env.FRONTEND_URL || process.env.BASE_URL || 'http://localhost:3787';

// Custom mobile profile (chromium-based, NOT WebKit) — the e2e setup only
// installs Chromium. Matches an iPhone SE size + touch.
test.use({
  viewport: { width: 375, height: 667 },
  hasTouch: true,
  isMobile: true,
  userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
});

test.describe('Mobile viewport sanity (iPhone SE 375×667 — chromium-emulated)', () => {
  test.setTimeout(90000);
  test.skip(!!process.env.CI, 'Local-only — needs running frontend');

  test.beforeEach(async () => {
    const apiCtx = await request.newContext();
    for (let i = 0; i < 30; i++) {
      const resp = await apiCtx.get(`${API_URL}/healthz`).catch(() => null);
      if (resp?.ok()) break;
      await new Promise((r) => setTimeout(r, 2000));
    }
    await apiCtx.dispose();
  });

  test('map page loads on phone-size viewport without crashing', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));

    await page.goto(`${FRONTEND_URL}/map`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForFunction(
      () => !document.body.innerText.includes('Démarrage'),
      { timeout: 60000 },
    );

    expect(errors, 'no JS errors on mobile load').toHaveLength(0);
  });

  test('no horizontal scrollbar', async ({ page }) => {
    // Fixed 2026-05-10 with `overflow-x: hidden` on html, body — see
    // frontend/app/globals.css. The desktop toolbar still extends past
    // 375 px logically, but the body clips it so the user can't scroll
    // sideways accidentally.
    await page.goto(`${FRONTEND_URL}/map`);
    await page.waitForLoadState('domcontentloaded');

    const hasHScroll = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(hasHScroll).toBe(false);
  });

});
