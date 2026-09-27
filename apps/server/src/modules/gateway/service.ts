/**
 * Approval gateway commands. Every command runs in one tenant transaction with the ticket and its
 * action/draft row-locked, re-checks permissions and clearance, and writes audit + outbox events in the
 * same transaction. Execution and sending happen in idempotent jobs after an undo/recall window.
 */
import type { RejectReason } from '@ci/contracts';
import { and, eq } from 'drizzle-orm';
import { withTenant, type Tx } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { cellOf, CHECKER_ESCALATION_MIN, RECALL_WINDOW_SEC, UNDO_WINDOW_SEC } from '../../domain/risk.js';
import { isOpen } from '../../domain/transitions.js';
import { AI_ACTOR, actorOf, SYSTEM_ACTOR, type Ctx } from '../../platform/context.js';
import { canonicalJson, sha256 } from '../../platform/crypto.js';
import { clock } from '../../platform/clock.js';
import { conflict, forbidden, unprocessable } from '../../platform/errors.js';
import { cancelJob, enqueue, type JobRow } from '../../platform/jobs.js';
import { CLEARANCE, requireCap, requireClearance } from '../../platform/rbac.js';
import { publish } from '../../platform/outbox.js';
import { addComment, lockTicket, recordTicketEvent, setSubtask, systemNote, updateTicket } from '../tickets/ops.js';
import type { TicketRow } from '../tickets/queries.js';
import { connector } from './connectors.js';
import { currentAction, currentDraft, type ActionRow, type TemplateRow } from './gate.js';

const REJECT_NOTES: Record<RejectReason, string> = {
  wrong_type: 'Correction stored: this pattern should not be filed as that query type. Reassigned to you.',
  bad_field: 'Correction stored against the extraction rule. Reassigned to you.',
  tone: 'Correction stored as a tone example for this customer segment. Reassigned to you.',
  needs_human: 'Proposed a hard stop rule for this pattern — it applies once an Admin approves it. Reassigned to you.',
};

export async function bucketerFor(tx: Tx, t: TicketRow): Promise<string> {
  if (!t.departmentId) return 'Retail Bucketer';
  const [d] = await tx.select({ name: s.departments.name }).from(s.departments).where(eq(s.departments.id, t.departmentId));
  return d?.name === 'Trade & Payments' ? 'Trade Bucketer' : 'Retail Bucketer';
}

export async function storeFeedback(
  tx: Tx,
  ctx: Ctx | null,
  t: TicketRow,
  agentName: string,
  kind: 'override' | 'edit' | 'rejection',
  text: string,
  fix: 'prompt' | 'context',
  diff?: { original: string; current: string },
): Promise<void> {
  const [agent] = await tx
    .select({ id: s.agents.id })
    .from(s.agents)
    .where(and(eq(s.agents.orgId, t.orgId), eq(s.agents.name, agentName)));
  await tx.insert(s.feedback).values({
    orgId: t.orgId,
    agentId: agent?.id ?? null,
    agentName,
    ticketId: t.id,
    ticketNumber: `QRY-${t.number}`,
    kind,
    text,
    fix,
    diff: diff ?? null,
    createdBy: ctx?.user.id ?? null,
    createdAt: clock.now(),
  });
  await tx.insert(s.predictionOutcomes).values({ orgId: t.orgId, agentName, ticketId: t.id, confidence: t.confidence, correct: false });
}

async function recordApproval(tx: Tx, ctx: Ctx, t: TicketRow, kind: 'action' | 'draft' | 'reply', subjectId: string, step: 'maker' | 'checker' | 'sender', openedEvidence: boolean) {
  await tx.insert(s.approvals).values({ orgId: t.orgId, ticketId: t.id, subjectKind: kind, subjectId, userId: ctx.user.id, step, openedEvidence });
}

const execKey = (actionId: string, at: Date) => `exec:${actionId}:${at.getTime()}`;

