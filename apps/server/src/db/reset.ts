/** Development only: drop everything, re-run migrations, re-seed. */
import { env } from '../config/env.js';
import { createPool } from './client.js';
import { runMigrations } from './migrate.js';
import { seed } from './seed/index.js';

async function main() {
  if (env.NODE_ENV === 'production') throw new Error('refusing to reset a production database');
  const pool = createPool(env.DATABASE_ADMIN_URL, 1);
  await pool.query('drop schema if exists drizzle cascade; drop schema public cascade; create schema public;');
  await pool.end();
  await runMigrations();
  await seed();
  console.log('database reset and seeded');
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
