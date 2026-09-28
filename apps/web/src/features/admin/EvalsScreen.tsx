/**
 * Evals: labelled datasets per deployment (calibration and test splits) and the runs that score a
 * deployment version against them. A passed run on a version's exact configuration is what lets it take
 * real mail. `evals.view` reads; `evals.run` manages datasets and starts runs.
 */
import type { DeploymentDTO, EvalDatasetDTO } from '@ci/contracts';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../lib/api';
import { ago } from '../../lib/format';
import { keys, useDeployment, useDeployments, useEvalDatasets, useEvalRuns } from '../../lib/queries';
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
import { StartRunModal } from './evals/StartRunModal';
import { RUN_STATE, shortHash } from './model';
import s from './admin.module.css';

const TITLE = 'Evals';
const SUB =
  'Labelled mail per deployment. Calibration cases fit the confidence; test cases score the gates. A version takes real mail only after a run on its exact configuration passes every gate.';

export default function EvalsScreen() {
  const deployments = useDeployments();
  return (
    <Page>
      <div className="rise">
        <PageHeader title={TITLE} subtitle={SUB} />
      </div>
      {isForbidden(deployments.error) ? (
        <NoAccess error={deployments.error} what="evals" who="Evals are for team leads and admins." />
      ) : (
        <Loadable query={deployments} skeleton={<Skeleton h={240} />}>
          {(list) =>
            list.length ? (
              <Evals deployments={list} />
            ) : (
              <EmptyState
                title="No deployments"
                text="Evals score deployment versions; there are none yet."
              />
            )
          }
        </Loadable>
      )}
    </Page>
  );
}

function Evals({ deployments }: { deployments: DeploymentDTO[] }) {
  const can = useCaps();
  const canRun = can('evals.run');
  const [params, setParams] = useSearchParams();
  const current = deployments.find((d) => d.id === params.get('deployment')) ?? deployments[0]!;
  const datasets = useEvalDatasets(current.id);
  const runs = useEvalRuns({ deploymentId: current.id });
  const detail = useDeployment(canRun ? current.id : null);
  const [creating, setCreating] = useState(false);
  const [starting, setStarting] = useState(false);

  if (isForbidden(datasets.error))
    return <NoAccess error={datasets.error} what="evals" who="Evals are for team leads and admins." />;

  return (
    <>
      <div className={s.actions} style={{ marginBottom: 13 }}>
        <label className={s.row} style={{ gap: 6 }}>
          <span className={s.fieldLabel}>Deployment</span>
          <Select
            value={current.id}
            onChange={(e) =>
              setParams(
                (p) => {
                  p.set('deployment', e.target.value);
                  return p;
                },
                { replace: true },
              )
            }
          >
            {deployments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </Select>
        </label>
        <Link to={`/admin/deployments/${current.id}`} className={s.muted}>
          Open deployment →
        </Link>
        {canRun && (
          <span className={s.push} style={{ display: 'flex', gap: 8 }}>
            <Button onClick={() => setCreating(true)}>+ New dataset</Button>
            <Button variant="dark" onClick={() => setStarting(true)} disabled={!detail.data}>
              Run evals
            </Button>
          </span>
        )}
      </div>

      <Card className={s.card} title="Datasets" flush>
        <Loadable query={datasets} skeleton={<Skeleton h={80} style={{ margin: 12 }} />}>
          {(list) =>
            list.length ? (
              <DatasetTable list={list} />
            ) : (
              <EmptyState
                title="No datasets for this deployment"
                text={canRun ? 'Create one and add labelled cases.' : 'An admin can create one.'}
              />
            )
          }
        </Loadable>
      </Card>

      <Card className={s.card} title="Runs" meta="newest first" flush>
        <Loadable query={runs} skeleton={<Skeleton h={80} style={{ margin: 12 }} />}>
          {(list) =>
            list.length ? (
              <div className={s.scroll}>
                <table className={s.table} aria-label="Eval runs">
                  <thead>
                    <tr>
                      <th scope="col">Run</th>
                      <th scope="col">Version</th>
                      <th scope="col">Dataset</th>
                      <th scope="col">Gates</th>
                      <th scope="col">Config</th>
                      <th scope="col">By</th>
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
                        <td className={s.mono}>v{r.version}</td>
                        <td>{r.datasetName}</td>
                        <td>
                          {r.gates.length
                            ? `${r.gates.filter((g) => g.passed).length} of ${r.gates.length} passed`
                            : '—'}
                        </td>
                        <td>
                          <span className={s.mono}>{shortHash(r.configHash)}</span>{' '}
                          {!r.current && <span className={s.muted}>(outdated)</span>}
                        </td>
                        <td>{r.createdBy?.name ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="No runs yet" text="Runs appear here as soon as they are started." />
            )
          }
        </Loadable>
      </Card>

      {canRun && <NewDatasetModal open={creating} onClose={() => setCreating(false)} deployment={current} />}
      {canRun && detail.data && (
        <StartRunModal
          key={current.id}
          open={starting}
          onClose={() => setStarting(false)}
          deploymentId={current.id}
          versions={detail.data.versions}
          initialVersionId={detail.data.draftVersionId ?? detail.data.activeVersionId ?? undefined}
        />
      )}
    </>
  );
}

function DatasetTable({ list }: { list: EvalDatasetDTO[] }) {
  return (
    <table className={s.table} aria-label="Datasets">
      <thead>
        <tr>
          <th scope="col">Dataset</th>
          <th scope="col" className={s.num}>
            Cases
          </th>
          <th scope="col" className={s.num}>
            Calibration
          </th>
          <th scope="col" className={s.num}>
            Test
          </th>
          <th scope="col">Snapshot</th>
        </tr>
      </thead>
      <tbody>
        {list.map((d) => (
          <tr key={d.id}>
            <td>
              <Link to={`/admin/evals/datasets/${d.id}`} className={s.rowLink}>
                {d.name}
              </Link>
              {d.description && <div className={s.sub}>{d.description}</div>}
            </td>
            <td className={s.num}>{d.cases}</td>
            <td className={s.num}>{d.splits.calibration}</td>
            <td className={s.num}>{d.splits.test}</td>
            <td className={s.mono} title={d.snapshot}>
              {shortHash(d.snapshot)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function NewDatasetModal({
  open,
  onClose,
  deployment,
}: {
  open: boolean;
  onClose: () => void;
  deployment: DeploymentDTO;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const create = useInlineAction(
    () =>
      api.post<EvalDatasetDTO>('/v1/evals/datasets', {
        deploymentId: deployment.id,
        name,
        description: description || undefined,
      }),
    { invalidate: [keys.evals], success: (d) => `Dataset “${d.name}” created. Add labelled cases to it.` },
  );
  const close = () => {
    create.reset();
    setName('');
    setDescription('');
    onClose();
  };
  return (
    <Modal
      open={open}
      onClose={close}
      title={`New dataset for ${deployment.name}`}
      width={480}
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button
            variant="dark"
            disabled={!name.trim()}
            loading={create.isPending}
            onClick={() => create.mutate(undefined, { onSuccess: close })}
          >
            Create dataset
          </Button>
        </>
      }
    >
      <div className={s.stack}>
        <label className={s.field}>
          <span className={s.fieldLabel}>Name</span>
          <Input value={name} maxLength={120} data-autofocus onChange={(e) => setName(e.target.value)} />
        </label>
        <label className={s.field}>
          <span className={s.fieldLabel}>Description</span>
          <TextArea value={description} maxLength={600} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <ProblemAlert error={create.error} />
      </div>
    </Modal>
  );
}