/** Queue execution after the undo window (reversible) or immediately (irreversible). */
async function scheduleExecution(tx: Tx, t: TicketRow, a: ActionRow, tpl: TemplateRow): Promise<Date> {
  const now = clock.now();
  const executeAfter = tpl.reversible ? new Date(now.getTime() + UNDO_WINDOW_SEC * 1000) : now;
  await tx.update(s.actionInstances).set({ state: 'scheduled', executeAfter, failure: null }).where(eq(s.actionInstances.id, a.id));
  await enqueue(tx, {
    orgId: t.orgId,
    kind: 'execute_action',
    payload: { actionId: a.id },
    runAt: executeAfter,
    dedupeKey: execKey(a.id, executeAfter),
  });
  return executeAfter;
}

export interface ApproveResult {
  outcome: 'awaiting_checker' | 'scheduled' | 'sending' | 'taken';
}

export async function approve(tx: Tx, ctx: Ctx, ticketId: string, openedEvidence: boolean): Promise<ApproveResult> {
  const t = await lockTicket(tx, ctx.orgId, ticketId);
  if (!isOpen(t.status)) throw conflict('ticket_closed', 'This ticket is already closed.');
  const actor = actorOf(ctx);

  // ── Lane A: action ────────────────────────────────────────────────────
  if (t.lane === 'auto') {
    const cur = await currentAction(tx, ctx.orgId, t.id, true);
    if (!cur) throw unprocessable('no_action', 'There is no filled action on this ticket.');
    const { a, t: tpl } = cur;
    const cell = cellOf(tpl.reversible, tpl.moneyMoves);

    if (a.state === 'drafted' || a.state === 'failed') {
      requireCap(ctx, 'action.approve_maker', 'approve actions');
      await requireClearance(tx, ctx, t.departmentId, CLEARANCE.resolve, 'approve this action');
      await tx.update(s.actionInstances).set({ makerId: ctx.user.id, makerAt: clock.now() }).where(eq(s.actionInstances.id, a.id));
      await recordApproval(tx, ctx, t, 'action', a.id, 'maker', openedEvidence);
      await setSubtask(tx, t.orgId, t.id, 'a3', true, ctx.user.id);

      if (a.chain === 'dual') {
        await tx.update(s.actionInstances).set({ state: 'awaiting_checker' }).where(eq(s.actionInstances.id, a.id));
        await updateTicket(tx, t, { status: 'awaiting_approval', nextMove: 'Waiting for the checker' });
        await enqueue(tx, {
          orgId: t.orgId,
          kind: 'escalate_checker',
          payload: { actionId: a.id },
          runAt: new Date(clock.now().getTime() + CHECKER_ESCALATION_MIN * 60_000),
          dedupeKey: `esc:${a.id}`,
        });
        await systemNote(tx, t.orgId, t.id, `${ctx.user.name} approved ${tpl.code} as maker. Waiting for a checker with approve clearance.`);
        await recordTicketEvent(tx, t, {
          actor,
          action: 'action.maker_approved',
          summary: `${ctx.user.name} approved ${tpl.code} as maker on QRY-${t.number}`,
          data: { actionId: a.id, cell, openedEvidence },
          feed: { tone: 'info', meta: `QRY-${t.number} · maker` },
        });
        await publish(tx, t.orgId, 'gate.updated', { ticketId: t.id });
        return { outcome: 'awaiting_checker' };
      }
      const at = await scheduleExecution(tx, t, a, tpl);
      await updateTicket(tx, t, { status: 'executing', nextMove: tpl.reversible ? 'Executing after the undo window' : 'Executing now' });
      await recordTicketEvent(tx, t, {
        actor,
        action: 'action.approved',
        summary: `${ctx.user.name} approved ${tpl.code} on QRY-${t.number}`,
        data: { actionId: a.id, cell, executeAfter: at.toISOString(), openedEvidence },
      });
      return { outcome: 'scheduled' };
    }

    if (a.state === 'awaiting_checker') {
      requireCap(ctx, 'action.approve_checker', 'approve as checker');
      if (a.makerId === ctx.user.id) throw forbidden('The maker cannot also be the checker.', 'maker_checker');
      await requireClearance(tx, ctx, t.departmentId, CLEARANCE.approve, 'check this action');
      await tx.update(s.actionInstances).set({ checkerId: ctx.user.id, checkerAt: clock.now() }).where(eq(s.actionInstances.id, a.id));
      await recordApproval(tx, ctx, t, 'action', a.id, 'checker', openedEvidence);
      await setSubtask(tx, t.orgId, t.id, 'a4', true, ctx.user.id);
      await cancelJob(tx, t.orgId, `esc:${a.id}`);
      const at = await scheduleExecution(tx, t, a, tpl);
      await updateTicket(tx, t, { status: 'executing', nextMove: 'Executing in core banking' });
      await recordTicketEvent(tx, t, {
        actor,
        action: 'action.checker_approved',
        summary: `${ctx.user.name} approved ${tpl.code} as checker on QRY-${t.number}`,
        data: { actionId: a.id, cell, executeAfter: at.toISOString(), openedEvidence },
        feed: { tone: 'info', meta: `QRY-${t.number} · checker` },
      });
      return { outcome: 'scheduled' };
    }
    throw conflict('not_pending', 'This action is not waiting for an approval.');
  }

  // ── Lane B: draft ─────────────────────────────────────────────────────
  if (t.lane === 'draft') {
    const d = await currentDraft(tx, ctx.orgId, t.id, true);
    if (!d || d.state !== 'draft') throw conflict('not_pending', 'There is no draft waiting to be sent.');
    requireCap(ctx, 'ticket.reply', 'send replies');
    await requireClearance(tx, ctx, t.departmentId, CLEARANCE.resolve, 'reply for this team');
    if (!d.currentBody.trim()) throw unprocessable('empty_draft', 'The draft is empty.');
    const sendAfter = new Date(clock.now().getTime() + RECALL_WINDOW_SEC * 1000);
    await tx.update(s.drafts).set({ state: 'scheduled', sendAfter, sentBy: ctx.user.id, updatedAt: clock.now() }).where(eq(s.drafts.id, d.id));
    await recordApproval(tx, ctx, t, 'draft', d.id, 'sender', openedEvidence);
    await setSubtask(tx, t.orgId, t.id, 'b3', true, ctx.user.id);
    await enqueue(tx, { orgId: t.orgId, kind: 'send_draft', payload: { draftId: d.id }, runAt: sendAfter, dedupeKey: `send:${d.id}:${sendAfter.getTime()}` });
    await updateTicket(tx, t, { nextMove: 'Sending — recallable for 60s' });
    await recordTicketEvent(tx, t, {
      actor,
      action: 'draft.approved',
      summary: `${ctx.user.name} approved the reply on QRY-${t.number}`,
      data: { draftId: d.id, edited: d.currentBody !== d.originalBody, openedEvidence },
    });
    return { outcome: 'sending' };
  }

  // ── Lane C: take ownership ────────────────────────────────────────────
  requireCap(ctx, 'ticket.work', 'work tickets');
  await requireClearance(tx, ctx, t.departmentId, CLEARANCE.resolve, 'take this ticket');
  if (t.acceptedAt && t.assigneeId === ctx.user.id) throw conflict('already_taken', 'You already own this ticket.');
  await updateTicket(tx, t, {
    assigneeId: ctx.user.id,
    ownerKind: 'user',
    acceptedAt: clock.now(),
    status: t.status === 'triaging' || t.status === 'awaiting_approval' ? 'with_human' : t.status,
    nextMove: 'You own this — agree a dated next step',
  });
  await systemNote(tx, t.orgId, t.id, `${ctx.user.name} took this on. The AI's summary and context stay attached to the thread.`);
  await recordTicketEvent(tx, t, { actor, action: 'ticket.taken', summary: `${ctx.user.name} took QRY-${t.number}` });
  return { outcome: 'taken' };
}

