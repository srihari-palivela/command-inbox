/**
 * Monitoring for admins and leads: the pipeline, reply times against targets, what happened to drafts,
 * per-version and per-agent quality, spend, knowledge and mailbox health. Every number is counted from
 * records by the API; counts link to the tickets behind them.
 */
import type { MonitoringCountDTO, MonitoringDTO } from '@ci/contracts';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useState } from 'react';
import { api } from '../../lib/api';
import { money, num } from '../../lib/format';
import { keys } from '../../lib/queries';
import { Card, Chip, EmptyState, Loadable, Meter, Page, PageHeader, Skeleton } from '../../ui';
import s from '../admin/admin.module.css';

const RANGES = [7, 30, 90] as const;
const LEVEL_COLOR: Record<string, string> = {
  healthy: 'var(--ok)',
  degraded: 'var(--warn)',
  down: 'var(--bad)',
  unknown: 'var(--muted)',
};

const pct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);

export default function MonitoringScreen() {
  const [days, setDays] = useState<(typeof RANGES)[number]>(7);
  const q = useQuery({
    queryKey: keys.monitoring(days),
    queryFn: () => api.get<MonitoringDTO>(`/v1/insights/monitoring?days=${days}`),
    refetchInterval: 60_000,
  });
  return (
    <Page>
      <PageHeader
        title="Monitoring"
        subtitle="Counted from the records, not estimated. Select a number to see the tickets behind it."
        actions={
          <div role="group" aria-label="Period" style={{ display: 'flex', gap: 6 }}>
            {RANGES.map((d) => (
              <Chip key={d} on={days === d} onClick={() => setDays(d)}>
                {d} days
              </Chip>
            ))}
          </div>
        }
      />
      <Loadable query={q} skeleton={<Skeleton h={480} />}>
        {(m) => <Dashboard m={m} />}
      </Loadable>
    </Page>
  );
}

function Count({ c }: { c: MonitoringCountDTO }) {
  const body = (
    <>
      <div className={s.metricVal}>{num(c.count)}</div>
      <div className={s.metricLbl}>{c.label}</div>
    </>
  );
  return c.href ? (
    <Link to={c.href} className={s.metric} style={{ textDecoration: 'none', color: 'inherit' }}>
      {body}
    </Link>
  ) : (
    <div className={s.metric}>{body}</div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className={s.metric}>
      <div className={s.metricVal}>{value}</div>
      <div className={s.metricLbl}>{label}</div>
    </div>
  );
}

