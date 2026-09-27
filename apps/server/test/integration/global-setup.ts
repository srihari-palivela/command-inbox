/**
 * Create (if needed), migrate and seed the integration-test database once per run. Uses its own
 * database so running the suite never touches development data.
 */
import pg from 'pg';

const adminUrl = process.env.TEST_DATABASE_ADMIN_URL ?? 'postgres://postgres:postgres@localhost:5432/command_inbox_test';

export default async function setup() {
  const target = new URL(adminUrl);
  const dbName = target.pathname.slice(1);
  if (!dbName.endsWith('_test')) throw new Error(`refusing to reset "${dbName}": integration tests only run against a *_test database`);

  const server = new URL(adminUrl);
  server.pathname = '/postgres';
  const admin = new pg.Client({ connectionString: server.toString() });
  await admin.connect();
  const exists = await admin.query('select 1 from pg_database where datname = $1', [dbName]);
  if (!exists.rowCount) await admin.query(`create database "${dbName}"`);
  await admin.query(`do $$ begin
      if not exists (select 1 from pg_roles where rolname = 'ci_app') then create role ci_app login password 'ci_app'; end if;
    end $$;`);
  await admin.end();

  const db = new pg.Client({ connectionString: adminUrl });
  await db.connect();
  await db.query('drop schema if exists drizzle cascade; drop schema public cascade; create schema public;');
  await db.end();

  // The app's env module reads process.env at import time, so point it at the test database first.
  process.env.DATABASE_ADMIN_URL = adminUrl;
  process.env.DATABASE_URL = process.env.TEST_DATABASE_URL ?? 'postgres://ci_app:ci_app@localhost:5432/command_inbox_test';
  process.env.NODE_ENV = 'test';
  const { runMigrations } = await import('../../src/db/migrate.js');
  const { seed } = await import('../../src/db/seed/index.js');
  await runMigrations(adminUrl);
  await seed(adminUrl);
}
