/** Start an eval run: a deployment version scored on one of its datasets (frozen at start). */
import type { DeploymentVersionDTO, EvalRunDTO, ModelPolicyDTO, ModelProviderKey } from '@ci/contracts';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, newIdempotencyKey } from '../../../lib/api';
import { keys, useEvalDatasets } from '../../../lib/queries';
import { Button, Modal } from '../../../ui';
import { ProblemAlert, Select, useInlineAction } from '../bits';
import s from '../admin.module.css';

export function StartRunModal({
  open,
  onClose,
  deploymentId,
  versions,
  initialVersionId,
}: {
  open: boolean;
  onClose: () => void;
  deploymentId: string;
  versions: DeploymentVersionDTO[];
  initialVersionId?: string;
}) {
  const navigate = useNavigate();
  const datasets = useEvalDatasets(open ? deploymentId : null);
  const runnable = versions.filter((v) => v.state !== 'retired');
  const [versionId, setVersionId] = useState(initialVersionId ?? runnable[0]?.id ?? '');
  const [datasetId, setDatasetId] = useState('');
  const [provider, setProvider] = useState<ModelProviderKey | ''>('');
  const policy = useQuery({
    queryKey: keys.modelPolicy,
    queryFn: () => api.get<ModelPolicyDTO>('/v1/workspace/model-policy'),
    enabled: open,
  }).data;
  const chosenDataset = datasetId || datasets.data?.find((d) => d.splits.test > 0)?.id || '';
  const start = useInlineAction(
    () =>
      api.post<EvalRunDTO>(
        '/v1/evals/runs',
        { deploymentVersionId: versionId, datasetId: chosenDataset, provider: provider || null },
        { idempotencyKey: newIdempotencyKey() },
      ),
    { invalidate: [keys.evals, keys.deployments], success: (r) => `Eval of v${r.version} started.` },
  );
  const close = () => {
    start.reset();
    onClose();
  };
  const noDatasets = datasets.data && datasets.data.length === 0;
  return (
    <Modal
      open={open}
      onClose={close}
      title="Run evals"
      subtitle="The dataset and the version’s configuration hash are frozen when the run starts. Editing the draft afterwards makes the run stop counting for publishing."
      width={500}
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button
            variant="dark"
            loading={start.isPending}
            disabled={!versionId || !chosenDataset}
            onClick={() =>
              start.mutate(undefined, { onSuccess: (r) => navigate(`/admin/evals/runs/${r.id}`) })
            }
          >
            Start run
          </Button>
        </>
      }
    >
      <div className={s.stack}>
        <label className={s.field}>
          <span className={s.fieldLabel}>Version</span>
          <Select value={versionId} onChange={(e) => setVersionId(e.target.value)} data-autofocus>
            {runnable.map((v) => (
              <option key={v.id} value={v.id}>
                v{v.version} · {v.state}
              </option>
            ))}
          </Select>
        </label>
        <label className={s.field}>
          <span className={s.fieldLabel}>Dataset</span>
          <Select
            value={chosenDataset}
            onChange={(e) => setDatasetId(e.target.value)}
            disabled={!datasets.data}
          >
            {(datasets.data ?? []).map((d) => (
              <option key={d.id} value={d.id} disabled={d.splits.test === 0}>
                {d.name} — {d.splits.test} test / {d.splits.calibration} calibration
                {d.splits.test === 0 ? ' (needs test cases)' : ''}
              </option>
            ))}
          </Select>
          {noDatasets && (
            <span className={s.fieldHint}>
              This deployment has no dataset yet. Create one on the Evals screen.
            </span>
          )}
        </label>
        <label className={s.field}>
          <span className={s.fieldLabel}>Language model</span>
          <Select value={provider} onChange={(e) => setProvider(e.target.value as ModelProviderKey | '')}>
            <option value="">As configured — counts for publishing</option>
            {(policy?.providers ?? [])
              .filter((p) => p.allowed)
              .map((p) => (
                <option key={p.key} value={p.key}>
                  All model nodes on {p.name} — a comparison, does not count for publishing
                </option>
              ))}
          </Select>
          <span className={s.fieldHint}>
            Run the same dataset once per provider to compare them: adjudication, grounded drafts, cost and
            latency are recorded on each run.
          </span>
        </label>
        <ProblemAlert error={start.error ?? datasets.error} />
      </div>
    </Modal>
  );
}
