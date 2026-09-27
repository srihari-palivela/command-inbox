/**
 * The approval gateway's read model: what the person looking at a ticket may do right now, and why not.
 * The same checks run again inside every command, so the UI can never grant more than the server.
 */
import type { ApprovalChain, GateDTO, UserRef } from '@ci/contracts';
import { and, desc, eq, gte, inArray, ne, sql } from 'drizzle-orm';
import type { Tx } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { isOpen } from '../../domain/transitions.js';
import { clock } from '../../platform/clock.js';
import type { Ctx } from '../../platform/context.js';
import { CLEARANCE, CLEARANCE_LABEL, clearanceOf } from '../../platform/rbac.js';
import type { TicketRow } from '../tickets/queries.js';

export type ActionRow = typeof s.actionInstances.$inferSelect;
export type DraftRow = typeof s.drafts.$inferSelect;
export type TemplateRow = typeof s.actionTemplates.$inferSelect;

export async function currentAction(tx: Tx, orgId: string, ticketId: string, lock = false) {
  const q = tx
    .select({ a: s.actionInstances, t: s.actionTemplates })
    .from(s.actionInstances)
    .innerJoin(s.actionTemplates, eq(s.actionTemplates.id, s.actionInstances.templateId))
    .where(and(eq(s.actionInstances.orgId, orgId), eq(s.actionInstances.ticketId, ticketId)))
    .orderBy(desc(s.actionInstances.createdAt))
    .limit(1);
  const [row] = lock ? await q.for('update', { of: s.actionInstances }) : await q;
  return row ?? null;
}

export async function currentDraft(tx: Tx, orgId: string, ticketId: string, lock = false): Promise<DraftRow | null> {
  const q = tx.select().from(s.drafts).where(and(eq(s.drafts.orgId, orgId), eq(s.drafts.ticketId, ticketId)));
  const [row] = lock ? await q.for('update') : await q;
  return row ?? null;
}

export async function userRef(tx: Tx, id: string | null): Promise<UserRef | null> {
  if (!id) return null;
  const [u] = await tx.select({ id: s.users.id, name: s.users.name, initials: s.users.initials }).from(s.users).where(eq(s.users.id, id));
  return u ?? null;
}

/** Least-loaded available checker: lead/admin, approve clearance in the department, not the maker. */
export async function proposeChecker(tx: Tx, orgId: string, departmentId: string | null, excludeUserId: string | null): Promise<UserRef | null> {
  if (!departmentId) return null;
  const rows = await tx.execute<{ id: string; name: string; initials: string; load: number }>(sql`
    select u.id, u.name, u.initials,
           (m.base_load + (select count(*) from tickets t where t.org_id = ${orgId} and t.assignee_id = u.id
                             and t.status not in ('resolved','closed')))::float / greatest(m.capacity, 1) as load
      from memberships m
      join users u on u.id = m.user_id
      join clearances c on c.org_id = m.org_id and c.user_id = m.user_id and c.department_id = ${departmentId} and c.level >= 3
      left join staff_availability a on a.org_id = m.org_id and a.user_id = m.user_id
     where m.org_id = ${orgId} and m.role in ('lead','admin')
       and (${excludeUserId}::uuid is null or u.id <> ${excludeUserId}::uuid)
       and coalesce(a.status, 'available') <> 'away'
     order by load asc, u.name asc
     limit 1`);
  const r = rows.rows[0];
  return r ? { id: r.id, name: r.name, initials: r.initials } : null;
}

/** "No similar action on this account in 90 days" — computed before commitment, not after. */
export async function duplicateCheck(tx: Tx, a: ActionRow): Promise<{ clear: boolean; text: string }> {
  const since = new Date(clock.now().getTime() - 90 * 24 * 3600_000);
  const [row] = await tx
    .select({ n: sql<number>`count(*)::int` })
    .from(s.actionInstances)
    .where(
      and(
        eq(s.actionInstances.orgId, a.orgId),
        eq(s.actionInstances.templateId, a.templateId),
        eq(s.actionInstances.accountRef, a.accountRef),
        ne(s.actionInstances.id, a.id),
        inArray(s.actionInstances.state, ['executed', 'scheduled', 'executing']),
        gte(s.actionInstances.createdAt, since),
      ),
    );
  const n = row?.n ?? 0;
  return n === 0
    ? { clear: true, text: 'No similar action on this account in 90 days' }
    : { clear: false, text: `${n} similar action${n > 1 ? 's' : ''} on this account in the last 90 days — check for a duplicate` };
}

export function approvalsNeeded(chain: ApprovalChain): number {
  return chain === 'dual' ? 2 : chain === 'auto' ? 0 : 1;
}

