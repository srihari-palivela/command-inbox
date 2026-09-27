/**
 * Triage pipeline. Model calls run outside any database transaction; every stage emits a trace span;
 * the outcome (lane, gate artefacts, routing, audit) commits in a single tenant transaction.
 *
 *   intake → guard → bucket rules/bucketer → extract | draft | brief → policy engine → priority → router
 */
import { LANE_NAME, type Lane } from '@ci/contracts';
import { and, asc, eq, inArray, ne, sql } from 'drizzle-orm';
import { withTenant, type Tx } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { decideLane } from '../../domain/lane.js';
import { maskPii } from '../../domain/pii.js';
import { rankPriority } from '../../domain/priority.js';
import { cellOf, chainFor } from '../../domain/risk.js';
import { slaBudget } from '../../domain/sla.js';
import { AI_ACTOR } from '../../platform/context.js';
import { canonicalJson, sha256 } from '../../platform/crypto.js';
import { clock } from '../../platform/clock.js';
import { enqueue, type JobRow } from '../../platform/jobs.js';
import { logger } from '../../platform/logger.js';
import { publish } from '../../platform/outbox.js';
import { pickAssignee } from '../people/routing.js';
import { lockTicket, nextNumber, recordTicketEvent, systemNote, updateTicket } from '../tickets/ops.js';
import { withFallback } from './providers/index.js';
import {
  TEMPLATE_FIELDS,
  type AgentConfig,
  type BriefResult,
  type ClassifyResult,
  type DraftResult,
  type ExtractResult,
  type GuardResult,
  type Staged,
  type TaxonomyEntry,
  type ThreadInput,
} from './providers/types.js';

interface SpanRec {
  seq: number;
  offsetMs: number;
  agent: string;
  model: string;
  action: string;
  output: string;
  latencyMs: number;
  tokens: number | null;
  costMinor: number | null;
  status: 'ok' | 'flag' | 'stop';
}

/** Which action template (if any) a query type maps to, refined by the thread's wording. */
export function templateFor(queryType: string | null, text: string): string | null {
  const t = text.toLowerCase();
  switch (queryType) {
    case 'Stop payment instruction':
      return 'ACT-STP-014';
    case 'Statement re-issue':
      return /cheque book/.test(t) ? 'ACT-CHQ-001' : 'ACT-STM-002';
    case 'Certificate requests':
    case 'Interest certificate':
      return /balance confirmation/.test(t) ? 'ACT-LTR-011' : 'ACT-CRT-004';
    case 'Foreclosure quotes':
      return 'ACT-LON-007';
    case 'Account maintenance':
      if (/registered mobile|mobile number/.test(t) && /otp/.test(t)) return 'ACT-KYC-021';
      if (/cheque book/.test(t)) return 'ACT-CHQ-001';
      if (/standing instruction/.test(t)) return 'ACT-SI-009';
      return null;
    case 'Disputed transactions':
      if (/sent twice|duplicate neft/.test(t)) return 'ACT-PAY-025';
      if (/temporary hold|blocked while travelling/.test(t)) return 'ACT-CRD-008';
      return null;
    case 'Balance & charge queries':
      return /late payment fee/.test(t) && /waive/.test(t) ? 'ACT-FEE-017' : null;
    default:
      return null;
  }
}

