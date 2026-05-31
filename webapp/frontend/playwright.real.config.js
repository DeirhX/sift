import { defineConfig, devices } from '@playwright/test'

// Opt-in "real pipeline" e2e: runs the actual photo_audit + build_db analysis
// and the file reorganization against a COPY of a real library. Slow and
// environment-dependent (ML inference, possible model downloads), so it lives
// in its own config + npm script (`npm run test:e2e:real`) and is NOT part of
// the default fast suite.
//
//   E2E_SOURCE_FOLDER   library to copy from (default: E:\F\!To Pictures)
//   E2E_SOURCE_LIMIT    images to copy       (default: 12)
//   E2E_ANALYZE_TIMEOUT ms to wait for analysis to finish (default: 900000)
const PORT = Number(process.env.E2E_REAL_PORT || 8766)
const ANALYZE_TIMEOUT = Number(process.env.E2E_ANALYZE_TIMEOUT || 900_000)

export default defineConfig({
  testDir: './e2e/real',
  workers: 1,
  // Generous: the single test waits for a full analysis run plus apply/undo.
  timeout: ANALYZE_TIMEOUT + 120_000,
  expect: { timeout: 15_000 },
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'python e2e/serve_real.py',
    url: `http://127.0.0.1:${PORT}/api/meta`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
})
