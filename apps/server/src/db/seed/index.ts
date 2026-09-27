/**
 * Seeds three tenants. Apex Bank carries the full designed scenario; Meridian and Northwind are small,
 * so switching workspace visibly changes the data (and demonstrates tenant isolation).
 */
import { initialsOf } from '@ci/contracts';
import { and, eq } from 'drizzle-orm';
import { drizzle } from 'drizzle-orm/node-postgres';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { env } from '../../config/env.js';
import { audit } from '../../platform/audit.js';
import { AI_ACTOR, type Actor } from '../../platform/context.js';
import { sha256, canonicalJson } from '../../platform/crypto.js';
import { cellOf, chainFor } from '../../domain/risk.js';
import { createPool, type Tx } from '../client.js';
import * as s from '../schema.js';
import {
  ACTION_TEMPLATES, AGENTS, ALERTS, BACKGROUND_LOAD, BOARDS, BUCKET_RULES, CELL_COUNTS, CONNECTORS, COURSES,
  DEPARTMENTS, DIAL, EXTRA_GAPS, FEEDBACK, GAPS, KDOCS, KSOURCES, MAILBOXES, NOTIFS, ORGS, PEOPLE, PRIORITY_RULES,
  QUERY_TYPES, SERIES, type PersonName,
} from './data.js';
import { DETAILED, HISTORY, LIGHT, type SeedTicket } from './tickets.js';

const MIN = 60_000;
const DAY = 24 * 60 * MIN;

export async function seed(url = env.DATABASE_ADMIN_URL, now = new Date()): Promise<void> {
  const pool = createPool(url, 2);
  const db = drizzle(pool, { schema: s });
  const ago = (min: number) => new Date(now.getTime() - min * MIN);
  const inMin = (min: number) => new Date(now.getTime() + min * MIN);

  try {
    await db.transaction(async (tx) => {
      // ── Orgs & people ─────────────────────────────────────────────────────
      const orgRows = await tx
        .insert(s.orgs)
        .values(ORGS.map((o) => ({ ...o, headcount: o.slug === 'apex' ? 34 : o.slug === 'meridian' ? 11 : 6 })))
        .returning();
      const org = Object.fromEntries(orgRows.map((o) => [o.slug, o])) as Record<string, (typeof orgRows)[number]>;
      const apex = org.apex!.id;

      const userRows = await tx
        .insert(s.users)
        .values(PEOPLE.map((p) => ({ email: p.email, name: p.name, initials: initialsOf(p.name) })))
        .returning();
      const uid = Object.fromEntries(userRows.map((u) => [u.name, u.id])) as Record<PersonName, string>;

      await tx.insert(s.memberships).values(
        PEOPLE.map((p, i) => ({
          orgId: apex,
          userId: uid[p.name],
          role: p.role,
          title: p.title,
          pod: p.pod,
          capacity: p.cap,
          years: p.years,
          baseLoad: BACKGROUND_LOAD[p.name],
          joinedAt: new Date(now.getTime() - (p.years * 365 + i * 17) * DAY),
        })),
      );
      // P. Sharma has a different role in each workspace — the org switcher shows it.
      await tx.insert(s.memberships).values([
        { orgId: org.meridian!.id, userId: uid['P. Sharma'], role: 'admin', title: 'Workspace admin', pod: 'Pilot team', capacity: 10 },
        { orgId: org.meridian!.id, userId: uid['R. Menon'], role: 'lead', title: 'Pilot lead', pod: 'Pilot team', capacity: 8 },
        { orgId: org.northwind!.id, userId: uid['P. Sharma'], role: 'staff', title: 'Support staff', pod: 'Members desk', capacity: 12 },
        { orgId: org.northwind!.id, userId: uid['A. Kapoor'], role: 'admin', title: 'Admin', pod: 'Members desk', capacity: 6 },
      ]);

      await seedApex(tx, apex, uid, now, ago, inMin);
      await seedSmallOrg(tx, org.meridian!.id, uid, now, 'meridian');
      await seedSmallOrg(tx, org.northwind!.id, uid, now, 'northwind');
    });
  } finally {
    await pool.end();
  }
}

type Uid = Record<PersonName, string>;

