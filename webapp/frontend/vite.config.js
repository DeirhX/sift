import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

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
})
