import { existsSync } from 'node:fs';
import { chromium, defineConfig, devices } from '@playwright/test';

// Browser tests run against a real API + web + Postgres. See e2e/README.md.
// The servers are started by the caller (locally: `npm run e2e` from the repo
// root; in CI: the e2e job), so this config only points at them.

// A Claude Code cloud session has a Chromium of its own at /opt/pw-browsers, but
// not the build this pinned Playwright installs. Use it when that build is
// missing; CI and a machine that ran `npx playwright install` use their own.
const CLOUD_CHROMIUM = '/opt/pw-browsers/chromium';
const executablePath =
  !existsSync(chromium.executablePath()) && existsSync(CLOUD_CHROMIUM) ? CLOUD_CHROMIUM : undefined;

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  // Locally a passing run is a row of dots and the totals; failures still print
  // in full. Pass `--reporter=list` to see each test by name.
  reporter: process.env.CI ? [['list'], ['github']] : 'dot',
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], launchOptions: { executablePath } } }],
});
