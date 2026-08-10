/**
 * CHEMINS COMMUNS — Core E2E tests: Auth (Register/Login + Strava Quick Connect)
 *
 * Split from core.spec.ts — no logic changes.
 */
import { test, expect, request } from '@playwright/test';

const API_URL = process.env.API_URL || 'http://localhost:8787';
const BASE_URL = process.env.BASE_URL || 'http://localhost:3787';

// Return empty graph tiles for ALL core tests so client-side routing falls
// through to the server cascade.
test.beforeEach(async ({ page }) => {
  await page.route('**/routing/graph/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ v: 1, edges: [] }),
    });
  });
});

// ── Helpers ────────────────────────────────────────────────────────────────

async function registerAndLogin(apiContext: Awaited<ReturnType<typeof request.newContext>>) {
  const uid = `${Date.now()}_${Math.floor(Math.random() * 100000)}`;
  const email = `test_${uid}@example.com`;
  const password = 'testpassword123';
  const username = `testuser_${uid}`;

  const regResp = await apiContext.post(`${API_URL}/auth/register`, {
    data: { email, password, username },
  });
  expect(regResp.status()).toBe(201);
  const data = await regResp.json();
  return { token: data.access_token, email, password, username, user_id: data.user_id };
}

// ── Test 1: Register + Login ──────────────────────────────────────────────

test.describe('1. Register / Login', () => {
  test('magic-link request sends a login email and shows confirmation', async ({ page }) => {
    // Public auth is passwordless magic-link (the email → link flow). The
    // legacy email+password form is dev-only and absent from the prod build,
    // so the real user-facing signup/login path is the magic-link request.
    const email = `e2e_${Date.now()}@example.com`;

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await page.fill('[data-testid="email-login-input"]', email);
    await Promise.all([
      page.waitForResponse(
        (r) => r.url().includes('/auth/email/request') && r.status() === 200,
        { timeout: 10000 }
      ),
      page.click('[data-testid="email-login-submit"]'),
    ]);

    // The form is replaced by the "check your inbox" confirmation state.
    await expect(page.locator('[data-testid="email-login-sent"]')).toBeVisible({ timeout: 10000 });
  });

  test('login with registered credentials returns JWT', async () => {
    const apiContext = await request.newContext();
    const email = `login_${Date.now()}@example.com`;

    // Register first
    await apiContext.post(`${API_URL}/auth/register`, {
      data: { email, password: 'testpass456', username: 'loginuser' },
    });

    // Login
    const loginResp = await apiContext.post(`${API_URL}/auth/login`, {
      form: { username: email, password: 'testpass456' },
    });
    expect(loginResp.status()).toBe(200);
    const data = await loginResp.json();
    expect(data.access_token).toBeTruthy();
    expect(data.token_type).toBe('bearer');

    await apiContext.dispose();
  });
});

// ── Test 2: Quick Connect Strava (TEST_MODE stubs) ────────────────────────

test.describe('2. Quick Connect — Strava (TEST_MODE)', () => {
  test('Strava connect endpoint behaves correctly', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);

    // With ENABLE_STRAVA_INTEGRATION=false, expect 501
    // With ENABLE_STRAVA_INTEGRATION=true + TEST_MODE=true, expect redirect or stub
    const connectResp = await apiContext.get(`${API_URL}/integrations/strava/connect`, {
      headers: { Authorization: `Bearer ${token}` },
      maxRedirects: 0,
    });

    const status = connectResp.status();
    // Either 501 (integration disabled) or 302/307 (redirect to stub/strava)
    // FastAPI RedirectResponse defaults to 307 Temporary Redirect
    expect([501, 302, 307, 200]).toContain(status);

    await apiContext.dispose();
  });

  test('Strava import_all stub completes immediately in TEST_MODE', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);

    // Only run full import test if integration is enabled
    const connectResp = await apiContext.get(`${API_URL}/integrations/strava/connect`, {
      headers: { Authorization: `Bearer ${token}` },
      maxRedirects: 0,
    });

    if (connectResp.status() === 501) {
      // Integration disabled — verify 501 is returned correctly
      expect(connectResp.status()).toBe(501);
      await apiContext.dispose();
      return;
    }

    // Integration enabled + TEST_MODE: import_all returns COMPLETED immediately
    const importResp = await apiContext.post(`${API_URL}/integrations/strava/import_all`, {
      headers: { Authorization: `Bearer ${token}` },
    });

    if (importResp.status() === 400) {
      // Not in TEST_MODE: Strava token not linked (OAuth flow not completed), test not applicable
      await apiContext.dispose();
      return;
    }
    expect(importResp.status()).toBe(200);
    const importData = await importResp.json();
    expect(importData.job_id).toBeTruthy();
    expect(['COMPLETED', 'RUNNING', 'PENDING']).toContain(importData.status);

    if (importData.status !== 'COMPLETED') {
      // Poll until COMPLETED
      let attempts = 0;
      let jobStatus = importData.status;
      while (jobStatus !== 'COMPLETED' && jobStatus !== 'FAILED' && attempts < 10) {
        await new Promise((r) => setTimeout(r, 500));
        const pollResp = await apiContext.get(
          `${API_URL}/integrations/strava/jobs/${importData.job_id}`,
          { headers: { Authorization: `Bearer ${token}` } }
        );
        const pollData = await pollResp.json();
        jobStatus = pollData.status;
        attempts++;
      }
      expect(jobStatus).toBe('COMPLETED');
    }

    await apiContext.dispose();
  });
});
