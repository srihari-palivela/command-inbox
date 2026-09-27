import type {
  ActivityDTO,
  CustomKpiDTO,
  KpiBody,
  KpiMetric,
  KpiTileDTO,
  Lane,
  PerformanceDTO,
  QueryTypeSpeedDTO,
  ResultsDTO,
  ShiftDTO,
} from '@ci/contracts';
import { and, asc, desc, eq, isNotNull, isNull, sql } from 'drizzle-orm';
import type { Tx } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { audit } from '../../platform/audit.js';
import { clock } from '../../platform/clock.js';
import { actorOf, type Ctx } from '../../platform/context.js';
import { forbidden, notFound } from '../../platform/errors.js';
import { publish } from '../../platform/outbox.js';
import { requireCap } from '../../platform/rbac.js';
import { listStaff } from '../people/service.js';

export const METRIC_LABEL: Record<KpiMetric, string> = {
  fr: 'First reply time (min)',
  tat: 'Time to resolve (h)',
  accept: 'Drafts sent unedited (%)',
  reopen: 'Reopen rate (%)',
  auto: 'Handled end-to-end by AI (%)',
  csat: 'CSAT after close (of 5)',
  cost: 'Model spend per query (₹)',
  awo: 'Approved without opening the evidence (%)',
};
const LOWER_IS_BETTER: KpiMetric[] = ['fr', 'tat', 'reopen', 'cost', 'awo'];

async function series(tx: Tx, orgId: string, metric: string): Promise<number[]> {
  const rows = await tx
    .select({ value: s.dailyMetrics.value })
    .from(s.dailyMetrics)
    .where(and(eq(s.dailyMetrics.orgId, orgId), eq(s.dailyMetrics.metric, metric)))
    .orderBy(asc(s.dailyMetrics.day));
  return rows.map((r) => r.value);
}

/** Live values computed from tickets where there is enough data; otherwise the latest weekly point. */
async function liveMetrics(tx: Tx, orgId: string): Promise<Partial<Record<KpiMetric, number>>> {
  const since = new Date(clock.now().getTime() - 7 * 86400_000);
  const out: Partial<Record<KpiMetric, number>> = {};
  const [drafts] = await tx.execute<{ sent: number; unedited: number }>(sql`
    select count(*)::int as sent, count(*) filter (where trim(current_body) = trim(original_body))::int as unedited
      from drafts where org_id = ${orgId} and state = 'sent' and sent_at >= ${since}`).then((r) => r.rows);
  if (drafts && drafts.sent >= 20) out.accept = Math.round((drafts.unedited / drafts.sent) * 100);
  const [awo] = await tx.execute<{ n: number; without: number }>(sql`
    select count(*)::int as n, count(*) filter (where not opened_evidence)::int as without
      from approvals where org_id = ${orgId} and created_at >= ${since}`).then((r) => r.rows);
  if (awo && awo.n >= 20) out.awo = Math.round((awo.without / awo.n) * 100);
  return out;
}

export async function approveWithoutOpenPct(tx: Tx, orgId: string): Promise<number> {
  const [awo] = await tx.execute<{ n: number; without: number }>(sql`
    select count(*)::int as n, count(*) filter (where not opened_evidence)::int as without
      from approvals where org_id = ${orgId} and created_at >= now() - interval '7 days'`).then((r) => r.rows);
  if (awo && awo.n > 0) return Math.round((awo.without / awo.n) * 100);
  const hist = await series(tx, orgId, 'weekly.awo');
  return hist.at(-1) ?? 0;
}

function tile(key: string, label: string, values: number[], unit: string, digest: string, opts: { decimals?: number; bad?: boolean; lowerBetter?: boolean; valueOverride?: string } = {}): KpiTileDTO {
  const last = values.at(-1) ?? 0;
  const first = values[0] ?? last;
  const change = first ? ((last - first) / first) * 100 : 0;
  const pts = opts.decimals === undefined && Math.abs(last) < 10 && key === 'reopen';
  const trendPct = pts ? `${last - first >= 0 ? '+' : '−'}${Math.abs(last - first).toFixed(1)}pt` : `${change >= 0 ? '+' : '−'}${Math.abs(Math.round(change))}%`;
  const good = opts.lowerBetter ? last <= first : last >= first;
  return {
    key,
    label,
    value: opts.valueOverride ?? (opts.decimals !== undefined ? last.toFixed(opts.decimals) : String(last)),
    unit,
    trendPct,
    trendGood: good,
    spark: values,
    digest,
    tone: opts.bad ? 'bad' : 'neutral',
  };
}

