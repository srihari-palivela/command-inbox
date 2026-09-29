import type {
  AlertDTO,
  AutoAssignResultDTO,
  CustomKpiDTO,
  KpiTileDTO,
  QueryTypeSpeedDTO,
  StaffDTO,
} from '@ci/contracts';
import { useState } from 'react';
import { api } from '../../lib/api';
import { formatMinutes } from '../../lib/format';
import { LANE_TONE, loadTone } from '../../lib/presentation';
import { keys, useAction, useAutoAssign, usePerformance } from '../../lib/queries';
import { toast } from '../../lib/toast';
import {
  Button,
  Card,
  Dot,
  EmptyState,
  Eyebrow,
  Loadable,
  Meter,
  Page,
  PageHeader,
  Pill,
  Skeleton,
  Spark,
  cx,
} from '../../ui';
import { AVAIL, fmtNum, forbiddenText, Obs, sinceShort, stagger, useCan } from './bits';
import { KpiModal } from './KpiModal';
import s from './Performance.module.css';

/** Approve-without-open rate above this is the rubber-stamping alarm (frontend review §4). */
const AWO_ALERT_PCT = 15;

export default function PerformanceScreen() {
  const q = usePerformance();
  const can = useCan();
  const [kpiOpen, setKpiOpen] = useState(false);
  const canKpi = can('kpi.manage');

  return (
    <Page>
      <PageHeader
        title="Performance"
        subtitle="Are we answering fast enough, and is anyone about to miss a deadline?"
        actions={
          <>
            <Button
              size="sm"
              style={{ color: 'var(--accent)', fontWeight: 600 }}
              onClick={() => setKpiOpen(true)}
              disabled={!canKpi}
              title={
                canKpi
                  ? 'Create a KPI and monitor it from now on'
                  : 'Only a team lead or admin can create KPIs'
              }
            >
              + New KPI
            </Button>
            <span
              className={s.period}
              title="Tiles show the weekly series for the last 12 weeks; the latest week is the big number."
            >
              <span className="mono">12 wks</span> · weekly
            </span>
          </>
        }
      />
      {forbiddenText(q.error) ? (
        <EmptyState
          title="Team lead or admin only"
          text={`${forbiddenText(q.error)} Ask your team lead if you need this view.`}
        />
      ) : (
        <Loadable query={q} skeleton={<PerfSkeleton />}>
          {(d) => (
            <>
              <div className={s.tiles}>
                {d.tiles.map((t, i) => (
                  <Tile key={t.key} t={t} i={i} />
                ))}
              </div>
              <Canary pct={d.approveWithoutOpenPct} />
              {d.customKpis.length > 0 && <CustomKpis list={d.customKpis} canManage={canKpi} />}
              <div className={s.split}>
                <SpeedTable rows={d.queryTypes} />
                <div className={s.side}>
                  <Alerts alerts={d.alerts} />
                  <StaffLoad staff={d.staff} canAssign={can('people.auto_assign')} />
                </div>
              </div>
              <KpiModal open={kpiOpen} onClose={() => setKpiOpen(false)} metrics={d.metrics} />
            </>
          )}
        </Loadable>
      )}
    </Page>
  );
}

function PerfSkeleton() {
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <div className={s.tiles}>
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} h={150} />
        ))}
      </div>
      <div className={s.split}>
        <Skeleton h={420} />
        <Skeleton h={420} />
      </div>
    </div>
  );
}

// ── KPI tiles ────────────────────────────────────────────────────────────────
function sparkColor(t: KpiTileDTO): string {
  if (t.tone === 'bad') return 'var(--bad-soft-bar)';
  if (!t.trendGood) return 'var(--warn-soft-bar)';
  return 'var(--accent)';
}

function Tile({ t, i }: { t: KpiTileDTO; i: number }) {
  return (
    <section className={s.tile} style={stagger(i)} aria-label={t.label}>
      <div className={s.tileLabel}>{t.label}</div>
      <div className={s.tileValueRow}>
        <span
          className={cx('mono', s.tileValue)}
          style={{ color: t.tone === 'bad' ? 'var(--bad)' : 'var(--ink)' }}
        >
          {t.value}
        </span>
        <span className={s.tileUnit}>{t.unit}</span>
        <span
          className={s.tileTrend}
          style={{ color: t.trendGood ? 'var(--ok)' : 'var(--warn)' }}
          title={t.trendGood ? 'Moving the right way over 12 weeks' : 'Moving the wrong way over 12 weeks'}
        >
          {t.trendPct}
          <span className="sr-only">{t.trendGood ? ' (improving)' : ' (worsening)'}</span>
        </span>
      </div>
      <div className={s.tileSpark}>
        <Spark
          values={t.spark}
          color={sparkColor(t)}
          height={22}
          label={`${t.label}, last 12 weeks: ${t.spark.join(', ')}`}
        />
      </div>
      <Obs>{t.digest}</Obs>
    </section>
  );
}

