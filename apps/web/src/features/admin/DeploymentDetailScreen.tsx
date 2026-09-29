/**
 * One deployment: its version history, the selected version's rollout and eval runs, the configuration
 * editor (drafts only) and its mailbox bindings. The selected version and editor section are in the URL.
 */
import type { DeploymentDetailDTO, DeploymentVersionDTO } from '@ci/contracts';
import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../lib/api';
import { ago, clockTime } from '../../lib/format';
import { keys, useDeployment, useEvalRuns } from '../../lib/queries';
import { Button, Card, cx, EmptyState, Loadable, Page, PageHeader, Skeleton } from '../../ui';
import { isForbidden, NoAccess, ProblemAlert, Tone, useCaps, useInlineAction } from './bits';
import { ConfigEditor } from './deployments/ConfigEditor';
import { Mailboxes } from './deployments/Mailboxes';
import { Rollout } from './deployments/Rollout';
import { TestBench } from './deployments/TestBench';
import { RolloutPills } from './DeploymentsScreen';
import { StartRunModal } from './evals/StartRunModal';
import { RUN_STATE, shortHash, VERSION_STATE } from './model';
import s from './admin.module.css';

export default function DeploymentDetailScreen() {
  const { deploymentId } = useParams();
  const q = useDeployment(deploymentId);
  return (
    <Page>
      <nav className={s.crumbs} aria-label="Breadcrumb">
        <Link to="/admin/deployments">Deployments</Link>
        <span aria-hidden>/</span>
        <span>{q.data?.name ?? '…'}</span>
      </nav>
      {isForbidden(q.error) ? (
        <>
          <PageHeader title="Deployment" />
          <NoAccess error={q.error} what="deployments" who="Deployments are managed by admins." />
        </>
      ) : (
        <Loadable
          query={q}
          skeleton={
            <div className={s.stack}>
              <Skeleton h={28} w="30%" />
              <Skeleton h={320} />
            </div>
          }
        >
          {(d) => <Detail d={d} />}
        </Loadable>
      )}
    </Page>
  );
}

function Detail({ d }: { d: DeploymentDetailDTO }) {
  const can = useCaps();
  const canEdit = can('deployment.edit');
  const [params, setParams] = useSearchParams();
  const wanted = params.get('version');
  const fallback =
    d.versions.find((v) => v.state === 'draft') ??
    d.versions.find((v) => v.id === d.activeVersionId) ??
    d.versions[0];
  const selected = d.versions.find((v) => v.id === wanted) ?? fallback;
  const select = (id: string) =>
    setParams(
      (p) => {
        p.set('version', id);
        return p;
      },
      { replace: false },
    );

  const newDraft = useInlineAction(
    () => api.post<DeploymentVersionDTO>(`/v1/deployments/${d.id}/versions`, {}),
    {
      invalidate: [keys.deployments],
      success: (v) => `Draft v${v.version} started from the live version.`,
    },
  );

  return (
    <>
      <div className="rise">
        <PageHeader
          title={d.name}
          subtitle={
            <>
              <span className="mono">{d.key}</span>
              {d.description ? ` · ${d.description}` : ''}
            </>
          }
          actions={
            <>
              <RolloutPills d={d} />
              {canEdit && !d.draftVersionId && (
                <Button
                  variant="dark"
                  loading={newDraft.isPending}
                  onClick={() => newDraft.mutate(undefined, { onSuccess: (v) => select(v.id) })}
                >
                  Start a new draft
                </Button>
              )}
              {canEdit && d.draftVersionId && selected?.id !== d.draftVersionId && (
                <Button onClick={() => select(d.draftVersionId!)}>Open the draft</Button>
              )}
            </>
          }
        />
      </div>
      <ProblemAlert error={newDraft.error} />

      <div className={s.split} style={{ marginTop: 4 }}>
        <div>
          <Card className={s.card} title="Versions" meta={`${d.versions.length}`} flush>
            <ol className={s.versions} aria-label="Version history">
              {d.versions.map((v) => (
                <li key={v.id}>
                  <button
                    type="button"
                    className={cx(s.version, v.id === selected?.id && s.versionOn)}
                    aria-current={v.id === selected?.id ? 'true' : undefined}
                    onClick={() => select(v.id)}
                  >
                    <span className={s.versionHead}>
                      <span className={s.vnum}>v{v.version}</span>
                      <Tone tone={VERSION_STATE[v.state]}>
                        {VERSION_STATE[v.state].label}
                        {v.state === 'canary' && v.canaryPercent !== null ? ` · ${v.canaryPercent}%` : ''}
                      </Tone>
                      <span className={cx(s.mono, s.push, s.muted)}>{shortHash(v.configHash)}</span>
                    </span>
                    {v.notes && <span className={s.sub}>{v.notes}</span>}
                    <span className={s.muted}>{versionLine(v)}</span>
                  </button>
                </li>
              ))}
            </ol>
          </Card>
          <Mailboxes d={d} />
        </div>
        <div>
          {selected ? (
            <>
              <VersionSummary v={selected} />
              <Rollout key={`rollout:${selected.id}`} d={d} v={selected} />
              <VersionRuns key={`runs:${selected.id}`} d={d} v={selected} />
              <ConfigEditor
                key={`${selected.id}:${selected.configHash}`}
                deploymentId={d.id}
                version={selected}
                editable={selected.state === 'draft' && canEdit}
              />
              {canEdit && (
                <TestBench
                  key={`bench:${selected.id}:${selected.configHash}`}
                  deploymentId={d.id}
                  version={selected}
                  hasLive={!!d.activeVersionId && d.activeVersionId !== selected.id}
                />
              )}
            </>
          ) : (
            <Card>
              <EmptyState title="No versions" text="This deployment has no version yet." />
            </Card>
          )}
        </div>
      </div>
    </>
  );
}

