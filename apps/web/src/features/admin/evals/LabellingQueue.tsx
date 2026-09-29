/**
 * Label real mail: the deployment's recent tickets, masked, one at a time. A person says what the mail is
 * (category, hard stop), what should happen (lane) and whether the AI's draft would do; the label becomes
 * an eval case tied to the ticket, so a mail is labelled once per dataset. The AI's own answer is shown as
 * a starting point to confirm or correct, never saved without a person choosing.
 */
import type { LabelBody, LabellingQueueDTO } from '@ci/contracts';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../../lib/api';
import { keys } from '../../../lib/queries';
import { Button, Modal, Skeleton } from '../../../ui';
import { Check, ProblemAlert, Select, useInlineAction } from '../bits';
import s from '../admin.module.css';

const labellingKey = (datasetId: string) => ['evals', 'labelling', datasetId] as const;

export function LabellingQueue({
  datasetId,
  open,
  onClose,
}: {
  datasetId: string;
  open: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: labellingKey(datasetId),
    queryFn: () => api.get<LabellingQueueDTO>(`/v1/evals/datasets/${datasetId}/labelling?limit=20`),
    enabled: open,
  });
  const [skipped, setSkipped] = useState<Set<string>>(new Set());
  const candidate = q.data?.candidates.find((c) => !skipped.has(c.ticketId));

  return (
    <Modal
      open={open}
      onClose={onClose}
      width={760}
      title="Label real mail"
      subtitle={
        q.data
          ? `${q.data.labelled} labelled here · ${q.data.test} test / ${q.data.calibration} calibration cases in the dataset`
          : undefined
      }
    >
      {q.isPending ? (
        <Skeleton h={320} />
      ) : q.error ? (
        <ProblemAlert error={q.error} />
      ) : !candidate ? (
        <p className={s.muted}>
          Nothing left to label right now. New mail appears here once it has been triaged.
        </p>
      ) : (
        <CandidateForm
          key={candidate.ticketId}
          data={q.data!}
          ticketId={candidate.ticketId}
          onSkip={() => setSkipped(new Set([...skipped, candidate.ticketId]))}
          onLabelled={(next) => qc.setQueryData(labellingKey(datasetId), next)}
          datasetId={datasetId}
          invalidate={() => void qc.invalidateQueries({ queryKey: keys.evals })}
        />
      )}
    </Modal>
  );
}

function CandidateForm({
  data,
  ticketId,
  datasetId,
  onSkip,
  onLabelled,
  invalidate,
}: {
  data: LabellingQueueDTO;
  ticketId: string;
  datasetId: string;
  onSkip: () => void;
  onLabelled: (next: LabellingQueueDTO) => void;
  invalidate: () => void;
}) {
  const c = data.candidates.find((x) => x.ticketId === ticketId)!;
  const [category, setCategory] = useState('');
  const [hardStop, setHardStop] = useState(c.suggested.hardStop);
  const [lane, setLane] = useState<'' | 'draft' | 'manual'>('');
  const [draftOk, setDraftOk] = useState<'' | 'yes' | 'no'>('');
  const [split, setSplit] = useState<'test' | 'calibration'>(
    data.calibration * 2 < data.test ? 'calibration' : 'test',
  );
  const save = useInlineAction(
    (body: LabelBody) => api.post<LabellingQueueDTO>(`/v1/evals/datasets/${datasetId}/labels`, body),
    { success: `QRY-${c.number} labelled.` },
  );
  const suggestion = data.categories.find((x) => x.key === c.suggested.category);
  const submit = () =>
    save.mutate(
      {
        ticketId: c.ticketId,
        category,
        hardStop,
        lane: lane || null,
        draftAcceptable: draftOk === '' ? null : draftOk === 'yes',
        split,
      },
      {
        onSuccess: (next) => {
          onLabelled(next);
          invalidate();
        },
      },
    );

  return (
    <div className={s.stack}>
      <div className={s.itemCard}>
        <div className={s.itemHead}>
          <span className={s.name}>QRY-{c.number}</span>
          <span className={`${s.muted} ${s.push}`}>Personal data is masked</span>
        </div>
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>{c.subject || '(no subject)'}</div>
        <pre className={s.json} style={{ whiteSpace: 'pre-wrap', maxHeight: 260 }}>
          {c.body || '(no text)'}
        </pre>
        <p className={s.muted} style={{ margin: '6px 0 0', fontSize: 12 }}>
          The AI said: {suggestion?.name ?? 'no category'}
          {c.suggested.hardStop ? ' · hard stop' : ''} · {c.suggested.lane}
        </p>
      </div>
      <div className={s.fields}>
        <label className={s.field}>
          <span className={s.fieldLabel}>What is it?</span>
          <Select value={category} onChange={(e) => setCategory(e.target.value)} data-autofocus>
            <option value="">Choose a category…</option>
            {data.categories.map((x) => (
              <option key={x.key} value={x.key}>
                {x.name}
                {x.key === c.suggested.category ? ' (the AI’s answer)' : ''}
              </option>
            ))}
          </Select>
        </label>
        <label className={s.field}>
          <span className={s.fieldLabel}>What should happen?</span>
          <Select value={lane} onChange={(e) => setLane(e.target.value as typeof lane)}>
            <option value="">Not judged</option>
            <option value="draft">A drafted reply, approved by a person</option>
            <option value="manual">A person handles it from scratch</option>
          </Select>
        </label>
        <label className={s.field}>
          <span className={s.fieldLabel}>Would the AI’s draft do?</span>
          <Select value={draftOk} onChange={(e) => setDraftOk(e.target.value as typeof draftOk)}>
            <option value="">Not judged / no draft</option>
            <option value="yes">Yes, with at most light edits</option>
            <option value="no">No</option>
          </Select>
        </label>
        <label className={s.field}>
          <span className={s.fieldLabel}>Use it for</span>
          <Select value={split} onChange={(e) => setSplit(e.target.value as typeof split)}>
            <option value="test">Test (gates are scored on these)</option>
            <option value="calibration">Calibration (fits confidence)</option>
          </Select>
        </label>
      </div>
      <Check checked={hardStop} onChange={setHardStop}>
        Hard stop: this must go to a person (regulator, legal notice, fraud, vulnerable customer…)
      </Check>
      <ProblemAlert error={save.error} />
      <div className={s.row}>
        <Button variant="ghost" onClick={onSkip}>
          Skip
        </Button>
        <Button
          variant="dark"
          className={s.push}
          disabled={!category}
          loading={save.isPending}
          onClick={submit}
        >
          Save label and next
        </Button>
      </div>
    </div>
  );
}
