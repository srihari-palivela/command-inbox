import type { CopilotAnswerDTO, NlFilterDTO, SearchResultDTO, TicketFilters, TicketSummaryDTO } from '@ci/contracts';
import { ticketNumber } from '@ci/contracts';
import { and, asc, desc, eq, ilike, or, sql } from 'drizzle-orm';
import type { Tx } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { parseNaturalFilters, type Chip } from '../../domain/nl-filter.js';
import { atRisk } from '../../domain/sla.js';
import { isOpen } from '../../domain/transitions.js';
import type { Ctx } from '../../platform/context.js';
import { listTickets } from '../tickets/queries.js';
import { provider, withFallback } from '../triage/providers/index.js';
import { results } from '../insights/service.js';

/** Global ⌘K search: tickets, customers, knowledge and policies from one box. */
export async function search(tx: Tx, ctx: Ctx, q: string): Promise<SearchResultDTO> {
  const term = q.trim();
  if (term.length < 2) return { tickets: [], customers: [], knowledge: [], policies: [] };
  const like = `%${term.replace(/[%_]/g, '')}%`;
  const num = /^(?:qry-)?(\d{3,})$/i.exec(term);
  const tickets = await tx
    .select({ id: s.tickets.id, number: s.tickets.number, subject: s.tickets.subject, lane: s.tickets.lane })
    .from(s.tickets)
    .where(and(eq(s.tickets.orgId, ctx.orgId), or(ilike(s.tickets.subject, like), ilike(s.tickets.fromName, like), ilike(s.tickets.bucket, like), num ? eq(s.tickets.number, Number(num[1])) : sql`false`)))
    .orderBy(desc(s.tickets.receivedAt))
    .limit(6);
  const customers = await tx.execute<{ id: string; cif: string; name: string; tickets: number; latest: string | null }>(sql`
    select c.id, c.cif, c.name,
           (select count(*) from tickets t where t.org_id = c.org_id and t.customer_id = c.id)::int as tickets,
           (select t.id from tickets t where t.org_id = c.org_id and t.customer_id = c.id order by t.received_at desc limit 1) as latest
      from customers c
     where c.org_id = ${ctx.orgId} and (c.name ilike ${like} or c.cif ilike ${like} or c.email ilike ${like})
     order by c.name limit 5`);
  const knowledge = ctx.capabilities.has('ticket.work')
    ? await tx
        .select({ id: s.knowledgeDocs.id, title: s.knowledgeDocs.title, section: s.knowledgeDocs.section, status: s.knowledgeDocs.status })
        .from(s.knowledgeDocs)
        .where(and(eq(s.knowledgeDocs.orgId, ctx.orgId), or(ilike(s.knowledgeDocs.title, like), ilike(s.knowledgeDocs.section, like), ilike(s.knowledgeDocs.body, like))))
        .orderBy(asc(s.knowledgeDocs.title))
        .limit(5)
    : [];
  const rules = await tx
    .select({ id: s.bucketRules.id, text: s.bucketRules.description })
    .from(s.bucketRules)
    .where(and(eq(s.bucketRules.orgId, ctx.orgId), ilike(s.bucketRules.description, like)))
    .limit(3);
  const pri = await tx
    .select({ id: s.priorityRules.id, text: s.priorityRules.description })
    .from(s.priorityRules)
    .where(and(eq(s.priorityRules.orgId, ctx.orgId), ilike(s.priorityRules.description, like)))
    .limit(3);
  const tpl = await tx
    .select({ id: s.actionTemplates.id, text: s.actionTemplates.name, code: s.actionTemplates.code })
    .from(s.actionTemplates)
    .where(and(eq(s.actionTemplates.orgId, ctx.orgId), or(ilike(s.actionTemplates.name, like), ilike(s.actionTemplates.code, like))))
    .limit(3);
  return {
    tickets: tickets.map((t) => ({ id: t.id, number: ticketNumber(t.number), subject: t.subject, lane: t.lane as 'auto' })),
    customers: customers.rows.map((c) => ({ id: c.id, cif: c.cif, name: c.name, tickets: c.tickets, latestTicketId: c.latest })),
    knowledge,
    policies: [
      ...rules.map((r) => ({ id: r.id, text: r.text, kind: 'Bucketing rule' })),
      ...pri.map((r) => ({ id: r.id, text: r.text, kind: 'Priority rule' })),
      ...tpl.map((r) => ({ id: r.id, text: `${r.code} · ${r.text}`, kind: 'Action template' })),
    ],
  };
}

