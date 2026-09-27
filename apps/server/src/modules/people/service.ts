import type { AutoAssignResultDTO, PeopleDTO, Priority, Role, StaffDTO } from '@ci/contracts';
import { and, asc, eq, sql } from 'drizzle-orm';
import type { Tx } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { atRisk, computeSla } from '../../domain/sla.js';
import { audit } from '../../platform/audit.js';
import { clock } from '../../platform/clock.js';
import { actorOf, type Ctx } from '../../platform/context.js';
import { notFound } from '../../platform/errors.js';
import { publish } from '../../platform/outbox.js';
import { CLEARANCE_LABEL, requireCap } from '../../platform/rbac.js';
import { recordTicketEvent, systemNote, updateTicket } from '../tickets/ops.js';
import { candidates } from './routing.js';

export async function listStaff(tx: Tx, ctx: Ctx): Promise<StaffDTO[]> {
  const rows = await tx.execute<{
    id: string; name: string; initials: string; role: Role; title: string; pod: string; years: number;
    capacity: number; open: number; status: string | null; checkin: string | null; calendar: string | null;
  }>(sql`
    select u.id, u.name, u.initials, m.role, m.title, m.pod, m.years, m.capacity,
           (m.base_load + (select count(*) from tickets t where t.org_id = ${ctx.orgId} and t.assignee_id = u.id
                             and t.status not in ('resolved','closed')))::int as open,
           a.status, a.checkin, a.calendar
      from memberships m
      join users u on u.id = m.user_id
      left join staff_availability a on a.org_id = m.org_id and a.user_id = u.id
     where m.org_id = ${ctx.orgId}
     order by case m.role when 'staff' then 0 when 'lead' then 1 else 2 end, m.joined_at`);
  const clear = await tx.select().from(s.clearances).where(eq(s.clearances.orgId, ctx.orgId));
  return rows.rows.map((r) => ({
    id: r.id,
    name: r.name,
    initials: r.initials,
    role: r.role,
    title: r.title,
    pod: r.pod,
    years: r.years,
    open: r.open,
    capacity: r.capacity,
    availability: (r.status ?? 'available') as StaffDTO['availability'],
    checkin: r.checkin ?? '',
    calendar: r.calendar ?? '',
    clearances: Object.fromEntries(clear.filter((c) => c.userId === r.id).map((c) => [c.departmentId, c.level])),
    isMe: r.id === ctx.user.id,
  }));
}

export async function people(tx: Tx, ctx: Ctx): Promise<PeopleDTO> {
  requireCap(ctx, 'insights.view', 'view skills and clearance');
  const departments = await tx
    .select({ id: s.departments.id, name: s.departments.name })
    .from(s.departments)
    .where(and(eq(s.departments.orgId, ctx.orgId), eq(s.departments.inMatrix, true)))
    .orderBy(asc(s.departments.sort));
  return { staff: await listStaff(tx, ctx), departments, editable: ctx.capabilities.has('people.edit_clearance') };
}

export async function setClearance(tx: Tx, ctx: Ctx, userId: string, departmentId: string, level: number): Promise<void> {
  requireCap(ctx, 'people.edit_clearance', 'change someone’s clearance');
  const [u] = await tx.select().from(s.users).where(eq(s.users.id, userId));
  const [d] = await tx.select().from(s.departments).where(and(eq(s.departments.orgId, ctx.orgId), eq(s.departments.id, departmentId)));
  if (!u || !d) throw notFound('Person or team');
  await tx
    .insert(s.clearances)
    .values({ orgId: ctx.orgId, userId, departmentId, level })
    .onConflictDoUpdate({ target: [s.clearances.orgId, s.clearances.userId, s.clearances.departmentId], set: { level } });
  await audit(tx, ctx.orgId, {
    actor: actorOf(ctx),
    action: 'clearance.changed',
    entity: 'clearance',
    entityId: `${userId}:${departmentId}`,
    summary: `${u.name} · ${d.name} → ${CLEARANCE_LABEL[level]}`,
    data: { level },
  });
  await publish(tx, ctx.orgId, 'people.updated', {});
}

