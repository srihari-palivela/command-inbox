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
  build: { sourcemap: true, target: 'es2022' },
  test: { environment: 'jsdom', include: ['src/**/*.test.{ts,tsx}'] },
});
