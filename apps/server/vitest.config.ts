import { defineConfig } from 'vitest/config';

/**
 * Two suites:
 * - unit: pure domain rules, no I/O.
 * - integration: the real Fastify app against a real Postgres (its own `_test` database, reset and
 *   seeded once per run), exercising RLS, the audit chain, the gateway and idempotency end to end.
 */
const adminBase = process.env.TEST_DATABASE_ADMIN_URL ?? 'postgres://postgres:postgres@localhost:5432/command_inbox_test';
const appBase = process.env.TEST_DATABASE_URL ?? 'postgres://ci_app:ci_app@localhost:5432/command_inbox_test';

export default defineConfig({
  test: {
    projects: [
      { test: { name: 'unit', include: ['src/**/*.test.ts'], environment: 'node' } },
      {
        test: {
          name: 'integration',
          include: ['test/integration/**/*.test.ts'],
          environment: 'node',
          globalSetup: ['test/integration/global-setup.ts'],
          fileParallelism: false,
          testTimeout: 30_000,
          hookTimeout: 120_000,
          env: {
            NODE_ENV: 'test',
            DATABASE_URL: appBase,
            DATABASE_ADMIN_URL: adminBase,
            DEMO_MODE: 'true',
            LLM_PROVIDER: 'heuristic',
            EMBEDDED_WORKER: 'false',
            LOG_LEVEL: 'silent',
          },
        },
      },
    ],
  },
});
