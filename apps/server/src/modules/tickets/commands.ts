import { LANE_AUTONOMY, LANE_NAME, STATUS_LABEL, ticketNumber, type Lane, type TicketStatus } from '@ci/contracts';
import { and, eq, sql } from 'drizzle-orm';
import type { Tx } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { canTransition } from '../../domain/transitions.js';
import { clock } from '../../platform/clock.js';
import { actorOf, type Ctx } from '../../platform/context.js';
import { badRequest, conflict, forbidden, notFound } from '../../platform/errors.js';
import { cancelJob, enqueue } from '../../platform/jobs.js';
import { CLEARANCE, clearanceOf, requireCap, requireClearance } from '../../platform/rbac.js';
import { storeFeedback, bucketerFor } from '../gateway/service.js';
import { currentAction, currentDraft } from '../gateway/gate.js';
import { pickAssignee } from '../people/routing.js';
import { addComment, lockTicket, nextNumber, recordTicketEvent, setSubtask, systemNote, updateTicket } from './ops.js';
import { findTicket } from './queries.js';

async function requireTicket(tx: Tx, ctx: Ctx, idOrNumber: string) {
  const t = await findTicket(tx, ctx.orgId, idOrNumber);
  if (!t) throw notFound('Ticket');
  return lockTicket(tx, ctx.orgId, t.id);
}

export async function transition(tx: Tx, ctx: Ctx, id: string, to: TicketStatus, expectVersion?: number): Promise<void> {
  requireCap(ctx, 'ticket.work', 'change ticket status');
  const t = await requireTicket(tx, ctx, id);
  const from = t.status as TicketStatus;
  if (!canTransition(from, to)) {
    throw conflict('illegal_transition', `A ticket cannot move from "${STATUS_LABEL[from]}" to "${STATUS_LABEL[to]}" directly.`);
  }
  await requireClearance(tx, ctx, t.departmentId, CLEARANCE.resolve, 'change this ticket');
  const now = clock.now();
  const patch: Partial<typeof s.tickets.$inferInsert> = { status: to };
  if (to === 'waiting_customer') patch.pausedAt = now;
  if (from === 'waiting_customer' && t.pausedAt && t.dueAt) {
    // The clock was paused: push the deadline out by the paused duration.
    patch.dueAt = new Date(t.dueAt.getTime() + (now.getTime() - t.pausedAt.getTime()));
    patch.pausedAt = null;
  }
  if (to === 'resolved') {
    patch.resolvedAt = now;
    patch.resolution = t.resolution ?? `Resolved by ${ctx.user.name}`;
  }
  if (to === 'closed') patch.closedAt = now;
  if ((from === 'resolved' || from === 'closed') && to === 'with_human') {
    patch.reopenCount = t.reopenCount + 1;
    patch.resolvedAt = null;
    patch.closedAt = null;
    patch.resolution = null;
  }
  patch.nextMove = to === 'waiting_customer' ? 'Waiting on the customer — clock paused' : to === 'resolved' ? `Resolved by ${ctx.user.name}` : t.nextMove;
  await updateTicket(tx, t, patch, expectVersion);
  await systemNote(tx, t.orgId, t.id, `${ctx.user.name} moved the ticket from ${STATUS_LABEL[from]} to ${STATUS_LABEL[to]}.`);
  await recordTicketEvent(tx, t, { actor: actorOf(ctx), action: 'ticket.transitioned', summary: `QRY-${t.number}: ${from} → ${to}`, data: { from, to } });
}

export async function assign(tx: Tx, ctx: Ctx, id: string, userId: string | null | undefined): Promise<{ assignee: string; reason: string }> {
  requireCap(ctx, 'ticket.assign', 'reassign a ticket to someone else');
  const t = await requireTicket(tx, ctx, id);
  let target: { id: string; name: string; reason: string } | null;
  if (userId) {
    const [u] = await tx.select().from(s.users).where(eq(s.users.id, userId));
    if (!u) throw notFound('Person');
    const level = await clearanceOf(tx, ctx.orgId, u.id, t.departmentId);
    if (level < CLEARANCE.resolve) throw forbidden(`${u.name} is not cleared to resolve work for this team.`, 'clearance_required');
    target = { id: u.id, name: u.name, reason: 'assigned by a team lead' };
  } else {
    target = await pickAssignee(tx, ctx.orgId, t.departmentId, t.bucket, t.assigneeId);
  }
  if (!target) throw conflict('no_candidate', 'Nobody available is cleared for this team. Escalate to the team lead.');
  await updateTicket(tx, t, { assigneeId: target.id, ownerKind: 'user' });
  await systemNote(tx, t.orgId, t.id, `Assigned to ${target.name} — ${target.reason}.`);
  await recordTicketEvent(tx, t, { actor: actorOf(ctx), action: 'ticket.assigned', summary: `QRY-${t.number} assigned to ${target.name}`, data: { to: target.id } });
  return { assignee: target.name, reason: target.reason };
}