export async function performance(tx: Tx, ctx: Ctx): Promise<PerformanceDTO> {
  requireCap(ctx, 'insights.view', 'view performance');
  const orgId = ctx.orgId;
  const fr = await series(tx, orgId, 'weekly.fr');
  const tat = await series(tx, orgId, 'weekly.tat');
  const missed = await series(tx, orgId, 'weekly.missed');
  const reopen = await series(tx, orgId, 'weekly.reopen');
  const [vol] = await tx.execute<{ n: number }>(sql`select coalesce(sum(volume_24h), 0)::int as n from mailboxes where org_id = ${orgId}`).then((r) => r.rows);
  const tiles: KpiTileDTO[] = [
    tile('fr', 'Time to first reply', fr, 'min typical', 'Fell every week for 12 weeks; the floor is now auto-acknowledgement, not people.', { lowerBetter: true }),
    tile('tat', 'Time to fully resolve', tat, 'hours', 'Improvement is flattening — the remaining hours sit in disputes, not in drafting.', { decimals: 1, lowerBetter: true }),
    tile('missed', 'Missed deadlines', missed, `of ${(vol?.n ?? 0).toLocaleString('en-IN')}`, 'All misses this week are disputes tickets past the provisional-credit window.', { bad: true, lowerBetter: true }),
    tile('reopen', 'Reopen rate', reopen, '%', 'Creeping up 8 weeks straight — reopens cluster on fee answers citing the stale schedule.', { decimals: 1, lowerBetter: true }),
  ];

  const qts = await tx
    .select({ q: s.queryTypes, dept: s.departments.name })
    .from(s.queryTypes)
    .leftJoin(s.departments, eq(s.departments.id, s.queryTypes.departmentId))
    .where(and(eq(s.queryTypes.orgId, orgId), eq(s.queryTypes.showOnSpeed, true)))
    .orderBy(asc(s.queryTypes.sort));
  const queryTypes: QueryTypeSpeedDTO[] = qts.map(({ q, dept }) => ({
    id: q.id,
    name: q.name === 'Foreclosure quotes' ? 'Lending & foreclosure' : q.name,
    department: dept ?? 'Unowned',
    lane: (q.name === 'Foreclosure quotes' ? 'manual' : q.defaultLane) as Lane,
    volume: q.monthlyVolume,
    baselineHours: q.baselineHours ?? 0,
    actualHours: q.actualHours ?? 0,
    late: q.lateCount,
    owner: q.ownerLabel,
  }));

  const alertRows = await tx.select().from(s.alerts).where(and(eq(s.alerts.orgId, orgId), isNull(s.alerts.resolvedAt))).orderBy(asc(s.alerts.createdAt));
  const kpiRows = await tx
    .select()
    .from(s.kpis)
    .where(and(eq(s.kpis.orgId, orgId), sql`(${s.kpis.scope} = 'team' or ${s.kpis.ownerId} = ${ctx.user.id})`))
    .orderBy(asc(s.kpis.createdAt));
  const live = await liveMetrics(tx, orgId);
  const metricKeys = Object.keys(METRIC_LABEL) as KpiMetric[];
  const metricSeries = new Map<KpiMetric, number[]>();
  for (const k of metricKeys) {
    const ser = await series(tx, orgId, `weekly.${k === 'tat' ? 'tat' : k}`);
    const v = live[k];
    metricSeries.set(k, v === undefined ? ser : [...ser.slice(0, -1), v]);
  }
  const current = (k: KpiMetric) => metricSeries.get(k)?.at(-1) ?? 0;
  const customKpis: CustomKpiDTO[] = kpiRows.map((k) => {
    const metric = k.metric as KpiMetric;
    const lower = LOWER_IS_BETTER.includes(metric);
    const cur = current(metric);
    return {
      id: k.id,
      name: k.name,
      metric,
      metricLabel: METRIC_LABEL[metric],
      viz: k.viz as 'bars' | 'number',
      scope: k.scope as 'team' | 'me',
      target: k.target,
      current: cur,
      lowerIsBetter: lower,
      onTrack: lower ? cur <= k.target : cur >= k.target,
      spark: (metricSeries.get(metric) ?? []).slice(-7),
    };
  });

  return {
    tiles,
    queryTypes,
    alerts: alertRows.map((a) => ({
      id: a.id,
      sevLabel: a.sevLabel,
      sevKind: a.sevKind as 'late' | 'pattern' | 'drift' | 'capacity',
      bucket: a.bucket,
      text: a.text,
      actionLabel: a.actionLabel,
      owner: a.owner,
      at: a.createdAt.toISOString(),
    })),
    staff: await listStaff(tx, ctx),
    customKpis,
    metrics: metricKeys.map((k) => ({ key: k, label: METRIC_LABEL[k], current: current(k) })),
    approveWithoutOpenPct: await approveWithoutOpenPct(tx, orgId),
  };
}