// ── Rubber-stamping canary ───────────────────────────────────────────────────
function Canary({ pct }: { pct: number }) {
  const over = pct > AWO_ALERT_PCT;
  const tone = over
    ? { fg: 'var(--warn)', bg: 'var(--warn-bg)', line: 'var(--warn-line)' }
    : { fg: 'var(--ok)', bg: 'var(--ok-bg-2)', line: 'var(--ok-line)' };
  return (
    <section
      className={s.canary}
      style={{ borderColor: tone.line }}
      aria-label="Approved without opening the evidence"
    >
      <div className={s.canaryMain}>
        <div className={s.canaryLabel}>Approved without opening the evidence</div>
        <div className={s.canaryNote}>
          Canary for rubber-stamping — alert above {AWO_ALERT_PCT}%. Share of approvals in the last 7 days
          where the approver never opened the reasoning, fields or draft.
        </div>
      </div>
      <div className={s.canaryGauge}>
        <Meter
          pct={pct}
          color={over ? 'var(--warn-dot)' : 'var(--ok-dot)'}
          height={6}
          label={`Approved without opening the evidence: ${pct}%`}
        />
        <span
          className={s.canaryMark}
          style={{ left: `${AWO_ALERT_PCT}%` }}
          aria-hidden
          title={`Alert threshold ${AWO_ALERT_PCT}%`}
        />
      </div>
      <span className={cx('mono', s.canaryValue)} style={{ color: over ? 'var(--warn)' : 'var(--ink)' }}>
        {pct}
        <span className={s.canaryPct}>%</span>
      </span>
      <Pill fg={tone.fg} bg={tone.bg}>
        {over ? 'Above alert line' : 'Within limit'}
      </Pill>
    </section>
  );
}

// ── Custom KPIs ──────────────────────────────────────────────────────────────
function CustomKpis({ list, canManage }: { list: CustomKpiDTO[]; canManage: boolean }) {
  const remove = useAction((k: CustomKpiDTO) => api.del(`/v1/kpis/${k.id}`), {
    invalidate: [keys.performance],
    success: (_r, k) =>
      `${k.name} removed from ${k.scope === 'team' ? 'the team dashboard' : 'your dashboard'}.`,
  });
  return (
    <div className={s.customWrap}>
      <Eyebrow style={{ marginBottom: 8 }}>Custom KPIs</Eyebrow>
      <div className={s.customGrid}>
        {list.map((k, i) => {
          const canRemove = canManage || k.scope === 'me';
          const pending = remove.isPending && remove.variables?.id === k.id;
          return (
            <section key={k.id} className={s.custom} style={stagger(i)} aria-label={k.name}>
              <div className={s.customHead}>
                <span className={s.customName} title={k.name}>
                  {k.name}
                </span>
                {canRemove && (
                  <button
                    type="button"
                    className={s.remove}
                    onClick={() => remove.mutate(k)}
                    disabled={pending}
                    aria-label={`Remove ${k.name}`}
                    title="Remove"
                  >
                    ×
                  </button>
                )}
              </div>
              <div className={s.customMetric}>{k.metricLabel}</div>
              <div className={s.customValueRow}>
                <span className={cx('mono', s.customValue)}>{fmtNum(k.current)}</span>
                <span className={cx('mono', s.customTarget)}>
                  target {k.lowerIsBetter ? '≤' : '≥'} {fmtNum(k.target)}
                </span>
                <Pill
                  fg={k.onTrack ? 'var(--ok)' : 'var(--bad-text)'}
                  bg={k.onTrack ? 'var(--ok-bg-2)' : 'var(--bad-bg)'}
                  style={{ marginLeft: 'auto', flex: '0 0 auto' }}
                >
                  {k.onTrack ? 'On track' : 'Off track'}
                </Pill>
              </div>
              {k.viz === 'bars' && k.spark.length > 0 && (
                <div className={s.customSpark}>
                  <Spark
                    values={k.spark}
                    color={k.onTrack ? 'var(--accent)' : 'var(--bad-soft-bar)'}
                    height={20}
                    label={`${k.metricLabel}, last ${k.spark.length} weeks: ${k.spark.join(', ')}`}
                  />
                </div>
              )}
              <Pill
                fg={k.scope === 'team' ? 'var(--accent)' : 'var(--text-2)'}
                bg={k.scope === 'team' ? 'var(--accent-bg)' : 'var(--surface-3)'}
              >
                {k.scope === 'team' ? 'Team dashboard' : 'Just you'}
              </Pill>
            </section>
          );
        })}
      </div>
    </div>
  );
}