/**
 * Override the lane. Moving toward less autonomy is open to anyone who works the ticket; moving toward
 * more needs a team lead, and the pipeline still applies policy (it cannot auto-execute a locked cell).
 */
export async function overrideLane(tx: Tx, ctx: Ctx, id: string, lane: Lane): Promise<void> {
  requireCap(ctx, 'ticket.work', 'override the handling');
  const t = await requireTicket(tx, ctx, id);
  if (t.lane === lane) return;
  const up = LANE_AUTONOMY[lane] > LANE_AUTONOMY[t.lane as Lane];
  if (up) requireCap(ctx, 'ticket.override_up', 'give the AI more autonomy on a ticket');
  const agent = lane === 'manual' || t.lane === 'manual' ? 'Guardrail Sentinel' : await bucketerFor(tx, t);
  await storeFeedback(
    tx,
    ctx,
    t,
    agent,
    'override',
    `Staff overrode the handling: ${LANE_NAME[t.lane as Lane]} → ${LANE_NAME[lane]}. The thread re-queues down the new path.`,
    lane === 'manual' ? 'prompt' : 'context',
  );

  // Park whatever was waiting at the gate on the old path.
  const cur = await currentAction(tx, ctx.orgId, t.id, true);
  if (cur && ['drafted', 'awaiting_checker'].includes(cur.a.state)) {
    await tx.update(s.actionInstances).set({ state: 'cancelled', rejectedNote: 'Lane overridden' }).where(eq(s.actionInstances.id, cur.a.id));
    await cancelJob(tx, t.orgId, `esc:${cur.a.id}`);
  }
  const d = await currentDraft(tx, ctx.orgId, t.id, true);
  if (d && d.state === 'draft' && lane !== 'draft') {
    await tx.update(s.drafts).set({ state: 'discarded' }).where(eq(s.drafts.id, d.id));
  }

  if (lane === 'manual') {
    await updateTicket(tx, t, {
      lane,
      laneNote: `Overridden by ${ctx.user.name} · was ${t.lane}`,
      status: 'with_human',
      assigneeId: t.assigneeId ?? ctx.user.id,
      ownerKind: 'user',
      nextMove: 'Handle it yourself — the AI stood down',
    });
  } else {
    await updateTicket(tx, t, { lane, laneNote: `Overridden by ${ctx.user.name} · was ${t.lane}`, status: 'triaging', nextMove: 'Re-running down the new path' });
    await enqueue(tx, { orgId: t.orgId, kind: 'triage', payload: { ticketId: t.id, forceLane: lane }, dedupeKey: `triage:${t.id}:${clock.now().getTime()}` });
  }
  await systemNote(tx, t.orgId, t.id, `${ctx.user.name} overrode the handling to ${LANE_NAME[lane]}. Override stored against ${agent}.`);
  await recordTicketEvent(tx, t, {
    actor: actorOf(ctx),
    action: 'ticket.lane_overridden',
    summary: `QRY-${t.number} re-queued as ${lane}`,
    data: { from: t.lane, to: lane, agent },
    feed: { tone: 'muted', meta: `QRY-${t.number} · override` },
  });
}

export async function comment(tx: Tx, ctx: Ctx, id: string, kind: 'note' | 'public', body: string): Promise<void> {
  requireCap(ctx, kind === 'public' ? 'ticket.reply' : 'ticket.work', kind === 'public' ? 'reply to customers' : 'add notes');
  const t = await requireTicket(tx, ctx, id);
  await addComment(tx, t.orgId, t.id, actorOf(ctx), kind, body);
  await updateTicket(tx, t, { loggedMinutes: t.loggedMinutes + 2 });
  await recordTicketEvent(tx, t, { actor: actorOf(ctx), action: kind === 'note' ? 'ticket.noted' : 'ticket.replied', summary: `${ctx.user.name} added a ${kind === 'note' ? 'note' : 'reply'} on QRY-${t.number}` });
}

export async function toggleSubtask(tx: Tx, ctx: Ctx, id: string, key: string, done: boolean): Promise<void> {
  requireCap(ctx, 'ticket.work', 'update sub-tasks');
  const t = await requireTicket(tx, ctx, id);
  const [row] = await tx
    .select()
    .from(s.subtasks)
    .where(and(eq(s.subtasks.orgId, ctx.orgId), eq(s.subtasks.ticketId, t.id), eq(s.subtasks.key, key)));
  if (!row) throw notFound('Sub-task');
  if (row.owner === 'AI') throw forbidden('This sub-task is completed by the AI pipeline.');
  await setSubtask(tx, ctx.orgId, t.id, key, done, ctx.user.id);
  await recordTicketEvent(tx, t, { actor: actorOf(ctx), action: 'subtask.toggled', summary: `${row.label}: ${done ? 'done' : 'reopened'}` });
}

