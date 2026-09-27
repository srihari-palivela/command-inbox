import { and, asc, desc, eq, gt, sql } from 'drizzle-orm';
import type { Tx } from '../db/client.js';
import { auditEvents } from '../db/schema.js';
import { clock } from './clock.js';
import type { Actor } from './context.js';
import { canonicalJson, sha256 } from './crypto.js';

export const GENESIS = '0'.repeat(64);

export interface AuditInput {
  actor: Actor;
  action: string;
  entity: string;
  entityId?: string | null;
  ticketId?: string | null;
  summary: string;
  data?: Record<string, unknown>;
  /** Also show this event in the AI activity rail. */
  feed?: { tone: 'ok' | 'stop' | 'flag' | 'info' | 'muted'; meta: string };
  at?: Date;
}

function hashOf(prev: string, orgId: string, at: Date, e: AuditInput): string {
  return sha256(
    prev +
      canonicalJson({
        orgId,
        at: at.toISOString(),
        actorKind: e.actor.kind,
        actorId: e.actor.id,
        actorName: e.actor.name,
        action: e.action,
        entity: e.entity,
        entityId: e.entityId ?? null,
        ticketId: e.ticketId ?? null,
        summary: e.summary,
        data: e.data ?? {},
      }),
  );
}

/**
 * Append an event to the tenant's hash chain. Serialised per org with a transaction-scoped advisory
 * lock so concurrent writers cannot fork the chain.
 */
export async function audit(tx: Tx, orgId: string, e: AuditInput): Promise<void> {
  await tx.execute(sql`select pg_advisory_xact_lock(hashtextextended(${orgId + ':audit'}, 0))`);
  const [last] = await tx
    .select({ hash: auditEvents.hash })
    .from(auditEvents)
    .where(eq(auditEvents.orgId, orgId))
    .orderBy(desc(auditEvents.seq))
    .limit(1);
  const prevHash = last?.hash ?? GENESIS;
  const at = e.at ?? clock.now();
  await tx.insert(auditEvents).values({
    orgId,
    at,
    actorKind: e.actor.kind,
    actorId: e.actor.id,
    actorName: e.actor.name,
    action: e.action,
    entity: e.entity,
    entityId: e.entityId ?? null,
    ticketId: e.ticketId ?? null,
    summary: e.summary,
    feedTone: e.feed?.tone ?? null,
    feedMeta: e.feed?.meta ?? null,
    data: e.data ?? {},
    prevHash,
    hash: hashOf(prevHash, orgId, at, e),
  });
}

/** Recompute the chain. Any edited, inserted or removed row breaks it at that sequence number. */
export async function verifyAuditChain(tx: Tx, orgId: string): Promise<{ ok: boolean; events: number; brokenAt: number | null }> {
  let prev = GENESIS;
  let count = 0;
  let cursor = 0;
  for (;;) {
    const rows = await tx
      .select()
      .from(auditEvents)
      .where(and(eq(auditEvents.orgId, orgId), gt(auditEvents.seq, cursor)))
      .orderBy(asc(auditEvents.seq))
      .limit(1000);
    if (!rows.length) break;
    for (const r of rows) {
      const expected = hashOf(prev, orgId, r.at, {
        actor: { kind: r.actorKind as Actor['kind'], id: r.actorId, name: r.actorName, initials: '' },
        action: r.action,
        entity: r.entity,
        entityId: r.entityId,
        ticketId: r.ticketId,
        summary: r.summary,
        data: r.data,
      });
      if (r.prevHash !== prev || r.hash !== expected) return { ok: false, events: count, brokenAt: r.seq };
      prev = r.hash;
      count++;
      cursor = r.seq;
    }
  }
  return { ok: true, events: count, brokenAt: null };
}