// ── Speed by query type ──────────────────────────────────────────────────────
const ratioOf = (r: QueryTypeSpeedDTO) => (r.baselineHours ? (r.actualHours / r.baselineHours) * 100 : 0);
const speedColor = (ratio: number) =>
  ratio <= 30 ? 'var(--ok)' : ratio <= 60 ? 'var(--accent)' : 'var(--warn)';

function speedDigest(rows: QueryTypeSpeedDTO[]): string | null {
  if (!rows.length) return null;
  const sorted = [...rows].sort((a, b) => ratioOf(a) - ratioOf(b));
  const fast = sorted.filter((r) => ratioOf(r) <= 10).map((r) => r.name);
  const slow = sorted.at(-1)!;
  const lateTotal = rows.reduce((a, r) => a + r.late, 0);
  const lateMax = [...rows].sort((a, b) => b.late - a.late)[0]!;
  const parts: string[] = [];
  if (fast.length)
    parts.push(`${fast.join(' and ')} ${fast.length > 1 ? 'are' : 'is'} effectively instant now.`);
  parts.push(`${slow.name} moved least — ${Math.round(ratioOf(slow))}% of its baseline time remains.`);
  if (lateTotal > 0)
    parts.push(`${lateMax.name} has the most late tickets (${lateMax.late} of ${lateTotal}).`);
  else parts.push('Nothing is late right now.');
  return parts.join(' ');
}

function SpeedTable({ rows }: { rows: QueryTypeSpeedDTO[] }) {
  const digest = speedDigest(rows);
  return (
    <Card
      flush
      className={s.speed}
      title="Speed by query type"
      actions={
        <span className={s.legend}>
          <span className={s.legendItem}>
            <span className={s.legendLine} style={{ background: 'var(--disabled)' }} />
            before
          </span>
          <span className={s.legendItem}>
            <span className={s.legendLine} style={{ background: 'var(--accent)' }} />
            now
          </span>
        </span>
      }
    >
      {rows.length === 0 ? (
        <EmptyState
          title="No query types tracked yet"
          text="Query types appear here once they are marked for speed tracking in Who owns what."
        />
      ) : (
        <div role="table" aria-label="Speed by query type">
          <div role="row" className={cx(s.speedRow, s.speedHead)}>
            <span role="columnheader">Query type</span>
            <span role="columnheader">Volume</span>
            <span role="columnheader">Speed vs before</span>
            <span role="columnheader">Late</span>
            <span role="columnheader">Owner</span>
          </div>
          {rows.map((r, i) => {
            const lane = LANE_TONE[r.lane];
            const ratio = ratioOf(r);
            const color = speedColor(ratio);
            return (
              <div role="row" key={r.id} className={s.speedRow} style={stagger(i, 0.04, 0.05)}>
                <div role="cell" className={s.qtCell}>
                  <div className={s.qtName}>
                    <span className={s.lanePill} style={{ color: lane.fg, background: lane.bg }}>
                      {lane.word}
                    </span>
                    <span className={s.qtText}>{r.name}</span>
                  </div>
                  <div className={s.qtDept}>{r.department}</div>
                </div>
                <span role="cell" className={cx('mono', s.vol)}>
                  {r.volume.toLocaleString('en-IN')}
                </span>
                <div
                  role="cell"
                  className={s.bars}
                  aria-label={`${r.baselineHours}h before, ${r.actualHours}h now — ${Math.round(ratio)}% of baseline`}
                >
                  <div className={s.barTrack}>
                    <div className={s.barBase} />
                    <div
                      className={s.barAct}
                      style={{ width: `${Math.max(4, Math.min(100, ratio))}%`, background: color }}
                    />
                  </div>
                  <div className={cx('mono', s.barLabels)}>
                    <span style={{ color: 'var(--muted)' }}>{r.baselineHours.toFixed(1)}h</span>
                    <span style={{ color, fontWeight: 500 }}>{r.actualHours.toFixed(1)}h</span>
                  </div>
                </div>
                <span
                  role="cell"
                  className={cx('mono', s.late)}
                  style={{ color: r.late > 0 ? 'var(--bad)' : 'var(--muted-2)' }}
                >
                  {r.late}
                  {r.late > 0 && <span className="sr-only"> late</span>}
                </span>
                <span role="cell" className={s.owner}>
                  {r.owner}
                </span>
              </div>
            );
          })}
        </div>
      )}
      {digest && <Obs footer>{digest}</Obs>}
    </Card>
  );
}

