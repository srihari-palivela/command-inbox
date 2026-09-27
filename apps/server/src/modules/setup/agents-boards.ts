import type {
  AgentBody,
  AgentDTO,
  AgentsOverviewDTO,
  AgentTemplate,
  BoardBody,
  BoardDTO,
  CalibrationBandDTO,
  FeedbackDTO,
} from '@ci/contracts';
import { initialsOf } from '@ci/contracts';
import { and, asc, desc, eq, gte, inArray, sql } from 'drizzle-orm';
import type { Tx } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { audit } from '../../platform/audit.js';
import { clock } from '../../platform/clock.js';
import { actorOf, type Ctx } from '../../platform/context.js';
import { conflict, notFound } from '../../platform/errors.js';
import { publish } from '../../platform/outbox.js';
import { requireCap } from '../../platform/rbac.js';

// ── Boards ────────────────────────────────────────────────────────────────────
export async function listBoards(tx: Tx, ctx: Ctx): Promise<BoardDTO[]> {
  const boards = await tx.select().from(s.boards).where(eq(s.boards.orgId, ctx.orgId)).orderBy(asc(s.boards.sort), asc(s.boards.createdAt));
  const mbs = await tx.select().from(s.mailboxes).where(eq(s.mailboxes.orgId, ctx.orgId));
  const links = await tx
    .select({ boardId: s.agentBoards.boardId, id: s.agents.id, name: s.agents.name, sort: s.agents.sort })
    .from(s.agentBoards)
    .innerJoin(s.agents, eq(s.agents.id, s.agentBoards.agentId))
    .where(eq(s.agentBoards.orgId, ctx.orgId))
    .orderBy(asc(s.agents.sort));
  const open = await tx.execute<{ board_id: string; n: number }>(sql`
    select board_id, count(*)::int n from tickets where org_id = ${ctx.orgId} and status not in ('resolved','closed') group by board_id`);
  const openBy = new Map(open.rows.map((r) => [r.board_id, r.n]));
  return boards.map((b) => {
    const mb = mbs.find((m) => m.id === b.mailboxId);
    return {
      id: b.id,
      key: b.key,
      name: b.name,
      source: mb?.address ?? '',
      provider: (mb?.provider ?? 'dev') as BoardDTO['provider'],
      volume24h: mb?.volume24h ?? 0,
      open: openBy.get(b.id) ?? 0,
      autoRatePct: b.autoRatePct,
      state: b.state as BoardDTO['state'],
      team: b.team,
      agents: links.filter((l) => l.boardId === b.id).map((l) => ({ id: l.id, name: l.name })),
    };
  });
}

/**
 * Create a board from a newly connected mailbox. The mailbox starts read-only in observe mode: the AI
 * shadows the queue and scores nothing until an admin promotes the board.
 */
export async function createBoard(tx: Tx, ctx: Ctx, body: BoardBody): Promise<{ board: BoardDTO; mailboxId: string }> {
  requireCap(ctx, 'setup.edit', 'create a board');
  const [dup] = await tx.select().from(s.mailboxes).where(and(eq(s.mailboxes.orgId, ctx.orgId), eq(s.mailboxes.address, body.mailbox)));
  if (dup) throw conflict('mailbox_exists', `${body.mailbox} is already connected.`);
  const [mb] = await tx
    .insert(s.mailboxes)
    .values({
      orgId: ctx.orgId,
      address: body.mailbox,
      provider: body.provider,
      departmentId: body.departmentId ?? null,
      teamLabel: '',
      permissions: ['read'],
      state: 'observe',
      volume24h: 0,
      sort: 100,
    })
    .returning();
  const key = body.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 24) || 'board';
  const [b] = await tx
    .insert(s.boards)
    .values({ orgId: ctx.orgId, key: `${key}-${Date.now().toString(36).slice(-4)}`, name: body.name, mailboxId: mb!.id, team: 'Not staffed yet', state: 'observe', sort: 100 })
    .returning();
  const guard = await tx.select().from(s.agents).where(and(eq(s.agents.orgId, ctx.orgId), eq(s.agents.role, 'guard')));
  if (guard[0]) await tx.insert(s.agentBoards).values({ orgId: ctx.orgId, agentId: guard[0].id, boardId: b!.id });
  await audit(tx, ctx.orgId, {
    actor: actorOf(ctx),
    action: 'board.created',
    entity: 'board',
    entityId: b!.id,
    summary: `${body.name} created · reading ${body.mailbox} over ${body.provider}`,
    data: { provider: body.provider },
  });
  await publish(tx, ctx.orgId, 'setup.updated', { area: 'boards' });
  return { board: (await listBoards(tx, ctx)).find((x) => x.id === b!.id)!, mailboxId: mb!.id };
}

