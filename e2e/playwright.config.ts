import { defineConfig, devices } from '@playwright/test';

const BASE_URL = process.env.BASE_URL || 'http://localhost:3787';
const API_URL = process.env.API_URL || 'http://localhost:8787';

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  // Retries: 1 in CI catches transient flakes without tripling worst-case time.
  // Local: 0 — fast feedback (re-run manually if needed).
  retries: process.env.CI ? 1 : 0,
  // Workers: 4 (ubuntu-latest has 4 vCPUs; Macs handle 4+ easily).
  // Override via PLAYWRIGHT_WORKERS=N for tuning.
  workers: process.env.PLAYWRIGHT_WORKERS
    ? Number(process.env.PLAYWRIGHT_WORKERS)
    : 4,
  timeout: process.env.CI ? 60000 : 30000,
  reporter: [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'default',
      use: { ...devices['Desktop Chrome'] },
      testIgnore: /routing-heatmap-fixture|routing-quality-montpellier/,
    },
    {
      name: 'heatmap-fixture',
      use: { ...devices['Desktop Chrome'] },
      testMatch: /routing-heatmap-fixture|routing-quality-montpellier/,
      dependencies: ['default'],
    },
  ],
});
