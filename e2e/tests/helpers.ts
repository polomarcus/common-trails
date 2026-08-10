import type { Page } from '@playwright/test';

/**
 * Wait for routing to complete — works for BOTH server routing and client-graph (A* in browser).
 *
 * Uses a two-step approach:
 * 1. Wait for `data-route-status="routing"` to appear (routing started)
 * 2. Wait for `data-route-status="idle"` to appear (routing finished)
 *
 * Step 1 has a short timeout with .catch() to handle edge cases where routing
 * completes faster than Playwright can observe the transient "routing" state
 * (e.g., React batching setRouting(true) + setRouting(false) in one render).
 * In that case, the 2s penalty is still faster than the old fixed 2000ms waits.
 */
export async function waitForRouteComplete(page: Page, timeoutMs = 10000) {
  // Step 1: Wait for routing to START (avoid resolving on pre-existing idle state)
  await page.waitForSelector('[data-route-status="routing"]', { timeout: 2000 }).catch(() => {});
  // Step 2: Wait for routing to FINISH
  await page.waitForSelector('[data-route-status="idle"]', { timeout: timeoutMs });
}