export async function reject(tx: Tx, ctx: Ctx, ticketId: string, reason: RejectReason): Promise<void> {
  requireCap(ctx, 'ticket.work', 'send work back');
  const t = await lockTicket(tx, ctx.orgId, ticketId);
  if (!isOpen(t.status)) throw conflict('ticket_closed', 'This ticket is already closed.');
  const note = REJECT_NOTES[reason];
  const agentName =
    reason === 'wrong_type' ? await bucketerFor(tx, t) : reason === 'bad_field' ? 'Field Extractor' : reason === 'tone' ? 'Reply Drafter' : 'Guardrail Sentinel';

  if (t.lane === 'auto') {
    const cur = await currentAction(tx, ctx.orgId, t.id, true);
    if (cur && ['drafted', 'awaiting_checker', 'failed'].includes(cur.a.state)) {
      await tx.update(s.actionInstances).set({ state: 'rejected', rejectedNote: note }).where(eq(s.actionInstances.id, cur.a.id));
      await cancelJob(tx, t.orgId, `esc:${cur.a.id}`);
    } else {
      throw conflict('not_pending', 'Nothing is waiting at the gate to send back.');
    }
  } else if (t.lane === 'draft') {
    const d = await currentDraft(tx, ctx.orgId, t.id, true);
    if (!d || d.state !== 'draft') throw conflict('not_pending', 'Nothing is waiting at the gate to send back.');
    await tx.update(s.drafts).set({ state: 'discarded', updatedAt: clock.now() }).where(eq(s.drafts.id, d.id));
  } else {
    throw conflict('not_pending', 'The AI has already stepped back on this ticket.');
  }

  await storeFeedback(tx, ctx, t, agentName, 'rejection', note, reason === 'tone' || reason === 'needs_human' ? 'prompt' : 'context');
  if (reason === 'needs_human') {
    await tx.insert(s.proposedRules).values({
      orgId: t.orgId,
      text: `Always route to a person: queries like QRY-${t.number} (“${t.subject.slice(0, 80)}”)`,
      ticketId: t.id,
      ticketNumber: `QRY-${t.number}`,
      proposedBy: ctx.user.id,
      proposedByName: ctx.user.name,
    });
  }
  await updateTicket(tx, t, {
    lane: 'manual',
    laneNote: `Sent back by ${ctx.user.name}`,
    status: 'with_human',
    assigneeId: ctx.user.id,
    ownerKind: 'user',
    acceptedAt: clock.now(),
    nextMove: 'Handle it yourself — the AI stood down',
  });
  await systemNote(tx, t.orgId, t.id, `${ctx.user.name} sent the AI's work back (${reason.replace('_', ' ')}). ${note}`);
  await recordTicketEvent(tx, t, {
    actor: actorOf(ctx),
    action: 'gate.rejected',
    summary: `${ctx.user.name} sent QRY-${t.number} back: ${reason}`,
    data: { reason, agent: agentName },
    feed: { tone: 'muted', meta: `QRY-${t.number} · correction` },
  });
}

