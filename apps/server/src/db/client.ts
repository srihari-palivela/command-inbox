import { sql } from 'drizzle-orm';
import { drizzle, type NodePgDatabase } from 'drizzle-orm/node-postgres';
import pg from 'pg';
import { env } from '../config/env.js';
import * as schema from './schema.js';

export type Schema = typeof schema;
export type Db = NodePgDatabase<Schema>;
/** A transaction handle; tenant work always runs inside one. */
export type Tx = Parameters<Parameters<Db['transaction']>[0]>[0];
export type Executor = Db | Tx;

export function createPool(url: string, max = env.DB_POOL_MAX): pg.Pool {
  const pool = new pg.Pool({ connectionString: url, max, idleTimeoutMillis: 30_000 });
  pool.on('error', (err) => {
    // Idle client errors (e.g. server restart) must not crash the process.
    console.error('[db] idle client error', err.message);
  });
  return pool;
}

export const pool = createPool(env.DATABASE_URL);
export const db: Db = drizzle(pool, { schema });

/**
 * Run `fn` in a transaction bound to one tenant. Row-level security policies read `app.org_id`,
 * so a query that forgets its `org_id` filter still cannot see another bank's rows.
 */
export async function withTenant<T>(orgId: string, fn: (tx: Tx) => Promise<T>, database: Db = db): Promise<T> {
  return database.transaction(async (tx) => {
    await tx.execute(sql`select set_config('app.org_id', ${orgId}, true)`);
    return fn(tx);
  });
}

export async function closeDb(): Promise<void> {
  await pool.end();
}
