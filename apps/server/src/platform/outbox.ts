import { sql } from 'drizzle-orm';
import type { Tx } from '../db/client.js';
import { outbox } from '../db/schema.js';

export type Topic =
  | 'ticket.updated'
  | 'ticket.created'
  | 'activity.created'
  | 'notification.created'
  | 'gate.updated'
  | 'call.updated'
  | 'setup.updated'
  | 'people.updated'
  | 'insights.updated'
  | 'learning.updated';

export const EVENTS_CHANNEL = 'ci_events';

/**
 * Record a domain event in the same transaction as the change (transactional outbox) and notify
 * listeners on commit. NOTIFY is transactional in Postgres: a rolled-back change emits nothing.
 */
export async function publish(
  tx: Tx,
  orgId: string,
  topic: Topic,
  payload: Record<string, unknown> = {},
): Promise<void> {
  await tx.insert(outbox).values({ orgId, topic, payload });
  const message = JSON.stringify({ orgId, topic, ...payload }).slice(0, 7900);
  await tx.execute(sql`select pg_notify(${EVENTS_CHANNEL}, ${message})`);
}