// ── Agents ────────────────────────────────────────────────────────────────────
export const AGENT_TEMPLATES: AgentsOverviewDTO['templates'] = [
  { key: 'bucketing', label: 'Bucketing agent', abbr: 'BK', what: 'Sorts each mail into one query type from your taxonomy', recommendedModel: 'claude-sonnet-5',
    prompt: 'You sort incoming email into exactly one query type from the approved taxonomy. Return the type, a confidence between 0 and 1, and the phrases that drove the choice. If two types fit equally, return "ambiguous" and let a person decide.' },
  { key: 'extraction', label: 'Extraction agent', abbr: 'EX', what: 'Fills action templates with values quoted from the thread', recommendedModel: 'claude-sonnet-5',
    prompt: 'You pull the fields an action template needs out of an email thread. Every value must be quoted verbatim from the thread or a system record — mark anything you inferred, and never guess an amount, an account number or a date.' },
  { key: 'drafting', label: 'Drafting agent', abbr: 'DR', what: 'Writes cited replies from approved knowledge only', recommendedModel: 'claude-sonnet-5',
    prompt: 'You draft a reply using only knowledge-manager-approved sources, citing each one. Where the approved material does not cover part of the question, say so plainly and raise a gap ticket. Never fill a gap from memory.' },
  { key: 'summarisation', label: 'Summarisation agent', abbr: 'SM', what: 'Briefs a human with context before they open the ticket', recommendedModel: 'claude-sonnet-5',
    prompt: 'You brief a human before they open a ticket. Assemble the history, the records and the deadlines that matter. You never write to the customer.' },
  { key: 'policy_guard', label: 'Policy guard', abbr: 'PG', what: 'Stops the pipeline on hard rules before anything runs', recommendedModel: 'claude-haiku-4-5',
    prompt: 'You read every thread before anything else runs and stop the pipeline on a hard rule: a named regulator, a legal notice, suspected fraud, a vulnerable-customer signal, or a repeat contact past the limit. You never generate customer-facing text.' },
];

export const AGENT_MODELS = [
  { id: 'claude-opus-5', note: 'Deepest judgement · highest cost' },
  { id: 'claude-sonnet-5', note: 'Best balance · ₹0.19–0.42 per 1k mails' },
  { id: 'claude-haiku-4-5', note: 'Fast and cheap · ₹0.02–0.06 per 1k mails' },
];

const ROLE_OF: Record<AgentTemplate, string> = {
  bucketing: 'bucketer',
  extraction: 'extractor',
  drafting: 'drafter',
  summarisation: 'summariser',
  policy_guard: 'guard',
};

async function calibration(tx: Tx, orgId: string): Promise<Map<string, CalibrationBandDTO[]>> {
  const rows = await tx.execute<{ agent_name: string; band: number; n: number; observed: number | null }>(sql`
    select agent_name, least(floor(confidence * 10), 9)::int as band, count(*)::int n,
           avg(case when correct then 1.0 when correct = false then 0.0 end)::float as observed
      from prediction_outcomes
     where org_id = ${orgId} and correct is not null
     group by agent_name, band
     order by agent_name, band`);
  const out = new Map<string, CalibrationBandDTO[]>();
  for (const r of rows.rows) {
    if (r.band < 4) continue;
    const list = out.get(r.agent_name) ?? [];
    list.push({ band: `${(r.band / 10).toFixed(1)}–${((r.band + 1) / 10).toFixed(1)}`, predicted: (r.band + 0.5) / 10, observed: r.observed, n: r.n });
    out.set(r.agent_name, list);
  }
  return out;
}

