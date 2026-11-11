/**
 * CHEMINS COMMUNS — Core E2E tests: Auth (Register/Login)
 *
 * Split from core.spec.ts — no logic changes.
 */
import { test, expect, request } from '@playwright/test';

const API_URL = process.env.API_URL || 'http://localhost:8787';
const BASE_URL = process.env.BASE_URL || 'http://localhost:3787';

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