export async function results(tx: Tx, ctx: Ctx): Promise<ResultsDTO> {
  requireCap(ctx, 'insights.view', 'view results');
  const baseline = await series(tx, ctx.orgId, 'exec.baseline');
  const actual = await series(tx, ctx.orgId, 'exec.actual');
  const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
  const baselineHours = Math.round(avg(baseline) * 10) / 10;
  const nowHours = actual.at(-1) ?? 0;
  // Coverage is derived from the taxonomy: each query type's monthly volume by its handling lane.
  const cov = await tx.execute<{ lane: Lane; volume: number }>(sql`
    select default_lane as lane, sum(monthly_volume)::int as volume from query_types
     where org_id = ${ctx.orgId} and show_on_map group by default_lane`);
  const total = cov.rows.reduce((a, r) => a + r.volume, 0) || 1;
  const lanes: Lane[] = ['auto', 'draft', 'manual'];
  return {
    baselineHours,
    nowHours,
    deltaPct: baselineHours ? Math.round(((nowHours - baselineHours) / baselineHours) * 100) : 0,
    capacityMultiple: nowHours ? Math.round((baselineHours / nowHours) * 10) / 10 : 0,
    days: baseline.map((b, i) => ({ label: `d${i + 1}`, baseline: b, actual: actual[i] ?? 0 })),
    coverage: lanes.map((lane) => {
      const v = cov.rows.find((r) => r.lane === lane)?.volume ?? 0;
      return { lane, pct: Math.round((v / total) * 100), volume: v * 5 };
    }),
    phases: [
      { n: 1, label: 'Drafts only', scope: 'The AI writes, a human sends every reply', state: 'Complete', current: false },
      { n: 2, label: 'Actions that can be undone', scope: 'Statements, certificates, cheque books', state: 'Live now', current: true },
      { n: 3, label: 'Actions where money moves', scope: 'Fee reversals and provisional credit, with approval', state: 'Next · gated on Risk', current: false },
      { n: 4, label: 'Wider autonomy', scope: 'One risk group at a time, approval everywhere else', state: 'Gated on Risk', current: false },
    ],
    pools: [
      { label: 'Capacity released', metric: `${nowHours ? (baselineHours / nowHours).toFixed(1) : '—'}× per FTE`, note: 'Same headcount, more queries closed per person per day at the current coverage mix.' },
      { label: 'Complaint deflection', metric: '−38% repeat contacts', note: 'Second and third emails on the same thread fall sharply once first response drops under 15 minutes.' },
      { label: 'Audit position', metric: '100% traced', note: 'Every suggestion, approval, edit and execution carries an actor, a timestamp and a source. No silent automation.' },
    ],
  };
}

export async function createKpi(tx: Tx, ctx: Ctx, body: KpiBody): Promise<void> {
  requireCap(ctx, 'kpi.manage', 'create a KPI');
  await tx.insert(s.kpis).values({ orgId: ctx.orgId, ownerId: ctx.user.id, ...body });
  await audit(tx, ctx.orgId, { actor: actorOf(ctx), action: 'kpi.created', entity: 'kpi', summary: `KPI "${body.name}" created on the ${body.scope === 'team' ? 'team' : 'personal'} dashboard` });
}

export async function deleteKpi(tx: Tx, ctx: Ctx, id: string): Promise<void> {
  const [k] = await tx.select().from(s.kpis).where(and(eq(s.kpis.orgId, ctx.orgId), eq(s.kpis.id, id)));
  if (!k) throw notFound('KPI');
  if (k.ownerId !== ctx.user.id && !ctx.capabilities.has('kpi.manage')) throw forbidden('Only the owner or a team lead can remove this KPI.');
  await tx.delete(s.kpis).where(eq(s.kpis.id, id));
}