function Dashboard({ m }: { m: MonitoringDTO }) {
  const received = m.funnel.find((f) => f.key === 'received')?.count || 0;
  const spendPct = m.spend.capMinor ? (m.spend.monthMinor / m.spend.capMinor) * 100 : 0;
  return (
    <div className={s.stack}>
      <Card title="Pipeline" meta={`last ${m.days} days`}>
        <div className={s.metrics}>
          {m.funnel.map((c) => (
            <Count key={c.key} c={c} />
          ))}
        </div>
        {received > 0 && (
          <div style={{ display: 'grid', gap: 6, marginTop: 12 }}>
            {m.funnel.map((c) => (
              <div
                key={c.key}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '180px 1fr 60px',
                  gap: 10,
                  alignItems: 'center',
                  fontSize: 12,
                }}
              >
                <span>{c.label}</span>
                <Meter
                  pct={(c.count / received) * 100}
                  color="var(--accent)"
                  label={`${c.label}: ${c.count} of ${received}`}
                />
                <span className="mono" style={{ textAlign: 'right' }}>
                  {Math.round((c.count / received) * 100)}%
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>

      <div className={s.two}>
        <Card title="Reply times" meta="against your targets">
          <div className={s.metrics}>
            <Stat
              label="First reply (median)"
              value={m.sla.firstReplyMedianMin == null ? '—' : `${m.sla.firstReplyMedianMin} min`}
            />
            <Stat
              label="Resolved in (median)"
              value={m.sla.resolveMedianHours == null ? '—' : `${m.sla.resolveMedianHours} h`}
            />
            <Count c={m.sla.breached} />
            <Count c={m.sla.atRisk} />
          </div>
          {m.sla.byPriority.length > 0 && (
            <table className={s.table} aria-label="Deadlines by priority" style={{ marginTop: 10 }}>
              <thead>
                <tr>
                  <th scope="col">Priority</th>
                  <th scope="col" className={s.num}>
                    Tickets
                  </th>
                  <th scope="col" className={s.num}>
                    Missed deadline
                  </th>
                </tr>
              </thead>
              <tbody>
                {m.sla.byPriority.map((p) => (
                  <tr key={p.priority}>
                    <td>{p.priority}</td>
                    <td className={s.num}>{p.total}</td>
                    <td className={s.num}>{p.breached}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Drafts" meta="what people did with them">
          <div className={s.metrics}>
            <Stat label="Sent" value={num(m.drafts.sent)} />
            <Stat label="Sent unedited" value={num(m.drafts.unedited)} />
            <Stat label="Edited before sending" value={num(m.drafts.edited)} />
            <Stat label="Sent back" value={num(m.drafts.discarded)} />
            <Stat
              label="Mean edit distance"
              value={m.drafts.meanEditDistance == null ? '—' : m.drafts.meanEditDistance.toFixed(2)}
            />
          </div>
          {m.drafts.rejectReasons.length > 0 && (
            <p className={s.muted} style={{ margin: '10px 0 0', fontSize: 12 }}>
              Why work was sent back:{' '}
              {m.drafts.rejectReasons.map((r) => `${r.reason.replace('_', ' ')} (${r.count})`).join(' · ')}
            </p>
          )}
        </Card>
      </div>

      <Card title="Quality by deployment version" flush>
        {m.versions.length === 0 ? (
          <EmptyState title="No triage runs in this period" />
        ) : (
          <div className={s.scroll}>
            <table className={s.table} aria-label="Quality by deployment version">
              <thead>
                <tr>
                  <th scope="col">Deployment</th>
                  <th scope="col" className={s.num}>
                    Mails
                  </th>
                  <th scope="col" className={s.num}>
                    Asked System 2
                  </th>
                  <th scope="col" className={s.num}>
                    Fell back
                  </th>
                  <th scope="col" className={s.num}>
                    Cost per mail
                  </th>
                </tr>
              </thead>
              <tbody>
                {m.versions.map((v) => (
                  <tr key={`${v.deployment}:${v.version}`}>
                    <td>
                      {v.deployment}
                      {v.version != null && ` v${v.version}`}
                    </td>
                    <td className={s.num}>{v.mails}</td>
                    <td className={s.num}>{pct(v.escalationRate)}</td>
                    <td className={s.num}>{pct(v.degradedRate)}</td>
                    <td className={s.num}>{v.costPerMailMinor == null ? '—' : money(v.costPerMailMinor)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Steps and models" flush>
        <div className={s.scroll}>
          <table className={s.table} aria-label="Steps and models">
            <thead>
              <tr>
                <th scope="col">Step</th>
                <th scope="col">Model</th>
                <th scope="col" className={s.num}>
                  Calls
                </th>
                <th scope="col" className={s.num}>
                  p50
                </th>
                <th scope="col" className={s.num}>
                  p95
                </th>
                <th scope="col" className={s.num}>
                  Flagged
                </th>
                <th scope="col" className={s.num}>
                  Cost
                </th>
              </tr>
            </thead>
            <tbody>
              {m.nodes.map((n) => (
                <tr key={`${n.agent}:${n.model}`}>
                  <td>{n.agent}</td>
                  <td className="mono">{n.model}</td>
                  <td className={s.num}>{n.calls}</td>
                  <td className={s.num}>{n.p50Ms == null ? '—' : `${n.p50Ms} ms`}</td>
                  <td className={s.num}>{n.p95Ms == null ? '—' : `${n.p95Ms} ms`}</td>
                  <td className={s.num}>{n.flagged}</td>
                  <td className={s.num}>{money(n.costMinor)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className={s.two}>
        <Card title="Model spend" meta="this month">
          <div className={s.metrics}>
            <Stat label="Spent" value={money(m.spend.monthMinor)} />
            <Stat
              label="Monthly budget"
              value={m.spend.capMinor == null ? 'No cap' : money(m.spend.capMinor)}
            />
          </div>
          {m.spend.capMinor != null && (
            <div style={{ marginTop: 10 }}>
              <Meter
                pct={spendPct}
                color={spendPct >= 100 ? 'var(--bad)' : spendPct >= 80 ? 'var(--warn)' : 'var(--accent)'}
                label="Share of the monthly budget spent"
              />
            </div>
          )}
        </Card>
        <Card title="Knowledge">
          <div className={s.metrics}>
            <Stat label="Approved documents" value={num(m.knowledge.approved)} />
            <Stat label="Awaiting approval" value={num(m.knowledge.pending)} />
            <Stat label="Expired" value={num(m.knowledge.stale)} />
            <Stat label="Expiring in 14 days" value={num(m.knowledge.expiringSoon)} />
            <Stat label="Open gaps" value={num(m.knowledge.openGaps)} />
          </div>
          {m.knowledge.mostCited.length > 0 && (
            <p className={s.muted} style={{ margin: '10px 0 0', fontSize: 12 }}>
              Most cited: {m.knowledge.mostCited.map((d) => `${d.title} (${d.citations})`).join(' · ')}
            </p>
          )}
          <p style={{ margin: '8px 0 0', fontSize: 12 }}>
            <Link to="/setup/knowledge">Knowledge →</Link>
          </p>
        </Card>
      </div>

      <Card title="Mailboxes" meta={`${m.openAlerts} open alert${m.openAlerts === 1 ? '' : 's'}`} flush>
        {m.mailboxes.length === 0 ? (
          <EmptyState title="No connected mailboxes" text="Connect one under Where mail arrives." />
        ) : (
          <table className={s.table} aria-label="Mailbox health">
            <thead>
              <tr>
                <th scope="col">Mailbox</th>
                <th scope="col">Health</th>
                <th scope="col" className={s.num}>
                  Arrival lag
                </th>
                <th scope="col" className={s.num}>
                  Mail, 24 h
                </th>
              </tr>
            </thead>
            <tbody>
              {m.mailboxes.map((b) => (
                <tr key={b.id}>
                  <td>
                    <Link to="/setup/mailboxes">{b.address}</Link>
                  </td>
                  <td style={{ color: LEVEL_COLOR[b.level] ?? 'inherit' }}>{b.level}</td>
                  <td className={s.num}>{b.lagSeconds == null ? '—' : `${b.lagSeconds} s`}</td>
                  <td className={s.num}>{b.messages24h}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
