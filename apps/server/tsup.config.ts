import { cpSync } from 'node:fs';
import { defineConfig } from 'tsup';

/**
 * Production build: one ESM file per process entry point. Workspace packages (@ci/contracts ships
 * TypeScript source) are bundled in; npm dependencies stay external and come from node_modules.
 * No code splitting, so each entry's "am I the main module?" guard sees its own file.
 */
export default defineConfig({
  entry: { main: 'src/main.ts', worker: 'src/worker.ts', migrate: 'src/db/migrate.ts', seed: 'src/db/seed/index.ts' },
  format: ['esm'],
  platform: 'node',
  target: 'node22',
  outDir: 'dist',
  clean: true,
  splitting: false,
  sourcemap: true,
  noExternal: [/^@ci\//],
  // migrate.ts resolves migrations next to itself: dist/migrations.
  onSuccess: async () => {
    cpSync('src/db/migrations', 'dist/migrations', { recursive: true });
  },
});
