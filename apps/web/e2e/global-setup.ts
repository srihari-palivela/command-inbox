import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

/** Reset and re-seed the database when asked (always in CI); locally the tests run against what's there. */
export default function globalSetup() {
  if (process.env.E2E_RESET !== '1') return;
  const root = fileURLToPath(new URL('../../..', import.meta.url));
  execSync('pnpm --filter @ci/server db:reset', { cwd: root, stdio: 'inherit' });
}
