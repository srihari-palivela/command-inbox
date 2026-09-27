import {
  STATUS_GROUP,
  initialsOf,
  ticketNumber,
  type InboxDTO,
  type Lane,
  type Priority,
  type TicketFilters,
  type TicketListDTO,
  type TicketStatus,
  type TicketSummaryDTO,
} from '@ci/contracts';
import { and, asc, desc, eq, inArray, isNull, or, sql, type SQL } from 'drizzle-orm';
import type { Tx } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { atRisk, computeSla } from '../../domain/sla.js';
import { isOpen } from '../../domain/transitions.js';
import { clock } from '../../platform/clock.js';
import type { Ctx } from '../../platform/context.js';

export type TicketRow = typeof s.tickets.$inferSelect;

export interface SummaryExtras {
  departmentName: string | null;
  boardName: string | null;
  assignee: { id: string; name: string; initials: string } | null;
  threadCount: number;
  actionState: string | null;
  actionChain: string | null;
  draftState: string | null;
}

export async function orgBar(tx: Tx, orgId: string): Promise<number> {
  const [o] = await tx.select({ bar: s.orgs.confidenceBar }).from(s.orgs).where(eq(s.orgs.id, orgId));
  return o?.bar ?? 0.78;
}

/** Load tickets with the joins every summary needs. */
export async function loadSummaries(tx: Tx, orgId: string, where?: SQL): Promise<{ row: TicketRow; x: SummaryExtras }[]> {
  const rows = await tx
    .select({
      t: s.tickets,
      dept: s.departments.name,
      board: s.boards.name,
      aId: s.users.id,
      aName: s.users.name,
      aInit: s.users.initials,
    })
    .from(s.tickets)
    .leftJoin(s.departments, eq(s.departments.id, s.tickets.departmentId))
    .leftJoin(s.boards, eq(s.boards.id, s.tickets.boardId))
    .leftJoin(s.users, eq(s.users.id, s.tickets.assigneeId))
    .where(and(eq(s.tickets.orgId, orgId), where))
    .orderBy(asc(s.tickets.dueAt), desc(s.tickets.number));
  if (!rows.length) return [];
  const ids = rows.map((r) => r.t.id);

  const threadCounts = await tx
    .select({ ticketId: s.messages.ticketId, n: sql<number>`count(*)::int` })
    .from(s.messages)
    .where(and(eq(s.messages.orgId, orgId), inArray(s.messages.ticketId, ids)))
    .groupBy(s.messages.ticketId);
  const actions = await tx
    .select({ ticketId: s.actionInstances.ticketId, state: s.actionInstances.state, chain: s.actionInstances.chain, createdAt: s.actionInstances.createdAt })
    .from(s.actionInstances)
    .where(and(eq(s.actionInstances.orgId, orgId), inArray(s.actionInstances.ticketId, ids)))
    .orderBy(desc(s.actionInstances.createdAt));
  const drafts = await tx
    .select({ ticketId: s.drafts.ticketId, state: s.drafts.state })
    .from(s.drafts)
    .where(and(eq(s.drafts.orgId, orgId), inArray(s.drafts.ticketId, ids)));

  const tc = new Map(threadCounts.map((r) => [r.ticketId, r.n]));
  const act = new Map<string, { state: string; chain: string }>();
  for (const a of actions) if (!act.has(a.ticketId)) act.set(a.ticketId, a);
  const dr = new Map(drafts.map((d) => [d.ticketId, d.state]));

  return rows.map((r) => ({
    row: r.t,
    x: {
      departmentName: r.dept,
      boardName: r.board,
      assignee: r.aId ? { id: r.aId, name: r.aName!, initials: r.aInit! } : null,
      threadCount: tc.get(r.t.id) ?? 0,
      actionState: act.get(r.t.id)?.state ?? null,
      actionChain: act.get(r.t.id)?.chain ?? null,
      draftState: dr.get(r.t.id) ?? null,
    },
  }));
}

export function toSummary(row: TicketRow, x: SummaryExtras, now: Date): TicketSummaryDTO {
  const open = isOpen(row.status);
  const pendingGate =
    !open
      ? null
      : x.actionState === 'drafted'
        ? 'maker'
        : x.actionState === 'awaiting_checker'
          ? 'checker'
          : row.lane === 'draft' && x.draftState === 'draft' && row.status === 'awaiting_approval'
            ? 'send'
            : null;
  return {
    id: row.id,
    number: ticketNumber(row.number),
    subject: row.subject,
    fromName: row.fromName,
    fromInitials: initialsOf(row.fromName),
    bucket: row.bucket,
    departmentId: row.departmentId,
    department: x.departmentName ?? 'Unowned',
    lane: row.lane as Lane,
    originalLane: row.originalLane as Lane,
    laneNote: row.laneNote,
    status: row.status as TicketStatus,
    priority: row.priority as Priority,
    segment: row.segment,
    confidence: row.confidence,
    receivedAt: row.receivedAt.toISOString(),
    sla: computeSla(row, now),
    assignee: x.assignee,
    ownerKind: row.ownerKind as TicketSummaryDTO['ownerKind'],
    nextMove: row.nextMove,
    boardId: row.boardId,
    boardName: x.boardName ?? '',
    threadCount: x.threadCount,
    pendingGate,
  };
}