async function seedApex(
  tx: Tx,
  orgId: string,
  uid: Uid,
  now: Date,
  ago: (m: number) => Date,
  inMin: (m: number) => Date,
): Promise<void> {
  const person = (name: PersonName): Actor => ({ kind: 'user', id: uid[name], name, initials: initialsOf(name) });

  await tx.insert(s.userSettings).values(
    Object.values(uid).map((userId) => ({
      orgId,
      userId,
      prefs: {},
      signature: '',
    })),
  );
  await tx
    .update(s.userSettings)
    .set({ signature: 'Priyanka Sharma\nCustomer Service · Apex Bank\nThis mailbox is monitored 08:00–20:00 IST.' })
    .where(and(eq(s.userSettings.orgId, orgId), eq(s.userSettings.userId, uid['P. Sharma'])));

  // ── Departments, taxonomy, clearance, availability ──────────────────────
  const deptRows = await tx
    .insert(s.departments)
    .values(
      DEPARTMENTS.map((d, i) => ({
        orgId,
        name: d.name,
        ownerId: uid[d.owner as PersonName],
        sort: i,
        readinessPct: d.readiness,
        readinessNote: d.note,
        gapNote: d.gapNote,
        risk: d.risk,
        inMatrix: d.inMatrix,
      })),
    )
    .returning();
  const dept = Object.fromEntries(deptRows.map((d) => [d.name, d.id])) as Record<string, string>;

  const qtRows = await tx
    .insert(s.queryTypes)
    .values(
      QUERY_TYPES.map((q, i) => ({
        orgId,
        name: q.name,
        departmentId: q.dept ? dept[q.dept]! : null,
        defaultLane: q.lane,
        monthlyVolume: q.vol,
        live: q.live,
        baselineHours: q.base,
        actualHours: q.act,
        lateCount: q.late,
        ownerLabel: q.owner,
        showOnMap: q.map,
        showOnSpeed: q.speed,
        sort: i,
      })),
    )
    .returning();
  const qt = Object.fromEntries(qtRows.map((q) => [q.name, q.id])) as Record<string, string>;

  await tx.insert(s.clearances).values(
    PEOPLE.flatMap((p) =>
      Object.entries(p.clear).map(([d, level]) => ({ orgId, userId: uid[p.name], departmentId: dept[d]!, level })),
    ),
  );
  await tx.insert(s.staffAvailability).values(
    PEOPLE.map((p) => ({ orgId, userId: uid[p.name], status: p.avail, checkin: p.checkin, calendar: p.cal })),
  );

  // ── Mailboxes & boards ──────────────────────────────────────────────────
  const mbRows = await tx
    .insert(s.mailboxes)
    .values(
      MAILBOXES.map((m, i) => ({
        orgId,
        address: m.address,
        provider: 'microsoft',
        departmentId: m.dept ? dept[m.dept]! : null,
        teamLabel: m.team,
        permissions: [...m.perms],
        state: m.state,
        volume24h: m.vol,
        lastSyncAt: ago(1),
        sort: i,
      })),
    )
    .returning();
  const mailbox = Object.fromEntries(mbRows.map((m) => [m.address, m])) as Record<string, (typeof mbRows)[number]>;

  const boardRows = await tx
    .insert(s.boards)
    .values(
      BOARDS.map((b, i) => ({
        orgId,
        key: b.key,
        name: b.name,
        mailboxId: mailbox[b.mailbox]!.id,
        team: b.team,
        state: b.state,
        autoRatePct: b.auto,
        sort: i,
      })),
    )
    .returning();
  const board = Object.fromEntries(boardRows.map((b) => [b.key, b])) as Record<string, (typeof boardRows)[number]>;

  // ── Agents ──────────────────────────────────────────────────────────────
  const agentRows = await tx
    .insert(s.agents)
    .values(
      AGENTS.map((a, i) => ({
        orgId,
        name: a.name,
        abbr: a.abbr,
        template: a.template,
        model: a.model,
        state: a.state,
        version: a.version,
        prompt: a.prompt,
        evalScore: a.score,
        costPer1kMinor: a.cost,
        role: a.role,
        sort: i,
        createdAt: new Date(now.getTime() - (120 - i * 9) * DAY),
      })),
    )
    .returning();
  const agentId = Object.fromEntries(agentRows.map((a) => [a.name, a.id])) as Record<string, string>;
  for (const a of AGENTS) {
    const id = agentId[a.name]!;
    await tx.insert(s.agentVersions).values(
      Array.from({ length: Math.min(3, a.version) }, (_, k) => {
        const version = a.version - (Math.min(3, a.version) - 1 - k);
        return {
          orgId,
          agentId: id,
          version,
          prompt: a.prompt,
          model: a.model,
          evalStatus: 'passed',
          createdBy: 'A. Kapoor',
          createdAt: new Date(now.getTime() - (Math.min(3, a.version) - k) * 11 * DAY),
        };
      }),
    );
    await tx
      .insert(s.agentBoards)
      .values(a.boards.map((b) => ({ orgId, agentId: id, boardId: board[b]!.id })));
    await tx
      .insert(s.agentEvals)
      .values(a.evals.map(([label, value, tone], i) => ({ orgId, agentId: id, label, value, tone, sort: i })));
  }
  // Calibration history: observed accuracy by confidence band. Trade Bucketer is slightly overconfident.
  const outcomes: (typeof s.predictionOutcomes.$inferInsert)[] = [];
  let seedN = 7;
  const rand = () => {
    seedN = (seedN * 16807) % 2147483647;
    return seedN / 2147483647;
  };
  for (const a of AGENTS.filter((x) => x.role === 'bucketer' || x.role === 'extractor' || x.role === 'drafter')) {
    const skew = a.name === 'Trade Bucketer' ? 0.08 : 0.01;
    for (let i = 0; i < 240; i++) {
      const conf = 0.4 + rand() * 0.6;
      outcomes.push({
        orgId,
        agentName: a.name,
        confidence: Math.round(conf * 100) / 100,
        correct: rand() < Math.max(0, conf - skew),
        createdAt: new Date(now.getTime() - rand() * 30 * DAY),
      });
    }
  }
  await tx.insert(s.predictionOutcomes).values(outcomes);

  await tx.insert(s.feedback).values(
    FEEDBACK.map((f) => ({
      orgId,
      agentId: agentId[f.agent]!,
      agentName: f.agent,
      ticketNumber: f.ticket,
      kind: f.kind,
      text: f.text,
      fix: f.fix,
      createdAt: new Date(now.getTime() - f.daysAgo * DAY),
    })),
  );

  // ── Action library & autonomy dial ──────────────────────────────────────
  const tplRows = await tx
    .insert(s.actionTemplates)
    .values(
      ACTION_TEMPLATES.map((a, i) => ({
        orgId,
        code: a.code,
        name: a.name,
        system: a.system,
        endpoint: a.endpoint,
        owner: a.owner,
        reversible: a.rev,
        moneyMoves: a.money,
        approval: a.approval,
        stpPct: a.stp,
        monthlyVolume: a.vol,
        state: !a.rev && a.money ? 'suggest_only' : 'active',
        sort: i,
      })),
    )
    .returning();
  const tpl = Object.fromEntries(tplRows.map((t) => [t.code, t])) as Record<string, (typeof tplRows)[number]>;
  await tx.insert(s.autonomyDial).values(
    (Object.keys(DIAL) as (keyof typeof DIAL)[]).map((cell) => ({
      orgId,
      cell,
      level: DIAL[cell],
      locked: cell === '1-1',
      countOverride: CELL_COUNTS[cell],
      updatedBy: uid['A. Kapoor'],
    })),
  );

  // ── Rules ───────────────────────────────────────────────────────────────
  await tx.insert(s.bucketRules).values(
    BUCKET_RULES.map((r, i) => ({ orgId, sort: i, description: r.description, target: r.target, kind: r.kind, hits: r.hits, pattern: r.pattern ? structuredClone(r.pattern) as unknown as { any?: string[]; all?: string[]; regex?: string; queryType?: string } : null })),
  );
  await tx.insert(s.priorityRules).values(PRIORITY_RULES.map((r, i) => ({ orgId, sort: i, ...r, enabled: true })));

  // ── Knowledge ───────────────────────────────────────────────────────────
  const srcRows = await tx
    .insert(s.knowledgeSources)
    .values(
      KSOURCES.map((k, i) => ({
        orgId,
        name: k.name,
        kind: k.kind,
        abbr: k.abbr,
        docCount: k.docs,
        docUnit: k.unit,
        approvedCount: k.approved,
        health: k.health,
        syncNote: k.syncNote,
        note: k.note,
        lastSyncAt: ago(k.syncMinAgo),
        sort: i,
      })),
    )
    .returning();
  const src = Object.fromEntries(srcRows.map((r) => [r.name, r.id])) as Record<string, string>;
  const docRows = await tx
    .insert(s.knowledgeDocs)
    .values(
      KDOCS.map((d) => ({
        orgId,
        sourceId: src[d.source]!,
        title: d.title,
        section: d.section,
        owner: d.owner,
        departmentId: dept[d.dept]!,
        status: d.status,
        verifiedAt: new Date(now.getTime() - d.verifiedDaysAgo * DAY),
        body: d.body,
      })),
    )
    .returning();
  const doc = Object.fromEntries(KDOCS.map((d, i) => [d.key, docRows[i]!])) as Record<string, (typeof docRows)[number]>;

  await tx.insert(s.gapTickets).values([
    ...GAPS.map((g) => ({
      orgId,
      number: g.number,
      severity: g.severity,
      question: g.question,
      detail: g.detail,
      hits: g.hits,
      owner: g.owner,
      state: g.state,
      cta: g.cta,
      openedAt: new Date(now.getTime() - g.openDays * DAY),
      closedAt: 'closedDaysAgo' in g ? new Date(now.getTime() - g.closedDaysAgo * DAY) : null,
    })),
    ...EXTRA_GAPS.map(([q, owner], i) => ({
      orgId,
      number: 390 - i * 2,
      severity: 'stale',
      question: q,
      detail: 'Raised automatically when the agent could not ground an answer. Waiting on the content owner.',
      hits: 4 + i * 3,
      owner,
      state: 'Waiting on owner',
      cta: 'Nudge owner',
      openedAt: new Date(now.getTime() - (10 + i * 2) * DAY),
      closedAt: null,
    })),
  ]);

  // ── Insights, admin, learning ───────────────────────────────────────────
  await tx.insert(s.alerts).values(
    ALERTS.map((a) => ({
      orgId,
      sevLabel: a.sevLabel,
      sevKind: a.sevKind,
      bucket: a.bucket,
      text: a.text,
      actionLabel: a.action,
      owner: a.owner,
      createdAt: ago(a.minAgo),
    })),
  );
  await tx.insert(s.connectors).values(CONNECTORS.map((c, i) => ({ orgId, ...c, sort: i })));

  const courseRows = await tx
    .insert(s.courses)
    .values(
      COURSES.map((c, i) => ({
        orgId,
        key: c.key,
        title: c.title,
        minutes: c.minutes,
        cards: c.cards.map((x) => ({ ...x })),
        quiz: c.quiz.map((x) => ({ ...x, options: [...x.options] })),
        baselinePct: c.baseline,
        sort: i,
      })),
    )
    .returning();
  const course = Object.fromEntries(courseRows.map((c) => [c.key, c.id])) as Record<string, string>;
  await tx.insert(s.notifications).values(
    NOTIFS.map((n) => ({
      orgId,
      kind: n.kind,
      source: n.source,
      title: n.title,
      body: n.body,
      courseId: n.course ? course[n.course]! : null,
      urgent: n.urgent,
      createdAt: ago(n.minAgo),
    })),
  );

  const metricRows: (typeof s.dailyMetrics.$inferInsert)[] = [];
  const dayStr = (d: Date) => d.toISOString().slice(0, 10);
  for (const key of ['fr', 'tat', 'missed', 'reopen', 'accept', 'auto', 'csat', 'cost', 'awo'] as const) {
    SERIES[key].forEach((value, i) => {
      metricRows.push({ orgId, metric: `weekly.${key}`, day: dayStr(new Date(now.getTime() - (11 - i) * 7 * DAY)), value });
    });
  }
  SERIES.execBaseline.forEach((value, i) =>
    metricRows.push({ orgId, metric: 'exec.baseline', day: dayStr(new Date(now.getTime() - (13 - i) * DAY)), value }),
  );
  SERIES.execActual.forEach((value, i) =>
    metricRows.push({ orgId, metric: 'exec.actual', day: dayStr(new Date(now.getTime() - (13 - i) * DAY)), value }),
  );
  await tx.insert(s.dailyMetrics).values(metricRows);

  // ── Customers & tickets ─────────────────────────────────────────────────
  const all = [...DETAILED, ...LIGHT];
  const custByCif = new Map<string, string>();
  for (const t of all) {
    if (custByCif.has(t.customer.cif)) continue;
    const [row] = await tx
      .insert(s.customers)
      .values({
        orgId,
        cif: t.customer.cif,
        name: t.customer.name,
        email: t.customer.email,
        phone: t.customer.phone,
        segment: t.customer.segment,
        sinceYear: t.customer.since,
        account: t.customer.account,
      })
      .returning();
    custByCif.set(t.customer.cif, row!.id);
  }

  // Past (closed) tickets: customer history is a query over real tickets.
  for (const h of HISTORY) {
    const received = new Date(now.getTime() - h.daysAgo * DAY);
    const qid = qt[h.queryType]!;
    const q = QUERY_TYPES.find((x) => x.name === h.queryType)!;
    await tx.insert(s.tickets).values({
      orgId,
      number: h.number,
      customerId: custByCif.get(h.cif)!,
      subject: h.subject,
      fromName: DETAILED.find((d) => d.customer.cif === h.cif)!.customer.name,
      fromEmail: DETAILED.find((d) => d.customer.cif === h.cif)!.customer.email,
      receivedAt: received,
      lane: q.lane,
      originalLane: q.lane,
      status: 'closed',
      priority: 'P3',
      departmentId: q.dept ? dept[q.dept]! : null,
      queryTypeId: qid,
      bucket: h.queryType,
      confidence: 0.8,
      ownerKind: 'user',
      assigneeId: uid['A. Fernandes'],
      slaMinutes: 1440,
      dueAt: new Date(received.getTime() + DAY),
      resolvedAt: new Date(received.getTime() + 3 * 60 * MIN),
      closedAt: new Date(received.getTime() + 4 * 60 * MIN),
      resolution: h.outcome,
      nextMove: h.outcome,
      createdAt: received,
      updatedAt: received,
    });
  }

  const feedEvents: { at: Date; input: Parameters<typeof audit>[2] }[] = [];

  for (const t of all) {
    const boardRow = board[t.board]!;
    const mb = mbRows.find((m) => m.id === boardRow.mailboxId)!;
    const received = ago(t.receivedMinAgo);
    const assigneeId = t.owner === 'AI' || t.owner === 'Unassigned' ? null : uid[t.owner];
    const [ticket] = await tx
      .insert(s.tickets)
      .values({
        orgId,
        number: t.number,
        boardId: boardRow.id,
        mailboxId: mb.id,
        customerId: custByCif.get(t.customer.cif)!,
        subject: t.subject,
        fromName: t.customer.name,
        fromEmail: t.customer.email,
        receivedAt: received,
        lane: t.lane,
        originalLane: t.lane,
        laneNote: t.laneNote,
        status: t.status,
        priority: t.priority,
        segment: t.segment,
        departmentId: t.dept ? dept[t.dept]! : null,
        queryTypeId: qt[t.queryType] ?? null,
        bucket: t.bucketLabel ?? t.queryType,
        confidence: t.confidence,
        assigneeId,
        ownerKind: t.owner === 'AI' ? 'ai' : t.owner === 'Unassigned' ? 'unassigned' : 'user',
        slaMinutes: t.slaMinutes,
        dueAt: t.dueInMin === null ? new Date(received.getTime() + t.slaMinutes * MIN) : inMin(t.dueInMin),
        pausedAt: t.status === 'waiting_customer' ? ago(Math.min(120, t.receivedMinAgo / 2)) : null,
        firstReplyAt: new Date(received.getTime() + 60_000),
        resolvedAt: t.resolvedMinAgo !== undefined ? ago(t.resolvedMinAgo) : null,
        reopenCount: t.reopenCount ?? 0,
        regulatoryFlag: t.regulatoryFlag ?? null,
        sentiment: t.sentiment ?? 'neutral',
        nextMove: t.nextMove,
        category: t.category,
        subcategory: t.subcategory,
        product: t.product,
        splitProposed: t.splitProposed ?? false,
        loggedMinutes: t.loggedMinutes ?? 0,
        resolution: t.status === 'resolved' ? (t.owner === 'AI' ? 'Resolved by the AI' : 'Resolved by staff') : null,
        createdAt: received,
        updatedAt: received,
      })
      .returning();
    const ticketId = ticket!.id;
    const deptName = t.dept ?? t.deptLabel ?? 'Unowned';

    // Thread
    for (const m of t.thread ?? []) {
      const internal = m.internal ?? false;
      await tx.insert(s.messages).values({
        orgId,
        ticketId,
        direction: internal ? 'note' : 'inbound',
        fromName: internal ? 'Command Inbox' : t.customer.name,
        fromAddr: internal ? 'no-reply@commandinbox.bank' : t.customer.email,
        toAddr: internal ? 'internal note — not sent' : mb.address,
        body: m.body,
        sentAt: ago(m.minAgo),
      });
    }

    // Triage run + trace
    const traceId = `TRC-${t.number}-01`;
    const reasoning =
      t.reasoning ??
      `Classified as ${t.queryType.toLowerCase()} with ${t.confidence >= 0.9 ? 'high' : t.confidence >= 0.78 ? 'moderate' : 'low'} confidence (${t.confidence.toFixed(2)}). ${t.laneNote}.`;
    const spans = buildSpans(t, deptName);
    const cost = spans.reduce((a, x) => a + (x.costMinor ?? 0), 0);
    const [run] = await tx
      .insert(s.triageRuns)
      .values({
        orgId,
        ticketId,
        traceId,
        reasoning,
        evidence: (t.evidence ?? []).map(([tag, quote, why]) => ({ tag, quote, why })),
        confidence: t.confidence,
        lane: t.lane,
        latencyMs: t.latencyMs,
        costMinor: cost,
        provider: 'seed',
        createdAt: received,
      })
      .returning();
    await tx.insert(s.traceSpans).values(spans.map((x) => ({ orgId, runId: run!.id, ...x })));

    // Action / draft / brief
    if (t.action) {
      const template = tpl[t.action.code]!;
      const cell = cellOf(template.reversible, template.moneyMoves);
      const fields = t.action.fields.map((f) => ({ ...f, inferred: f.source.startsWith('inferred') }));
      await tx.insert(s.actionInstances).values({
        orgId,
        ticketId,
        templateId: template.id,
        accountRef: t.action.account,
        fields,
        validation: t.action.validation,
        state: t.action.state,
        chain: chainFor(cell, template.approval as 'auto' | 'single' | 'dual', DIAL[cell]),
        idempotencyKey: sha256(canonicalJson({ orgId, code: template.code, fields: fields.map((f) => [f.label, f.value]) })),
        executedAt: t.action.state === 'executed' ? received : null,
        externalRef: t.action.state === 'executed' ? `STM-${t.number}-OK` : null,
      });
    }
    if (t.draft) {
      await tx.insert(s.drafts).values({
        orgId,
        ticketId,
        subject: t.draft.subject,
        toAddr: t.customer.email,
        originalBody: t.draft.body,
        currentBody: t.draft.body,
        citations: t.draft.cites.map((k, i) => ({
          n: i + 1,
          docId: doc[k]!.id,
          doc: doc[k]!.title,
          section: doc[k]!.section,
          verifiedAt: doc[k]!.verifiedAt!.toISOString(),
          owner: doc[k]!.owner,
        })),
        flagged: t.draft.flagged,
        state: 'draft',
      });
    }
    if (t.brief) {
      await tx.insert(s.briefs).values({
        orgId,
        ticketId,
        why: t.brief.why,
        summary: t.brief.summary,
        context: t.brief.context.map(([label, value]) => ({ label, value })),
        suggestions: t.brief.suggestions.map(([label, meta]) => ({ label, meta })),
      });
    }

    // Sub-tasks (Jira-style), by lane
    const subs =
      t.lane === 'auto'
        ? [
            ['a1', 'Verify the signatory against the mandate', 'AI', true],
            ['a2', 'Extract and validate the action fields', 'AI', true],
            ['a3', 'Approve as maker', 'You', t.status === 'resolved'],
            ['a4', 'Counter-approve as checker', 'Team lead', t.status === 'resolved'],
          ]
        : t.lane === 'draft'
          ? [
              ['b1', 'Find approved sources for the answer', 'AI', true],
              ['b2', 'Draft the reply with citations', 'AI', true],
              ['b3', 'Read the flagged paragraph', 'You', false],
              ['b4', 'Send and close', 'You', false],
            ]
          : [
              ['c1', 'Pull the history and records for the brief', 'AI', true],
              ['c2', t.number === 48199 ? 'Decide on provisional credit' : 'Decide the next step', 'You', false],
              ['c3', 'Give the customer a dated commitment', 'You', false],
              ['c4', t.regulatoryFlag ? 'Notify Compliance' : 'Close the loop with the customer', 'You', false],
            ];
    await tx.insert(s.subtasks).values(
      subs.map(([key, label, owner, done], i) => ({ orgId, ticketId, key: key as string, label: label as string, owner: owner as string, sort: i, done: done as boolean })),
    );

    // History (system) + seeded human log
    const assigneeName = t.owner === 'AI' ? 'the AI' : t.owner === 'Unassigned' ? 'nobody yet' : t.owner;
    const sys = (min: number, body: string) =>
      tx.insert(s.comments).values({ orgId, ticketId, kind: 'system', authorName: 'Command Inbox', authorInitials: 'AI', body, createdAt: ago(min) });
    await sys(t.receivedMinAgo, `Read the email and classified it as ${(t.bucketLabel ?? t.queryType).toLowerCase()} with ${t.confidence >= 0.9 ? 'high' : t.confidence >= 0.78 ? 'moderate' : 'low'} confidence (${t.confidence.toFixed(2)}).`);
    await sys(
      t.receivedMinAgo - 0.02,
      t.lane === 'manual'
        ? 'Stood down — no customer-facing text generated. Assembled the context instead.'
        : t.lane === 'draft'
          ? 'Drafted a reply grounded in approved sources and attached the citations.'
          : 'Filled the action template and ran the validation checks.',
    );
    await sys(t.receivedMinAgo - 0.04, `Routed to ${deptName} and placed in the queue by urgency.`);
    if (assigneeId) {
      await sys(
        t.receivedMinAgo - 0.06,
        `Assigned to ${assigneeName} — closest skill match for ${(t.bucketLabel ?? t.queryType).toLowerCase()}, lightest live load in ${deptName}.`,
      );
    }
    for (const l of t.log ?? []) {
      await tx.insert(s.comments).values({
        orgId,
        ticketId,
        kind: l.kind,
        authorId: uid[l.who],
        authorName: l.who,
        authorInitials: initialsOf(l.who),
        body: l.text,
        createdAt: ago(l.minAgo),
      });
    }
    for (const [ext, name, size] of t.attachments ?? []) {
      await tx.insert(s.attachments).values({ orgId, ticketId, ext, name, size, storageKey: `seed/${t.number}/${name}` });
    }

    // Linked objects
    const links: { kind: string; label: string; ref: string | null }[] = [];
    if (t.action) links.push({ kind: 'ACTION', label: `${t.action.code} · ${tpl[t.action.code]!.name}`, ref: t.action.code });
    if (t.draft) for (const k of t.draft.cites) links.push({ kind: 'SOURCE', label: `${doc[k]!.title} ${doc[k]!.section}`, ref: doc[k]!.id });
    if (t.brief) links.push({ kind: 'POLICY', label: t.brief.why, ref: null });
    if (t.gap) links.push({ kind: 'GAP', label: `GAP-0${t.gap} · receivables as margin`, ref: `GAP-0${t.gap}` });
    if (t.dispute) links.push({ kind: 'DISPUTE', label: t.dispute, ref: t.dispute.split(' ')[0]! });
    links.push({ kind: 'AUDIT', label: `AUD-${t.number}`, ref: null });
    await tx.insert(s.ticketLinks).values(links.map((l, i) => ({ orgId, ticketId, ...l, sort: i })));

    // Audit trail for the triage itself (not in the activity rail unless listed below)
    feedEvents.push({
      at: received,
      input: {
        actor: AI_ACTOR,
        action: 'triage.completed',
        entity: 'ticket',
        entityId: ticketId,
        ticketId,
        summary: `Classified QRY-${t.number} as ${t.queryType} (${t.confidence.toFixed(2)}) → ${t.lane}`,
        data: { lane: t.lane, confidence: t.confidence, traceId },
      },
    });
  }

  // The AI activity rail from the design, as real audit events.
  const byNumber = async (n: number) => (await tx.query.tickets.findFirst({ where: (tk, { and, eq }) => and(eq(tk.orgId, orgId), eq(tk.number, n)) }))!.id;
  const feed: [number, number | null, string, 'ok' | 'stop' | 'flag' | 'info' | 'muted', string, Actor][] = [
    [1101, 48199, 'Held QRY-48199 from all customer-facing output — ombudsman keyword', 'stop', 'policy 7.1', AI_ACTOR],
    [340, 48188, 'Raised knowledge gap GAP-0412 · receivables as margin under DA terms', 'flag', 'QRY-48188', AI_ACTOR],
    [264, 48195, 'Executed ACT-STM-002 · statement Apr–Jun emailed to registered address', 'ok', 'QRY-48195 · auto', AI_ACTOR],
    [182, null, 'Re-prioritised 14 threads after an ageing sweep; 3 moved up the queue', 'info', 'queue', AI_ACTOR],
    [167, 48211, 'Mandate check passed for M. Raghavan on CIF 8830412', 'info', 'QRY-48211', AI_ACTOR],
    [149, 48199, 'Notified team lead R. Menon — QRY-48199 is 26 minutes from its deadline', 'stop', 'escalation', AI_ACTOR],
    [120, null, 'Learned correction: reason code CONTRACT_TERMINATED preferred over DISPUTE', 'muted', 'from your edit', person('P. Sharma')],
  ];
  for (const [min, n, text, tone, meta, actor] of feed) {
    const ticketId = n ? await byNumber(n) : null;
    feedEvents.push({
      at: ago(min),
      input: { actor, action: 'ai.activity', entity: n ? 'ticket' : 'queue', entityId: ticketId, ticketId, summary: text, feed: { tone, meta } },
    });
  }
  feedEvents.sort((a, b) => a.at.getTime() - b.at.getTime());
  for (const e of feedEvents) await audit(tx, orgId, { ...e.input, at: e.at });

  await tx.insert(s.counters).values([
    { orgId, name: 'ticket', value: 48216 },
    { orgId, name: 'gap', value: 412 },
  ]);
}

