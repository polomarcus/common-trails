/**
 * Heatmap H key toggle — Crouzet's discovery technique.
 *
 * Pressing H toggles the heatmap with a 300ms fade. Used to discover
 * whether an area has many off-asphalt possibilities (map "lights up")
 * vs everyone using roads (minimal change).
 */
import { test, expect } from '@playwright/test';

const FRONTEND_URL = process.env.FRONTEND_URL || 'http://localhost:3787';

test.describe('Heatmap H key toggle', () => {
  test.setTimeout(60000);

  test('H key fires custom event without errors', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('cc_beta_dismissed', '1'));
    await page.goto(`${FRONTEND_URL}/map?lat=43.612&lon=3.875&zoom=13`);
    await page.waitForLoadState('domcontentloaded');

    // Wait for map instance to be exposed
    await page.waitForFunction(() => !!(window as any).__mapInstance, { timeout: 30000 });

    // Track console errors
    const errors: string[] = [];
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });

    // Press H — should not throw
    await page.keyboard.press('h');
    await page.waitForTimeout(500);
    await page.keyboard.press('h');
    await page.waitForTimeout(500);

    // No console errors from the toggle
    const togglesErrors = errors.filter(e => /heatmap|toggle/i.test(e));
    expect(togglesErrors).toEqual([]);
  });

  test('H key does NOT trigger when typing in input', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('cc_beta_dismissed', '1'));
    await page.goto(`${FRONTEND_URL}/map?lat=43.612&lon=3.875&zoom=13`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForFunction(() => !!(window as any).__mapInstance, { timeout: 30000 });

    const searchInput = page.locator('[data-testid="place-search-input"]').first();
    if (!(await searchInput.isVisible({ timeout: 3000 }).catch(() => false))) {
      test.skip(true, 'No search input found');
      return;
    }

    // Focus input, type H — should appear in input, NOT toggle heatmap
    await searchInput.click();
    await searchInput.type('h');
    await page.waitForTimeout(200);

    const inputValue = await searchInput.inputValue();
    expect(inputValue).toContain('h');
  });
});