const DAY = 24 * 3600_000;

/** What a board shows: everything open, plus what closed in the last 24 h (not old history or merged). */
function visibleOnBoard(t: TicketSummaryDTO, row: TicketRow, now: Date): boolean {
  if (row.mergedIntoId) return false;
  if (row.status === 'closed' && (!row.resolvedAt || now.getTime() - row.resolvedAt.getTime() > DAY)) return false;
  if (row.status === 'resolved' && row.resolvedAt && now.getTime() - row.resolvedAt.getTime() > DAY) return false;
  return true;
}

export type FilterFn = (t: TicketSummaryDTO) => boolean;

export function matcher(key: keyof TicketFilters, value: string, ctx: Ctx, bar: number, boardKeys: Map<string, string>): FilterFn {
  switch (key) {
    case 'board':
      return (t) => (t.boardId ? boardKeys.get(t.boardId) === value : false);
    case 'status':
      return (t) => STATUS_GROUP[t.status] === value;
    case 'lane':
      return (t) => t.lane === value;
    case 'team':
      return (t) => t.departmentId === value;
    case 'owner':
      return (t) =>
        value === 'mine'
          ? t.assignee?.id === ctx.user.id
          : value === 'ai'
            ? t.ownerKind === 'ai'
            : value === 'unassigned'
              ? t.ownerKind === 'unassigned'
              : t.assignee?.id === value;
    case 'due':
      return (t) => (value === 'risk' ? atRisk(t.sla.tone) : value === 'open' ? isOpen(t.status) : !isOpen(t.status));
    case 'conf':
      return (t) => (value === 'low' ? t.confidence < bar : t.confidence >= 0.9);
    case 'pri':
      return (t) => t.priority === value;
    case 'bucket':
      return (t) => t.bucket === value;
    case 'q': {
      const q = value.toLowerCase();
      return (t) =>
        [t.number, t.subject, t.bucket, t.department, t.fromName, t.assignee?.name ?? (t.ownerKind === 'ai' ? 'agent' : 'unassigned')]
          .join(' ')
          .toLowerCase()
          .includes(q);
    }
  }
}

const FACET_KEYS: (keyof TicketFilters)[] = ['pri', 'bucket', 'status', 'lane', 'team', 'owner', 'due', 'conf'];
const FACET_VALUES: Partial<Record<keyof TicketFilters, string[]>> = {
  pri: ['P1', 'P2', 'P3', 'P4'],
  status: ['triage', 'approval', 'executing', 'human', 'customer', 'resolved'],
  lane: ['auto', 'draft', 'manual'],
  owner: ['mine', 'ai', 'unassigned'],
  due: ['risk', 'open', 'closed'],
  conf: ['low', 'high'],
};

/**
 * Board/list query with faceted counts: each facet value is counted against every *other* active
 * filter, so the numbers in a dropdown always predict what selecting it will show.
 * Scale note: this evaluates in memory over the open working set (bounded by the 24 h window);
 * past ~10k open tickets per tenant it moves to per-facet GROUP BY queries.
 */
