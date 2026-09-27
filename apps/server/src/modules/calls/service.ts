import type { CallDTO } from '@ci/contracts';
import { initialsOf } from '@ci/contracts';
import { and, eq } from 'drizzle-orm';
import type { Tx } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { clock } from '../../platform/clock.js';
import { actorOf, AI_ACTOR, type Ctx } from '../../platform/context.js';
import { conflict, notFound } from '../../platform/errors.js';
import { requireCap } from '../../platform/rbac.js';
import { addComment, lockTicket, recordTicketEvent, setSubtask, updateTicket } from '../tickets/ops.js';
import { findTicket } from '../tickets/queries.js';
import { DIAL_MS, LINE_MS, outcomeFor, scriptFor } from './scripts.js';

type CallRow = typeof s.calls.$inferSelect;

const fmt = (sec: number) => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;

/** Live view: dialing → live, transcript revealed as the call progresses. */
function view(c: CallRow, t: typeof s.tickets.$inferSelect, phone: string): CallDTO {
  const now = clock.now().getTime();
  const elapsed = now - c.startedAt.getTime();
  let state = c.state as CallDTO['state'];
  let lines = c.script.length;
  let duration = c.durationSec;
  if (state === 'dialing' || state === 'live') {
    state = elapsed < DIAL_MS ? 'dialing' : 'live';
    const liveMs = Math.max(0, elapsed - DIAL_MS);
    lines = state === 'live' ? Math.min(c.script.length, Math.floor(liveMs / LINE_MS) + 1) : 0;
    duration = Math.floor(liveMs / 1000);
  }
  return {
    id: c.id,
    ticketId: c.ticketId,
    state,
    startedAt: c.startedAt.toISOString(),
    durationSec: duration,
    number: '+91 •••• ••' + phone.replace(/\D/g, '').slice(-4),
    customerName: t.fromName,
    customerInitials: initialsOf(t.fromName),
    transcript: c.script.slice(0, lines),
    scriptLength: c.script.length,
    summary: c.summary,
    updates: c.updates,
    recording: c.recordingKey ? `call-qry-${t.number}.mp3 · ${fmt(duration)}` : null,
  };
}

async function load(tx: Tx, ctx: Ctx, callId: string) {
  const [c] = await tx.select().from(s.calls).where(and(eq(s.calls.orgId, ctx.orgId), eq(s.calls.id, callId))).for('update');
  if (!c) throw notFound('Call');
  const [t] = await tx.select().from(s.tickets).where(eq(s.tickets.id, c.ticketId));
  const [cust] = t!.customerId ? await tx.select().from(s.customers).where(eq(s.customers.id, t!.customerId)) : [];
  return { c, t: t!, phone: cust?.phone ?? '0000' };
}

export async function startCall(tx: Tx, ctx: Ctx, ticketIdOrNumber: string): Promise<CallDTO> {
  requireCap(ctx, 'ticket.work', 'call customers');
  const found = await findTicket(tx, ctx.orgId, ticketIdOrNumber);
  if (!found) throw notFound('Ticket');
  const t = await lockTicket(tx, ctx.orgId, found.id);
  const [c] = await tx
    .insert(s.calls)
    .values({ orgId: ctx.orgId, ticketId: t.id, startedBy: ctx.user.id, state: 'live', startedAt: clock.now(), script: scriptFor(t.number, t.subject) })
    .returning();
  const [cust] = t.customerId ? await tx.select().from(s.customers).where(eq(s.customers.id, t.customerId)) : [];
  return view(c!, t, cust?.phone ?? '0000');
}

export async function getCall(tx: Tx, ctx: Ctx, callId: string): Promise<CallDTO> {
  const { c, t, phone } = await load(tx, ctx, callId);
  return view(c, t, phone);
}

export async function endCall(tx: Tx, ctx: Ctx, callId: string): Promise<CallDTO> {
  const { c, t, phone } = await load(tx, ctx, callId);
  if (c.state !== 'live' && c.state !== 'dialing') throw conflict('not_live', 'This call has already ended.');
  const live = view(c, t, phone);
  const outcome = outcomeFor(t.number);
  const [updated] = await tx
    .update(s.calls)
    .set({
      state: 'wrap',
      endedAt: clock.now(),
      durationSec: live.durationSec,
      script: live.transcript,
      summary: outcome.summary,
      updates: outcome.updates,
      recordingKey: `calls/${t.orgId}/${c.id}.mp3`,
    })
    .where(eq(s.calls.id, c.id))
    .returning();
  return view(updated!, t, phone);
}

export async function saveCall(tx: Tx, ctx: Ctx, callId: string, discard: boolean): Promise<void> {
  const { c, t } = await load(tx, ctx, callId);
  if (c.state !== 'wrap') throw conflict('not_wrapped', 'End the call before saving it.');
  if (discard) {
    await tx.update(s.calls).set({ state: 'discarded' }).where(eq(s.calls.id, c.id));
    return;
  }
  const locked = await lockTicket(tx, ctx.orgId, t.id);
  const outcome = outcomeFor(t.number);
  const dur = fmt(c.durationSec);
  await tx.update(s.calls).set({ state: 'saved' }).where(eq(s.calls.id, c.id));
  await addComment(tx, t.orgId, t.id, { ...actorOf(ctx), name: 'Call recording' }, 'call', `Recording saved (${dur}) with full transcript, ${c.script.length} turns. ${c.summary ?? ''}`);
  await addComment(tx, t.orgId, t.id, AI_ACTOR, 'system', `Ticket context updated from the call: ${outcome.updates.join(' · ')}`);
  await tx.insert(s.attachments).values([
    { orgId: t.orgId, ticketId: t.id, ext: 'MP3', name: `Call recording · ${dur}`, size: `${dur} min`, storageKey: c.recordingKey },
    { orgId: t.orgId, ticketId: t.id, ext: 'TXT', name: `Call transcript · ${c.script.length} turns`, size: '4 KB', storageKey: `calls/${t.orgId}/${c.id}.txt` },
  ]);
  if ('completeSubtask' in outcome && outcome.completeSubtask) await setSubtask(tx, t.orgId, t.id, outcome.completeSubtask, true, ctx.user.id);
  await updateTicket(tx, locked, {
    loggedMinutes: locked.loggedMinutes + Math.max(1, Math.round(c.durationSec / 60)),
    sentiment: t.number === 48199 ? 'de-escalating' : locked.sentiment,
  });
  await recordTicketEvent(tx, locked, { actor: actorOf(ctx), action: 'call.saved', summary: `${ctx.user.name} saved a ${dur} call on QRY-${t.number}`, data: { callId: c.id } });
}
