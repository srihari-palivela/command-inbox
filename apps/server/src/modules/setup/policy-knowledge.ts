import type {
  ActionsDTO,
  ActionTemplateBody,
  ActionTemplateDTO,
  AdminDTO,
  GapDTO,
  KnowledgeDTO,
  KnowledgeSourceBody,
  PoliciesDTO,
  RiskCell,
  RiskCellDTO,
  TaxonomyDTO,
} from '@ci/contracts';
import { and, asc, desc, eq, isNull, sql } from 'drizzle-orm';
import type { Tx } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { APPROVAL_CYCLES, CELL_TITLE, cellOf, LOCKED_CELL, MAX_DIAL } from '../../domain/risk.js';
import { audit } from '../../platform/audit.js';
import { clock } from '../../platform/clock.js';
import { actorOf, type Ctx } from '../../platform/context.js';
import { conflict, forbidden, notFound, unprocessable } from '../../platform/errors.js';
import { enqueue, type JobRow } from '../../platform/jobs.js';
import { publish } from '../../platform/outbox.js';
import { POLICY_MATRIX, requireCap } from '../../platform/rbac.js';
import { withTenant } from '../../db/client.js';

// ── Action library & autonomy dial ────────────────────────────────────────────
const CELL_META: Record<RiskCell, { phase: string; audit: string }> = {
  '0-0': {
    phase: 'Phase 3 candidate',
    audit:
      'This is the only cell approved for autonomous execution, and only for the actions marked auto below.',
  },
  '0-1': {
    phase: 'Two approvers, always',
    audit:
      'Irreversible even without money moving — a closed account or a withdrawn nomination cannot be quietly undone.',
  },
  '1-0': {
    phase: 'Approval plus an undo window',
    audit:
      'Money moves but can be reversed. A 30-second undo window applies, after which reversal needs its own approval.',
  },
  '1-1': {
    phase: 'A human signs, always',
    audit:
      'This cell will not be automated. The dial is locked to suggest-only by policy, not by configuration.',
  },
};

const GATE_NOTE: Record<RiskCell, string> = {
  '0-0': 'Risk sign-off held 12 Jun. Sampled review of 5% of executions runs weekly.',
  '0-1': 'Raising this dial requires Risk & Compliance sign-off plus a 30-day sampled review.',
  '1-0': 'Auto-execute is available only after 60 days at dual approval with reversal rate under 0.5%.',
  '1-1':
    'Locked by policy. Irreversible and financially material actions always carry a named human approver.',
};

export async function actionsOverview(tx: Tx, ctx: Ctx): Promise<ActionsDTO> {
  requireCap(ctx, 'setup.view', 'view the action library');
  const templates = await tx
    .select()
    .from(s.actionTemplates)
    .where(eq(s.actionTemplates.orgId, ctx.orgId))
    .orderBy(asc(s.actionTemplates.sort));
  const dial = await tx.select().from(s.autonomyDial).where(eq(s.autonomyDial.orgId, ctx.orgId));
  const tpl: ActionTemplateDTO[] = templates.map((t) => ({
    id: t.id,
    code: t.code,
    name: t.name,
    system: t.system,
    owner: t.owner,
    approval: t.approval as ActionTemplateDTO['approval'],
    stpPct: t.stpPct,
    volume: t.monthlyVolume,
    cell: cellOf(t.reversible, t.moneyMoves),
    state: t.state as ActionTemplateDTO['state'],
  }));
  const cells: RiskCellDTO[] = (['0-0', '0-1', '1-0', '1-1'] as RiskCell[]).map((cell) => {
    const d = dial.find((x) => x.cell === cell);
    const inCell = tpl.filter((t) => t.cell === cell).length;
    return {
      cell,
      title: CELL_TITLE[cell],
      count: Math.max(d?.countOverride ?? 0, inCell),
      dial: d?.level ?? 0,
      locked: cell === LOCKED_CELL || !!d?.locked,
      phase: CELL_META[cell].phase,
      audit: CELL_META[cell].audit,
      gateNote: GATE_NOTE[cell],
    };
  });
  return { cells, templates: tpl, autonomousCells: cells.filter((c) => c.dial >= 2).length };
}