// ── Agent-raised alerts ──────────────────────────────────────────────────────
const SEV_TONE: Record<AlertDTO['sevKind'], { fg: string; bg: string }> = {
  late: { fg: 'var(--bad)', bg: 'var(--bad-bg)' },
  pattern: { fg: 'var(--warn)', bg: 'var(--warn-bg)' },
  drift: { fg: 'var(--warn)', bg: 'var(--warn-bg)' },
  capacity: { fg: 'var(--accent)', bg: 'var(--accent-bg-2)' },
};

function Alerts({ alerts }: { alerts: AlertDTO[] }) {
  const act = useAction(
    (v: { id: string; mode: 'act' | 'notify' }) =>
      api.post<{ message: string }>(`/v1/alerts/${v.id}/${v.mode}`),
    {
      invalidate: [keys.performance, keys.activity, keys.me],
      success: (r) => r.message,
    },
  );
  // Newest first, as the agent raised them.
  const list = [...alerts].sort((a, b) => b.at.localeCompare(a.at));
  const busy = (id: string, mode: 'act' | 'notify') =>
    act.isPending && act.variables?.id === id && act.variables.mode === mode;
  return (
    <section className={s.alerts} aria-labelledby="alerts-title">
      <header className={s.alertsHead}>
        {list.length > 0 && <Dot color="var(--bad)" size={6} pulse />}
        <h3 id="alerts-title" className={s.alertsTitle}>
          Agent-raised alerts
        </h3>
        <span className={cx('mono', s.alertsCount)}>{list.length} active</span>
      </header>
      {list.length === 0 ? (
        <EmptyState
          title="No active alerts"
          text="The agent raises an alert when a deadline is close, a pattern appears, or confidence drifts."
        />
      ) : (
        list.map((a) => {
          const t = SEV_TONE[a.sevKind];
          return (
            <article key={a.id} className={s.alert}>
              <div className={s.alertTop}>
                <span className={s.sev} style={{ color: t.fg, background: t.bg }}>
                  {a.sevLabel}
                </span>
                <span className={s.alertBucket}>{a.bucket}</span>
                <time
                  className={cx('mono', s.alertWhen)}
                  dateTime={a.at}
                  title={new Date(a.at).toLocaleString('en-GB')}
                >
                  {sinceShort(a.at)}
                </time>
              </div>
              <p className={s.alertText}>{a.text}</p>
              <div className={s.alertActions}>
                <Button
                  size="sm"
                  variant="soft"
                  loading={busy(a.id, 'act')}
                  disabled={act.isPending}
                  onClick={() => act.mutate({ id: a.id, mode: 'act' })}
                >
                  {a.actionLabel}
                </Button>
                <Button
                  size="sm"
                  loading={busy(a.id, 'notify')}
                  disabled={act.isPending}
                  onClick={() => act.mutate({ id: a.id, mode: 'notify' })}
                >
                  Notify {a.owner}
                </Button>
              </div>
            </article>
          );
        })
      )}
    </section>
  );
}

// ── Staff load & availability ────────────────────────────────────────────────
const pctOf = (p: StaffDTO) => Math.round((p.open / Math.max(1, p.capacity)) * 100);

