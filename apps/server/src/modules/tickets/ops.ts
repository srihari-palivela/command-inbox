import { ticketNumber } from '@ci/contracts';
import { and, eq, sql } from 'drizzle-orm';
import type { Tx } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { audit, type AuditInput } from '../../platform/audit.js';
import { clock } from '../../platform/clock.js';
import type { Actor } from '../../platform/context.js';
import { conflict, notFound } from '../../platform/errors.js';
import { publish } from '../../platform/outbox.js';
import type { TicketRow } from './queries.js';

/** Row-lock a ticket for the rest of the transaction. */
export async function lockTicket(tx: Tx, orgId: string, id: string): Promise<TicketRow> {
  const [row] = await tx
    .select()
    .from(s.tickets)
    .where(and(eq(s.tickets.orgId, orgId), eq(s.tickets.id, id)))
    .for('update');
  if (!row) throw notFound('Ticket');
  return row;
}

/** Update a ticket, bumping its version. Pass `expectVersion` to enforce optimistic concurrency. */
export async function updateTicket(
  tx: Tx,
  t: TicketRow,
  patch: Partial<typeof s.tickets.$inferInsert>,
  expectVersion?: number,
): Promise<TicketRow> {
  if (expectVersion !== undefined && expectVersion !== t.version) {
    throw conflict('stale_version', 'This ticket changed since you opened it.', 'Reload to see the latest version, then try again.');
  }
  const [row] = await tx
    .update(s.tickets)
    .set({ ...patch, version: sql`${s.tickets.version} + 1`, updatedAt: clock.now() })
    .where(and(eq(s.tickets.orgId, t.orgId), eq(s.tickets.id, t.id)))
    .returning();
  return row!;
}

export async function systemNote(tx: Tx, orgId: string, ticketId: string, body: string): Promise<void> {
  await tx.insert(s.comments).values({ orgId, ticketId, kind: 'system', authorName: 'Command Inbox', authorInitials: 'AI', body, createdAt: clock.now() });
}

export async function addComment(tx: Tx, orgId: string, ticketId: string, actor: Actor, kind: 'note' | 'public' | 'call' | 'system', body: string): Promise<void> {
  await tx.insert(s.comments).values({
    orgId,
    ticketId,
    kind,
    authorId: actor.id,
    authorName: actor.name,
    authorInitials: actor.initials,
    body,
    createdAt: clock.now(),
  });
}

export async function setSubtask(tx: Tx, orgId: string, ticketId: string, key: string, done: boolean, userId: string | null): Promise<void> {
  await tx
    .update(s.subtasks)
    .set({ done, doneBy: done ? userId : null, doneAt: done ? clock.now() : null })
    .where(and(eq(s.subtasks.orgId, orgId), eq(s.subtasks.ticketId, ticketId), eq(s.subtasks.key, key)));
}

/** Audit + outbox for a ticket change, in one call. */
export async function recordTicketEvent(tx: Tx, t: Pick<TicketRow, 'orgId' | 'id' | 'number'>, e: Omit<AuditInput, 'entity' | 'entityId' | 'ticketId'>): Promise<void> {
  await audit(tx, t.orgId, { ...e, entity: 'ticket', entityId: t.id, ticketId: t.id });
  await publish(tx, t.orgId, 'ticket.updated', { ticketId: t.id, number: ticketNumber(t.number) });
  if (e.feed) await publish(tx, t.orgId, 'activity.created', { ticketId: t.id });
}

export async function nextNumber(tx: Tx, orgId: string, counter: 'ticket' | 'gap'): Promise<number> {
  const [row] = await tx
    .update(s.counters)
    .set({ value: sql`${s.counters.value} + 1` })
    .where(and(eq(s.counters.orgId, orgId), eq(s.counters.name, counter)))
    .returning({ value: s.counters.value });
  if (row) return row.value;
  const [created] = await tx.insert(s.counters).values({ orgId, name: counter, value: counter === 'ticket' ? 1000 : 1 }).returning();
  return created!.value;
}