export async function setDial(tx: Tx, ctx: Ctx, cell: RiskCell, level: number): Promise<void> {
  requireCap(ctx, 'autonomy.change', 'change the autonomy dial');
  if (cell === LOCKED_CELL && level > 0)
    throw forbidden('This cell is locked to suggest-only by policy.', 'cell_locked');
  if (level > MAX_DIAL[cell])
    throw unprocessable(
      'dial_cap',
      'Auto-execute is only possible for actions that can be undone and move no money.',
    );
  const [before] = await tx
    .select()
    .from(s.autonomyDial)
    .where(and(eq(s.autonomyDial.orgId, ctx.orgId), eq(s.autonomyDial.cell, cell)));
  await tx
    .insert(s.autonomyDial)
    .values({ orgId: ctx.orgId, cell, level, locked: cell === LOCKED_CELL, updatedBy: ctx.user.id })
    .onConflictDoUpdate({
      target: [s.autonomyDial.orgId, s.autonomyDial.cell],
      set: { level, updatedBy: ctx.user.id, updatedAt: clock.now() },
    });
  await audit(tx, ctx.orgId, {
    actor: actorOf(ctx),
    action: 'autonomy.changed',
    entity: 'autonomy_dial',
    entityId: cell,
    summary: `Autonomy for "${CELL_TITLE[cell]}" set to ${['suggest only', 'suggest + approve', 'auto-execute'][level]}`,
    data: { from: before?.level ?? null, to: level },
    feed: { tone: 'flag', meta: 'autonomy dial' },
  });
  await publish(tx, ctx.orgId, 'setup.updated', { area: 'actions' });
}

export async function createActionTemplate(
  tx: Tx,
  ctx: Ctx,
  body: ActionTemplateBody,
): Promise<ActionTemplateDTO> {
  requireCap(ctx, 'setup.edit', 'create an action template');
  // Next free number after the highest existing ACT-NEW code (a count would collide once any are removed).
  const [n] = await tx
    .select({
      n: sql<number>`coalesce(max(nullif(substring(${s.actionTemplates.code} from 'ACT-NEW-(\\d+)'), '')::int), 0)::int`,
    })
    .from(s.actionTemplates)
    .where(eq(s.actionTemplates.orgId, ctx.orgId));
  const code = `ACT-NEW-${String((n?.n ?? 0) + 1).padStart(3, '0')}`;
  const money = body.cell.startsWith('1');
  const reversible = body.cell.endsWith('0');
  const [t] = await tx
    .insert(s.actionTemplates)
    .values({
      orgId: ctx.orgId,
      code,
      name: body.name,
      system: body.system,
      endpoint: 'PENDING',
      owner: 'Risk review',
      reversible,
      moneyMoves: money,
      approval: reversible && !money ? 'single' : 'dual',
      monthlyVolume: 0,
      state: 'pending_review',
      sort: 1000,
    })
    .returning();
  await audit(tx, ctx.orgId, {
    actor: actorOf(ctx),
    action: 'action_template.created',
    entity: 'action_template',
    entityId: t!.id,
    summary: `${body.name} created as suggest-only in “${CELL_TITLE[body.cell]}” — pending Risk review`,
  });
  await publish(tx, ctx.orgId, 'setup.updated', { area: 'actions' });
  return {
    id: t!.id,
    code,
    name: t!.name,
    system: t!.system,
    owner: t!.owner,
    approval: t!.approval as 'single' | 'dual',
    stpPct: null,
    volume: 0,
    cell: body.cell,
    state: 'pending_review',
  };
}

