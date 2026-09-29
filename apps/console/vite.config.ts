import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const api = process.env.API_URL ?? 'http://localhost:4000';

export default defineConfig({
  plugins: [react()],
  // The console shares the web app's design system (UI kit, tokens, fonts) instead of forking it.
  resolve: { alias: { '@web': fileURLToPath(new URL('../web/src', import.meta.url)) } },
  server: {
    port: 5174,
    strictPort: true,
    host: true,
    // Same-origin in development: the operator session cookie works without CORS.
    proxy: { '/v1': { target: api, changeOrigin: false } },
  },
  preview: { port: 4174, proxy: { '/v1': { target: api, changeOrigin: false } } },
  build: { sourcemap: true, target: 'es2022' },
  test: { environment: 'jsdom', include: ['src/**/*.test.{ts,tsx}'] },
});
