/**
 * Mailbox bindings. Every mailbox belongs to exactly one deployment, so a mailbox leaves this deployment
 * only by being bound to another one; binding needs a published version to triage with.
 */
import type { DeploymentDetailDTO, DeploymentDTO } from '@ci/contracts';
import { useState } from 'react';
import { api } from '../../../lib/api';
import { keys, useDeployments } from '../../../lib/queries';
import { Button, Card } from '../../../ui';
import { Check, ProblemAlert, useCaps, useInlineAction } from '../bits';
import s from '../admin.module.css';

export function Mailboxes({ d }: { d: DeploymentDetailDTO }) {
  const can = useCaps();
  const canEdit = can('deployment.edit');
  const all = useDeployments();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const bind = useInlineAction(
    (ids: string[]) =>
      api.put<DeploymentDTO>(`/v1/deployments/${d.id}/mailboxes`, {
        mailboxIds: [...d.mailboxes.map((m) => m.id), ...ids],
      }),
    {
      invalidate: [keys.deployments],
      success: (_r, ids) => `Bound ${ids.length} mailbox${ids.length === 1 ? '' : 'es'} to ${d.name}.`,
    },
  );
  const elsewhere = (all.data ?? [])
    .filter((x) => x.id !== d.id)
    .flatMap((x) => x.mailboxes.map((m) => ({ ...m, owner: x.name })));
  const noLive = d.activeVersionId === null;

  return (
    <Card
      className={s.card}
      title="Mailboxes"
      meta={`${d.mailboxes.length} bound`}
      actions={
        canEdit && elsewhere.length > 0 ? (
          <Button
            size="sm"
            variant="dark"
            disabled={!picked.size || noLive}
            loading={bind.isPending}
            onClick={() => bind.mutate([...picked], { onSuccess: () => setPicked(new Set()) })}
          >
            Bind selected
          </Button>
        ) : undefined
      }
    >
      <div className={s.stack}>
        {d.mailboxes.length ? (
          <ul className={s.blockers} aria-label={`Mailboxes bound to ${d.name}`}>
            {d.mailboxes.map((m) => (
              <li key={m.id} className={s.row}>
                <span className={s.mailbox}>{m.address}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className={s.muted}>No mailbox is categorised by this deployment yet.</p>
        )}
        {d.mailboxes.length > 0 && (
          <p className={s.muted}>
            A mailbox always belongs to one deployment: to move one away, bind it from the other deployment.
          </p>
        )}
        {canEdit && elsewhere.length > 0 && (
          <fieldset className={s.fieldset} disabled={noLive}>
            <legend className={s.fieldLabel} style={{ marginBottom: 6 }}>
              Move here from another deployment
            </legend>
            <div className={s.stack} style={{ gap: 6 }}>
              {elsewhere.map((m) => (
                <Check
                  key={m.id}
                  checked={picked.has(m.id)}
                  onChange={(on) =>
                    setPicked((p) => {
                      const n = new Set(p);
                      if (on) n.add(m.id);
                      else n.delete(m.id);
                      return n;
                    })
                  }
                >
                  <span className="mono">{m.address}</span> <span className={s.muted}>now in {m.owner}</span>
                </Check>
              ))}
            </div>
          </fieldset>
        )}
        {canEdit && noLive && (
          <p className={s.muted}>Publish a version of this deployment before binding mailboxes to it.</p>
        )}
        <ProblemAlert error={bind.error} />
      </div>
    </Card>
  );
}