// ── Rules & policies ─────────────────────────────────────────────────────────
export async function policies(tx: Tx, ctx: Ctx): Promise<PoliciesDTO> {
  requireCap(ctx, 'setup.view', 'view rules and policies');
  const bucket = await tx
    .select()
    .from(s.bucketRules)
    .where(eq(s.bucketRules.orgId, ctx.orgId))
    .orderBy(asc(s.bucketRules.sort));
  const pri = await tx
    .select()
    .from(s.priorityRules)
    .where(eq(s.priorityRules.orgId, ctx.orgId))
    .orderBy(asc(s.priorityRules.sort));
  const proposed = await tx
    .select()
    .from(s.proposedRules)
    .where(eq(s.proposedRules.orgId, ctx.orgId))
    .orderBy(desc(s.proposedRules.createdAt));
  return {
    bucketRules: bucket.map((r) => ({
      id: r.id,
      description: r.description,
      target: r.target,
      kind: r.kind,
      hits: r.hits,
    })),
    priorityRules: pri.map((r) => ({
      id: r.id,
      key: r.key,
      description: r.description,
      target: r.target,
      hard: r.hard,
      enabled: r.enabled || r.hard,
      hits: r.hits,
    })),
    matrix: POLICY_MATRIX,
    cycles: APPROVAL_CYCLES,
    proposedRules: proposed.map((p) => ({
      id: p.id,
      text: p.text,
      ticketNumber: p.ticketNumber,
      proposedBy: p.proposedByName,
      at: p.createdAt.toISOString(),
      status: p.status,
    })),
  };
}

export async function togglePriorityRule(tx: Tx, ctx: Ctx, id: string, enabled: boolean): Promise<void> {
  requireCap(ctx, 'rules.edit', 'change priority rules');
  const [r] = await tx
    .select()
    .from(s.priorityRules)
    .where(and(eq(s.priorityRules.orgId, ctx.orgId), eq(s.priorityRules.id, id)));
  if (!r) throw notFound('Rule');
  if (r.hard)
    throw forbidden('Hard rules cannot be switched off — they are policy, not preference.', 'hard_rule');
  await tx.update(s.priorityRules).set({ enabled }).where(eq(s.priorityRules.id, id));
  await audit(tx, ctx.orgId, {
    actor: actorOf(ctx),
    action: 'rule.toggled',
    entity: 'priority_rule',
    entityId: id,
    summary: `Priority rule "${r.description}" ${enabled ? 'enabled' : 'disabled'}`,
  });
  await publish(tx, ctx.orgId, 'setup.updated', { area: 'policies' });
}

/** Staff can only *propose* a hard stop; an admin decides (the matrix says only Admin edits rules). */
export async function decideProposedRule(tx: Tx, ctx: Ctx, id: string, approve: boolean): Promise<void> {
  requireCap(ctx, 'rules.edit', 'approve rule changes');
  const [p] = await tx
    .select()
    .from(s.proposedRules)
    .where(and(eq(s.proposedRules.orgId, ctx.orgId), eq(s.proposedRules.id, id)));
  if (!p) throw notFound('Proposed rule');
  if (p.status !== 'pending') throw conflict('already_decided', 'This proposal was already decided.');
  await tx
    .update(s.proposedRules)
    .set({ status: approve ? 'approved' : 'rejected', decidedBy: ctx.user.id })
    .where(eq(s.proposedRules.id, id));
  if (approve) {
    const [max] = await tx
      .select({ n: sql<number>`coalesce(max(${s.bucketRules.sort}), 0)::int` })
      .from(s.bucketRules)
      .where(eq(s.bucketRules.orgId, ctx.orgId));
    await tx.insert(s.bucketRules).values({
      orgId: ctx.orgId,
      sort: (max?.n ?? 0) + 1,
      description: p.text,
      target: 'Always a person — hard stop',
      kind: 'Hard stop',
      hits: 'new',
      pattern: null,
    });
  }
  await audit(tx, ctx.orgId, {
    actor: actorOf(ctx),
    action: approve ? 'rule.approved' : 'rule.rejected',
    entity: 'proposed_rule',
    entityId: id,
    summary: `${approve ? 'Approved' : 'Rejected'} proposed rule from ${p.proposedByName}`,
  });
  await publish(tx, ctx.orgId, 'setup.updated', { area: 'policies' });
}