function versionLine(v: DeploymentVersionDTO): string {
  if (v.publishedAt && v.state !== 'draft')
    return `published ${ago(v.publishedAt)}${v.publishedBy ? ` by ${v.publishedBy.name}` : ''}`;
  if (v.editedAt) return `edited ${ago(v.editedAt)}${v.editedBy ? ` by ${v.editedBy.name}` : ''}`;
  return `created ${ago(v.createdAt)}${v.createdBy ? ` by ${v.createdBy.name}` : ''}`;
}

function VersionSummary({ v }: { v: DeploymentVersionDTO }) {
  return (
    <Card className={s.card} title={`Version ${v.version}`} meta={<Tone tone={VERSION_STATE[v.state]} />}>
      <dl className={s.meta}>
        <dt>Config hash</dt>
        <dd className={s.mono} title={v.configHash}>
          {v.configHash}
        </dd>
        <dt>Created</dt>
        <dd>
          {clockTime(v.createdAt)}
          {v.createdBy ? ` by ${v.createdBy.name}` : ' by the system'}
        </dd>
        {v.editedAt && (
          <>
            <dt>Last edit</dt>
            <dd>
              {clockTime(v.editedAt)}
              {v.editedBy ? ` by ${v.editedBy.name}` : ''}
            </dd>
          </>
        )}
        {v.publishedAt && (
          <>
            <dt>Published</dt>
            <dd>
              {clockTime(v.publishedAt)}
              {v.publishedBy ? ` by ${v.publishedBy.name}` : ''}
              {v.evalRunId && (
                <>
                  {' · '}
                  <Link to={`/admin/evals/runs/${v.evalRunId}`}>eval evidence</Link>
                </>
              )}
            </dd>
          </>
        )}
        {v.retiredAt && (
          <>
            <dt>Retired</dt>
            <dd>{clockTime(v.retiredAt)}</dd>
          </>
        )}
        {v.notes && (
          <>
            <dt>Notes</dt>
            <dd>{v.notes}</dd>
          </>
        )}
      </dl>
    </Card>
  );
}

function VersionRuns({ d, v }: { d: DeploymentDetailDTO; v: DeploymentVersionDTO }) {
  const can = useCaps();
  const canView = can('evals.view');
  const canRun = can('evals.run');
  const runs = useEvalRuns({ versionId: v.id }, canView);
  const [starting, setStarting] = useState(false);
  if (!canView) return null;
  return (
    <Card
      className={s.card}
      title={`Eval runs on v${v.version}`}
      actions={
        canRun && v.state !== 'retired' ? (
          <Button size="sm" variant="soft" onClick={() => setStarting(true)}>
            Run evals
          </Button>
        ) : undefined
      }
      flush
    >
      <Loadable query={runs} skeleton={<Skeleton h={60} style={{ margin: 12 }} />}>
        {(list) =>
          list.length ? (
            <table className={s.table} aria-label={`Eval runs on v${v.version}`}>
              <thead>
                <tr>
                  <th scope="col">Run</th>
                  <th scope="col">Dataset</th>
                  <th scope="col">Gates</th>
                  <th scope="col">Counts for publishing</th>
                </tr>
              </thead>
              <tbody>
                {list.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <Link to={`/admin/evals/runs/${r.id}`} className={s.row} style={{ gap: 6 }}>
                        <Tone tone={RUN_STATE[r.state]} />
                        <span>{ago(r.createdAt)}</span>
                      </Link>
                    </td>
                    <td>{r.datasetName}</td>
                    <td>
                      {r.gates.length
                        ? `${r.gates.filter((g) => g.passed).length} of ${r.gates.length} passed`
                        : '—'}
                    </td>
                    <td>
                      {r.current ? (
                        r.state === 'passed' ? (
                          <span className={s.pass}>Yes</span>
                        ) : (
                          <span className={s.muted}>No — did not pass</span>
                        )
                      ) : (
                        <span className={s.muted}>No — the config changed after this run</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div style={{ padding: '14px 15px' }} className={s.muted}>
              No eval has scored this version yet.
              {v.state !== 'published' && v.state !== 'retired'
                ? ' A passing run on this exact configuration is needed before it can take real mail.'
                : ''}
            </div>
          )
        }
      </Loadable>
      {canRun && (
        <StartRunModal
          open={starting}
          onClose={() => setStarting(false)}
          deploymentId={d.id}
          versions={d.versions}
          initialVersionId={v.id}
        />
      )}
    </Card>
  );
}
