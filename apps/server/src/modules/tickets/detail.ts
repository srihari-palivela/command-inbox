import type {
  ActionDTO,
  CustomerDTO,
  DraftDTO,
  PastTicketDTO,
  TicketDetailDTO,
  TicketStatus,
  TraceDTO,
} from '@ci/contracts';
import { ticketNumber } from '@ci/contracts';
import { and, asc, desc, eq, ne } from 'drizzle-orm';
import type { Tx } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { cellOf } from '../../domain/risk.js';
import { allowedTransitions } from '../../domain/transitions.js';
import { clock } from '../../platform/clock.js';
import type { Ctx } from '../../platform/context.js';
import { notFound } from '../../platform/errors.js';
import { computeGate, currentAction, currentDraft, duplicateCheck, userRef } from '../gateway/gate.js';
import { findTicket, loadSummaries, toSummary } from './queries.js';

function outcomeOf(t: typeof s.tickets.$inferSelect): { outcome: string; tone: 'ok' | 'warn' | 'bad' } {
  if (t.resolution) {
    const bad = /no resolution/i.test(t.resolution);
    const warn = /1d|days/i.test(t.resolution);
    return { outcome: t.resolution, tone: bad ? 'bad' : warn ? 'warn' : 'ok' };
  }
  if (!t.resolvedAt) return { outcome: 'Still open', tone: 'warn' };
  const h = (t.resolvedAt.getTime() - t.receivedAt.getTime()) / 3600_000;
  return {
    outcome: h < 12 ? `Resolved in ${Math.max(1, Math.round(h))}h` : 'Resolved',
    tone: h > 24 ? 'warn' : 'ok',
  };
}