// ── Knowledge ────────────────────────────────────────────────────────────────
const agoLabel = (d: Date | null) => {
  if (!d) return 'never';
  const m = Math.round((clock.now().getTime() - d.getTime()) / 60_000);
  if (m < 60) return `${Math.max(1, m)}m ago`;
  if (m < 1440) return `${Math.round(m / 60)}h ago`;
  return `${Math.round(m / 1440)}d ago`;
};

export async function knowledge(tx: Tx, ctx: Ctx): Promise<KnowledgeDTO> {
  requireCap(ctx, 'setup.view', 'view knowledge');
  const sources = await tx
    .select()
    .from(s.knowledgeSources)
    .where(eq(s.knowledgeSources.orgId, ctx.orgId))
    .orderBy(asc(s.knowledgeSources.sort), asc(s.knowledgeSources.createdAt));
  const depts = await tx
    .select()
    .from(s.departments)
    .where(eq(s.departments.orgId, ctx.orgId))
    .orderBy(asc(s.departments.sort));
  const gaps = await tx
    .select()
    .from(s.gapTickets)
    .where(eq(s.gapTickets.orgId, ctx.orgId))
    .orderBy(asc(s.gapTickets.closedAt), desc(s.gapTickets.hits));
  const qts = await tx
    .select()
    .from(s.queryTypes)
    .where(and(eq(s.queryTypes.orgId, ctx.orgId), eq(s.queryTypes.showOnMap, true)));
  const openGaps = gaps.filter((g) => !g.closedAt);
  const quotable = qts.length
    ? Math.round((qts.filter((q) => q.live && q.departmentId).length / qts.length) * 100)
    : 0;
  // Invariant check: sent replies citing anything that was not approved content.
  const [ungrounded] = await tx
    .execute<{ n: number }>(
      sql`
      select count(*)::int as n from drafts d
       where d.org_id = ${ctx.orgId} and d.state = 'sent'
         and exists (select 1 from jsonb_array_elements(d.citations) c
                       join knowledge_docs k on k.id = (c->>'docId')::uuid
                      where k.status = 'pending')`,
    )
    .then((r) => r.rows);
  const order: Record<string, number> = { blocking: 0, stale: 1, unowned: 2, resolved: 3 };
  return {
    sources: sources.map((k) => ({
      id: k.id,
      name: k.name,
      kind: k.kind,
      abbr: k.abbr,
      docs: `${k.docCount} ${k.docUnit}`,
      approved: `${k.approvedCount} approved`,
      sync: k.syncNote || (k.lastSyncAt ? `Synced ${agoLabel(k.lastSyncAt)}` : 'Connecting…'),
      health: k.health as 'ok' | 'warn' | 'bad',
      note: k.note,
    })),
    readiness: depts.map((d) => ({ department: d.name, pct: d.readinessPct, note: d.readinessNote })),
    honest: { quotablePct: quotable, ungroundedSentences: ungrounded?.n ?? 0, openGaps: openGaps.length },
    gaps: gaps
      .slice()
      .sort((a, b) => order[a.severity]! - order[b.severity]! || b.hits - a.hits)
      .filter((g, i) => i < 5 || !g.closedAt)
      .slice(0, 8)
      .map((g): GapDTO => ({
        id: g.id,
        number: `GAP-${String(g.number).padStart(4, '0')}`,
        severity: g.severity as GapDTO['severity'],
        question: g.question,
        detail: g.detail,
        hits: g.hits,
        age: g.closedAt
          ? 'closed'
          : `open ${Math.max(1, Math.round((clock.now().getTime() - g.openedAt.getTime()) / 86400_000))} days`,
        owner: g.owner,
        state: g.state,
        cta: g.cta,
      })),
  };
}