/** Undo inside the window: nothing has reached the core system or the customer yet. */
export async function undo(tx: Tx, ctx: Ctx, ticketId: string): Promise<void> {
  const t = await lockTicket(tx, ctx.orgId, ticketId);
  const now = clock.now();
  if (t.lane === 'auto') {
    const cur = await currentAction(tx, ctx.orgId, t.id, true);
    if (!cur || cur.a.state !== 'scheduled') throw conflict('not_undoable', 'There is nothing to undo.');
    if (!cur.t.reversible) throw conflict('irreversible', 'This action cannot be undone.');
    if (!cur.a.executeAfter || cur.a.executeAfter <= now) throw conflict('window_closed', 'The undo window has closed.');
    const mine = cur.a.makerId === ctx.user.id || cur.a.checkerId === ctx.user.id;
    if (!mine && !ctx.capabilities.has('action.approve_checker')) throw forbidden('Only an approver or a team lead can undo.');
    await tx
      .update(s.actionInstances)
      .set({ state: 'drafted', makerId: null, makerAt: null, checkerId: null, checkerAt: null, executeAfter: null })
      .where(eq(s.actionInstances.id, cur.a.id));
    // Cancel exactly this scheduled execution. (The job also re-checks state, so a race cannot execute it.)
    await cancelJob(tx, t.orgId, execKey(cur.a.id, cur.a.executeAfter));
    await setSubtask(tx, t.orgId, t.id, 'a3', false, null);
    await setSubtask(tx, t.orgId, t.id, 'a4', false, null);
    await updateTicket(tx, t, { status: 'awaiting_approval', nextMove: 'Back at the gate after undo' });
    await recordTicketEvent(tx, t, { actor: actorOf(ctx), action: 'action.undone', summary: `${ctx.user.name} undid ${cur.t.code} inside the window` });
    return;
  }
  if (t.lane === 'draft') {
    const d = await currentDraft(tx, ctx.orgId, t.id, true);
    if (!d || d.state !== 'scheduled') throw conflict('not_undoable', 'There is nothing to recall.');
    if (!d.sendAfter || d.sendAfter <= now) throw conflict('window_closed', 'The recall window has closed.');
    await tx.update(s.drafts).set({ state: 'draft', sendAfter: null, updatedAt: now }).where(eq(s.drafts.id, d.id));
    await cancelJob(tx, t.orgId, `send:${d.id}:${d.sendAfter.getTime()}`);
    await setSubtask(tx, t.orgId, t.id, 'b3', false, null);
    await updateTicket(tx, t, { nextMove: 'Recalled — review & send draft' });
    await recordTicketEvent(tx, t, { actor: actorOf(ctx), action: 'draft.recalled', summary: `${ctx.user.name} recalled the reply on QRY-${t.number}` });
    return;
  }
  throw conflict('not_undoable', 'There is nothing to undo.');
}

