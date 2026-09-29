import { useNavigate } from 'react-router-dom';
import { api } from '../../lib/api';
import { keys, useAction, useAdmin, useMe } from '../../lib/queries';
import { toast } from '../../lib/toast';
import { Button, Loadable, Page, PageHeader, Skeleton } from '../../ui';
import s from './mailboxes/Mailboxes.module.css';
import { MailboxConnectionsCard } from './mailboxes/Connections';
import { AuditCard, ConnectorsCard, GuardrailsCard } from './mailboxes/parts';

export default function MailboxesScreen() {
  const q = useAdmin();
  const me = useMe().data;
  const canVerify = !!me?.capabilities.includes('audit.verify');
  const navigate = useNavigate();
  // Demo mode only: send a sample customer email through the real intake → triage pipeline.
  const simulate = useAction(
    () => api.post<{ number: number; created: boolean }>('/v1/dev/simulate-mail', {}),
    {
      invalidate: [keys.inboxAll, keys.ticketsAll, keys.activity, keys.admin, keys.me],
      success: (r) => {
        toast.show(`QRY-${r.number} arrived. The agents are triaging it now.`, {
          label: 'Open ticket',
          run: () => navigate(`/tickets?ticket=QRY-${r.number}`),
        });
        return null;
      },
    },
  );

  return (
    <Page>
      <PageHeader
        title="Where mail arrives"
        subtitle="Which mailboxes the AI reads, which systems it may touch, and what it is forbidden to do."
        actions={
          me?.demoMode ? (
            <Button
              variant="primary"
              onClick={() => simulate.mutate(undefined)}
              loading={simulate.isPending}
              title="Sends a sample customer email through the real intake and triage pipeline"
            >
              Simulate an email
            </Button>
          ) : undefined
        }
      />
      <Loadable
        query={q}
        skeleton={
          <div className={s.grid}>
            <Skeleton h={360} />
            <Skeleton h={480} />
          </div>
        }
      >
        {(a) => (
          <div className={s.grid}>
            <MailboxConnectionsCard canEdit={!!me?.capabilities.includes('setup.edit')} />
            <div className={s.stack}>
              <ConnectorsCard connectors={a.connectors} />
              <GuardrailsCard guardrails={a.guardrails} />
              {canVerify && <AuditCard />}
            </div>
          </div>
        )}
      </Loadable>
    </Page>
  );
}