export async function actOnAlert(tx: Tx, ctx: Ctx, id: string, mode: 'act' | 'notify'): Promise<{ message: string }> {
  requireCap(ctx, 'insights.view', 'act on alerts');
  const [a] = await tx.select().from(s.alerts).where(and(eq(s.alerts.orgId, ctx.orgId), eq(s.alerts.id, id)));
  if (!a) throw notFound('Alert');
  if (mode === 'act') {
    await tx.update(s.alerts).set({ resolvedAt: clock.now(), resolvedBy: ctx.user.id }).where(eq(s.alerts.id, id));
  }
  await tx.insert(s.notifications).values({
    orgId: ctx.orgId,
    kind: 'message',
    source: `${ctx.user.name} · alert`,
    title: mode === 'act' ? `${a.actionLabel} — ${a.bucket}` : `Heads-up for ${a.owner}: ${a.bucket}`,
    body: a.text,
    urgent: a.sevKind === 'late',
    createdBy: ctx.user.id,
  });
  await audit(tx, ctx.orgId, {
    actor: actorOf(ctx),
    action: mode === 'act' ? 'alert.actioned' : 'alert.notified',
    entity: 'alert',
    entityId: id,
    summary: mode === 'act' ? `${a.actionLabel} — ${a.bucket} alert cleared` : `${a.owner} notified about ${a.bucket.toLowerCase()}`,
    feed: { tone: 'info', meta: 'performance' },
  });
  await publish(tx, ctx.orgId, 'activity.created', {});
  await publish(tx, ctx.orgId, 'notification.created', {});
  return { message: mode === 'act' ? `${a.actionLabel} — done. ${a.bucket} alert cleared.` : `${a.owner} notified about ${a.bucket.toLowerCase()}.` };
}

export async function activity(tx: Tx, ctx: Ctx, limit = 20): Promise<ActivityDTO[]> {
  const rows = await tx
    .select({ e: s.auditEvents, number: s.tickets.number })
    .from(s.auditEvents)
    .leftJoin(s.tickets, eq(s.tickets.id, s.auditEvents.ticketId))
    .where(and(eq(s.auditEvents.orgId, ctx.orgId), isNotNull(s.auditEvents.feedTone)))
    .orderBy(desc(s.auditEvents.seq))
    .limit(limit);
  return rows.map(({ e, number }) => ({
    id: e.id,
    text: e.summary,
    meta: e.feedMeta ?? '',
    tone: e.feedTone as ActivityDTO['tone'],
    at: e.at.toISOString(),
    ticketNumber: number ? `QRY-${number}` : null,
  }));
}

export async function shift(tx: Tx, ctx: Ctx): Promise<ShiftDTO> {
  const start = new Date(clock.now());
  start.setHours(0, 0, 0, 0);
  const [r] = await tx.execute<{ closed: number; ai: number; drafts: number; missed: number }>(sql`
    select
      (select count(*) from tickets where org_id = ${ctx.orgId} and resolved_at >= ${start})::int as closed,
      (select count(*) from tickets where org_id = ${ctx.orgId} and resolved_at >= ${start} and owner_kind = 'ai')::int as ai,
      (select count(*) from drafts where org_id = ${ctx.orgId} and state = 'sent' and sent_at >= ${start})::int as drafts,
      (select count(*) from tickets where org_id = ${ctx.orgId} and status not in ('resolved','closed','waiting_customer') and due_at < now())::int as missed`).then((x) => x.rows);
  const accept = await series(tx, ctx.orgId, 'weekly.accept');
  const live = await liveMetrics(tx, ctx.orgId);
  // Work closed on other channels today and the time-and-motion baseline arrive with the daily import.
  const today = clock.now().toISOString().slice(0, 10);
  const imported = await tx
    .select({ metric: s.dailyMetrics.metric, value: s.dailyMetrics.value })
    .from(s.dailyMetrics)
    .where(and(eq(s.dailyMetrics.orgId, ctx.orgId), eq(s.dailyMetrics.day, today), sql`${s.dailyMetrics.metric} like 'shift.%'`));
  const imp = (k: string) => imported.find((m) => m.metric === k)?.value ?? 0;
  return {
    closedToday: imp('shift.closed_imported') + (r?.closed ?? 0),
    sentAsDraftedPct: live.accept ?? accept.at(-1) ?? 0,
    // 12 min per AI-resolved ticket, 6 min per drafted reply (time-and-motion baseline).
    savedMinutes: imp('shift.saved_imported') + (r?.ai ?? 0) * 12 + (r?.drafts ?? 0) * 6,
    missedDeadlines: r?.missed ?? 0,
  };
}