export async function editActionFields(tx: Tx, ctx: Ctx, ticketId: string, edits: { label: string; value: string }[]): Promise<void> {
  requireCap(ctx, 'action.approve_maker', 'amend action fields');
  const t = await lockTicket(tx, ctx.orgId, ticketId);
  await requireClearance(tx, ctx, t.departmentId, CLEARANCE.resolve, 'amend this action');
  const cur = await currentAction(tx, ctx.orgId, t.id, true);
  if (!cur || cur.a.state !== 'drafted') throw conflict('not_editable', 'Fields can only be amended before approval.');
  const changed: string[] = [];
  const fields = cur.a.fields.map((f) => {
    const e = edits.find((x) => x.label === f.label);
    if (!e || e.value === f.value) return f;
    changed.push(`${f.label}: ${f.value} → ${e.value}`);
    return { label: f.label, value: e.value, source: `edited by ${ctx.user.name}`, inferred: false };
  });
  if (!changed.length) return;
  const idempotencyKey = sha256(canonicalJson({ orgId: t.orgId, code: cur.t.code, fields: fields.map((f) => [f.label, f.value]) }));
  const [clash] = await tx
    .select({ id: s.actionInstances.id })
    .from(s.actionInstances)
    .where(and(eq(s.actionInstances.orgId, t.orgId), eq(s.actionInstances.idempotencyKey, idempotencyKey)));
  if (clash && clash.id !== cur.a.id) throw conflict('duplicate_action', 'An identical action already exists — this would execute twice.');
  await tx.update(s.actionInstances).set({ fields, idempotencyKey }).where(eq(s.actionInstances.id, cur.a.id));
  await storeFeedback(tx, ctx, t, 'Field Extractor', 'edit', `Staff amended ${changed.length} field${changed.length > 1 ? 's' : ''}: ${changed.join('; ')}.`, 'context');
  await recordTicketEvent(tx, t, { actor: actorOf(ctx), action: 'action.fields_edited', summary: `${ctx.user.name} amended ${cur.t.code}`, data: { changed } });
}