const KIND_ABBR: Record<string, string> = {
  SharePoint: 'SP',
  Confluence: 'CF',
  'Google Drive': 'GD',
  S3: 'S3',
  Upload: 'UP',
};

export async function connectSource(
  tx: Tx,
  ctx: Ctx,
  body: KnowledgeSourceBody,
): Promise<{ id: string; name: string }> {
  requireCap(ctx, 'setup.edit', 'connect a knowledge source');
  const [src] = await tx
    .insert(s.knowledgeSources)
    .values({
      orgId: ctx.orgId,
      name: body.name ?? `New ${body.kind} source`,
      kind: body.kind,
      abbr: KIND_ABBR[body.kind] ?? 'KS',
      docCount: 0,
      docUnit: body.kind === 'Upload' ? 'files' : 'documents',
      approvedCount: 0,
      health: 'warn',
      syncNote: 'Connecting… first sync queued',
      note: 'Read-only. Content stays pending until a knowledge manager approves it.',
      sort: 100,
    })
    .returning();
  await enqueue(tx, {
    orgId: ctx.orgId,
    kind: 'knowledge_sync',
    payload: { sourceId: src!.id },
    runAt: new Date(clock.now().getTime() + 3000),
    dedupeKey: `ksync:${src!.id}:first`,
  });
  await audit(tx, ctx.orgId, {
    actor: actorOf(ctx),
    action: 'knowledge.source_connected',
    entity: 'knowledge_source',
    entityId: src!.id,
    summary: `${body.kind} connected — first sync queued; nothing is citable until approved`,
  });
  await publish(tx, ctx.orgId, 'setup.updated', { area: 'knowledge' });
  return { id: src!.id, name: src!.name };
}

export async function syncSource(tx: Tx, ctx: Ctx, id: string): Promise<{ message: string }> {
  requireCap(ctx, 'setup.edit', 'sync a knowledge source');
  const [src] = await tx
    .select()
    .from(s.knowledgeSources)
    .where(and(eq(s.knowledgeSources.orgId, ctx.orgId), eq(s.knowledgeSources.id, id)));
  if (!src) throw notFound('Source');
  if (src.health === 'bad') {
    await tx
      .update(s.knowledgeSources)
      .set({ syncNote: 'Re-consent requested from the owner' })
      .where(eq(s.knowledgeSources.id, id));
    await audit(tx, ctx.orgId, {
      actor: actorOf(ctx),
      action: 'knowledge.reconsent_requested',
      entity: 'knowledge_source',
      entityId: id,
      summary: `Re-consent request sent to the ${src.kind} owner`,
    });
    return { message: `Re-consent request sent to the ${src.kind} owner.` };
  }
  await enqueue(tx, {
    orgId: ctx.orgId,
    kind: 'knowledge_sync',
    payload: { sourceId: id },
    dedupeKey: `ksync:${id}:${clock.now().getTime()}`,
  });
  return { message: `Sync queued for ${src.name}.` };
}

/** Knowledge sync job (connector adapters per kind in production; the dev adapter counts pending docs). */
export async function runKnowledgeSync(job: JobRow): Promise<void> {
  await withTenant(job.orgId, async (tx) => {
    const [src] = await tx
      .select()
      .from(s.knowledgeSources)
      .where(
        and(eq(s.knowledgeSources.orgId, job.orgId), eq(s.knowledgeSources.id, String(job.payload.sourceId))),
      );
    if (!src || src.health === 'bad') return;
    const docs = src.docCount || 12 + (src.name.length % 20);
    await tx
      .update(s.knowledgeSources)
      .set({
        docCount: docs,
        health: 'ok',
        syncNote: '',
        lastSyncAt: clock.now(),
        note:
          src.approvedCount < docs
            ? `${docs - src.approvedCount} pending approval — not citable yet`
            : src.note,
      })
      .where(eq(s.knowledgeSources.id, src.id));
    await publish(tx, job.orgId, 'setup.updated', { area: 'knowledge' });
  });
}

