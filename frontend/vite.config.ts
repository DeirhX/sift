/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// React only ships its test helpers (act) in non-production builds. A machine
// with NODE_ENV=production set globally would otherwise fail every component
// test, so pin the test runner to NODE_ENV=test regardless of the shell.
if (process.env.VITEST || process.argv.some((arg) => /vitest/.test(arg))) {
  process.env.NODE_ENV = 'test'
}

// During dev, proxy API + image routes to the FastAPI server on :8000.
// The production build is served directly by FastAPI, so these paths line up.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api':   'http://127.0.0.1:8000',
      '/thumb': 'http://127.0.0.1:8000',
      '/img':   'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: 'dist',
  },
  // Vitest: component/unit tests live next to source as *.test.{ts,tsx}.
  // Playwright e2e specs under e2e/ are excluded so the two runners don't collide.
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './vitest.setup.ts',
    include: ['src/**/*.test.{ts,tsx}'],
    exclude: ['e2e/**', 'node_modules/**'],
    css: false,
  },
})