export async function editDraft(tx: Tx, ctx: Ctx, ticketId: string, body: string): Promise<void> {
  requireCap(ctx, 'ticket.reply', 'edit replies');
  const t = await lockTicket(tx, ctx.orgId, ticketId);
  const d = await currentDraft(tx, ctx.orgId, t.id, true);
  if (!d || d.state !== 'draft') throw conflict('not_editable', 'The draft can only be edited before it is sent.');
  await tx.update(s.drafts).set({ currentBody: body, updatedAt: clock.now() }).where(eq(s.drafts.id, d.id));
  await publish(tx, t.orgId, 'ticket.updated', { ticketId: t.id });
}

/** A free-text reply from the composer: sent after the same 60 s recall window. */
export async function reply(tx: Tx, ctx: Ctx, ticketId: string, body: string): Promise<{ replyId: string; sendAfter: string }> {
  requireCap(ctx, 'ticket.reply', 'send replies');
  const t = await lockTicket(tx, ctx.orgId, ticketId);
  await requireClearance(tx, ctx, t.departmentId, CLEARANCE.resolve, 'reply for this team');
  const sendAfter = new Date(clock.now().getTime() + RECALL_WINDOW_SEC * 1000);
  const [r] = await tx.insert(s.replies).values({ orgId: t.orgId, ticketId: t.id, body, state: 'scheduled', sendAfter, authorId: ctx.user.id }).returning();
  await recordApproval(tx, ctx, t, 'reply', r!.id, 'sender', true);
  await enqueue(tx, { orgId: t.orgId, kind: 'send_reply', payload: { replyId: r!.id }, runAt: sendAfter, dedupeKey: `reply:${r!.id}` });
  await recordTicketEvent(tx, t, { actor: actorOf(ctx), action: 'reply.scheduled', summary: `${ctx.user.name} replied on QRY-${t.number}` });
  return { replyId: r!.id, sendAfter: sendAfter.toISOString() };
}

export async function recallReply(tx: Tx, ctx: Ctx, replyId: string): Promise<void> {
  const [r] = await tx
    .select()
    .from(s.replies)
    .where(and(eq(s.replies.orgId, ctx.orgId), eq(s.replies.id, replyId)))
    .for('update');
  if (!r || r.state !== 'scheduled') throw conflict('not_undoable', 'There is nothing to recall.');
  if (r.authorId !== ctx.user.id) throw forbidden('Only the author can recall a reply.');
  if (r.sendAfter <= clock.now()) throw conflict('window_closed', 'The recall window has closed.');
  await tx.update(s.replies).set({ state: 'recalled' }).where(eq(s.replies.id, r.id));
  await cancelJob(tx, ctx.orgId, `reply:${r.id}`);
  await publish(tx, ctx.orgId, 'ticket.updated', { ticketId: r.ticketId });
}

// ── Jobs ──────────────────────────────────────────────────────────────────────

