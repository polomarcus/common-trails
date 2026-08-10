/**
 * E2E regressions for map interaction bugs that unit tests miss.
 *
 * These are the kind of bugs that only surface when a real DOM event
 * fires (HTML5 drag, pointer drag on a maplibre marker, etc.). Pure-
 * function tests under `frontend/lib/__tests__/` can't catch them.
 *
 * Each test here pins ONE production bug that already escaped to a user
 * (Paul), so a future refactor that re-introduces the same issue fails
 * CI rather than the user reporting it again.
 */
import { test, expect } from '@playwright/test';

const FRONTEND_URL = process.env.FRONTEND_URL || process.env.BASE_URL || 'http://localhost:3787';

test.describe('Map interaction regressions', () => {
  test.setTimeout(60_000);

  test.beforeEach(async ({ page }) => {
    await page.goto(`${FRONTEND_URL}/map?lat=43.65734&lon=3.87867&zoom=13`);
    await page.waitForLoadState('domcontentloaded');
    // Wait for the map container to be ready (its parent is the drop-zone target).
    await page.waitForSelector('[data-testid="map-container"]', { timeout: 30_000 });
  });

  // ── Drop overlay should only react to OS file drags ─────────────────
  // PR #367 regression: the GPX import overlay was painted on ANY HTML5
  // drag event over the map, including internal drags (waypoint markers,
  // text selection, links from a sidebar, even a tab being dragged in
  // from the OS). The fix gates the overlay on
  // `dataTransfer.types.includes('Files')`.

  test('internal drag (non-file types) does NOT show the GPX overlay', async ({ page }) => {
    // The exact bug Paul reported on 2026-05-30: dragging a marker /
    // selection inside the page painted the purple-dashed overlay over
    // the map and blocked panning. With the fix, only OS file drags
    // trigger the overlay; everything else is ignored.
    await page.evaluate(() => {
      // The drop-zone listeners are on the `.parentElement` of the map
      // container (the flex wrapper that holds the map + overlays).
      const map = document.querySelector('[data-testid="map-container"]')?.parentElement;
      if (!map) throw new Error('Map container not found');
      const dt = new DataTransfer();
      // Synthetic DataTransfer defaults to empty `types`; if we wanted to
      // assert with a specific non-file type we'd need a browser-native
      // event (page.dragAndDrop) — the empty-types case still pins the
      // fix because the production handler now refuses non-Files drags.
      map.dispatchEvent(new DragEvent('dragover', {
        bubbles: true, cancelable: true, dataTransfer: dt,
      }));
    });
    // Give React a frame to settle.
    await page.waitForTimeout(100);
    await expect(page.locator('[data-testid="gpx-drop-overlay"]')).toHaveCount(0);
  });

  // Note on the inverse case (OS file drag SHOULD show overlay):
  // synthesising `dataTransfer.types = ['Files']` programmatically does
  // not survive the DragEvent round-trip — `types` is a read-only
  // computed property on DataTransfer. The browser-native path
  // (`page.dragAndDrop` with a file) requires a different harness.
  // Manual verification is in the PR test plan; the production code
  // path is the same one used by the file-import flow, so a regression
  // there would also break the standard upload UI (already E2E-covered
  // in `core-gpx.spec.ts`).

  // ── No-U-turn invariant after a line drag (PR #366 regression) ────────
  // The full integration pin described here was BUILT: it now lives in
  // `drag-edit-quality.spec.ts`, which synthesizes a real maplibre line
  // drag and asserts `maxReverseTurnDeg(lineCoords) < 150°` on the live
  // route source. The placeholder `test.skip` that used to sit here was
  // removed (it was a no-op TODO for work that's since shipped).
});