export async function agentsOverview(tx: Tx, ctx: Ctx): Promise<AgentsOverviewDTO> {
  requireCap(ctx, 'setup.view', 'view AI agents');
  const rows = await tx.select().from(s.agents).where(eq(s.agents.orgId, ctx.orgId)).orderBy(asc(s.agents.sort), asc(s.agents.createdAt));
  const ids = rows.map((r) => r.id);
  const boards = ids.length
    ? await tx
        .select({ agentId: s.agentBoards.agentId, id: s.boards.id, name: s.boards.name })
        .from(s.agentBoards)
        .innerJoin(s.boards, eq(s.boards.id, s.agentBoards.boardId))
        .where(and(eq(s.agentBoards.orgId, ctx.orgId), inArray(s.agentBoards.agentId, ids)))
        .orderBy(asc(s.boards.sort))
    : [];
  const evals = ids.length ? await tx.select().from(s.agentEvals).where(and(eq(s.agentEvals.orgId, ctx.orgId), inArray(s.agentEvals.agentId, ids))).orderBy(asc(s.agentEvals.sort)) : [];
  const versions = ids.length ? await tx.select().from(s.agentVersions).where(and(eq(s.agentVersions.orgId, ctx.orgId), inArray(s.agentVersions.agentId, ids))).orderBy(desc(s.agentVersions.version)) : [];
  const calib = await calibration(tx, ctx.orgId);
  const monthStart = new Date(clock.now().getFullYear(), clock.now().getMonth(), 1);
  const [spend] = await tx.execute<{ minor: number }>(sql`
    select coalesce(sum(cost_minor), 0)::int as minor from triage_runs where org_id = ${ctx.orgId} and created_at >= ${monthStart}`).then((r) => r.rows);
  const fb = await tx.select().from(s.feedback).where(eq(s.feedback.orgId, ctx.orgId)).orderBy(desc(s.feedback.createdAt)).limit(50);

  const agents: AgentDTO[] = rows.map((a) => ({
    id: a.id,
    name: a.name,
    abbr: a.abbr,
    template: a.template as AgentTemplate,
    model: a.model,
    state: a.state as AgentDTO['state'],
    version: a.version,
    prompt: a.prompt,
    evalScore: a.evalScore,
    costPer1kMinor: a.costPer1kMinor,
    boards: boards.filter((b) => b.agentId === a.id).map((b) => ({ id: b.id, name: b.name })),
    evals: evals.filter((e) => e.agentId === a.id).map((e) => ({ label: e.label, value: e.value, tone: e.tone as 'ok' | 'warn' | 'neutral' })),
    calibration: calib.get(a.name) ?? [],
    versions: versions
      .filter((v) => v.agentId === a.id)
      .map((v) => ({ version: v.version, model: v.model, createdAt: v.createdAt.toISOString(), createdBy: v.createdBy, evalStatus: v.evalStatus })),
  }));
  // Spend recorded by billing before this deployment's own runs (imported monthly), plus live model spend.
  const [imported] = await tx
    .select({ value: s.dailyMetrics.value })
    .from(s.dailyMetrics)
    .where(and(eq(s.dailyMetrics.orgId, ctx.orgId), eq(s.dailyMetrics.metric, 'spend.imported_minor')))
    .orderBy(desc(s.dailyMetrics.day))
    .limit(1);
  const spendMonthMinor = Math.round(imported?.value ?? 0) + (spend?.minor ?? 0);
  return {
    agents,
    spendMonthMinor,
    feedback: fb.map(
      (f): FeedbackDTO => ({
        id: f.id,
        agentName: f.agentName,
        ticketNumber: f.ticketNumber,
        kind: f.kind as FeedbackDTO['kind'],
        text: f.text,
        fix: f.fix as FeedbackDTO['fix'],
        status: f.status as FeedbackDTO['status'],
        at: f.createdAt.toISOString(),
      }),
    ),
    templates: AGENT_TEMPLATES,
    models: AGENT_MODELS,
  };
}

export async function createAgent(tx: Tx, ctx: Ctx, body: AgentBody): Promise<void> {
  requireCap(ctx, 'setup.edit', 'add an agent');
  const [dup] = await tx.select().from(s.agents).where(and(eq(s.agents.orgId, ctx.orgId), eq(s.agents.name, body.name)));
  if (dup) throw conflict('agent_exists', `An agent called ${body.name} already exists.`);
  const boards = await tx.select().from(s.boards).where(and(eq(s.boards.orgId, ctx.orgId), inArray(s.boards.id, body.boardIds)));
  if (boards.length !== body.boardIds.length) throw notFound('Board');
  const [a] = await tx
    .insert(s.agents)
    .values({
      orgId: ctx.orgId,
      name: body.name,
      abbr: initialsOf(body.name) || 'NA',
      template: body.template,
      model: body.model,
      state: 'observe',
      version: 1,
      prompt: body.prompt,
      evalScore: null,
      costPer1kMinor: body.model.includes('haiku') ? 5 : body.model.includes('opus') ? 60 : 24,
      role: ROLE_OF[body.template],
      sort: 100,
    })
    .returning();
  await tx.insert(s.agentVersions).values({ orgId: ctx.orgId, agentId: a!.id, version: 1, prompt: body.prompt, model: body.model, evalStatus: 'queued', createdBy: ctx.user.name });
  await tx.insert(s.agentBoards).values(boards.map((b) => ({ orgId: ctx.orgId, agentId: a!.id, boardId: b.id })));
  await tx.insert(s.agentEvals).values([
    { orgId: ctx.orgId, agentId: a!.id, label: 'Golden set run', value: 'queued', tone: 'neutral', sort: 0 },
    { orgId: ctx.orgId, agentId: a!.id, label: 'Live traffic', value: 'observe only', tone: 'neutral', sort: 1 },
  ]);
  await audit(tx, ctx.orgId, {
    actor: actorOf(ctx),
    action: 'agent.created',
    entity: 'agent',
    entityId: a!.id,
    summary: `${body.name} created in observe mode (${body.model}) on ${boards.map((b) => b.name).join(', ')}`,
  });
  await publish(tx, ctx.orgId, 'setup.updated', { area: 'agents' });
}