/** Port of the prototype's trace builder: the pipeline stages a ticket went through. */
function buildSpans(t: SeedTicket, dept: string) {
  const bucketer = t.dept === 'Trade & Payments' ? 'Trade Bucketer' : 'Retail Bucketer';
  const stop = t.number === 48199;
  const spans: {
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
  }[] = [];
  const push = (offsetMs: number, agent: string, model: string, action: string, output: string, latencyMs: number, tokens: number | null, costMinor: number | null, status: 'ok' | 'flag' | 'stop') =>
    spans.push({ seq: spans.length + 1, offsetMs, agent, model, action, output, latencyMs, tokens, costMinor, status });
  const n = (t.thread ?? []).length || 1;
  push(0, 'Mail intake', '—', 'Fetched the thread over Graph API, masked PII before any model call', `${n} message${n > 1 ? 's' : ''} · sender matched to ${t.customer.cif}`, 128, null, null, 'ok');
  push(128, 'Guardrail Sentinel', 'claude-haiku-4-5', 'Screened for hard stop rules before anything else ran',
    stop ? 'STOP · ombudsman named + third contact — customer-facing generation suspended' : t.sentiment === 'vulnerable' ? 'STOP · vulnerable-customer signal — drafting suspended' : 'Clear · no hard stop fired',
    212, 1100, 4, stop || t.sentiment === 'vulnerable' ? 'stop' : 'ok');
  const bar = t.confidence >= 0.78;
  push(340, bucketer, 'claude-sonnet-5', 'Classified the query against the taxonomy',
    `${t.bucketLabel ?? t.queryType} · confidence ${t.confidence.toFixed(2)} · ${bar ? 'above the bar' : 'below the bar, human required'}`, 798, 3400, 19, bar ? 'ok' : 'flag');
  if (t.brief && t.number === 48199) {
    push(1100, 'Dispute Summariser', 'claude-sonnet-5', 'Assembled dispute history, merchant record and the chargeback clock for the human',
      'Brief attached · 5 context items · no customer-facing text generated', 1900, 6200, 28, 'ok');
  } else if (t.action) {
    const inferred = t.action.fields.filter((f) => f.source.startsWith('inferred')).length;
    push(1100, 'Field Extractor', 'claude-sonnet-5', `Filled ${t.action.code} from the thread and system records`,
      `${t.action.fields.length} fields · ${inferred} inferred, rest verified against core`, 644, 4100, 31, 'ok');
    const irreversible = ACTION_TEMPLATES.find((a) => a.code === t.action!.code)!;
    push(1800, 'Policy engine', 'rules', 'Looked up the action’s risk cell and approval route',
      `${irreversible.rev ? 'Can be undone' : 'Cannot be undone'} · ${irreversible.money ? 'Money moves' : 'No money moves'} → ${irreversible.rev && !irreversible.money ? 'the AI may act alone' : 'needs you and a second approver'}`,
      31, null, null, irreversible.rev ? 'ok' : 'flag');
  } else if (t.draft) {
    const gap = t.gap ? ` · 1 gap flagged, GAP-0${t.gap} raised` : ' · full coverage';
    push(1100, 'Reply Drafter', 'claude-sonnet-5', 'Drafted the reply from approved sources only',
      `${t.draft.cites.length} citation${t.draft.cites.length > 1 ? 's' : ''}${gap}`, 1200, 5800, 42, t.gap ? 'flag' : 'ok');
  } else if (t.splitProposed) {
    push(1100, 'Split proposer', 'claude-sonnet-5', 'Detected two intents owned by two departments',
      'Proposed split into two child tickets · held for your decision', 905, 3900, 22, 'flag');
  } else if (t.lane === 'manual') {
    push(1100, 'Summariser', 'claude-sonnet-5', 'Assembled the history and records for the person taking over',
      'Brief attached · no customer-facing text generated', 1100, 3600, 24, 'ok');
  } else if (t.lane === 'draft') {
    push(1100, 'Reply Drafter', 'claude-sonnet-5', 'Drafting from approved sources only', 'In progress', 900, 2100, 18, 'ok');
  } else {
    push(1100, 'Field Extractor', 'claude-sonnet-5', 'Filling the action template from the thread', 'In progress', 600, 2400, 18, 'ok');
  }
  const priNote: Record<string, string> = {
    P1: 'P1 · Critical · regulator, fraud, or money at risk today',
    P2: 'P2 · High · deadline inside 8 hours, or a repeat contact',
    P3: 'P3 · Normal · standard servicing, inside the day',
    P4: 'P4 · Low · informational, no deadline pressure',
  };
  push(2100, 'Priority Ranker', 'rules + claude-haiku-4-5', 'Scored urgency from deadline, sentiment, amount and repeat contacts', priNote[t.priority]!, 96, 800, 2, t.priority === 'P1' ? 'flag' : 'ok');
  push(2200, 'Router', 'rules', 'Placed the ticket with the right person at the right position', `Queued for ${dept} · clearance-checked · position by ${t.priority}`, 18, null, null, 'ok');
  return spans;
}