export async function watch(tx: Tx, ctx: Ctx, id: string, watching: boolean): Promise<void> {
  const t = await requireTicket(tx, ctx, id);
  if (watching) await tx.insert(s.watchers).values({ orgId: ctx.orgId, ticketId: t.id, userId: ctx.user.id }).onConflictDoNothing();
  else
    await tx
      .delete(s.watchers)
      .where(and(eq(s.watchers.orgId, ctx.orgId), eq(s.watchers.ticketId, t.id), eq(s.watchers.userId, ctx.user.id)));
}

export async function escalate(tx: Tx, ctx: Ctx, id: string): Promise<{ to: string }> {
  requireCap(ctx, 'ticket.work', 'escalate tickets');
  const t = await requireTicket(tx, ctx, id);
  const [dept] = t.departmentId ? await tx.select().from(s.departments).where(eq(s.departments.id, t.departmentId)) : [];
  const [owner] = dept?.ownerId ? await tx.select().from(s.users).where(eq(s.users.id, dept.ownerId)) : [];
  const higher = { P4: 'P3', P3: 'P2', P2: 'P1', P1: 'P1' } as const;
  await updateTicket(tx, t, { priority: higher[t.priority as keyof typeof higher] });
  if (owner) await tx.insert(s.watchers).values({ orgId: t.orgId, ticketId: t.id, userId: owner.id }).onConflictDoNothing();
  const to = owner?.name ?? 'the team lead';
  await systemNote(tx, t.orgId, t.id, `${ctx.user.name} escalated to ${to}${t.regulatoryFlag ? '. Compliance notified (regulatory flag on the thread)' : ''}.`);
  await recordTicketEvent(tx, t, {
    actor: actorOf(ctx),
    action: 'ticket.escalated',
    summary: `QRY-${t.number} escalated to ${to}`,
    feed: { tone: 'stop', meta: `QRY-${t.number} · escalation` },
  });
  return { to };
}

/** Split a multi-intent ticket into child tickets; each child is triaged on its own. */
export async function split(tx: Tx, ctx: Ctx, id: string): Promise<{ children: string[] }> {
  requireCap(ctx, 'ticket.work', 'split tickets');
  const t = await requireTicket(tx, ctx, id);
  const [first] = await tx.select().from(s.messages).where(and(eq(s.messages.orgId, ctx.orgId), eq(s.messages.ticketId, t.id))).orderBy(s.messages.sentAt).limit(1);
  const body = first?.body ?? t.subject;
  // Split at the pivot the customer used ("Separately", "Also", "Second"), else by sentence halves.
  const pivot = /\b(Separately|Also|Second(?:ly)?|In addition)[,:]?\s/i.exec(body);
  const parts = pivot
    ? [body.slice(0, pivot.index).trim(), body.slice(pivot.index).trim()]
    : (() => {
        const sentences = body.split(/(?<=[.?!])\s+/);
        const mid = Math.ceil(sentences.length / 2);
        return [sentences.slice(0, mid).join(' '), sentences.slice(mid).join(' ')];
      })();
  if (parts.some((p) => !p)) throw badRequest('cannot_split', 'This email does not contain two separable requests.');
  const now = clock.now();
  const children: string[] = [];
  for (const [i, part] of parts.entries()) {
    const number = await nextNumber(tx, ctx.orgId, 'ticket');
    const subject = `${part.split(/(?<=[.?!])\s/)[0]!.replace(/^(Two things\.\s*)/i, '').slice(0, 110)} (split from QRY-${t.number})`;
    const [child] = await tx
      .insert(s.tickets)
      .values({
        orgId: t.orgId,
        number,
        boardId: t.boardId,
        mailboxId: t.mailboxId,
        customerId: t.customerId,
        subject,
        fromName: t.fromName,
        fromEmail: t.fromEmail,
        receivedAt: t.receivedAt,
        lane: 'manual',
        originalLane: 'manual',
        laneNote: 'Split — being triaged',
        status: 'triaging',
        priority: t.priority,
        segment: t.segment,
        ownerKind: 'ai',
        slaMinutes: t.slaMinutes,
        dueAt: t.dueAt,
        parentId: t.id,
        nextMove: 'Triaging',
      })
      .returning();
    await tx.insert(s.messages).values({
      orgId: t.orgId,
      ticketId: child!.id,
      direction: 'inbound',
      fromName: t.fromName,
      fromAddr: t.fromEmail,
      toAddr: first?.toAddr ?? '',
      body: part.replace(/^(Two things\.\s*)/i, ''),
      sentAt: first?.sentAt ?? now,
    });
    await tx.insert(s.ticketLinks).values([
      { orgId: t.orgId, ticketId: child!.id, kind: 'PARENT', label: `QRY-${t.number} · ${t.subject.slice(0, 60)}`, ref: `QRY-${t.number}`, sort: 0 },
      { orgId: t.orgId, ticketId: t.id, kind: 'CHILD', label: `${ticketNumber(number)} · part ${i + 1}`, ref: ticketNumber(number), sort: 10 + i },
    ]);
    await enqueue(tx, { orgId: t.orgId, kind: 'triage', payload: { ticketId: child!.id }, dedupeKey: `triage:${child!.id}` });
    children.push(ticketNumber(number));
  }
  await updateTicket(tx, t, {
    status: 'resolved',
    resolvedAt: now,
    splitProposed: false,
    resolution: `Split into ${children.join(' and ')}`,
    nextMove: `Split into ${children.join(' and ')}`,
  });
  await setSubtask(tx, t.orgId, t.id, 'c2', true, ctx.user.id);
  await systemNote(tx, t.orgId, t.id, `${ctx.user.name} split this into ${children.join(' and ')}. Each is triaged and routed separately.`);
  await recordTicketEvent(tx, t, { actor: actorOf(ctx), action: 'ticket.split', summary: `QRY-${t.number} split into ${children.join(', ')}`, data: { children } });
  return { children };
}

