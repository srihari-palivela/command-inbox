import { useAdmin, useMe } from '../../lib/queries';
import { Loadable, Page, PageHeader, Skeleton } from '../../ui';
import s from './mailboxes/Mailboxes.module.css';
import { AuditCard, ConnectorsCard, GuardrailsCard, MailboxesCard } from './mailboxes/parts';

export default function MailboxesScreen() {
  const q = useAdmin();
  const me = useMe().data;
  const canVerify = !!me?.capabilities.includes('audit.verify');

  return (
    <Page>
      <PageHeader title="Where mail arrives" subtitle="Which mailboxes the AI reads, which systems it may touch, and what it is forbidden to do." />
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
            <MailboxesCard mailboxes={a.mailboxes} />
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