/** Small tenants: enough to show a different workspace, and to prove isolation. */
async function seedSmallOrg(tx: Tx, orgId: string, uid: Uid, now: Date, kind: 'meridian' | 'northwind') {
  const members = kind === 'meridian' ? (['P. Sharma', 'R. Menon'] as const) : (['P. Sharma', 'A. Kapoor'] as const);
  await tx.insert(s.userSettings).values(members.map((m) => ({ orgId, userId: uid[m], prefs: {}, signature: '' })));
  const [d] = await tx
    .insert(s.departments)
    .values({ orgId, name: kind === 'meridian' ? 'Wealth Desk' : 'Member Services', ownerId: uid[members[0]], readinessPct: 40, readinessNote: 'pilot content only' })
    .returning();
  await tx.insert(s.clearances).values(members.map((m) => ({ orgId, userId: uid[m], departmentId: d!.id, level: 3 })));
  await tx.insert(s.staffAvailability).values(members.map((m) => ({ orgId, userId: uid[m], status: 'available', checkin: 'Checked in 09:00', calendar: 'Free' })));
  const [q] = await tx
    .insert(s.queryTypes)
    .values({ orgId, name: 'General enquiry', departmentId: d!.id, defaultLane: 'draft', monthlyVolume: 40, ownerLabel: d!.name })
    .returning();
  const address = kind === 'meridian' ? 'wealth@meridian.example' : 'members@northwind.example';
  const [mb] = await tx
    .insert(s.mailboxes)
    .values({ orgId, address, provider: 'google', departmentId: d!.id, teamLabel: d!.name, permissions: ['read', 'label', 'draft'], state: 'streaming', volume24h: kind === 'meridian' ? 64 : 21 })
    .returning();
  const [b] = await tx
    .insert(s.boards)
    .values({ orgId, key: 'main', name: kind === 'meridian' ? 'Wealth mail' : 'Member mail', mailboxId: mb!.id, team: d!.name, state: 'observe', autoRatePct: 0 })
    .returning();
  await tx.insert(s.priorityRules).values(PRIORITY_RULES.map((r, i) => ({ orgId, sort: i, ...r, enabled: true })));
  for (const [dial, lvl] of Object.entries(DIAL)) {
    await tx.insert(s.autonomyDial).values({ orgId, cell: dial, level: Math.min(lvl, 1), locked: dial === '1-1' });
  }
  const [cust] = await tx
    .insert(s.customers)
    .values({ orgId, cif: 'CIF 100001', name: kind === 'meridian' ? 'Harini Balaji' : 'Tom Okafor', email: kind === 'meridian' ? 'harini.b@gmail.com' : 'tom.okafor@mail.example', segment: 'Retail', sinceYear: 2021, account: 'A/C ••0001' })
    .returning();
  const received = new Date(now.getTime() - 95 * MIN);
  const [t] = await tx
    .insert(s.tickets)
    .values({
      orgId, number: kind === 'meridian' ? 1201 : 3301, boardId: b!.id, mailboxId: mb!.id, customerId: cust!.id,
      subject: kind === 'meridian' ? 'Portfolio statement for Q2 not received' : 'How do I update my address on the membership?',
      fromName: cust!.name, fromEmail: cust!.email, receivedAt: received, lane: 'draft', originalLane: 'draft', laneNote: 'Observe mode — AI drafts are shadowed only',
      status: 'with_human', priority: 'P3', segment: 'Retail', departmentId: d!.id, queryTypeId: q!.id, bucket: 'General enquiry', confidence: 0.7,
      assigneeId: uid['P. Sharma'], ownerKind: 'user', slaMinutes: 1440, dueAt: new Date(received.getTime() + 1440 * MIN), nextMove: 'Reply to the customer',
      category: 'Servicing', subcategory: 'Information request', product: 'Savings account',
    })
    .returning();
  await tx.insert(s.messages).values({
    orgId, ticketId: t!.id, direction: 'inbound', fromName: cust!.name, fromAddr: cust!.email, toAddr: address,
    body: kind === 'meridian' ? 'I have not received my portfolio statement for the quarter ending June. Could you resend it?' : 'I moved house last month. What do I need to send you to update my address?',
    sentAt: received,
  });
  await tx.insert(s.counters).values([{ orgId, name: 'ticket', value: kind === 'meridian' ? 1201 : 3301 }, { orgId, name: 'gap', value: 1 }]);
  await audit(tx, orgId, { actor: AI_ACTOR, action: 'workspace.created', entity: 'org', entityId: orgId, summary: `Workspace ${kind} seeded`, at: received });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  seed()
    .then(() => console.log('seeded'))
    .catch((e) => {
      console.error(e);
      process.exit(1);
    });
}