const CHIP_TEXT: Record<string, (v: string, depts: { id: string; name: string }[]) => string> = {
  status: (v) => ({ triage: 'Agent triaging', approval: 'Awaiting approval', executing: 'Executing', human: 'With a human', customer: 'Waiting on customer', resolved: 'Resolved' })[v] ?? v,
  lane: (v) => `Handled: ${({ auto: 'Auto', draft: 'Draft', manual: 'You' } as Record<string, string>)[v] ?? v}`,
  team: (v, d) => `Team: ${d.find((x) => x.id === v)?.name ?? v}`,
  owner: (v) => `Owner: ${({ mine: 'me', ai: 'the AI', unassigned: 'nobody' } as Record<string, string>)[v] ?? v}`,
  due: (v) => ({ risk: 'Running late', open: 'Open only', closed: 'Closed only' })[v] ?? v,
  conf: (v) => (v === 'low' ? 'Below the bar' : 'High confidence'),
  pri: (v) => `Priority: ${v}`,
  bucket: (v) => `Type: ${v}`,
  board: (v) => `Board: ${v}`,
  q: (v) => `Text: “${v}”`,
};

/** Natural-language → filters. The model maps language to the filter schema; the parser is the fallback. */
export async function nlFilter(tx: Tx, ctx: Ctx, query: string): Promise<NlFilterDTO> {
  const departments = await tx.select({ id: s.departments.id, name: s.departments.name }).from(s.departments).where(eq(s.departments.orgId, ctx.orgId));
  const p = provider();
  let filters: TicketFilters | null = null;
  let used = 'heuristic';
  if (p.name === 'claude') {
    const r = await withFallback((prov) => prov.nlFilter(query, departments));
    if (r.value && !r.degraded) {
      filters = r.value.filters;
      used = 'claude';
    }
  }
  if (!filters) filters = parseNaturalFilters(query, departments).filters;
  const chips: Chip[] = (Object.entries(filters) as [keyof TicketFilters, string | undefined][])
    .filter(([, v]) => v)
    .map(([key, value]) => ({ key, value: String(value), text: CHIP_TEXT[key]!(String(value), departments) }));
  return { filters, chips, understood: chips.length > 0, provider: used };
}

/**
 * The ⌘K copilot. Facts always come from the live queue; the model (when configured) only phrases them.
 * Suggested actions (open a filtered list, go to a screen) are decided here, not by the model.
 */