export async function listTickets(tx: Tx, ctx: Ctx, filters: TicketFilters): Promise<TicketListDTO> {
  const now = clock.now();
  const bar = await orgBar(tx, ctx.orgId);
  const boards = await tx.select().from(s.boards).where(eq(s.boards.orgId, ctx.orgId)).orderBy(asc(s.boards.sort));
  const boardKeys = new Map(boards.map((b) => [b.id, b.key]));
  const departments = await tx
    .select({ id: s.departments.id, name: s.departments.name })
    .from(s.departments)
    .where(eq(s.departments.orgId, ctx.orgId))
    .orderBy(asc(s.departments.sort));

  const loaded = await loadSummaries(
    tx,
    ctx.orgId,
    and(isNull(s.tickets.mergedIntoId), or(sql`${s.tickets.status} <> 'closed'`, sql`${s.tickets.resolvedAt} > now() - interval '24 hours'`)),
  );
  const base = loaded.map((l) => ({ dto: toSummary(l.row, l.x, now), row: l.row })).filter((x) => visibleOnBoard(x.dto, x.row, now));
  const universe = base.map((b) => b.dto);

  const active = (Object.entries(filters) as [keyof TicketFilters, string | undefined][]).filter(
    ([, v]) => v !== undefined && v !== '',
  ) as [keyof TicketFilters, string][];
  const fns = new Map(active.map(([k, v]) => [k, matcher(k, v, ctx, bar, boardKeys)]));
  const passes = (t: TicketSummaryDTO, except?: keyof TicketFilters) =>
    [...fns.entries()].every(([k, fn]) => k === except || fn(t));

  const items = universe.filter((t) => passes(t));
  const buckets = [...new Set(universe.map((t) => t.bucket))];

  const facets: TicketListDTO['facets'] = {};
  for (const key of FACET_KEYS) {
    const values =
      key === 'bucket' ? buckets : key === 'team' ? departments.map((d) => d.id) : FACET_VALUES[key] ?? [];
    const counts: Record<string, number> = {};
    const pool = universe.filter((t) => passes(t, key));
    for (const v of values) {
      const fn = matcher(key, v, ctx, bar, boardKeys);
      counts[v] = pool.filter(fn).length;
    }
    facets[key] = counts;
  }

  // Board tabs ignore the board filter itself.
  const tabPool = universe.filter((t) => passes(t, 'board'));
  const open = items.filter((t) => isOpen(t.status));
  return {
    items,
    total: items.length,
    all: universe.length,
    facets,
    stats: {
      open: open.length,
      awaitingDecision: items.filter((t) => t.status === 'awaiting_approval').length,
      aiOwned: items.filter((t) => t.ownerKind === 'ai').length,
      atRisk: open.filter((t) => atRisk(t.sla.tone)).length,
      closedToday: items.filter((t) => !isOpen(t.status)).length,
    },
    boards: boards.map((b) => ({
      id: b.id,
      key: b.key,
      name: b.name,
      source: '',
      state: b.state as TicketListDTO['boards'][number]['state'],
      count: tabPool.filter((t) => t.boardId === b.id).length,
    })),
    buckets,
    departments,
  };
}

const PRI_RANK: Record<string, number> = { P1: 0, P2: 1, P3: 2, P4: 3 };

/**
 * The personal queue: open work assigned to me, work waiting for my check as a checker, and what the
 * AI closed for me today (post-hoc review). Ordered by what needs a decision soonest.
 */
export async function inbox(tx: Tx, ctx: Ctx, filter: 'all' | 'auto' | 'draft' | 'manual' | 'late'): Promise<InboxDTO> {
  const now = clock.now();
  const loaded = await loadSummaries(
    tx,
    ctx.orgId,
    and(
      isNull(s.tickets.mergedIntoId),
      or(
        and(
          eq(s.tickets.assigneeId, ctx.user.id),
          or(sql`${s.tickets.status} not in ('resolved','closed')`, sql`${s.tickets.resolvedAt} > now() - interval '24 hours'`),
        ),
        ctx.capabilities.has('action.approve_checker')
          ? sql`exists (select 1 from action_instances ai where ai.ticket_id = ${s.tickets.id} and ai.state = 'awaiting_checker' and ai.maker_id <> ${ctx.user.id})`
          : sql`false`,
      ),
    ),
  );
  const all = loaded
    .map((l) => toSummary(l.row, l.x, now))
    .sort((a, b) => {
      const ao = isOpen(a.status) ? 0 : 1;
      const bo = isOpen(b.status) ? 0 : 1;
      if (ao !== bo) return ao - bo;
      if (PRI_RANK[a.priority] !== PRI_RANK[b.priority]) return PRI_RANK[a.priority]! - PRI_RANK[b.priority]!;
      const al = a.sla.minutesLeft ?? 1e9;
      const bl = b.sla.minutesLeft ?? 1e9;
      return al - bl || b.number.localeCompare(a.number);
    });
  const late = (t: TicketSummaryDTO) => isOpen(t.status) && atRisk(t.sla.tone);
  const counts = {
    all: all.length,
    auto: all.filter((t) => t.lane === 'auto').length,
    draft: all.filter((t) => t.lane === 'draft').length,
    manual: all.filter((t) => t.lane === 'manual').length,
    late: all.filter(late).length,
  };
  const items = filter === 'all' ? all : filter === 'late' ? all.filter(late) : all.filter((t) => t.lane === filter);
  return { items, counts };
}

export async function findTicket(tx: Tx, orgId: string, idOrNumber: string): Promise<TicketRow | undefined> {
  const m = /^QRY-(\d+)$/i.exec(idOrNumber);
  const where = m
    ? and(eq(s.tickets.orgId, orgId), eq(s.tickets.number, Number(m[1])))
    : /^[0-9a-f-]{36}$/i.test(idOrNumber)
      ? and(eq(s.tickets.orgId, orgId), eq(s.tickets.id, idOrNumber))
      : undefined;
  if (!where) return undefined;
  const [row] = await tx.select().from(s.tickets).where(where);
  return row;
}
