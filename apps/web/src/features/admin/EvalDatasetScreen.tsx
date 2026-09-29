/**
 * One eval dataset: its labelled cases by split. Cases are archived rather than deleted, so finished runs
 * keep their evidence; any change moves the dataset's snapshot hash.
 */
import type { EvalCaseDTO, EvalDatasetDTO, EvalSplit } from '@ci/contracts';
import { useMemo, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../lib/api';
import { keys, useDeployment, useEvalCases, useEvalDataset } from '../../lib/queries';
import {
  Button,
  Card,
  Chip,
  EmptyState,
  Input,
  Loadable,
  Modal,
  Page,
  PageHeader,
  Skeleton,
  TextArea,
} from '../../ui';
import {
  Check,
  Confirm,
  isForbidden,
  NoAccess,
  ProblemAlert,
  Select,
  useCaps,
  useInlineAction,
} from './bits';
import { asConfig, listFromText, shortHash } from './model';
import { LabellingQueue } from './evals/LabellingQueue';
import s from './admin.module.css';

type SplitFilter = 'all' | EvalSplit;

export default function EvalDatasetScreen() {
  const { datasetId } = useParams();
  const q = useEvalDataset(datasetId);
  return (
    <Page>
      <nav className={s.crumbs} aria-label="Breadcrumb">
        <Link to="/admin/evals">Evals</Link>
        <span aria-hidden>/</span>
        <span>{q.data?.name ?? '…'}</span>
      </nav>
      {isForbidden(q.error) ? (
        <>
          <PageHeader title="Eval dataset" />
          <NoAccess error={q.error} what="evals" who="Evals are for team leads and admins." />
        </>
      ) : (
        <Loadable query={q} skeleton={<Skeleton h={300} />}>
          {(d) => <Dataset d={d} />}
        </Loadable>
      )}
    </Page>
  );
}

/** Category keys the deployment knows (live and draft), with their names, for labelling cases. */
function useCategories(deploymentId: string | null) {
  const dep = useDeployment(deploymentId);
  return useMemo(() => {
    const out = new Map<string, string>();
    for (const v of dep.data?.versions ?? []) {
      if (v.id !== dep.data?.activeVersionId && v.state !== 'draft') continue;
      for (const c of asConfig(v.config).taxonomy?.categories ?? [])
        if (c.key) out.set(c.key, c.name || c.key);
    }
    return out;
  }, [dep.data]);
}

function Dataset({ d }: { d: EvalDatasetDTO }) {
  const can = useCaps();
  const canRun = can('evals.run');
  const cases = useEvalCases(d.id);
  const categories = useCategories(d.deploymentId);
  const [params, setParams] = useSearchParams();
  const split = (params.get('split') as SplitFilter | null) ?? 'all';
  const setSplit = (v: SplitFilter) =>
    setParams(
      (p) => {
        if (v === 'all') p.delete('split');
        else p.set('split', v);
        return p;
      },
      { replace: true },
    );
  const [editing, setEditing] = useState<EvalCaseDTO | 'new' | null>(null);
  const [archiving, setArchiving] = useState<EvalCaseDTO | null>(null);
  const [labelling, setLabelling] = useState(false);
  const archive = useInlineAction((c: EvalCaseDTO) => api.del(`/v1/evals/cases/${c.id}`), {
    invalidate: [keys.evals],
    success: 'Case archived. Earlier runs keep it as evidence.',
  });

  return (
    <>
      <div className="rise">
        <PageHeader
          title={d.name}
          subtitle={d.description || undefined}
          actions={
            canRun ? (
              <>
                {d.deploymentId && <Button onClick={() => setLabelling(true)}>Label real mail</Button>}
                <Button variant="dark" onClick={() => setEditing('new')}>
                  + Add case
                </Button>
              </>
            ) : undefined
          }
        />
      </div>
      {labelling && <LabellingQueue datasetId={d.id} open onClose={() => setLabelling(false)} />}
      <div className={s.filters} style={{ marginBottom: 12 }} role="group" aria-label="Split">
        <Chip on={split === 'all'} count={d.cases} onClick={() => setSplit('all')}>
          All
        </Chip>
        <Chip
          on={split === 'calibration'}
          count={d.splits.calibration}
          onClick={() => setSplit('calibration')}
        >
          Calibration
        </Chip>
        <Chip on={split === 'test'} count={d.splits.test} onClick={() => setSplit('test')}>
          Test
        </Chip>
        <span className={`${s.muted} ${s.push}`}>
          Snapshot <span className={s.mono}>{shortHash(d.snapshot)}</span>
        </span>
      </div>
      <Card className={s.card} flush>
        <Loadable query={cases} skeleton={<Skeleton h={200} style={{ margin: 12 }} />}>
          {(all) => {
            const list = split === 'all' ? all : all.filter((c) => c.split === split);
            if (!list.length)
              return <EmptyState title="No cases here" text={canRun ? 'Add a labelled case.' : undefined} />;
            return (
              <div className={s.scroll}>
                <table className={s.table} aria-label="Cases">
                  <thead>
                    <tr>
                      <th scope="col">Mail</th>
                      <th scope="col">Expected category</th>
                      <th scope="col">Hard stop</th>
                      <th scope="col">Split</th>
                      <th scope="col">Tags</th>
                      {canRun && (
                        <th scope="col">
                          <span className="sr-only">Actions</span>
                        </th>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((c) => (
                      <tr key={c.id}>
                        <td style={{ maxWidth: 420 }}>
                          <div className={s.name}>{c.input.subject || '(no subject)'}</div>
                          <div className={s.sub}>
                            {c.input.fromEmail ?? 'unknown sender'} · {c.source}
                          </div>
                        </td>
                        <td>
                          {categories.get(c.expected.category) ?? c.expected.category}
                          <div className={s.sub}>
                            <span className={s.mono}>{c.expected.category}</span>
                          </div>
                        </td>
                        <td>{c.expected.hardStop ? 'Yes' : '—'}</td>
                        <td>{c.split === 'test' ? 'Test' : 'Calibration'}</td>
                        <td className={s.muted}>{c.tags.join(', ') || '—'}</td>
                        {canRun && (
                          <td style={{ whiteSpace: 'nowrap' }}>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => setEditing(c)}
                              aria-label={`Edit case “${c.input.subject}”`}
                            >
                              Edit
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => setArchiving(c)}
                              aria-label={`Archive case “${c.input.subject}”`}
                            >
                              Archive
                            </Button>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          }}
        </Loadable>
      </Card>
      {canRun && editing && (
        <CaseModal
          datasetId={d.id}
          initial={editing === 'new' ? null : editing}
          categories={categories}
          onClose={() => setEditing(null)}
        />
      )}
      <Confirm
        open={!!archiving}
        title="Archive this case?"
        confirmLabel="Archive case"
        danger
        onClose={() => {
          archive.reset();
          setArchiving(null);
        }}
        pending={archive.isPending}
        error={archive.error}
        onConfirm={() => archiving && archive.mutate(archiving, { onSuccess: () => setArchiving(null) })}
      >
        “{archiving?.input.subject}” leaves the dataset. Runs that already scored it keep it as evidence; the
        dataset’s snapshot changes, so the next run is scored without it.
      </Confirm>
    </>
  );
}

function CaseModal({
  datasetId,
  initial,
  categories,
  onClose,
}: {
  datasetId: string;
  initial: EvalCaseDTO | null;
  categories: Map<string, string>;
  onClose: () => void;
}) {
  const [subject, setSubject] = useState(initial?.input.subject ?? '');
  const [body, setBody] = useState(initial?.input.body ?? '');
  const [fromEmail, setFromEmail] = useState(initial?.input.fromEmail ?? '');
  const [category, setCategory] = useState(initial?.expected.category ?? [...categories.keys()][0] ?? '');
  const [hardStop, setHardStop] = useState(initial?.expected.hardStop ?? false);
  const [split, setSplit] = useState<EvalSplit>(initial?.split ?? 'test');
  const [tags, setTags] = useState((initial?.tags ?? []).join(', '));
  const payload = () => ({
    input: { subject, body, fromEmail: fromEmail.trim() || undefined },
    expected: { category, hardStop },
    split,
    tags: listFromText(tags),
  });
  const save = useInlineAction(
    (): Promise<unknown> =>
      initial
        ? api.put<EvalCaseDTO>(`/v1/evals/cases/${initial.id}`, payload())
        : api.post<EvalCaseDTO[]>(`/v1/evals/datasets/${datasetId}/cases`, { cases: [payload()] }),
    { invalidate: [keys.evals], success: initial ? 'Case updated.' : 'Case added.' },
  );
  const known = categories.size > 0 && (!category || categories.has(category));
  return (
    <Modal
      open
      onClose={onClose}
      title={initial ? 'Edit case' : 'Add case'}
      subtitle="A labelled mail: what it says, and the category and hard-stop verdict it should get."
      width={600}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="dark"
            loading={save.isPending}
            disabled={!category || (!subject.trim() && !body.trim())}
            onClick={() => save.mutate(undefined, { onSuccess: onClose })}
          >
            {initial ? 'Save case' : 'Add case'}
          </Button>
        </>
      }
    >
      <div className={s.stack}>
        <label className={s.field}>
          <span className={s.fieldLabel}>Subject</span>
          <Input
            value={subject}
            maxLength={300}
            data-autofocus
            onChange={(e) => setSubject(e.target.value)}
          />
        </label>
        <label className={s.field}>
          <span className={s.fieldLabel}>Body</span>
          <TextArea value={body} maxLength={20000} onChange={(e) => setBody(e.target.value)} />
        </label>
        <div className={s.fields}>
          <label className={s.field}>
            <span className={s.fieldLabel}>From (optional)</span>
            <Input type="email" value={fromEmail} onChange={(e) => setFromEmail(e.target.value)} />
          </label>
          <label className={s.field}>
            <span className={s.fieldLabel}>Expected category</span>
            {known ? (
              <Select value={category} onChange={(e) => setCategory(e.target.value)}>
                {[...categories].map(([k, name]) => (
                  <option key={k} value={k}>
                    {name}
                  </option>
                ))}
              </Select>
            ) : (
              <Input className="mono" value={category} onChange={(e) => setCategory(e.target.value)} />
            )}
          </label>
          <label className={s.field}>
            <span className={s.fieldLabel}>Split</span>
            <Select value={split} onChange={(e) => setSplit(e.target.value as EvalSplit)}>
              <option value="test">Test — scores the gates</option>
              <option value="calibration">Calibration — fits confidence</option>
            </Select>
          </label>
          <label className={s.field}>
            <span className={s.fieldLabel}>Tags</span>
            <Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="comma, separated" />
          </label>
        </div>
        <Check checked={hardStop} onChange={setHardStop}>
          A hard stop should fire on this mail (it must reach a person)
        </Check>
        <ProblemAlert error={save.error} />
      </div>
    </Modal>
  );
}