async function loadContext(orgId: string, ticketId: string) {
  return withTenant(orgId, async (tx) => {
    const [t] = await tx.select().from(s.tickets).where(and(eq(s.tickets.orgId, orgId), eq(s.tickets.id, ticketId)));
    if (!t) return null;
    const msgs = await tx
      .select()
      .from(s.messages)
      .where(and(eq(s.messages.orgId, orgId), eq(s.messages.ticketId, ticketId), eq(s.messages.direction, 'inbound')))
      .orderBy(asc(s.messages.sentAt));
    const [org] = await tx.select().from(s.orgs).where(eq(s.orgs.id, orgId));
    const depts = await tx.select().from(s.departments).where(eq(s.departments.orgId, orgId));
    const qts = await tx.select().from(s.queryTypes).where(eq(s.queryTypes.orgId, orgId)).orderBy(asc(s.queryTypes.sort));
    const agentRows = await tx.select().from(s.agents).where(eq(s.agents.orgId, orgId));
    const boardAgents = t.boardId
      ? await tx.select({ agentId: s.agentBoards.agentId }).from(s.agentBoards).where(and(eq(s.agentBoards.orgId, orgId), eq(s.agentBoards.boardId, t.boardId)))
      : [];
    const bucketRules = await tx.select().from(s.bucketRules).where(eq(s.bucketRules.orgId, orgId)).orderBy(asc(s.bucketRules.sort));
    const priorityRules = await tx.select().from(s.priorityRules).where(eq(s.priorityRules.orgId, orgId));
    const templates = await tx.select().from(s.actionTemplates).where(eq(s.actionTemplates.orgId, orgId));
    const dial = await tx.select().from(s.autonomyDial).where(eq(s.autonomyDial.orgId, orgId));
    const prior = t.customerId
      ? await tx
          .select({ queryTypeId: s.tickets.queryTypeId, subject: s.tickets.subject })
          .from(s.tickets)
          .where(and(eq(s.tickets.orgId, orgId), eq(s.tickets.customerId, t.customerId), ne(s.tickets.id, t.id)))
      : [];
    const customer = t.customerId ? (await tx.select().from(s.customers).where(eq(s.customers.id, t.customerId)))[0] : undefined;
    const docs = await tx
      .select()
      .from(s.knowledgeDocs)
      .where(and(eq(s.knowledgeDocs.orgId, orgId), inArray(s.knowledgeDocs.status, ['approved', 'stale'])));
    return { t, msgs, org: org!, depts, qts, agentRows, boardAgents, bucketRules, priorityRules, templates, dial, prior, customer, docs };
  });
}

function agentFor(ctx: NonNullable<Awaited<ReturnType<typeof loadContext>>>, role: string, preferName?: string): AgentConfig {
  const onBoard = new Set(ctx.boardAgents.map((b) => b.agentId));
  const candidates = ctx.agentRows.filter((a) => a.role === role && a.state !== 'paused');
  const pick =
    (preferName && candidates.find((a) => a.name === preferName)) ?? candidates.find((a) => onBoard.has(a.id)) ?? candidates[0];
  return pick
    ? { name: pick.name, model: pick.model, prompt: pick.prompt }
    : { name: role, model: 'claude-sonnet-5', prompt: 'You are a careful banking operations assistant.' };
}