/** Execute an approved action. Idempotent: re-running after a crash cannot apply the effect twice. */
export async function runExecuteAction(job: JobRow): Promise<void> {
  const actionId = String(job.payload.actionId);
  const prepared = await withTenant(job.orgId, async (tx) => {
    const [row] = await tx
      .select({ a: s.actionInstances, t: s.actionTemplates })
      .from(s.actionInstances)
      .innerJoin(s.actionTemplates, eq(s.actionTemplates.id, s.actionInstances.templateId))
      .where(and(eq(s.actionInstances.orgId, job.orgId), eq(s.actionInstances.id, actionId)))
      .for('update', { of: s.actionInstances });
    if (!row || (row.a.state !== 'scheduled' && row.a.state !== 'executing')) return null;
    if (row.a.executeAfter && row.a.executeAfter > clock.now()) throw new Error('not due yet');
    const chainOk = row.a.chain !== 'dual' || (row.a.makerId && row.a.checkerId && row.a.makerId !== row.a.checkerId);
    const autoOk = row.a.chain === 'auto' || row.a.makerId;
    if (!chainOk || !autoOk) throw new Error(`refusing to execute ${row.t.code}: approval chain incomplete`);
    await tx.update(s.actionInstances).set({ state: 'executing' }).where(eq(s.actionInstances.id, row.a.id));
    return row;
  });
  if (!prepared) return;

  const result = await connector.execute({
    idempotencyKey: prepared.a.idempotencyKey,
    system: prepared.t.system,
    endpoint: prepared.t.endpoint,
    code: prepared.t.code,
    fields: prepared.a.fields,
  });

  await withTenant(job.orgId, async (tx) => {
    await tx
      .update(s.actionInstances)
      .set({ state: 'executed', executedAt: clock.now(), externalRef: result.externalRef })
      .where(eq(s.actionInstances.id, prepared.a.id));
    const t = await lockTicket(tx, job.orgId, prepared.a.ticketId);
    const maker = prepared.a.makerId ? (await tx.select().from(s.users).where(eq(s.users.id, prepared.a.makerId)))[0] : null;
    const checker = prepared.a.checkerId ? (await tx.select().from(s.users).where(eq(s.users.id, prepared.a.checkerId)))[0] : null;
    const by = maker ? `approved by ${maker.name}${checker ? `, checked by ${checker.name}` : ''}` : 'executed by the AI in the approved cell';
    await updateTicket(tx, t, {
      status: 'resolved',
      resolvedAt: clock.now(),
      resolution: `Action carried out · ${by}`,
      nextMove: `Executed ${clock.now().toISOString().slice(11, 16)}`,
    });
    await systemNote(tx, t.orgId, t.id, `Executed ${prepared.t.code} in ${prepared.t.system} (ref ${result.externalRef}) — ${by}. Written to the audit log.`);
    await tx.insert(s.predictionOutcomes).values({ orgId: t.orgId, agentName: 'Field Extractor', ticketId: t.id, confidence: t.confidence, correct: true });
    await recordTicketEvent(tx, t, {
      actor: maker ? AI_ACTOR : AI_ACTOR,
      action: 'action.executed',
      summary: `Executed ${prepared.t.code} · ${prepared.t.name.toLowerCase()} for QRY-${t.number}`,
      data: { externalRef: result.externalRef, idempotencyKey: prepared.a.idempotencyKey, alreadyApplied: result.alreadyApplied },
      feed: { tone: 'ok', meta: `QRY-${t.number} · ${maker ? 'approved' : 'auto'}` },
    });
  });
}

export async function runSendDraft(job: JobRow): Promise<void> {
  const draftId = String(job.payload.draftId);
  await withTenant(job.orgId, async (tx) => {
    const [d] = await tx.select().from(s.drafts).where(and(eq(s.drafts.orgId, job.orgId), eq(s.drafts.id, draftId))).for('update');
    if (!d || d.state !== 'scheduled') return;
    const t = await lockTicket(tx, job.orgId, d.ticketId);
    const [sender] = d.sentBy ? await tx.select().from(s.users).where(eq(s.users.id, d.sentBy)) : [];
    const [mb] = t.mailboxId ? await tx.select().from(s.mailboxes).where(eq(s.mailboxes.id, t.mailboxId)) : [];
    const now = clock.now();
    await tx.insert(s.messages).values({
      orgId: t.orgId,
      ticketId: t.id,
      direction: 'outbound',
      fromName: sender?.name ?? 'Customer care',
      fromAddr: mb?.address ?? 'customercare@bank.example',
      toAddr: d.toAddr,
      body: d.currentBody,
      sentAt: now,
    });
    await tx.update(s.drafts).set({ state: 'sent', sentAt: now, updatedAt: now }).where(eq(s.drafts.id, d.id));
    await setSubtask(tx, t.orgId, t.id, 'b4', true, d.sentBy);
    const edited = d.currentBody.trim() !== d.originalBody.trim();
    if (edited) {
      // Draft edits are the cheapest eval data there is — store the diff against the drafter.
      await storeFeedback(tx, null, t, 'Reply Drafter', 'edit', `Sent after staff edits on QRY-${t.number}. Diff stored for the next version’s golden set.`, 'prompt', {
        original: d.originalBody,
        current: d.currentBody,
      });
    } else {
      await tx.insert(s.predictionOutcomes).values({ orgId: t.orgId, agentName: 'Reply Drafter', ticketId: t.id, confidence: t.confidence, correct: true });
    }
    await addComment(tx, t.orgId, t.id, sender ? { kind: 'user', id: sender.id, name: sender.name, initials: sender.initials } : SYSTEM_ACTOR, 'public', `Sent the reply “${d.subject}”${edited ? ' (edited from the AI draft)' : ' as drafted'}.`);
    await updateTicket(tx, t, {
      status: 'resolved',
      resolvedAt: now,
      firstReplyAt: t.firstReplyAt ?? now,
      resolution: 'Reply sent and thread closed',
      nextMove: 'Reply sent',
    });
    await recordTicketEvent(tx, t, {
      actor: sender ? { kind: 'user', id: sender.id, name: sender.name, initials: sender.initials } : SYSTEM_ACTOR,
      action: 'draft.sent',
      summary: `Reply sent to ${t.fromName} on QRY-${t.number}${edited ? ' (edited)' : ''}`,
      data: { draftId: d.id, edited },
      feed: { tone: 'ok', meta: `QRY-${t.number} · sent` },
    });
  });
}