export async function actOnGap(tx: Tx, ctx: Ctx, id: string): Promise<{ message: string }> {
  requireCap(ctx, 'setup.edit', 'work gap tickets');
  const [g] = await tx
    .select()
    .from(s.gapTickets)
    .where(and(eq(s.gapTickets.orgId, ctx.orgId), eq(s.gapTickets.id, id)));
  if (!g) throw notFound('Gap');
  if (g.closedAt) return { message: `GAP-${String(g.number).padStart(4, '0')} is resolved.` };
  const next =
    g.owner === 'Unassigned'
      ? { state: 'Owner requested', owner: 'Branch Ops' }
      : { state: `${g.cta} — sent to ${g.owner}`, owner: g.owner };
  await tx.update(s.gapTickets).set(next).where(eq(s.gapTickets.id, id));
  await audit(tx, ctx.orgId, {
    actor: actorOf(ctx),
    action: 'gap.actioned',
    entity: 'gap',
    entityId: id,
    summary: `${g.cta} · GAP-${String(g.number).padStart(4, '0')} — sent to ${next.owner}`,
  });
  await publish(tx, ctx.orgId, 'setup.updated', { area: 'knowledge' });
  return { message: `${g.cta} · GAP-${String(g.number).padStart(4, '0')} — sent to ${next.owner}.` };
}

// ── Taxonomy ("Who owns what") ───────────────────────────────────────────────
export async function taxonomy(tx: Tx, ctx: Ctx): Promise<TaxonomyDTO> {
  requireCap(ctx, 'setup.view', 'view the responsibility map');
  const depts = await tx
    .select()
    .from(s.departments)
    .where(eq(s.departments.orgId, ctx.orgId))
    .orderBy(asc(s.departments.sort));
  const qts = await tx
    .select()
    .from(s.queryTypes)
    .where(and(eq(s.queryTypes.orgId, ctx.orgId), eq(s.queryTypes.showOnMap, true)))
    .orderBy(asc(s.queryTypes.sort));
  const users = await tx
    .select({ id: s.users.id, name: s.users.name, initials: s.users.initials })
    .from(s.users);
  // Unowned query types are shown under the team they arrive at, flagged, so the gap is visible.
  const retail = depts.find((d) => d.name === 'Retail Service Desk');
  const out = depts
    .map((d) => {
      const items = qts.filter((q) => q.departmentId === d.id || (!q.departmentId && d.id === retail?.id));
      return {
        id: d.id,
        name: d.name,
        owner: users.find((u) => u.id === d.ownerId) ?? null,
        gapNote: d.gapNote,
        tone: (d.risk ? 'risk' : 'normal') as 'risk' | 'normal',
        queryTypes: items.map((q) => ({
          id: q.id,
          name: q.mapName ?? q.name,
          lane: q.defaultLane as 'auto',
          volume: q.monthlyVolume,
          live: q.live && !!q.departmentId,
        })),
      };
    })
    .filter((d) => d.queryTypes.length > 0);
  const owned = qts.filter((q) => !!q.departmentId).length;
  return {
    departments: out,
    owned,
    total: qts.length,
    contract: [
      {
        title: 'The AI addresses the query',
        text: 'It reads, classifies, extracts, drafts and fills the action. On the volume marked Auto it can carry the work end to end.',
      },
      {
        title: 'You stay accountable',
        text: 'Every customer-facing sentence, and every action that cannot be undone, carries a named human approver. Approval is never implicit.',
      },
      {
        title: 'What it will not do',
        text: 'It will not answer from model recall, will not touch a query type that has no owner, and will not proceed past a hard stop rule.',
      },
      {
        title: 'What it admits',
        text: 'Below the bar it says so, hands over with context, and raises a gap ticket rather than guessing.',
      },
    ],
  };
}

