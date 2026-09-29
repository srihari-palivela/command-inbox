import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

/** Reset and re-seed the database when asked (always in CI); locally the tests run against what's there. */
export default function globalSetup() {
  if (process.env.E2E_RESET !== '1') return;
  const api = fileURLToPath(new URL('../../api', import.meta.url));
  execSync('uv run command-inbox-seed --reset', {
    cwd: api,
    stdio: 'inherit',
    env: { ...process.env, APP_ENV: 'development' },
  });
}