export async function computeGate(
  tx: Tx,
  ctx: Ctx,
  t: TicketRow,
  action: { a: ActionRow; t: TemplateRow } | null,
  draft: DraftRow | null,
): Promise<GateDTO> {
  const now = clock.now();
  const open = isOpen(t.status);
  const waiting = open ? Math.max(0, Math.round((now.getTime() - t.receivedAt.getTime()) / 60_000)) : null;
  const clearance = await clearanceOf(tx, ctx.orgId, ctx.user.id, t.departmentId);
  const base: GateDTO = {
    mode: 'manual',
    chain: null,
    state: 'open',
    maker: null,
    checker: null,
    proposedChecker: null,
    owner: await userRef(tx, t.assigneeId),
    customerWaitingMinutes: waiting,
    reversible: null,
    moneyMoves: null,
    duplicateClear: null,
    canApprove: false,
    blockedReason: null,
    canUndo: false,
    undoUntil: null,
    note: null,
  };
  const needClearance = (min: number, doing: string) =>
    clearance < min ? `Needs "${CLEARANCE_LABEL[min]}" clearance for ${doing} — you have "${CLEARANCE_LABEL[clearance]}".` : null;

  if (t.lane === 'auto' && action) {
    const a = action.a;
    const chain = a.chain as ApprovalChain;
    const g: GateDTO = {
      ...base,
      mode: 'action',
      chain,
      reversible: action.t.reversible,
      moneyMoves: action.t.moneyMoves,
      maker: await userRef(tx, a.makerId),
      checker: await userRef(tx, a.checkerId),
      note: a.failure,
    };
    const dup = await duplicateCheck(tx, a);
    g.duplicateClear = dup.clear;
    switch (a.state) {
      case 'drafted':
      case 'failed':
        g.state = 'open';
        g.proposedChecker = chain === 'dual' ? await proposeChecker(tx, ctx.orgId, t.departmentId, ctx.user.id) : null;
        if (!ctx.capabilities.has('action.approve_maker')) g.blockedReason = 'Your role cannot approve actions.';
        else g.blockedReason = needClearance(CLEARANCE.resolve, 'this team');
        if (!open) g.blockedReason = 'This ticket is closed.';
        break;
      case 'awaiting_checker':
        g.state = 'awaiting_checker';
        g.proposedChecker = await proposeChecker(tx, ctx.orgId, t.departmentId, a.makerId);
        if (a.makerId === ctx.user.id) g.blockedReason = 'You approved as maker — a different person must check it.';
        else if (!ctx.capabilities.has('action.approve_checker')) g.blockedReason = 'Only a team lead or admin can approve as checker.';
        else g.blockedReason = needClearance(CLEARANCE.approve, 'checking this team’s actions');
        break;
      case 'scheduled':
        g.state = 'scheduled';
        g.undoUntil = a.executeAfter?.toISOString() ?? null;
        g.canUndo =
          action.t.reversible &&
          !!a.executeAfter &&
          a.executeAfter > now &&
          (a.makerId === ctx.user.id || a.checkerId === ctx.user.id || ctx.capabilities.has('action.approve_checker'));
        g.blockedReason = 'Already approved.';
        break;
      case 'executing':
        g.state = 'executing';
        g.blockedReason = 'Executing in the core system.';
        break;
      case 'executed':
        g.state = 'done';
        g.blockedReason = 'Already executed.';
        break;
      case 'rejected':
      case 'cancelled':
        g.state = 'rejected';
        g.note = a.rejectedNote;
        g.blockedReason = 'Sent back.';
        break;
    }
    g.canApprove = (g.state === 'open' || g.state === 'awaiting_checker') && g.blockedReason === null;
    return g;
  }

  if (t.lane === 'draft' && draft) {
    const g: GateDTO = { ...base, mode: 'draft', chain: 'single_undo', reversible: true, moneyMoves: false };
    switch (draft.state) {
      case 'draft':
        g.state = 'open';
        if (!ctx.capabilities.has('ticket.reply')) g.blockedReason = 'Your role cannot send replies.';
        else g.blockedReason = needClearance(CLEARANCE.resolve, 'replying for this team');
        if (!draft.currentBody.trim()) g.blockedReason = 'The draft is empty.';
        if (!open) g.blockedReason = 'This ticket is closed.';
        break;
      case 'scheduled':
        g.state = 'scheduled';
        g.maker = await userRef(tx, draft.sentBy);
        g.undoUntil = draft.sendAfter?.toISOString() ?? null;
        g.canUndo = !!draft.sendAfter && draft.sendAfter > now && (draft.sentBy === ctx.user.id || ctx.capabilities.has('ticket.assign'));
        g.blockedReason = 'Sending.';
        break;
      case 'sent':
        g.state = 'done';
        g.maker = await userRef(tx, draft.sentBy);
        g.blockedReason = 'Already sent.';
        break;
      default:
        g.state = 'rejected';
        g.blockedReason = 'Draft discarded.';
    }
    g.canApprove = g.state === 'open' && g.blockedReason === null;
    return g;
  }

  // Manual lane: the AI has stepped back; the move is to take ownership.
  const g: GateDTO = { ...base, mode: 'manual', state: t.acceptedAt ? 'taken' : 'open' };
  if (!open) g.state = 'done';
  if (g.state === 'open') {
    if (!ctx.capabilities.has('ticket.work')) g.blockedReason = 'Your role cannot work tickets.';
    else g.blockedReason = needClearance(CLEARANCE.resolve, 'this team');
    g.canApprove = g.blockedReason === null;
  }
  return g;
}