export async function runTriage(job: JobRow): Promise<void> {
  const ticketId = String(job.payload.ticketId);
  const forceLane = (job.payload.forceLane as Lane | undefined) ?? undefined;
  const ctx = await loadContext(job.orgId, ticketId);
  if (!ctx) return;
  const { t } = ctx;
  if (t.status !== 'triaging') return; // idempotent: already triaged

  const started = Date.now();
  const spans: SpanRec[] = [];
  let degraded = false;
  const span = (agent: string, action: string, output: string, usage: Staged<unknown>['usage'] | null, status: SpanRec['status'], model?: string) => {
    spans.push({
      seq: spans.length + 1,
      offsetMs: Date.now() - started - (usage?.latencyMs ?? 0),
      agent,
      model: model ?? usage?.model ?? '—',
      action,
      output,
      latencyMs: usage?.latencyMs ?? 5,
      tokens: usage?.tokens ?? null,
      costMinor: usage?.costMinor ?? null,
      status,
    });
  };
  const stage = async <T>(fn: Parameters<typeof withFallback<Staged<T>>>[0]): Promise<Staged<T>> => {
    const r = await withFallback(fn);
    if (r.degraded) degraded = true;
    return r.value;
  };

  // ── 1. Intake: assemble the thread, mask PII before any model sees it ──
  const vault: Record<string, string> = {};
  const masked = ctx.msgs.map((m) => {
    const mk = maskPii(m.body);
    Object.assign(vault, mk.vault);
    return { from: m.fromName, body: mk.text };
  });
  const priorSame = ctx.prior.filter((p) => p.queryTypeId && p.queryTypeId === t.queryTypeId).length;
  const thread: ThreadInput = {
    subject: maskPii(t.subject).text,
    messages: masked,
    customer: { segment: t.segment, priorContacts: ctx.prior.length, priorSameTopic: priorSame },
  };
  span('Mail intake', 'Assembled the thread and masked PII before any model call', `${ctx.msgs.length} message${ctx.msgs.length === 1 ? '' : 's'} · ${Object.keys(vault).length} values masked · sender ${ctx.customer ? `matched to ${ctx.customer.cif}` : 'not matched to a customer'}`, null, 'ok', '—');

  // ── 2. Guardrail Sentinel ──
  const guardAgent = agentFor(ctx, 'guard');
  const guard = await stage<GuardResult>((p) => p.guard(thread, guardAgent));
  span(guardAgent.name, 'Screened for hard stop rules before anything else ran', guard.result.stop ? `STOP · ${guard.result.stop} — customer-facing generation suspended` : 'Clear · no hard stop fired', guard.usage, guard.result.stop ? 'stop' : 'ok');

  // ── 3. Bucketing: deterministic rules first, then the model ──
  const taxonomy: TaxonomyEntry[] = ctx.qts.map((q) => ({
    name: q.name,
    department: ctx.depts.find((d) => d.id === q.departmentId)?.name ?? null,
    lane: q.defaultLane,
    owned: !!q.departmentId,
    templateCode: null,
  }));
  const fullText = (t.subject + '\n' + ctx.msgs.map((m) => m.body).join('\n')).toLowerCase();
  const ruleHit = ctx.bucketRules.find((r) => {
    const p = r.pattern;
    if (!p?.queryType) return false;
    if (p.all && !p.all.every((w) => fullText.includes(w))) return false;
    if (p.any && !p.any.some((w) => fullText.includes(w))) return false;
    if (p.regex && !new RegExp(p.regex, 'i').test(fullText)) return false;
    return true;
  });
  const bucketAgent = agentFor(ctx, 'bucketer');
  const cls = await stage<ClassifyResult>((p) => p.classify(thread, taxonomy, bucketAgent));
  let classification = cls.result;
  if (ruleHit?.pattern?.queryType) {
    classification = { ...classification, queryType: ruleHit.pattern.queryType, confidence: Math.max(classification.confidence, 0.94) };
  }
  const qt = ctx.qts.find((q) => q.name === classification.queryType) ?? null;
  const bar = ctx.org.confidenceBar;
  const confidence = Math.round(classification.confidence * 100) / 100;
  span(
    bucketAgent.name,
    ruleHit ? `Bucketing rule ${ruleHit.sort + 1} fired, then classified against the taxonomy` : 'Classified the query against the taxonomy',
    `${classification.queryType ?? 'No matching query type'} · confidence ${confidence.toFixed(2)} · ${confidence >= bar ? 'above the bar' : 'below the bar, human required'}`,
    cls.usage,
    confidence >= bar ? 'ok' : 'flag',
  );

  const department = qt?.departmentId ? ctx.depts.find((d) => d.id === qt.departmentId) ?? null : null;
  const owned = !!qt?.departmentId;
  const templateCode = templateFor(classification.queryType, fullText);
  const template = templateCode ? ctx.templates.find((x) => x.code === templateCode) ?? null : null;

  // ── 4. Path-specific work ──
  let extraction: ExtractResult | null = null;
  let draft: DraftResult | null = null;
  let brief: BriefResult | null = null;
  const docsForDept = ctx.docs.filter((d) => !department || d.departmentId === department.id);
  const grounding = docsForDept.slice(0, 6).map((d, i) => ({ n: i + 1, title: d.title, section: d.section, body: d.body, id: d.id, owner: d.owner, verifiedAt: d.verifiedAt }));

  const wantAction = !!template && !classification.informational && !guard.result.stop && !classification.multiIntent;
  if ((wantAction && forceLane !== 'draft' && forceLane !== 'manual') || forceLane === 'auto') {
    if (template) {
      const extractor = agentFor(ctx, 'extractor');
      const ex = await stage<ExtractResult>((p) => p.extract(thread, { code: template.code, name: template.name, fields: TEMPLATE_FIELDS[template.code] ?? ['Account number'] }, extractor));
      // Re-bind masked values inside the bank boundary, now that no model will see them.
      extraction = { ...ex.result, fields: ex.result.fields.map((f) => ({ ...f, value: f.value.replace(/\[[A-Z]+_\d+\]/g, (tok) => vault[tok] ?? tok) })) };
      const inferred = extraction.fields.filter((f) => f.inferred).length;
      span(extractor.name, `Filled ${template.code} from the thread and system records`, `${extraction.fields.length} of ${(TEMPLATE_FIELDS[template.code] ?? []).length} fields · ${inferred} inferred${extraction.complete ? '' : ' · missing fields, a person must complete them'}`, ex.usage, extraction.complete ? 'ok' : 'flag');
    }
  }

  const decision = (() => {
    const base = decideLane({
      hardStop: guard.result.stop,
      confidence,
      bar,
      queryTypeOwned: owned,
      multiIntent: classification.multiIntent,
      hasTemplate: !!template && !!extraction,
      fieldsComplete: extraction?.complete ?? false,
      coverage: 'none',
      informational: classification.informational || !template,
    });
    return base;
  })();

  let lane: Lane = decision.lane;
  let laneNote = decision.note;
  if (forceLane && !guard.result.stop) {
    if (forceLane === 'auto' && extraction?.complete) lane = 'auto';
    else if (forceLane === 'draft') lane = 'draft';
    else if (forceLane === 'manual') lane = 'manual';
    laneNote = lane === forceLane ? `Re-run as ${LANE_NAME[lane]} after an override` : `Override to ${forceLane} not possible — ${decision.note}`;
  }

  // Informational queries that passed the gates try the grounded drafting path.
  if ((lane === 'manual' && !guard.result.stop && owned && !classification.multiIntent && confidence >= 0.6 && !template) || lane === 'draft') {
    const drafter = agentFor(ctx, 'drafter');
    const dr = await stage<DraftResult>((p) => p.draft(thread, grounding, drafter, t.fromName));
    draft = dr.result;
    span(drafter.name, 'Drafted the reply from approved sources only', `${draft.citations.length} citation${draft.citations.length === 1 ? '' : 's'} · ${draft.coverage === 'full' ? 'full coverage' : draft.coverage === 'partial' ? '1 gap flagged' : 'no approved source — nothing quotable'}`, dr.usage, draft.coverage === 'full' ? 'ok' : 'flag');
    const d2 = decideLane({
      hardStop: null,
      confidence,
      bar,
      queryTypeOwned: owned,
      multiIntent: false,
      hasTemplate: false,
      fieldsComplete: false,
      coverage: draft.coverage,
      informational: true,
    });
    if (forceLane === 'draft' || d2.lane === 'draft') {
      lane = 'draft';
      laneNote = forceLane === 'draft' ? laneNote : d2.note;
    }
    if (lane !== 'draft') draft = null;
  }

  if (lane === 'manual') {
    const summariser = agentFor(ctx, 'summariser');
    const facts = [
      ctx.customer ? `Customer: ${ctx.customer.name} · ${ctx.customer.cif}` : null,
      `Prior contacts: ${ctx.prior.length} (${priorSame} on this topic)`,
      department ? `Owning team: ${department.name}` : 'Owning team: none — query type unowned',
      classification.multiIntent ? `Intents: ${classification.intents.join(' + ')}` : null,
    ]
      .filter(Boolean)
      .join('\n');
    const br = await stage<BriefResult>((p) => p.brief(thread, facts, summariser));
    brief = br.result;
    if (classification.multiIntent) brief.suggestions.unshift({ label: 'Split into two child tickets', meta: 'recommended' });
    span(
      classification.multiIntent ? 'Split proposer' : summariser.name,
      classification.multiIntent ? 'Detected two intents owned by different teams' : 'Assembled context for the person taking over',
      classification.multiIntent ? 'Proposed split into two child tickets · held for your decision' : `Brief attached · ${brief.context.length} context items · no customer-facing text generated`,
      br.usage,
      classification.multiIntent ? 'flag' : 'ok',
    );
  }

  // ── 5. Policy engine ──
  let chain: ReturnType<typeof chainFor> | null = null;
  if (lane === 'auto' && template) {
    const cell = cellOf(template.reversible, template.moneyMoves);
    const dial = ctx.dial.find((d) => d.cell === cell)?.level ?? 1;
    chain = chainFor(cell, template.approval as 'auto' | 'single' | 'dual', dial);
    span('Policy engine', 'Looked up the action’s risk cell and approval route', `${template.reversible ? 'Can be undone' : 'Cannot be undone'} · ${template.moneyMoves ? 'Money moves' : 'No money moves'} → ${chain === 'auto' ? 'the AI may act alone' : chain === 'dual' ? 'maker + checker' : 'one approver'}`, null, chain === 'dual' ? 'flag' : 'ok', 'rules');
    if (chain === 'auto') laneNote = 'Can be undone — the AI already did it';
    else if (chain === 'dual') laneNote = 'Filled in, waiting on two approvers';
    else laneNote = 'Filled in, waiting on one approver';
  }

  // ── 6. Priority ranker ──
  const escalation = guard.result.regulatorNamed || guard.result.repeatContact;
  const provisional = slaBudget(t.priority, t.segment, escalation);
  const due = new Date(t.receivedAt.getTime() + provisional * 60_000);
  const minutesLeft = Math.round((due.getTime() - clock.now().getTime()) / 60_000);
  const ranked = rankPriority(
    {
      regulatorNamed: guard.result.regulatorNamed,
      vulnerable: guard.result.vulnerable,
      minutesLeft,
      amountInr: extraction?.amountInr ?? null,
      contactCount: guard.result.repeatContact ? Math.max(3, ctx.prior.length + 1) : ctx.prior.length + 1,
      informational: classification.informational && !template,
    },
    ctx.priorityRules.map((r) => ({ key: r.key, hard: r.hard, enabled: r.enabled })),
  );
  const slaMinutes = slaBudget(ranked.priority, t.segment, escalation);
  span('Priority Ranker', 'Scored urgency from deadline, sentiment, amount and repeat contacts', `${ranked.priority} · rules fired: ${ranked.fired.length ? ranked.fired.join(', ') : 'none (default P3)'}`, null, ranked.priority === 'P1' ? 'flag' : 'ok', 'rules + claude-haiku-4-5');

  if (degraded) {
    lane = 'manual';
    laneNote = 'AI partly unavailable — handed to a person with the raw thread';
    chain = null;
    draft = null;
  }

  // ── 7. Commit ──
  await withTenant(job.orgId, async (tx) => {
    const locked = await lockTicket(tx, job.orgId, ticketId);
    if (locked.status !== 'triaging') return;

    const assignee =
      chain === 'auto' ? null : await pickAssignee(tx, job.orgId, department?.id ?? null, classification.queryType ?? 'this query', null);
    span('Router', 'Placed the ticket with the right person at the right position', assignee ? `Assigned to ${assignee.name} · clearance-checked · position by ${ranked.priority}` : chain === 'auto' ? 'Owned by the AI · auto-execution cell' : 'No cleared person available — left unassigned for the team lead', null, assignee || chain === 'auto' ? 'ok' : 'flag', 'rules');

    const totalMs = Date.now() - started;
    const traceId = `TRC-${locked.number}-${String(await nextRunNo(tx, job.orgId, ticketId)).padStart(2, '0')}`;
    const cost = spans.reduce((a, x) => a + (x.costMinor ?? 0), 0);
    const evidence = [
      { tag: 'INTENT', quote: classification.phrases[0] ? `"${classification.phrases[0]}"` : classification.intents.join(' + ') || 'no clear intent', why: classification.multiIntent ? 'more than one intent' : 'drove the classification' },
      ...(guard.result.stop ? [{ tag: 'POLICY', quote: guard.result.stop, why: 'hard stop rule' }] : []),
      ...(extraction ? [{ tag: 'ENTITY', quote: extraction.fields.slice(0, 3).map((f) => f.value).join(' · '), why: extraction.complete ? 'all mandatory fields present' : 'some fields missing' }] : []),
      ...(draft ? [{ tag: draft.coverage === 'full' ? 'MATCH' : 'GAP', quote: draft.coverage === 'full' ? `${draft.citations.length} approved source${draft.citations.length === 1 ? '' : 's'} cover the answer` : 'part of the question has no approved source', why: draft.coverage === 'full' ? 'approved source' : 'gap ticket raised' }] : []),
    ];
    const reasoning = composeReasoning({ lane, confidence, bar, guard: guard.result, classification, template, chain, draft, degraded, owned });
    const [run] = await tx
      .insert(s.triageRuns)
      .values({ orgId: job.orgId, ticketId, traceId, reasoning, evidence, confidence, lane, latencyMs: totalMs, costMinor: cost, provider: degraded ? 'degraded' : guard.usage.model === 'rules' ? 'heuristic' : 'claude' })
      .returning();
    await tx.insert(s.traceSpans).values(spans.map((x) => ({ orgId: job.orgId, runId: run!.id, ...x })));

    const links: { kind: string; label: string; ref: string | null }[] = [];
    let status: 'awaiting_approval' | 'with_human' | 'executing' = lane === 'manual' ? 'with_human' : 'awaiting_approval';

    if (lane === 'auto' && template && extraction && chain) {
      const fields = extraction.fields;
      const idempotencyKey = sha256(canonicalJson({ orgId: job.orgId, code: template.code, fields: fields.map((f) => [f.label, f.value]) }));
      const [existing] = await tx.select({ id: s.actionInstances.id }).from(s.actionInstances).where(and(eq(s.actionInstances.orgId, job.orgId), eq(s.actionInstances.idempotencyKey, idempotencyKey)));
      if (existing) {
        // Same instruction already exists: never create a second executable copy.
        status = 'with_human';
        lane = 'manual';
        laneNote = 'Duplicate of an existing instruction — held for a person';
      } else {
        const [ai] = await tx
          .insert(s.actionInstances)
          .values({
            orgId: job.orgId,
            ticketId,
            templateId: template.id,
            accountRef: fields.find((f) => /account|card/i.test(f.label))?.value ?? '',
            fields,
            validation: extraction.complete ? 'All mandatory fields extracted. Verified against the customer record where available.' : 'Some fields are missing.',
            state: chain === 'auto' ? 'scheduled' : 'drafted',
            chain,
            idempotencyKey,
            executeAfter: chain === 'auto' ? clock.now() : null,
          })
          .returning();
        if (chain === 'auto') {
          status = 'executing';
          await enqueue(tx, { orgId: job.orgId, kind: 'execute_action', payload: { actionId: ai!.id }, dedupeKey: `exec:${ai!.id}:auto` });
        }
        links.push({ kind: 'ACTION', label: `${template.code} · ${template.name}`, ref: template.code });
      }
    }

    if (lane === 'draft' && draft) {
      const cites = draft.citations.map((n, i) => {
        const g = grounding.find((x) => x.n === n)!;
        return { n: i + 1, docId: g.id, doc: g.title, section: g.section, verifiedAt: (g.verifiedAt ?? clock.now()).toISOString(), owner: g.owner };
      });
      // Renumber [n] markers to match the citation order.
      let body = draft.body;
      draft.citations.forEach((n, i) => {
        body = body.replaceAll(`[${n}]`, `[§${i + 1}]`);
      });
      body = body.replaceAll('[§', '[');
      await tx
        .insert(s.drafts)
        .values({ orgId: job.orgId, ticketId, subject: `Re: ${t.subject}`, toAddr: t.fromEmail, originalBody: body, currentBody: body, citations: cites, flagged: draft.flagged, state: 'draft' })
        .onConflictDoUpdate({ target: s.drafts.ticketId, set: { originalBody: body, currentBody: body, citations: cites, flagged: draft.flagged, state: 'draft', updatedAt: clock.now() } });
      for (const c of cites) links.push({ kind: 'SOURCE', label: `${c.doc} ${c.section}`, ref: c.docId });
      if (draft.coverage !== 'full' && draft.gapQuestion) {
        const n = await nextNumber(tx, job.orgId, 'gap');
        await tx.insert(s.gapTickets).values({
          orgId: job.orgId,
          number: n,
          severity: 'blocking',
          question: draft.gapQuestion,
          detail: `Raised from QRY-${locked.number}: the drafter could not ground this part of the answer in approved content.`,
          hits: 1,
          owner: department?.name ?? 'Unassigned',
          state: 'Needs content',
          cta: 'Write content',
        });
        links.push({ kind: 'GAP', label: `GAP-${String(n).padStart(4, '0')} · raised from this ticket`, ref: `GAP-${String(n).padStart(4, '0')}` });
        await recordTicketEvent(tx, locked, {
          actor: AI_ACTOR,
          action: 'gap.raised',
          summary: `Raised knowledge gap GAP-${String(n).padStart(4, '0')} · ${draft.gapQuestion.slice(0, 80)}`,
          feed: { tone: 'flag', meta: `QRY-${locked.number}` },
        });
      }
    }

    if (lane === 'manual' && brief) {
      await tx
        .insert(s.briefs)
        .values({ orgId: job.orgId, ticketId, why: guard.result.stop ? `Hard stop · ${guard.result.stop}` : laneNote, summary: brief.summary, context: brief.context, suggestions: brief.suggestions })
        .onConflictDoUpdate({ target: s.briefs.ticketId, set: { summary: brief.summary, context: brief.context, suggestions: brief.suggestions } });
      links.push({ kind: 'POLICY', label: guard.result.stop ? `Hard stop · ${guard.result.stop}` : laneNote, ref: null });
    }
    links.push({ kind: 'AUDIT', label: `AUD-${locked.number}`, ref: null });
    await tx.delete(s.ticketLinks).where(and(eq(s.ticketLinks.orgId, job.orgId), eq(s.ticketLinks.ticketId, ticketId), inArray(s.ticketLinks.kind, ['ACTION', 'SOURCE', 'POLICY', 'AUDIT'])));
    await tx.insert(s.ticketLinks).values(links.map((l, i) => ({ orgId: job.orgId, ticketId, ...l, sort: i })));

    await tx.delete(s.subtasks).where(and(eq(s.subtasks.orgId, job.orgId), eq(s.subtasks.ticketId, ticketId)));
    const subs =
      lane === 'auto'
        ? chain === 'dual'
          ? [['a1', 'Verify the request against the customer record', 'AI', true], ['a2', 'Extract and validate the action fields', 'AI', true], ['a3', 'Approve as maker', 'You', false], ['a4', 'Counter-approve as checker', 'Team lead', false]]
          : [['a1', 'Verify the request against the customer record', 'AI', true], ['a2', 'Extract and validate the action fields', 'AI', true], ['a3', chain === 'auto' ? 'Sampled post-hoc review' : 'Approve', chain === 'auto' ? 'Team lead' : 'You', false]]
        : lane === 'draft'
          ? [['b1', 'Find approved sources for the answer', 'AI', true], ['b2', 'Draft the reply with citations', 'AI', true], ['b3', draft?.flagged.length ? 'Read the flagged paragraph' : 'Read the draft', 'You', false], ['b4', 'Send and close', 'You', false]]
          : [['c1', 'Pull the history and records for the brief', 'AI', true], ['c2', classification.multiIntent ? 'Decide whether to split' : 'Decide the next step', 'You', false], ['c3', 'Give the customer a dated commitment', 'You', false], ['c4', guard.result.regulatorNamed ? 'Notify Compliance' : 'Close the loop with the customer', 'You', false]];
    await tx.insert(s.subtasks).values(subs.map(([key, label, owner, done], i) => ({ orgId: job.orgId, ticketId, key: key as string, label: label as string, owner: owner as string, done: done as boolean, sort: i })));

    const bucket = classification.queryType ?? 'Unclassified';
    await systemNote(tx, job.orgId, ticketId, `Read the email and classified it as ${bucket.toLowerCase()} with ${confidence >= 0.9 ? 'high' : confidence >= bar ? 'moderate' : 'low'} confidence (${confidence.toFixed(2)}).`);
    await systemNote(tx, job.orgId, ticketId, lane === 'manual' ? 'Stood down — no customer-facing text generated. Assembled the context instead.' : lane === 'draft' ? 'Drafted a reply grounded in approved sources and attached the citations.' : 'Filled the action template and ran the validation checks.');
    await systemNote(tx, job.orgId, ticketId, `Routed to ${department?.name ?? 'no owning team'} and placed in the queue by urgency.`);
    if (assignee) await systemNote(tx, job.orgId, ticketId, `Assigned to ${assignee.name} — ${assignee.reason}.`);

    await updateTicket(tx, locked, {
      lane,
      originalLane: forceLane ? locked.originalLane : lane,
      laneNote,
      status,
      priority: ranked.priority,
      departmentId: department?.id ?? null,
      queryTypeId: qt?.id ?? null,
      bucket,
      confidence,
      assigneeId: assignee?.id ?? null,
      ownerKind: assignee ? 'user' : chain === 'auto' ? 'ai' : 'unassigned',
      slaMinutes,
      dueAt: new Date(locked.receivedAt.getTime() + slaMinutes * 60_000),
      regulatoryFlag: guard.result.regulatorNamed ? 'Regulator named' : null,
      sentiment: guard.result.sentiment,
      splitProposed: classification.multiIntent,
      nextMove: status === 'executing' ? 'Executing in the approved cell' : lane === 'auto' ? (chain === 'dual' ? 'Your approval + checker' : 'Your approval') : lane === 'draft' ? (draft?.flagged.length ? 'Check flagged paragraph' : 'Review & send draft') : classification.multiIntent ? 'Split into two tickets' : 'Human commitment due',
      category: lane === 'manual' ? (guard.result.stop ? 'Complaints' : 'Servicing') : lane === 'draft' ? 'Servicing' : 'Transactions',
      subcategory: bucket,
    });
    await tx.insert(s.predictionOutcomes).values({ orgId: job.orgId, agentName: bucketAgent.name, ticketId, confidence, correct: null });

    await recordTicketEvent(tx, locked, {
      actor: AI_ACTOR,
      action: 'triage.completed',
      summary: `Classified QRY-${locked.number} as ${bucket} (${confidence.toFixed(2)}) → ${lane}`,
      data: { lane, confidence, traceId, degraded, provider: run!.provider },
      ...(guard.result.stop ? { feed: { tone: 'stop' as const, meta: `QRY-${locked.number} · hard stop` } } : {}),
    });
    await publish(tx, job.orgId, 'ticket.created', { ticketId });
  });
  logger.info({ ticketId, lane, ms: Date.now() - started }, 'triage complete');
}

