import { drizzle } from 'drizzle-orm/node-postgres';
import { migrate } from 'drizzle-orm/node-postgres/migrator';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { env } from '../config/env.js';
import { createPool } from './client.js';

const here = path.dirname(fileURLToPath(import.meta.url));

export async function runMigrations(url = env.DATABASE_ADMIN_URL): Promise<void> {
  const pool = createPool(url, 1);
  try {
    await migrate(drizzle(pool), { migrationsFolder: path.join(here, 'migrations') });
  } finally {
    await pool.end();
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  runMigrations()
    .then(() => console.log('migrations applied'))
    .catch((e) => {
      console.error(e);
      process.exit(1);
    });
}
