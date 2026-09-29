import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const api = process.env.API_URL ?? 'http://localhost:4000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    // Same-origin in development: the session cookie and SSE stream work without CORS.
    proxy: { '/v1': { target: api, changeOrigin: false } },
  },
  preview: { port: 4173, proxy: { '/v1': { target: api, changeOrigin: false } } },
  // The entry chunk is React 19 + router + TanStack Query + the shell (~127 kB gzipped); every screen is
  // its own lazy chunk. Warn only if the entry grows past that budget.
  build: { sourcemap: true, target: 'es2022', chunkSizeWarningLimit: 450 },
  test: { environment: 'jsdom', include: ['src/**/*.test.{ts,tsx}'] },
});
