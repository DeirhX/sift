import { defineConfig, devices } from '@playwright/test'

// Dedicated config for the windowing/virtualization proof: it boots the real
// backend against a LARGE seeded library (E2E_SCALE scene piles) on its own
// port, so the default fast 6-image e2e run stays untouched. Run with
// `npm run test:e2e:scale`.
const PORT = Number(process.env.E2E_PORT || 8766)
const SCALE = process.env.E2E_SCALE || '200'

export default defineConfig({
  testDir: './e2e',
  testMatch: ['windowing.spec.js'],
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'python e2e/serve_fixture.py',
    url: `http://127.0.0.1:${PORT}/api/meta`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: { E2E_PORT: String(PORT), E2E_SCALE: String(SCALE) },
  },
})
