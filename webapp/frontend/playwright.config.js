import { defineConfig, devices } from '@playwright/test'

const PORT = Number(process.env.E2E_PORT || 8765)

// The webServer launches the REAL FastAPI backend against a freshly-seeded
// fixture DB and serves the production frontend build (dist). `npm run test:e2e`
// builds dist first. Single worker: the tests share one server + SQLite DB.
export default defineConfig({
  testDir: './e2e',
  // The real-pipeline suite has its own config (playwright.real.config.js).
  testIgnore: ['real/**'],
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 7_000 },
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
  },
})
