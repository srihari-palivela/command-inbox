/**
 * Deployments: each tenant runs one or more mailbox categorisations side by side. The list shows what is
 * live, what is rolling out (shadow, canary) and which mailboxes each one categorises. `deployment.view`
 * reads it; `deployment.edit` creates deployments and drafts; `deployment.publish` rolls them out.
 */
import type { DeploymentDetailDTO, DeploymentDTO } from '@ci/contracts';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, newIdempotencyKey } from '../../lib/api';
import { keys, useDeployments } from '../../lib/queries';
import {
  Button,
  Card,
  EmptyState,
  Input,
  Loadable,
  Modal,
  Page,
  PageHeader,
  Skeleton,
  TextArea,
} from '../../ui';
import { isForbidden, NoAccess, ProblemAlert, Select, Tone, useCaps, useInlineAction } from './bits';
import { slugify, VERSION_STATE } from './model';
import s from './admin.module.css';

const TITLE = 'Deployments';
const SUB =
  'Each deployment categorises its own mailboxes with its own taxonomy, rules and thresholds. Versions are immutable once published and roll out through shadow and canary.';

export default function DeploymentsScreen() {
  const q = useDeployments();
  const can = useCaps();
  const [creating, setCreating] = useState(false);
  const canEdit = can('deployment.edit');
  return (
    <Page>
      <div className="rise">
        <PageHeader
          title={TITLE}
          subtitle={SUB}
          actions={
            canEdit ? (
              <Button variant="dark" onClick={() => setCreating(true)} disabled={!q.data}>
                + New deployment
              </Button>
            ) : undefined
          }
        />
      </div>
      {isForbidden(q.error) ? (
        <NoAccess error={q.error} what="deployments" who="Deployments are managed by admins." />
      ) : (
        <Loadable query={q} skeleton={<Skeleton h={220} />}>
          {(list) => (
            <>
              {!canEdit && (
                <p className={s.muted} style={{ marginBottom: 10 }}>
                  You can look at deployments; only an Admin can change them.
                </p>
              )}
              <DeploymentTable list={list} />
              {canEdit && (
                <NewDeploymentModal open={creating} onClose={() => setCreating(false)} deployments={list} />
              )}
            </>
          )}
        </Loadable>
      )}
    </Page>
  );
}

export function RolloutPills({ d }: { d: DeploymentDTO }) {
  return (
    <span className={s.row} style={{ gap: 5 }}>
      {d.activeVersion !== null ? (
        <Tone tone={VERSION_STATE.published}>Published v{d.activeVersion}</Tone>
      ) : (
        <span className={s.muted}>Nothing published</span>
      )}
      {d.shadowVersion !== null && <Tone tone={VERSION_STATE.shadow}>Shadow v{d.shadowVersion}</Tone>}
      {d.canary && (
        <Tone tone={VERSION_STATE.canary}>
          Canary v{d.canary.version} · {d.canary.percent}%
        </Tone>
      )}
      {d.draftVersionId && <Tone tone={VERSION_STATE.draft}>Draft</Tone>}
    </span>
  );
}

function DeploymentTable({ list }: { list: DeploymentDTO[] }) {
  if (!list.length)
    return (
      <Card>
        <EmptyState title="No deployments yet" text="Every workspace starts with its default deployment." />
      </Card>
    );
  return (
    <Card flush className={s.card}>
      <div className={s.scroll}>
        <table className={s.table} aria-label="Deployments">
          <thead>
            <tr>
              <th scope="col">Deployment</th>
              <th scope="col">Key</th>
              <th scope="col">Live version</th>
              <th scope="col">Rollout</th>
              <th scope="col">Mailboxes</th>
            </tr>
          </thead>
          <tbody>
            {list.map((d, i) => (
              <tr key={d.id} style={{ animationDelay: `${i * 0.03}s` }}>
                <td>
                  <Link to={`/admin/deployments/${d.id}`} className={s.rowLink}>
                    {d.name}
                  </Link>
                  {d.description && <div className={s.sub}>{d.description}</div>}
                </td>
                <td className={s.mono}>{d.key}</td>
                <td className={s.mono}>{d.activeVersion !== null ? `v${d.activeVersion}` : '—'}</td>
                <td>
                  <RolloutPills d={d} />
                </td>
                <td>
                  {d.mailboxes.length ? (
                    <span className={s.mailboxes}>
                      {d.mailboxes.map((m) => (
                        <span key={m.id} className={s.mailbox}>
                          {m.address}
                        </span>
                      ))}
                    </span>
                  ) : (
                    <span className={s.muted}>None bound</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function NewDeploymentModal({
  open,
  onClose,
  deployments,
}: {
  open: boolean;
  onClose: () => void;
  deployments: DeploymentDTO[];
}) {
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [key, setKey] = useState('');
  const [keyTouched, setKeyTouched] = useState(false);
  const [description, setDescription] = useState('');
  const live = deployments.filter((d) => d.activeVersionId);
  const [copyFrom, setCopyFrom] = useState(live.find((d) => d.key === 'default')?.id ?? live[0]?.id ?? '');
  const create = useInlineAction(
    () =>
      api.post<DeploymentDetailDTO>(
        '/v1/deployments',
        { key, name, description: description || undefined, copyFrom: copyFrom || undefined },
        { idempotencyKey: newIdempotencyKey() },
      ),
    {
      invalidate: [keys.deployments],
      success: (d) => `Created ${d.name} with draft v1. Edit it, run the evals, then publish.`,
    },
  );
  const close = () => {
    create.reset();
    onClose();
  };
  return (
    <Modal
      open={open}
      onClose={close}
      title="New deployment"
      subtitle="It starts as draft v1, copied from a live deployment. Nothing changes for customers until it is published."
      width={520}
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button
            variant="dark"
            loading={create.isPending}
            disabled={!name.trim() || !key}
            onClick={() =>
              create.mutate(undefined, { onSuccess: (d) => navigate(`/admin/deployments/${d.id}`) })
            }
          >
            Create draft
          </Button>
        </>
      }
    >
      <form
        className={s.stack}
        onSubmit={(e) => {
          e.preventDefault();
        }}
      >
        <label className={s.field}>
          <span className={s.fieldLabel}>Name</span>
          <Input
            value={name}
            data-autofocus
            maxLength={120}
            onChange={(e) => {
              setName(e.target.value);
              if (!keyTouched) setKey(slugify(e.target.value));
            }}
            placeholder="Trade operations"
          />
        </label>
        <label className={s.field}>
          <span className={s.fieldLabel}>Key</span>
          <Input
            value={key}
            className="mono"
            onChange={(e) => {
              setKeyTouched(true);
              setKey(e.target.value);
            }}
            placeholder="trade_ops"
          />
          <span className={s.fieldHint}>
            Lower case letters, digits and underscores; starts with a letter.
          </span>
        </label>
        <label className={s.field}>
          <span className={s.fieldLabel}>Description</span>
          <TextArea value={description} maxLength={600} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label className={s.field}>
          <span className={s.fieldLabel}>Start from</span>
          <Select value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)}>
            {live.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} (live v{d.activeVersion})
              </option>
            ))}
          </Select>
        </label>
        <ProblemAlert error={create.error} />
      </form>
    </Modal>
  );
}