/** A prompt or model change ships as a new version pending evals; the live version keeps running. */
export async function newAgentVersion(tx: Tx, ctx: Ctx, agentId: string, prompt: string, model?: string): Promise<{ version: number }> {
  requireCap(ctx, 'setup.edit', 'edit an agent prompt');
  const [a] = await tx.select().from(s.agents).where(and(eq(s.agents.orgId, ctx.orgId), eq(s.agents.id, agentId)));
  if (!a) throw notFound('Agent');
  const [latest] = await tx
    .select({ v: sql<number>`max(${s.agentVersions.version})::int` })
    .from(s.agentVersions)
    .where(and(eq(s.agentVersions.orgId, ctx.orgId), eq(s.agentVersions.agentId, agentId)));
  const version = Math.max(latest?.v ?? a.version, a.version) + 1;
  await tx.insert(s.agentVersions).values({ orgId: ctx.orgId, agentId, version, prompt, model: model ?? a.model, evalStatus: 'pending evals', createdBy: ctx.user.name });
  await audit(tx, ctx.orgId, { actor: actorOf(ctx), action: 'agent.version_created', entity: 'agent', entityId: agentId, summary: `${a.name} v${version} created — pending golden-set evals` });
  await publish(tx, ctx.orgId, 'setup.updated', { area: 'agents' });
  return { version };
}

export async function setAgentBoards(tx: Tx, ctx: Ctx, agentId: string, boardIds: string[]): Promise<void> {
  requireCap(ctx, 'setup.edit', 'attach an agent to a board');
  const [a] = await tx.select().from(s.agents).where(and(eq(s.agents.orgId, ctx.orgId), eq(s.agents.id, agentId)));
  if (!a) throw notFound('Agent');
  await tx.delete(s.agentBoards).where(and(eq(s.agentBoards.orgId, ctx.orgId), eq(s.agentBoards.agentId, agentId)));
  if (boardIds.length) await tx.insert(s.agentBoards).values(boardIds.map((boardId) => ({ orgId: ctx.orgId, agentId, boardId })));
  await audit(tx, ctx.orgId, { actor: actorOf(ctx), action: 'agent.boards_changed', entity: 'agent', entityId: agentId, summary: `${a.name} now on ${boardIds.length} board${boardIds.length === 1 ? '' : 's'}` });
  await publish(tx, ctx.orgId, 'setup.updated', { area: 'agents' });
}

export async function queueTuning(tx: Tx, ctx: Ctx, feedbackId: string): Promise<void> {
  requireCap(ctx, 'insights.view', 'queue agent tuning');
  const [f] = await tx
    .update(s.feedback)
    .set({ status: 'queued' })
    .where(and(eq(s.feedback.orgId, ctx.orgId), eq(s.feedback.id, feedbackId)))
    .returning();
  if (!f) throw notFound('Feedback');
  await audit(tx, ctx.orgId, {
    actor: actorOf(ctx),
    action: 'feedback.queued',
    entity: 'feedback',
    entityId: f.id,
    summary: `${f.fix === 'prompt' ? 'Prompt' : 'Context'} tuning queued for ${f.agentName}; case added to the golden set`,
  });
}

/** Recently-edited drafts, for the agents screen: the cheapest training signal there is. */
export async function draftEdits(tx: Tx, ctx: Ctx) {
  return tx
    .select()
    .from(s.feedback)
    .where(and(eq(s.feedback.orgId, ctx.orgId), eq(s.feedback.kind, 'edit'), gte(s.feedback.createdAt, new Date(clock.now().getTime() - 30 * 86400_000))))
    .orderBy(desc(s.feedback.createdAt));
}