export async function runSendReply(job: JobRow): Promise<void> {
  const replyId = String(job.payload.replyId);
  await withTenant(job.orgId, async (tx) => {
    const [r] = await tx.select().from(s.replies).where(and(eq(s.replies.orgId, job.orgId), eq(s.replies.id, replyId))).for('update');
    if (!r || r.state !== 'scheduled') return;
    const t = await lockTicket(tx, job.orgId, r.ticketId);
    const [author] = await tx.select().from(s.users).where(eq(s.users.id, r.authorId));
    const [mb] = t.mailboxId ? await tx.select().from(s.mailboxes).where(eq(s.mailboxes.id, t.mailboxId)) : [];
    const now = clock.now();
    await tx.insert(s.messages).values({
      orgId: t.orgId,
      ticketId: t.id,
      direction: 'outbound',
      fromName: author?.name ?? 'Customer care',
      fromAddr: mb?.address ?? 'customercare@bank.example',
      toAddr: t.fromEmail,
      body: r.body,
      sentAt: now,
    });
    await tx.update(s.replies).set({ state: 'sent', sentAt: now }).where(eq(s.replies.id, r.id));
    const actor = author ? { kind: 'user' as const, id: author.id, name: author.name, initials: author.initials } : SYSTEM_ACTOR;
    await addComment(tx, t.orgId, t.id, actor, 'public', r.body.length > 240 ? r.body.slice(0, 237) + '…' : r.body);
    await updateTicket(tx, t, { firstReplyAt: t.firstReplyAt ?? now });
    await recordTicketEvent(tx, t, { actor, action: 'reply.sent', summary: `Reply sent to ${t.fromName} on QRY-${t.number}` });
  });
}

/** Checker silent for 2 h: escalate to the department lead and say so on the ticket. */
export async function runEscalateChecker(job: JobRow): Promise<void> {
  const actionId = String(job.payload.actionId);
  await withTenant(job.orgId, async (tx) => {
    const [a] = await tx.select().from(s.actionInstances).where(and(eq(s.actionInstances.orgId, job.orgId), eq(s.actionInstances.id, actionId)));
    if (!a || a.state !== 'awaiting_checker') return;
    const t = await lockTicket(tx, job.orgId, a.ticketId);
    await updateTicket(tx, t, { priority: 'P1', nextMove: 'Checker silent 2h — escalated to the team lead' });
    await systemNote(tx, t.orgId, t.id, 'The checker has been silent for 2 hours. Escalated to the team lead per the approval policy.');
    await recordTicketEvent(tx, t, {
      actor: AI_ACTOR,
      action: 'action.checker_escalated',
      summary: `QRY-${t.number} escalated — checker silent for 2 hours`,
      feed: { tone: 'stop', meta: `QRY-${t.number} · escalation` },
    });
  });
}
