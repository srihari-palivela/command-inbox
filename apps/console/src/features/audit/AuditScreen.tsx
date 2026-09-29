/** The platform's own audit log: every operator action, hash-chained so tampering shows. */
import { Button, Card, EmptyState, Loadable, Page, PageHeader, Skeleton } from '@web/ui';
import { Link, useSearchParams } from 'react-router-dom';
import { count } from '../../lib/presentation';
import { useAudit, useAuditVerify, useTenants } from '../../lib/queries';
import { css as s, ProblemAlert } from '../../ui/bits';
import { AuditTable } from './AuditTable';

export default function AuditScreen() {
  const [params] = useSearchParams();
  const tenantId = params.get('tenantId') ?? undefined;
  const q = useAudit(tenantId);
  const tenants = useTenants();
  const verify = useAuditVerify();
  const nameOf = (id: string) => tenants.data?.find((t) => t.id === id)?.name;
  const result = verify.data;

  return (
    <Page>
      <div className="rise">
        <PageHeader
          title="Audit log"
          subtitle="Every sign-in and tenant change made in the console, in order. Each entry is chained to the one before it, so an edited or deleted entry breaks the chain."
          actions={
            <Button loading={verify.isPending} onClick={() => verify.mutate()}>
              Verify chain
            </Button>
          }
        />
      </div>
      {result && (
        <div
          className={`${s.notice} ${result.ok ? s.noticeOk : s.noticeBad}`}
          role="status"
          style={{ marginBottom: 13 }}
        >
          {result.ok
            ? `The chain is intact: all ${count(result.events)} entries check out.`
            : `The chain is broken at entry ${result.brokenAt ?? '?'} of ${count(result.events)}. Entries from there on can't be trusted.`}
        </div>
      )}
      {verify.error ? (
        <div style={{ marginBottom: 13 }}>
          <ProblemAlert error={verify.error} />
        </div>
      ) : null}
      {tenantId && (
        <div className={s.row} style={{ marginBottom: 10, fontSize: 12.5 }}>
          <span>
            Showing entries for <b>{nameOf(tenantId) ?? 'one tenant'}</b>.
          </span>
          <Link to="/audit">Show all</Link>
        </div>
      )}
      <Loadable query={q} skeleton={<Skeleton h={260} />}>
        {(events) => (
          <Card flush className={s.card}>
            {events.length ? (
              <AuditTable events={events} showTenant={!tenantId} tenantName={nameOf} />
            ) : (
              <EmptyState
                title="Nothing recorded yet"
                text="Operator sign-ins and tenant changes appear here."
              />
            )}
          </Card>
        )}
      </Loadable>
    </Page>
  );
}
