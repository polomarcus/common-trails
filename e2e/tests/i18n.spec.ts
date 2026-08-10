/**
 * CHEMINS COMMUNS — E2E tests: Language switch (FR ↔ EN)
 */
import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  // Clear localStorage to ensure default French locale
  await page.addInitScript(() => localStorage.removeItem('ct_locale'));
  // Stub routing graph to avoid network delays
  await page.route('**/routing/graph/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ v: 1, edges: [] }),
    });
  });
});

test.describe('Language switch', () => {
  // 2026-08 re-pin: the nav was decluttered (the personal-content pivot) — the
  // only top-level link left is "Carte"/"Map"; "Découvrir"/"Method" are no
  // longer nav items (Discover was removed; Method lives elsewhere). The stable
  // FR↔EN witnesses in the nav bar for a logged-out visitor are the top-level
  // "Carte"/"Map" link (nav.map) and the "Se connecter"/"Log in" button
  // (nav.login). These replace the stale "Découvrir"/"Discover"/"Method"
  // assertions, which matched nothing after the redesign.
  test('home page defaults to French and switches to English', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    // Default: French nav visible (wait for hydration + i18n load).
    // EXACT match — a substring 'Map' would also match "…heatmap" controls on
    // other pages; exact text pins the nav link/button precisely.
    await expect(page.locator('nav').getByText('Carte', { exact: true })).toBeVisible({ timeout: 10000 });
    await expect(page.locator('nav').getByText('Se connecter', { exact: true })).toBeVisible();

    // Language toggle is visible
    const toggle = page.locator('[data-testid="language-toggle"]').first();
    await expect(toggle).toBeVisible();

    // Click to switch to English
    await toggle.click();

    // Nav should now be in English
    await expect(page.locator('nav').getByText('Map', { exact: true })).toBeVisible();
    await expect(page.locator('nav').getByText('Log in', { exact: true })).toBeVisible();
  });

  test('language preference persists across page navigation', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    // Switch to English
    await page.locator('[data-testid="language-toggle"]').first().click();
    await expect(page.locator('nav').getByText('Map', { exact: true })).toBeVisible();

    // Navigate to another page via the nav (client-side — a cold reload can't be
    // used here because the beforeEach init-script wipes ct_locale on every
    // document load). The locale must survive the route change. /map has its own
    // chrome (not the shared TopNav), so the deterministic locale witness there
    // is the map layers button: English "Layers", not French "Calques".
    await page.locator('nav').getByText('Map', { exact: true }).click();
    // Gate on a /map-ready signal first — under CI worker contention maplibre
    // boot blocks the main thread.
    await expect(page.locator('[data-testid="map-layers-btn"]')).toBeVisible({ timeout: 20000 });

    // English persisted across the navigation: the map UI is in English.
    await expect(page.getByRole('button', { name: /Layers/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /Calques/ })).toHaveCount(0);
  });

  test('switching back to French restores original labels', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');

    const toggle = page.locator('[data-testid="language-toggle"]').first();

    // Switch to English
    await toggle.click();
    await expect(page.locator('nav').getByText('Map', { exact: true })).toBeVisible();

    // Switch back to French
    await toggle.click();
    await expect(page.locator('nav').getByText('Carte', { exact: true })).toBeVisible();
    await expect(page.locator('nav').getByText('Se connecter', { exact: true })).toBeVisible();
  });

  test('map page language toggle works', async ({ page }) => {
    await page.goto('/map/');
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('[data-testid="map-layers-btn"]')).toBeVisible({ timeout: 15000 });

    // Default French: "Calques" button visible
    await expect(page.getByRole('button', { name: /Calques/ })).toBeVisible();

    // Switch to English
    const toggle = page.locator('[data-testid="language-toggle"]');
    await expect(toggle).toBeVisible();
    await toggle.click();

    // "Layers" button should appear
    await expect(page.getByRole('button', { name: /Layers/ })).toBeVisible();
  });
});