export async function setDepartmentOwner(
  tx: Tx,
  ctx: Ctx,
  departmentId: string,
  userId?: string,
): Promise<{ owner: string }> {
  requireCap(ctx, 'setup.edit', 'change who owns a query type');
  const [d] = await tx
    .select()
    .from(s.departments)
    .where(and(eq(s.departments.orgId, ctx.orgId), eq(s.departments.id, departmentId)));
  if (!d) throw notFound('Team');
  let pickId = userId;
  if (!pickId) {
    const pool = await tx.execute<{ id: string }>(sql`
      select c.user_id as id from clearances c join users u on u.id = c.user_id
       where c.org_id = ${ctx.orgId} and c.department_id = ${departmentId} and c.level >= 3
         and c.user_id <> coalesce(${d.ownerId}::uuid, '00000000-0000-0000-0000-000000000000'::uuid)
       order by u.name limit 1`);
    pickId = pool.rows[0]?.id;
  }
  if (!pickId) throw conflict('no_candidate', 'Nobody else holds approve clearance for this team.');
  const [c] = await tx
    .select()
    .from(s.clearances)
    .where(
      and(
        eq(s.clearances.orgId, ctx.orgId),
        eq(s.clearances.userId, pickId),
        eq(s.clearances.departmentId, departmentId),
      ),
    );
  if (!c || c.level < 3)
    throw forbidden('The owner must hold approve clearance for this team.', 'clearance_required');
  const [u] = await tx.select().from(s.users).where(eq(s.users.id, pickId));
  await tx.update(s.departments).set({ ownerId: pickId }).where(eq(s.departments.id, departmentId));
  await audit(tx, ctx.orgId, {
    actor: actorOf(ctx),
    action: 'taxonomy.owner_changed',
    entity: 'department',
    entityId: departmentId,
    summary: `${d.name} now owned by ${u!.name}`,
  });
  await publish(tx, ctx.orgId, 'setup.updated', { area: 'map' });
  return { owner: u!.name };
}

// ── Where mail arrives ───────────────────────────────────────────────────────
export async function adminOverview(tx: Tx, ctx: Ctx): Promise<AdminDTO> {
  requireCap(ctx, 'setup.view', 'view mailboxes and connectors');
  const mbs = await tx
    .select()
    .from(s.mailboxes)
    .where(eq(s.mailboxes.orgId, ctx.orgId))
    .orderBy(asc(s.mailboxes.sort), asc(s.mailboxes.createdAt));
  const depts = await tx.select().from(s.departments).where(eq(s.departments.orgId, ctx.orgId));
  const conns = await tx
    .select()
    .from(s.connectors)
    .where(eq(s.connectors.orgId, ctx.orgId))
    .orderBy(asc(s.connectors.sort));
  return {
    mailboxes: mbs.map((m) => ({
      id: m.id,
      address: m.address,
      department: m.teamLabel || depts.find((d) => d.id === m.departmentId)?.name || 'Unassigned',
      permissions: m.permissions,
      volume24h: m.volume24h,
      state: m.state as AdminDTO['mailboxes'][number]['state'],
      provider: m.provider as AdminDTO['mailboxes'][number]['provider'],
    })),
    connectors: conns.map((c) => ({
      id: c.id,
      abbr: c.abbr,
      name: c.name,
      scope: c.scope,
      state: c.state as AdminDTO['connectors'][number]['state'],
    })),
    guardrails: [
      'PII masked before any text reaches the classifier; unmasked values are re-bound only at execution.',
      'Outbound email is only ever sent under a named approver — the AI holds no send scope of its own.',
      'Hard stop keywords (ombudsman, legal notice, regulator, fraud) suspend all generation on the thread.',
      'Every action carries an idempotency key; a duplicate approval cannot execute twice.',
      'Immutable, hash-chained audit log with actor, timestamp, source and confidence for every decision.',
    ],
  };
}

export async function openGapCount(tx: Tx, orgId: string): Promise<number> {
  const [r] = await tx
    .select({ n: sql<number>`count(*)::int` })
    .from(s.gapTickets)
    .where(and(eq(s.gapTickets.orgId, orgId), isNull(s.gapTickets.closedAt)));
  return r?.n ?? 0;
}