export async function getTicketDetail(tx: Tx, ctx: Ctx, idOrNumber: string): Promise<TicketDetailDTO> {
  const found = await findTicket(tx, ctx.orgId, idOrNumber);
  if (!found) throw notFound('Ticket');
  const [loaded] = await loadSummaries(tx, ctx.orgId, eq(s.tickets.id, found.id));
  const t = loaded!.row;
  const now = clock.now();
  const summary = toSummary(t, loaded!.x, now);

  const messages = await tx
    .select()
    .from(s.messages)
    .where(and(eq(s.messages.orgId, ctx.orgId), eq(s.messages.ticketId, t.id)))
    .orderBy(asc(s.messages.sentAt));

  const [run] = await tx
    .select()
    .from(s.triageRuns)
    .where(and(eq(s.triageRuns.orgId, ctx.orgId), eq(s.triageRuns.ticketId, t.id)))
    .orderBy(desc(s.triageRuns.createdAt))
    .limit(1);
  let trace: TraceDTO | null = null;
  if (run) {
    const spans = await tx
      .select()
      .from(s.traceSpans)
      .where(and(eq(s.traceSpans.orgId, ctx.orgId), eq(s.traceSpans.runId, run.id)))
      .orderBy(asc(s.traceSpans.seq));
    trace = {
      traceId: run.traceId,
      totalMs: run.latencyMs,
      costMinor: run.costMinor,
      spans: spans.map((x) => ({
        seq: x.seq,
        offsetMs: x.offsetMs,
        agent: x.agent,
        model: x.model,
        action: x.action,
        output: x.output,
        latencyMs: x.latencyMs,
        tokens: x.tokens,
        costMinor: x.costMinor,
        status: x.status as 'ok' | 'flag' | 'stop',
      })),
    };
  }

  const cur = await currentAction(tx, ctx.orgId, t.id);
  let action: ActionDTO | null = null;
  if (cur) {
    const dup = await duplicateCheck(tx, cur.a);
    action = {
      id: cur.a.id,
      templateCode: cur.t.code,
      name: cur.t.name,
      system: cur.t.system,
      endpoint: cur.t.endpoint,
      reversible: cur.t.reversible,
      moneyMoves: cur.t.moneyMoves,
      cell: cellOf(cur.t.reversible, cur.t.moneyMoves),
      chain: cur.a.chain as ActionDTO['chain'],
      fields: cur.a.fields,
      validation: cur.a.validation,
      state: cur.a.state as ActionDTO['state'],
      maker: await userRef(tx, cur.a.makerId),
      makerAt: cur.a.makerAt?.toISOString() ?? null,
      checker: await userRef(tx, cur.a.checkerId),
      checkerAt: cur.a.checkerAt?.toISOString() ?? null,
      executeAfter: cur.a.executeAfter?.toISOString() ?? null,
      executedAt: cur.a.executedAt?.toISOString() ?? null,
      externalRef: cur.a.externalRef,
      duplicate: dup,
    };
  }

  const d = await currentDraft(tx, ctx.orgId, t.id);
  const draft: DraftDTO | null = d
    ? {
        id: d.id,
        subject: d.subject,
        to: d.toAddr,
        originalBody: d.originalBody,
        currentBody: d.currentBody,
        citations: d.citations,
        flagged: d.flagged,
        state: d.state as DraftDTO['state'],
        sendAfter: d.sendAfter?.toISOString() ?? null,
        sentAt: d.sentAt?.toISOString() ?? null,
      }
    : null;

  const [brief] = await tx
    .select()
    .from(s.briefs)
    .where(and(eq(s.briefs.orgId, ctx.orgId), eq(s.briefs.ticketId, t.id)));
  const gate = await computeGate(tx, ctx, t, cur, d);

  const subtasks = await tx
    .select()
    .from(s.subtasks)
    .where(and(eq(s.subtasks.orgId, ctx.orgId), eq(s.subtasks.ticketId, t.id)))
    .orderBy(asc(s.subtasks.sort));
  const log = await tx
    .select()
    .from(s.comments)
    .where(and(eq(s.comments.orgId, ctx.orgId), eq(s.comments.ticketId, t.id)))
    .orderBy(desc(s.comments.createdAt));
  const watcherRows = await tx
    .select({ id: s.users.id, name: s.users.name, initials: s.users.initials })
    .from(s.watchers)
    .innerJoin(s.users, eq(s.users.id, s.watchers.userId))
    .where(and(eq(s.watchers.orgId, ctx.orgId), eq(s.watchers.ticketId, t.id)));
  const attachments = await tx
    .select()
    .from(s.attachments)
    .where(and(eq(s.attachments.orgId, ctx.orgId), eq(s.attachments.ticketId, t.id)))
    .orderBy(asc(s.attachments.createdAt));
  const links = await tx
    .select()
    .from(s.ticketLinks)
    .where(and(eq(s.ticketLinks.orgId, ctx.orgId), eq(s.ticketLinks.ticketId, t.id)))
    .orderBy(asc(s.ticketLinks.sort));

  let customer: CustomerDTO | null = null;
  if (t.customerId) {
    const [c] = await tx
      .select()
      .from(s.customers)
      .where(and(eq(s.customers.orgId, ctx.orgId), eq(s.customers.id, t.customerId)));
    if (c) {
      const past = await tx
        .select()
        .from(s.tickets)
        .where(and(eq(s.tickets.orgId, ctx.orgId), eq(s.tickets.customerId, c.id), ne(s.tickets.id, t.id)))
        .orderBy(desc(s.tickets.receivedAt));
      const history: PastTicketDTO[] = past.map((p) => ({
        number: ticketNumber(p.number),
        subject: p.subject,
        at: p.receivedAt.toISOString(),
        ...outcomeOf(p),
        sameTopic: !!t.queryTypeId && p.queryTypeId === t.queryTypeId,
      }));
      customer = {
        id: c.id,
        cif: c.cif,
        name: c.name,
        email: c.email,
        sinceYear: c.sinceYear,
        segment: c.segment,
        account: c.account,
        history,
      };
    }
  }

  const [mb] = t.mailboxId ? await tx.select().from(s.mailboxes).where(eq(s.mailboxes.id, t.mailboxId)) : [];
  const canWork = ctx.capabilities.has('ticket.work');

  return {
    ...summary,
    version: t.version,
    fromEmail: t.fromEmail,
    mailbox: mb?.address ?? '',
    category: t.category,
    subcategory: t.subcategory,
    product: t.product,
    regulatoryFlag: t.regulatoryFlag,
    reopenCount: t.reopenCount,
    firstReplyAt: t.firstReplyAt?.toISOString() ?? null,
    resolvedAt: t.resolvedAt?.toISOString() ?? null,
    splitProposed: t.splitProposed,
    messages: messages.map((m) => ({
      id: m.id,
      direction: m.direction as 'inbound' | 'outbound' | 'note',
      fromName: m.fromName,
      fromAddr: m.fromAddr,
      toAddr: m.toAddr,
      body: m.body,
      sentAt: m.sentAt.toISOString(),
    })),
    triage: run
      ? {
          reasoning: run.reasoning,
          evidence: run.evidence,
          confidence: run.confidence,
          latencyMs: run.latencyMs,
          provider: run.provider,
          classifiedAt: run.createdAt.toISOString(),
        }
      : null,
    action,
    draft,
    brief: brief
      ? { why: brief.why, summary: brief.summary, context: brief.context, suggestions: brief.suggestions }
      : null,
    gate,
    trace,
    subtasks: subtasks.map((x) => ({ key: x.key, label: x.label, owner: x.owner, done: x.done })),
    log: log.map((c) => ({
      id: c.id,
      kind: c.kind as 'note' | 'public' | 'call' | 'system',
      authorName: c.authorName,
      authorInitials: c.authorInitials,
      body: c.body,
      at: c.createdAt.toISOString(),
    })),
    watchers: watcherRows,
    watching: watcherRows.some((w) => w.id === ctx.user.id),
    attachments: attachments.map((a) => ({ id: a.id, ext: a.ext, name: a.name, size: a.size })),
    links: links.map((l) => ({ kind: l.kind, label: l.label, ref: l.ref })),
    customer,
    loggedMinutes: t.loggedMinutes,
    allowedTransitions: canWork ? allowedTransitions(t.status as TicketStatus) : [],
    permissions: {
      canWork,
      canAssign: ctx.capabilities.has('ticket.assign'),
      canOverrideUp: ctx.capabilities.has('ticket.override_up'),
      canReply: ctx.capabilities.has('ticket.reply'),
    },
  };
}