function loadDigest(staff: StaffDTO[]): string | null {
  if (staff.length < 2) return null;
  const over = staff.filter((p) => p.open > p.capacity);
  const byLoad = [...staff].sort((a, b) => pctOf(a) - pctOf(b));
  const lightest = byLoad.find((p) => p.availability === 'available' && p.role === 'staff') ?? byLoad[0]!;
  if (!over.length) {
    const busiest = byLoad.at(-1)!;
    return `Nobody is over capacity; the busiest is ${busiest.name} at ${pctOf(busiest)}%.`;
  }
  return `${over.map((p) => p.name).join(' and ')} ${over.length > 1 ? 'are' : 'is'} over capacity while ${lightest.name} sits at ${pctOf(lightest)}% — auto-assign only moves work to people cleared for it.`;
}

function moveLine(m: AutoAssignResultDTO['moves'][number]): string {
  const left =
    m.minutesLeft === null
      ? 'no deadline'
      : m.minutesLeft < 0
        ? `${formatMinutes(-m.minutesLeft)} late`
        : `${formatMinutes(m.minutesLeft)} left`;
  return m.to
    ? `${m.ticketNumber} (${m.priority}, ${left}) → ${m.to} — ${m.reason}.`
    : `${m.ticketNumber} (${m.priority}, ${left}) — ${m.reason}.`;
}

function StaffLoad({ staff, canAssign }: { staff: StaffDTO[]; canAssign: boolean }) {
  const auto = useAutoAssign();
  const [log, setLog] = useState<AutoAssignResultDTO | null>(null);
  const run = () =>
    auto.mutate(undefined, {
      onSuccess: (r) => {
        setLog(r);
        toast.show(
          `Auto-assign ran: ${r.checked} at-risk ticket${r.checked === 1 ? '' : 's'} checked against availability and clearance.`,
        );
      },
    });
  const digest = loadDigest(staff);
  return (
    <Card
      title="Staff load & availability"
      actions={<span className={s.cardNote}>Synced to calendars &amp; check-ins</span>}
      flush
      className={s.staffCard}
    >
      {staff.length === 0 ? (
        <EmptyState title="No one on this team yet" />
      ) : (
        <ul className={s.staffList}>
          {staff.map((p, i) => {
            const av = AVAIL[p.availability];
            const tone = loadTone(p.open, p.capacity);
            return (
              <li key={p.id}>
                <div className={s.staffTop}>
                  <Dot color={av.dot} size={6} />
                  <span className={s.staffName}>
                    {p.name}
                    {p.isMe && ' (you)'}
                  </span>
                  <span className={s.staffAvail} style={{ color: av.fg }}>
                    {av.label}
                  </span>
                  <span
                    className={cx('mono', s.staffOpen)}
                    style={{ color: tone }}
                    title={`${p.open} open of a capacity of ${p.capacity}`}
                  >
                    {p.open} / {p.capacity}
                  </span>
                </div>
                <Meter
                  pct={pctOf(p)}
                  color={tone}
                  label={`${p.name} load ${pctOf(p)}%`}
                  delay={0.06 + i * 0.05}
                />
                <div className={s.staffNote}>{[p.checkin, p.calendar].filter(Boolean).join(' · ')}</div>
              </li>
            );
          })}
        </ul>
      )}
      {digest && <Obs footer>{digest}</Obs>}
      <div className={s.assign}>
        <div className={s.assignHead}>
          <div style={{ minWidth: 0 }}>
            <div className={s.assignTitle}>Auto-assign at-risk work</div>
            <div className={s.assignText}>
              P1–P2, ageing and near-deadline tickets go to available, cleared staff with the lightest load.
            </div>
          </div>
          <Button
            variant="primary"
            onClick={run}
            loading={auto.isPending}
            disabled={!canAssign}
            title={canAssign ? undefined : 'Only a team lead or admin can run auto-assignment'}
          >
            Run now
          </Button>
        </div>
        {!canAssign && <div className={s.assignText}>Team lead or admin only.</div>}
        {log && (
          <ul className={s.log} aria-label="Auto-assign results" aria-live="polite">
            {log.moves.length === 0 ? (
              <li className={s.logItem}>
                <span className={s.logDot} />
                Nothing at risk is unassigned right now — no moves made.
              </li>
            ) : (
              log.moves.map((m) => (
                <li key={m.ticketNumber} className={s.logItem}>
                  <span className={s.logDot} style={m.to ? undefined : { background: 'var(--warn-dot)' }} />
                  <span>{moveLine(m)}</span>
                </li>
              ))
            )}
          </ul>
        )}
      </div>
    </Card>
  );
}
