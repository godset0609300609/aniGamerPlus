import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright E2E configuration.
 *
 * Tests run against a `vite preview` build (port 4173) so they exercise the
 * production bundle.  The real backend (scheduler + API) is started by
 * globalSetup in an isolated temp workspace so dev state is never touched.
 *
 * Ports:
 *   4173 – Vite preview (SPA)
 *   15000 – API process
 *   15001 – Scheduler process
 */
export default defineConfig({
  testDir: './e2e/tests',
  reporter: [['html', { open: 'never' }], ['list']],
  timeout: 60_000,
  retries: 0,
  globalSetup: './e2e/globalSetup.ts',
  globalTeardown: './e2e/globalTeardown.ts',

  use: {
    baseURL: 'http://localhost:4173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: {
    command: 'npm run build && npm run preview -- --port 4173',
    port: 4173,
    timeout: 120_000,
    reuseExistingServer: false,
    env: {
      // Point the SPA's /api proxy at the test API process.
      VITE_BACKEND_URL: 'http://localhost:15000',
    },
  },
})
