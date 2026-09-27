import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end tests run the real stack: Postgres, the API (with its embedded worker in dev) and the SPA.
 * They change data, so CI runs them against a freshly seeded database (E2E_RESET=1 resets before the run).
 */
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_PATH;

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    viewport: { width: 1600, height: 1000 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1600, height: 1000 }, launchOptions: executablePath ? { executablePath } : {} } }],
  webServer: [
    { command: 'pnpm --filter @ci/server dev', url: 'http://localhost:4000/healthz', reuseExistingServer: !process.env.CI, timeout: 60_000, cwd: '../..' },
    { command: 'pnpm --filter @ci/web dev', url: 'http://localhost:5173', reuseExistingServer: !process.env.CI, timeout: 60_000, cwd: '../..' },
  ],
});
