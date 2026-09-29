/** Fleet health: the job queue, and each tenant's mail, AI and spend. Aggregates only, no customer content. */
import type { FleetDTO, FleetTenantDTO } from '@ci/contracts';
import { useQuery } from '@tanstack/react-query';
import { Card, EmptyState, Loadable, Page, PageHeader, Skeleton } from '@web/ui';
import { Link } from 'react-router-dom';
import { api } from '../../lib/api';
import { count, when } from '../../lib/presentation';
import { keys } from '../../lib/queries';
import { minorToMajor } from '../../lib/tenant-form';
import { css as s } from '../../ui/bits';
import { StatusTag } from '../tenants/bits';

export default function FleetScreen() {
  const q = useQuery({
    queryKey: keys.fleet,
    queryFn: () => api.get<FleetDTO>('/v1/platform/fleet'),
    refetchInterval: 30_000,
  });
  return (
    <Page>
      <div className="rise">
        <PageHeader
          title="Fleet health"
          subtitle="The job queue and every tenant's mail, AI and spend. Paging runs in Prometheus on the same signals."
        />
      </div>
      <Loadable query={q} skeleton={<Skeleton h={320} />}>
        {(f) => (
          <div style={{ display: 'grid', gap: 14 }}>
            <Card flush className={s.card} title="Job queue" meta={`as of ${when(f.generatedAt)}`}>
              {f.queue.length === 0 ? (
                <EmptyState title="No jobs yet" />
              ) : (
                <div className={s.scroll}>
                  <table className={s.table} aria-label="Job queue">
                    <thead>
                      <tr>
                        <th scope="col">Kind</th>
                        <th scope="col" className={s.num}>
                          Due
                        </th>
                        <th scope="col" className={s.num}>
                          Running
                        </th>
                        <th scope="col" className={s.num}>
                          Failed, 24 h
                        </th>
                        <th scope="col" className={s.num}>
                          Oldest due
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {f.queue.map((q) => (
                        <tr key={q.kind}>
                          <td className="mono">{q.kind}</td>
                          <td className={s.num}>{count(q.due)}</td>
                          <td className={s.num}>{count(q.running)}</td>
                          <td
                            className={s.num}
                            style={q.failed24h ? { color: 'var(--bad-text)' } : undefined}
                          >
                            {count(q.failed24h)}
                          </td>
                          <td
                            className={s.num}
                            style={(q.oldestDueSeconds ?? 0) > 300 ? { color: 'var(--bad-text)' } : undefined}
                          >
                            {q.oldestDueSeconds == null ? '—' : `${count(q.oldestDueSeconds)} s`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
            <Card flush className={s.card} title="Tenants">
              <div className={s.scroll}>
                <table className={s.table} aria-label="Tenant health">
                  <thead>
                    <tr>
                      <th scope="col">Tenant</th>
                      <th scope="col">Status</th>
                      <th scope="col">Mailboxes</th>
                      <th scope="col">Last mail</th>
                      <th scope="col" className={s.num}>
                        Triaged, 24 h
                      </th>
                      <th scope="col" className={s.num}>
                        Fell back
                      </th>
                      <th scope="col" className={s.num}>
                        Open alerts
                      </th>
                      <th scope="col">Spend this month</th>
                    </tr>
                  </thead>
                  <tbody>
                    {f.tenants.map((t) => (
                      <TenantRow key={t.id} t={t} />
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        )}
      </Loadable>
    </Page>
  );
}

function TenantRow({ t }: { t: FleetTenantDTO }) {
  const spendPct = t.spendCapMinor ? Math.round((t.spendMonthMinor / t.spendCapMinor) * 100) : 0;
  const warn = { color: 'var(--bad-text)' };
  return (
    <tr>
      <td>
        <Link to={`/tenants/${t.id}`}>{t.name}</Link>
        {t.siemFailing && <div style={{ fontSize: 11.5, ...warn }}>SIEM delivery failing</div>}
      </td>
      <td>
        <StatusTag status={t.status} />
      </td>
      <td>
        {t.mailboxes}
        {t.mailboxesUnhealthy > 0 && <span style={warn}> · {t.mailboxesUnhealthy} unhealthy</span>}
        {t.streamsExpiring > 0 && <span style={warn}> · {t.streamsExpiring} renewing</span>}
      </td>
      <td>{when(t.lastMailAt)}</td>
      <td className={s.num}>{count(t.triaged24h)}</td>
      <td className={s.num} style={(t.degradedRate24h ?? 0) > 0.1 ? warn : undefined}>
        {t.degradedRate24h == null ? '—' : `${(t.degradedRate24h * 100).toFixed(1)}%`}
      </td>
      <td className={s.num}>{count(t.openAlerts)}</td>
      <td style={spendPct >= 100 ? warn : undefined}>
        {money(t.spendMonthMinor, t.currency, t.locale)} ({spendPct}% of plan)
      </td>
    </tr>
  );
}

function money(minor: number, currency: string, locale: string) {
  try {
    return new Intl.NumberFormat(locale, { style: 'currency', currency, maximumFractionDigits: 0 }).format(
      minorToMajor(minor),
    );
  } catch {
    return `${count(minorToMajor(minor))} ${currency}`;
  }
}