/**
 * Auto-assign at-risk work: open tickets that are P1/P2 or not on track, owned by the AI or nobody,
 * go to the least-loaded available person cleared to resolve for that team. Every move writes its reason.
 */
export async function autoAssign(tx: Tx, ctx: Ctx): Promise<AutoAssignResultDTO> {
  requireCap(ctx, 'people.auto_assign', 'run auto-assignment');
  const now = clock.now();
  const rows = await tx
    .select()
    .from(s.tickets)
    .where(and(eq(s.tickets.orgId, ctx.orgId), sql`${s.tickets.status} not in ('resolved','closed','waiting_customer')`, sql`${s.tickets.ownerKind} in ('ai','unassigned')`))
    .orderBy(asc(s.tickets.dueAt))
    .for('update');
  const atRiskRows = rows.filter((t) => t.priority === 'P1' || t.priority === 'P2' || atRisk(computeSla(t, now).tone));
  const loads = new Map<string, number>();
  const moves: AutoAssignResultDTO['moves'] = [];
  const [lead] = await tx.execute<{ name: string }>(sql`
    select u.name from memberships m join users u on u.id = m.user_id
     where m.org_id = ${ctx.orgId} and m.role = 'lead' order by m.joined_at limit 1`).then((r) => r.rows);

  for (const t of atRiskRows) {
    const minutesLeft = computeSla(t, now).minutesLeft;
    if (!t.departmentId) {
      moves.push({ ticketNumber: `QRY-${t.number}`, priority: t.priority as Priority, minutesLeft, to: null, reason: `no team owns ${t.bucket.toLowerCase()} — escalated to ${lead?.name ?? 'the team lead'}` });
      await systemNote(tx, ctx.orgId, t.id, `Auto-assign: no team owns this query type — escalated to ${lead?.name ?? 'the team lead'}.`);
      continue;
    }
    const pool = (await candidates(tx, ctx.orgId, t.departmentId))
      .filter((c) => c.availability === 'available')
      .map((c) => ({ ...c, open: c.open + (loads.get(c.id) ?? 0) }))
      .sort((a, b) => a.open / a.capacity - b.open / b.capacity);
    const pick = pool[0];
    if (!pick) {
      moves.push({ ticketNumber: `QRY-${t.number}`, priority: t.priority as Priority, minutesLeft, to: null, reason: `no available staff cleared for ${t.bucket.toLowerCase()} — escalated to ${lead?.name ?? 'the team lead'}` });
      continue;
    }
    loads.set(pick.id, (loads.get(pick.id) ?? 0) + 1);
    const reason = `available, cleared for ${pick.department}, lightest load (${pick.open}/${pick.capacity})`;
    await updateTicket(tx, t, { assigneeId: pick.id, ownerKind: 'user' });
    await systemNote(tx, ctx.orgId, t.id, `Auto-assigned to ${pick.name} — ${reason}.`);
    await recordTicketEvent(tx, t, { actor: actorOf(ctx), action: 'ticket.auto_assigned', summary: `QRY-${t.number} → ${pick.name}`, data: { reason } });
    moves.push({ ticketNumber: `QRY-${t.number}`, priority: t.priority as Priority, minutesLeft, to: pick.name, reason });
  }
  await audit(tx, ctx.orgId, {
    actor: actorOf(ctx),
    action: 'auto_assign.ran',
    entity: 'queue',
    summary: `Auto-assign checked ${atRiskRows.length} at-risk ticket${atRiskRows.length === 1 ? '' : 's'}`,
    feed: { tone: 'info', meta: 'queue · auto-assign' },
  });
  await publish(tx, ctx.orgId, 'activity.created', {});
  return { checked: atRiskRows.length, moves };
}