export async function ask(tx: Tx, ctx: Ctx, question: string): Promise<CopilotAnswerDTO> {
  const q = question.toLowerCase();
  const has = (...w: string[]) => w.some((x) => q.includes(x));
  const list = await listTickets(tx, ctx, {});
  const all = list.items;
  const open = all.filter((t) => isOpen(t.status));
  const late = open.filter((t) => atRisk(t.sla.tone));
  const approvals = open.filter((t) => t.status === 'awaiting_approval');
  const dual = approvals.filter((t) => t.pendingGate === 'maker' || t.pendingGate === 'checker');
  const bar = 0.78;
  const low = open.filter((t) => t.confidence < bar);
  const byTeam = (xs: TicketSummaryDTO[]) => {
    const m = new Map<string, number>();
    for (const t of xs) m.set(t.department, (m.get(t.department) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1])[0];
  };

  let base: Omit<CopilotAnswerDTO, 'provider'>;
  if (has('deadline', 'late', 'overdue', 'breach', 'at risk', 'miss')) {
    const worst = late.slice().sort((a, b) => (a.sla.minutesLeft ?? 0) - (b.sla.minutesLeft ?? 0))[0];
    const team = byTeam(late);
    base = {
      headline: `${late.length} open ticket${late.length === 1 ? ' is' : 's are'} inside the warning band.`,
      lines: worst
        ? [
            `The tightest is ${worst.number} — ${worst.subject}`,
            `It sits with ${worst.assignee?.name ?? (worst.ownerKind === 'ai' ? 'the AI' : 'nobody')} and has ${worst.sla.minutesLeft ?? 0} minutes left.`,
            team ? `${team[0]} accounts for most of the risk (${team[1]} ticket${team[1] === 1 ? '' : 's'}).` : '',
          ].filter(Boolean)
        : ['Nothing is close to its deadline right now.'],
      actions: [{ label: `Show the ${late.length} at-risk tickets`, filters: { due: 'risk' } }],
    };
  } else if (has('approval', 'approve', 'sign off', 'waiting on me')) {
    base = {
      headline: `${approvals.length} ticket${approvals.length === 1 ? ' is' : 's are'} waiting for a human decision.`,
      lines: [
        approvals.slice(0, 3).map((t) => t.number).join(', ') + (approvals.length ? ' are the oldest.' : ''),
        `${dual.length} carry a filled action; the rest are drafts waiting to be sent.`,
      ].filter((l) => l.trim() && l !== ' are the oldest.'),
      actions: [{ label: 'Show what needs approving', filters: { status: 'approval' } }],
    };
  } else if (has('confidence', 'unsure', 'below the bar', 'trade finance')) {
    const team = byTeam(low);
    base = {
      headline: `${low.length} open ticket${low.length === 1 ? ' falls' : 's fall'} below the ${bar.toFixed(2)} bar.`,
      lines: [
        team ? `${team[0]} is the weak spot (${team[1]} below the bar).` : 'No team stands out.',
        'Low confidence tracks stale or missing approved content — see What it knows for the open gap tickets.',
      ],
      actions: [{ label: 'Show the low-confidence tickets', filters: { conf: 'low' } }],
    };
  } else if (has('how is the ai', 'this week', 'results', 'performance', 'saving', 'doing')) {
    if (ctx.capabilities.has('insights.view')) {
      const r = await results(tx, ctx);
      const auto = r.coverage.find((c) => c.lane === 'auto')?.pct ?? 0;
      const draft = r.coverage.find((c) => c.lane === 'draft')?.pct ?? 0;
      base = {
        headline: `Time to resolve is down ${Math.abs(r.deltaPct)}%, from ${r.baselineHours}h to ${r.nowHours}h.`,
        lines: [`The AI carries ${auto}% of volume end to end and drafts another ${draft}%.`, `That is ${r.capacityMultiple}× the queries per person against the baseline.`],
        actions: [{ label: 'Open Results', to: '/results' }],
      };
    } else {
      base = { headline: 'Results are visible to team leads and admins.', lines: ['Ask your team lead for this week’s numbers.'], actions: [] };
    }
  } else if (has('unowned', 'unassigned', 'no owner')) {
    const un = open.filter((t) => t.ownerKind === 'unassigned');
    base = {
      headline: `${un.length} open ticket${un.length === 1 ? ' has' : 's have'} no owner.`,
      lines: ['Nothing unowned can be automated — those queries route to a person every time.', ...un.slice(0, 2).map((t) => `${t.number} · ${t.bucket}`)],
      actions: [{ label: 'Show unowned tickets', filters: { owner: 'unassigned' } }, ...(ctx.capabilities.has('setup.view') ? [{ label: 'Open Who owns what', to: '/setup/ownership' }] : [])],
    };
  } else {
    base = {
      headline: `${open.length} ticket${open.length === 1 ? ' is' : 's are'} open right now.`,
      lines: [
        `${approvals.length} wait on a human decision, ${late.length} are close to a deadline.`,
        `The AI is handling ${open.filter((t) => t.ownerKind === 'ai').length} on its own.`,
        'Ask about deadlines, approvals, confidence or results for a sharper answer.',
      ],
      actions: [{ label: 'Show all open tickets', filters: { due: 'open' } }],
    };
  }

  if (provider().name === 'claude') {
    const facts = [base.headline, ...base.lines].join('\n');
    const r = await withFallback((p) => p.answer(question, facts));
    if (r.value && !r.degraded) return { ...base, headline: r.value.headline, lines: r.value.lines, provider: 'claude' };
  }
  return { ...base, provider: 'heuristic' };
}