export async function merge(tx: Tx, ctx: Ctx, id: string, intoNumber: string): Promise<void> {
  requireCap(ctx, 'ticket.work', 'merge tickets');
  const t = await requireTicket(tx, ctx, id);
  const target = await findTicket(tx, ctx.orgId, intoNumber);
  if (!target) throw notFound(intoNumber);
  if (target.id === t.id) throw badRequest('same_ticket', 'A ticket cannot be merged into itself.');
  if (target.customerId !== t.customerId) throw conflict('different_customer', 'Only tickets from the same customer can be merged.');
  await tx.update(s.messages).set({ ticketId: target.id }).where(and(eq(s.messages.orgId, ctx.orgId), eq(s.messages.ticketId, t.id)));
  await updateTicket(tx, t, { status: 'closed', closedAt: clock.now(), mergedIntoId: target.id, resolution: `Merged into ${intoNumber}` });
  await tx.insert(s.ticketLinks).values([
    { orgId: t.orgId, ticketId: target.id, kind: 'MERGED', label: `QRY-${t.number} merged in`, ref: `QRY-${t.number}`, sort: 20 },
  ]);
  await systemNote(tx, t.orgId, target.id, `${ctx.user.name} merged QRY-${t.number} into this ticket; its messages are now on this thread.`);
  await recordTicketEvent(tx, t, { actor: actorOf(ctx), action: 'ticket.merged', summary: `QRY-${t.number} merged into ${intoNumber}` });
}

/** Start one of the brief's suggested moves: it becomes a sub-task owned by the person who started it. */
export async function startSuggestion(tx: Tx, ctx: Ctx, id: string, index: number): Promise<void> {
  requireCap(ctx, 'ticket.work', 'work tickets');
  const t = await requireTicket(tx, ctx, id);
  const [brief] = await tx.select().from(s.briefs).where(and(eq(s.briefs.orgId, ctx.orgId), eq(s.briefs.ticketId, t.id)));
  const sug = brief?.suggestions[index];
  if (!sug) throw notFound('Suggestion');
  const key = `s${index}`;
  await tx
    .insert(s.subtasks)
    .values({ orgId: t.orgId, ticketId: t.id, key, label: sug.label, owner: 'You', sort: 20 + index })
    .onConflictDoNothing();
  await addComment(tx, t.orgId, t.id, actorOf(ctx), 'note', `Started: ${sug.label}${sug.meta ? ` (${sug.meta})` : ''}.`);
  await recordTicketEvent(tx, t, { actor: actorOf(ctx), action: 'suggestion.started', summary: `${ctx.user.name} started "${sug.label}" on QRY-${t.number}` });
}

export async function logTime(tx: Tx, ctx: Ctx, id: string, minutes: number): Promise<void> {
  const t = await requireTicket(tx, ctx, id);
  await tx.update(s.tickets).set({ loggedMinutes: sql`${s.tickets.loggedMinutes} + ${minutes}` }).where(eq(s.tickets.id, t.id));
}
