import type { IntakeMessageBody } from '@ci/contracts';
import { and, desc, eq, gte, inArray, sql } from 'drizzle-orm';
import { db, withTenant, type Tx } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { audit } from '../../platform/audit.js';
import { clock } from '../../platform/clock.js';
import { SYSTEM_ACTOR } from '../../platform/context.js';
import { notFound } from '../../platform/errors.js';
import { enqueue } from '../../platform/jobs.js';
import { publish } from '../../platform/outbox.js';
import { nextNumber, systemNote, updateTicket } from '../tickets/ops.js';

const normaliseSubject = (subj: string) => subj.replace(/^\s*((re|fw|fwd)\s*:\s*)+/i, '').trim().toLowerCase();

export interface IngestResult {
  ticketId: string;
  number: number;
  created: boolean;
  duplicate: boolean;
}

export async function orgForMailbox(address: string): Promise<string | null> {
  const r = await db.execute<{ org: string | null }>(sql`select ci_mailbox_org(${address}) as org`);
  return r.rows[0]?.org ?? null;
}

/**
 * Ingest one inbound email: dedupe on provider message id, thread onto an open ticket from the same
 * sender (by In-Reply-To or subject within 14 days), otherwise open a new ticket and queue triage.
 */
export async function ingest(input: IntakeMessageBody, orgIdHint?: string): Promise<IngestResult> {
  const orgId = orgIdHint ?? (await orgForMailbox(input.mailbox));
  if (!orgId) throw notFound(`Mailbox ${input.mailbox}`);
  return withTenant(orgId, (tx) => ingestTx(tx, orgId, input));
}

async function ingestTx(tx: Tx, orgId: string, input: IntakeMessageBody): Promise<IngestResult> {
  const now = clock.now();
  const [mailbox] = await tx.select().from(s.mailboxes).where(and(eq(s.mailboxes.orgId, orgId), eq(s.mailboxes.address, input.mailbox)));
  if (!mailbox) throw notFound(`Mailbox ${input.mailbox}`);
  const providerId = input.messageId ?? `dev-${now.getTime()}-${Math.random().toString(36).slice(2, 8)}`;

  const inserted = await tx
    .insert(s.inboundMessages)
    .values({ orgId, mailboxId: mailbox.id, providerMessageId: providerId })
    .onConflictDoNothing()
    .returning({ id: s.inboundMessages.id });
  if (!inserted.length) {
    const [prev] = await tx.select().from(s.inboundMessages).where(and(eq(s.inboundMessages.orgId, orgId), eq(s.inboundMessages.providerMessageId, providerId)));
    const [t] = prev?.ticketId ? await tx.select().from(s.tickets).where(eq(s.tickets.id, prev.ticketId)) : [];
    return { ticketId: t?.id ?? '', number: t?.number ?? 0, created: false, duplicate: true };
  }

  // Threading
  let existing: typeof s.tickets.$inferSelect | undefined;
  if (input.inReplyTo) {
    const [m] = await tx.select().from(s.messages).where(and(eq(s.messages.orgId, orgId), eq(s.messages.providerMessageId, input.inReplyTo)));
    if (m) [existing] = await tx.select().from(s.tickets).where(eq(s.tickets.id, m.ticketId));
  }
  if (!existing) {
    const since = new Date(now.getTime() - 14 * 24 * 3600_000);
    const candidates = await tx
      .select()
      .from(s.tickets)
      .where(and(eq(s.tickets.orgId, orgId), eq(s.tickets.fromEmail, input.fromEmail), gte(s.tickets.receivedAt, since), inArray(s.tickets.status, ['triaging', 'awaiting_approval', 'executing', 'with_human', 'waiting_customer'])))
      .orderBy(desc(s.tickets.receivedAt));
    existing = candidates.find((c) => normaliseSubject(c.subject) === normaliseSubject(input.subject));
  }

  if (existing) {
    await tx.insert(s.messages).values({
      orgId, ticketId: existing.id, direction: 'inbound', fromName: input.fromName, fromAddr: input.fromEmail, toAddr: input.mailbox,
      body: input.body, sentAt: now, providerMessageId: providerId,
    });
    await tx.update(s.inboundMessages).set({ ticketId: existing.id }).where(eq(s.inboundMessages.id, inserted[0]!.id));
    if (existing.status === 'waiting_customer') {
      const resumed = existing.pausedAt && existing.dueAt ? new Date(existing.dueAt.getTime() + (now.getTime() - existing.pausedAt.getTime())) : existing.dueAt;
      await updateTicket(tx, existing, { status: 'with_human', pausedAt: null, dueAt: resumed, nextMove: 'Customer replied — pick it back up' });
      await systemNote(tx, orgId, existing.id, 'The customer replied. The deadline clock has resumed.');
    } else {
      await updateTicket(tx, existing, {});
      await systemNote(tx, orgId, existing.id, `New message from ${input.fromName} added to the thread.`);
    }
    await publish(tx, orgId, 'ticket.updated', { ticketId: existing.id });
    return { ticketId: existing.id, number: existing.number, created: false, duplicate: false };
  }

  // Customer match by email; unknown senders get a provisional record.
  let [customer] = await tx.select().from(s.customers).where(and(eq(s.customers.orgId, orgId), eq(s.customers.email, input.fromEmail)));
  if (!customer) {
    [customer] = await tx
      .insert(s.customers)
      .values({ orgId, cif: `CIF P-${now.getTime().toString().slice(-7)}`, name: input.fromName, email: input.fromEmail, segment: 'Retail', sinceYear: now.getFullYear(), account: '' })
      .returning();
  }
  const [board] = await tx.select().from(s.boards).where(and(eq(s.boards.orgId, orgId), eq(s.boards.mailboxId, mailbox.id)));
  const number = await nextNumber(tx, orgId, 'ticket');
  const [ticket] = await tx
    .insert(s.tickets)
    .values({
      orgId, number, boardId: board?.id ?? null, mailboxId: mailbox.id, customerId: customer!.id,
      subject: input.subject, fromName: input.fromName, fromEmail: input.fromEmail, receivedAt: now,
      lane: 'manual', originalLane: 'manual', laneNote: 'Being triaged', status: 'triaging', priority: 'P3',
      segment: customer!.segment, ownerKind: 'ai', slaMinutes: 1440, dueAt: new Date(now.getTime() + 1440 * 60_000), nextMove: 'Triaging',
    })
    .returning();
  await tx.insert(s.messages).values({
    orgId, ticketId: ticket!.id, direction: 'inbound', fromName: input.fromName, fromAddr: input.fromEmail, toAddr: input.mailbox,
    body: input.body, sentAt: now, providerMessageId: providerId,
  });
  await tx.update(s.inboundMessages).set({ ticketId: ticket!.id }).where(eq(s.inboundMessages.id, inserted[0]!.id));
  await enqueue(tx, { orgId, kind: 'triage', payload: { ticketId: ticket!.id }, dedupeKey: `triage:${ticket!.id}` });
  await audit(tx, orgId, { actor: SYSTEM_ACTOR, action: 'mail.received', entity: 'ticket', entityId: ticket!.id, ticketId: ticket!.id, summary: `Mail from ${input.fromName} to ${input.mailbox} opened QRY-${number}` });
  await publish(tx, orgId, 'ticket.created', { ticketId: ticket!.id });
  return { ticketId: ticket!.id, number, created: true, duplicate: false };
}