async function nextRunNo(tx: Tx, orgId: string, ticketId: string): Promise<number> {
  const [r] = await tx
    .select({ n: sql<number>`count(*)::int` })
    .from(s.triageRuns)
    .where(and(eq(s.triageRuns.orgId, orgId), eq(s.triageRuns.ticketId, ticketId)));
  return (r?.n ?? 0) + 1;
}

function composeReasoning(i: {
  lane: Lane;
  confidence: number;
  bar: number;
  guard: GuardResult;
  classification: ClassifyResult;
  template: { code: string; reversible: boolean; moneyMoves: boolean } | null;
  chain: string | null;
  draft: DraftResult | null;
  degraded: boolean;
  owned: boolean;
}): string {
  if (i.degraded) return 'The model provider was unavailable for part of this run, so nothing customer-facing was generated. The thread is with a person, unaltered.';
  const parts: string[] = [];
  if (i.guard.stop) parts.push(`A hard stop rule fired (${i.guard.stop}), so the agent will not draft customer-facing content and has assembled context for you instead.`);
  if (i.classification.multiIntent) parts.push(`The email carries ${i.classification.intents.length} intents (${i.classification.intents.join(' + ')}), owned by different teams — the agent proposes a split rather than guessing a primary intent.`);
  if (!i.owned) parts.push('No team owns this query type yet, so it cannot be automated and goes to a person every time.');
  parts.push(
    `Classified with ${i.confidence >= 0.9 ? 'high' : i.confidence >= i.bar ? 'moderate' : 'low'} confidence (${i.confidence.toFixed(2)}), ${i.confidence >= i.bar ? 'above' : 'below'} the ${i.bar.toFixed(2)} bar${i.classification.phrases.length ? `, driven by ${i.classification.phrases.map((p) => `“${p}”`).join(', ')}` : ''}.`,
  );
  if (i.lane === 'auto' && i.template) {
    parts.push(
      i.chain === 'auto'
        ? 'The action can be undone and no money moves — the one risk group where the AI is allowed to act alone. It executes now and is reviewed afterwards.'
        : `${i.template.reversible ? 'The action can be undone' : 'This one cannot be undone'} and ${i.template.moneyMoves ? 'money moves' : 'no money moves'}, so it stops here for ${i.chain === 'dual' ? 'your approval and a second approver' : 'your approval'} rather than acting on its own.`,
    );
  }
  if (i.lane === 'draft' && i.draft) {
    parts.push(
      i.draft.coverage === 'full'
        ? 'The draft is grounded only in knowledge-manager-approved material, with every policy statement cited.'
        : 'Only part of the question has approved coverage: the answerable part is drafted, the gap is marked in writing rather than filled, and a gap ticket was raised. Check the flagged paragraph before sending.',
    );
  }
  return parts.join(' ');
}
