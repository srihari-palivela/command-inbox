/** Every bank tenant on the platform, with where it is in its lifecycle. */
import type { TenantSummaryDTO } from '@ci/contracts';
import { Button, Card, EmptyState, Loadable, Page, PageHeader, Skeleton } from '@web/ui';
import { Link, useNavigate } from 'react-router-dom';
import { count, day } from '../../lib/presentation';
import { useCan, useTenants } from '../../lib/queries';
import { css as s } from '../../ui/bits';
import { ProvisioningTag, StatusTag } from './bits';

export default function TenantsScreen() {
  const q = useTenants();
  const can = useCan();
  const navigate = useNavigate();
  const canCreate = can('tenants.create');
  return (
    <Page>
      <div className="rise">
        <PageHeader
          title="Tenants"
          subtitle="Each bank is a tenant with its own data key, identity realm and workspace. Create one, let it provision, then invite the bank's first admin."
          actions={
            canCreate ? (
              <Button variant="dark" onClick={() => navigate('/tenants/new')}>
                + New tenant
              </Button>
            ) : undefined
          }
        />
      </div>
      <Loadable query={q} skeleton={<Skeleton h={220} />}>
        {(list) =>
          list.length ? (
            <TenantTable list={list} />
          ) : (
            <Card>
              <EmptyState
                title="No tenants yet"
                text={
                  canCreate
                    ? 'Create the first bank tenant. Provisioning sets up its data key, identity and starter deployment, then invites its first admin.'
                    : 'Tenants appear here once an operator creates them.'
                }
                action={
                  canCreate ? (
                    <Button variant="dark" onClick={() => navigate('/tenants/new')}>
                      + New tenant
                    </Button>
                  ) : undefined
                }
              />
            </Card>
          )
        }
      </Loadable>
    </Page>
  );
}

function TenantTable({ list }: { list: TenantSummaryDTO[] }) {
  return (
    <Card flush className={s.card}>
      <div className={s.scroll}>
        <table className={s.table} aria-label="Tenants">
          <thead>
            <tr>
              <th scope="col">Tenant</th>
              <th scope="col">Status</th>
              <th scope="col">Region</th>
              <th scope="col">Plan</th>
              <th scope="col" className={s.num}>
                Members
              </th>
              <th scope="col" className={s.num}>
                Mailboxes
              </th>
              <th scope="col">Provisioning</th>
              <th scope="col">Created</th>
            </tr>
          </thead>
          <tbody>
            {list.map((t, i) => (
              <tr key={t.id} style={{ animationDelay: `${i * 0.03}s` }}>
                <td>
                  <Link to={`/tenants/${t.id}`} className={s.rowLink}>
                    {t.name}
                  </Link>
                  <div className={`${s.sub} ${s.nowrap} mono`}>{t.slug}</div>
                </td>
                <td>
                  <StatusTag status={t.status} />
                </td>
                <td className={`${s.mono} ${s.nowrap}`}>{t.region || '—'}</td>
                <td>{t.plan}</td>
                <td className={s.num}>
                  {count(t.members)}
                  <div className={s.sub}>
                    {t.admins} {t.admins === 1 ? 'admin' : 'admins'}
                  </div>
                </td>
                <td className={s.num}>{count(t.mailboxes)}</td>
                <td>
                  <ProvisioningTag state={t.provisioning} />
                </td>
                <td className={s.nowrap}>{day(t.createdAt)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
