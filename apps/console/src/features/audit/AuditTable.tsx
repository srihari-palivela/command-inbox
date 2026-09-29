import type { PlatformAuditEventDTO } from '@ci/contracts';
import { Link } from 'react-router-dom';
import { when } from '../../lib/presentation';
import { css as s } from '../../ui/bits';

export function AuditTable({
  events,
  showTenant = true,
  tenantName,
}: {
  events: PlatformAuditEventDTO[];
  showTenant?: boolean;
  tenantName?: (id: string) => string | undefined;
}) {
  return (
    <div className={s.scroll}>
      <table className={s.table} aria-label="Platform audit events">
        <thead>
          <tr>
            <th scope="col" className={s.num}>
              Seq
            </th>
            <th scope="col">Time</th>
            <th scope="col">Operator</th>
            <th scope="col">Action</th>
            {showTenant && <th scope="col">Tenant</th>}
            <th scope="col">Summary</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.seq}>
              <td className={`${s.num} ${s.mono}`}>{e.seq}</td>
              <td className={s.nowrap}>{when(e.at)}</td>
              <td>{e.operatorEmail}</td>
              <td className={`${s.mono} ${s.nowrap}`}>{e.action}</td>
              {showTenant && (
                <td>
                  {e.tenantId ? (
                    <Link to={`/tenants/${e.tenantId}`}>{tenantName?.(e.tenantId) ?? 'Tenant'}</Link>
                  ) : (
                    <span className={s.muted}>—</span>
                  )}
                </td>
              )}
              <td style={{ color: 'var(--text-2)' }}>{e.summary}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