/** Sample mails for the demo simulator ("new mail arrives"), exercising each lane. */
export const SAMPLE_MAILS: IntakeMessageBody[] = [
  { mailbox: 'customercare@bank.example', fromName: 'Nisha Kapoor', fromEmail: 'nisha.kapoor@gmail.com', subject: 'Statement for January to March please', body: 'Could you email me the account statement for my savings account ending 5521 for January to March this year? The net banking copy is password protected and I need an unlocked PDF for my visa file.' },
  { mailbox: 'tradeops@bank.example', fromName: 'Ramesh Iyer', fromEmail: 'ramesh@iyerexports.in', subject: 'Stop payment on cheque 552310 immediately', body: 'Please place an immediate stop payment on cheque number 552310 for ₹4,75,000 issued on 2 Sep from our current account ending 8812. The supplier has cancelled the order.' },
  { mailbox: 'nri.desk@bank.example', fromName: 'Mary Joseph', fromEmail: 'mary.joseph@outlook.com', subject: 'Adding my son as a joint holder on NRE account', body: 'I live in Muscat and hold an NRE savings account. What documents do I need to add my son, who lives in Kochi, as a joint holder? Do I need to travel to India?' },
  { mailbox: 'disputes@bank.example', fromName: 'Karthik Rao', fromEmail: 'karthik.rao@gmail.com', subject: 'Third email — unauthorised card transaction, going to the ombudsman', body: 'This is the third time I am writing. There is an unauthorised transaction of ₹38,200 on my card from a merchant in Dubai. Nobody has replied. If I do not hear back by tomorrow I will complain to the Banking Ombudsman.' },
  { mailbox: 'customercare@bank.example', fromName: 'Fatema Lokhandwala', fromEmail: 'fatema.l@gmail.com', subject: 'Locker rent waiver request', body: 'The branch locker was inaccessible for three months during repairs. Please waive this year’s locker rent.' },
];
