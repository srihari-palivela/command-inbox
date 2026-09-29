/**
 * The test bench: paste a customer mail and see what this version would do — and, side by side, what the
 * live version does now. Nothing is saved (no ticket, no draft); model spend is real and counts against
 * the monthly budget. Personal data stays masked in what comes back.
 */
import type { BenchBody, BenchResultDTO, BenchRunDTO, DeploymentVersionDTO } from '@ci/contracts';
import { useState } from 'react';
import { api } from '../../../lib/api';
import { money } from '../../../lib/format';
import { Button, Card, Field, Input, TextArea } from '../../../ui';
import { Check, ProblemAlert, Tone, useInlineAction } from '../bits';
import { VERSION_STATE } from '../model';
import s from '../admin.module.css';

const LANE_LABEL = { auto: 'Automatic', draft: 'Draft for approval', manual: 'A person handles it' } as const;

export function TestBench({
  deploymentId,
  version,
  hasLive,
}: {
  deploymentId: string;
  version: DeploymentVersionDTO;
  hasLive: boolean;
}) {
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [compare, setCompare] = useState(hasLive);
  const run = useInlineAction((b: BenchBody) =>
    api.post<BenchResultDTO>(`/v1/deployments/${deploymentId}/versions/${version.id}/bench`, b),
  );
  const submit = () =>
    run.mutate({
      subject: subject.trim(),
      body: body.trim(),
      segment: 'Retail',
      compareWith: compare ? 'active' : null,
    });

  return (
    <Card className={s.card} title="Test bench" meta="Nothing is saved">
      <div className={s.stack}>
        <Field label="Subject">
          <Input value={subject} maxLength={300} onChange={(e) => setSubject(e.target.value)} />
        </Field>
        <Field label="Customer's email">
          <TextArea
            rows={5}
            value={body}
            maxLength={20000}
            placeholder="Paste a real (or realistic) customer email…"
            onChange={(e) => setBody(e.target.value)}
          />
        </Field>
        <div className={s.row}>
          {hasLive && (
            <Check checked={compare} onChange={setCompare}>
              Compare with the version handling mail now
            </Check>
          )}
          <Button
            variant="dark"
            className={s.push}
            disabled={!body.trim()}
            loading={run.isPending}
            onClick={submit}
          >
            Run v{version.version}
          </Button>
        </div>
        <ProblemAlert error={run.error} />
        {run.data && (
          <div className={run.data.runs.length > 1 ? s.two : undefined} aria-live="polite">
            {run.data.runs.map((r) => (
              <BenchRun key={r.versionId} r={r} />
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}

function BenchRun({ r }: { r: BenchRunDTO }) {
  return (
    <section className={s.itemCard} aria-label={`Version ${r.version} result`}>
      <div className={s.itemHead}>
        <span className={s.name}>Version {r.version}</span>
        <Tone tone={VERSION_STATE[r.state]} />
        <span className={`${s.muted} ${s.push}`}>
          {money(r.costMinor)} · {(r.latencyMs / 1000).toFixed(1)} s
        </span>
      </div>
      <dl className={s.meta}>
        <dt>Category</dt>
        <dd>
          {r.category ?? '—'} <span className={s.muted}>({r.confidence.toFixed(2)})</span>
        </dd>
        <dt>Lane</dt>
        <dd>
          {LANE_LABEL[r.lane]}
          {r.laneNote && <span className={s.muted}> · {r.laneNote}</span>}
        </dd>
        {r.hardStop && (
          <>
            <dt>Hard stop</dt>
            <dd style={{ color: 'var(--bad-text)' }}>{r.hardStop}</dd>
          </>
        )}
      </dl>
      {r.degraded.length > 0 && (
        <div className={s.notice} style={{ marginTop: 8 }}>
          Fell back to the deterministic model: {r.degraded.join('; ')}
        </div>
      )}
      {r.draft && (
        <div style={{ marginTop: 10 }}>
          <div className={s.fieldLabel}>
            Draft ·{' '}
            {r.draft.coverage === 'full'
              ? 'fully sourced'
              : r.draft.coverage === 'partial'
                ? 'partly sourced'
                : 'no approved source'}
          </div>
          <pre className={s.json} style={{ whiteSpace: 'pre-wrap', maxHeight: 280 }}>
            {r.draft.body}
          </pre>
          {r.draft.citations.length > 0 && (
            <div className={s.muted} style={{ fontSize: 12 }}>
              Sources:{' '}
              {r.draft.citations
                .map((c) => `[${c.n}] ${c.title}${c.section ? ` ${c.section}` : ''}`)
                .join(' · ')}
            </div>
          )}
          {r.draft.flagged.map((f, i) => (
            <div key={i} style={{ fontSize: 12, color: 'var(--warn)' }}>
              ⚑ {f}
            </div>
          ))}
        </div>
      )}
      {r.brief && (
        <div style={{ marginTop: 10 }}>
          <div className={s.fieldLabel}>Brief for the person</div>
          <p style={{ fontSize: 12.5, margin: '4px 0 0' }}>{r.brief.summary}</p>
        </div>
      )}
      {r.fields.length > 0 && (
        <dl className={s.meta} style={{ marginTop: 10 }}>
          {r.fields.map((f) => (
            <FragmentPair
              key={f.label}
              label={f.label}
              value={`${f.value}${f.inferred ? ' (inferred)' : ''}`}
            />
          ))}
        </dl>
      )}
      <details style={{ marginTop: 10 }}>
        <summary className={s.muted} style={{ cursor: 'pointer', fontSize: 12 }}>
          Every step ({r.spans.length})
        </summary>
        <ol style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 12, display: 'grid', gap: 4 }}>
          {r.spans.map((sp, i) => (
            <li key={i}>
              <strong>{sp.agent}</strong> <span className={s.muted}>({sp.model})</span> — {sp.action}:{' '}
              {sp.output}
              <span className={s.muted}>
                {' '}
                · {sp.latencyMs} ms{sp.tokens != null ? ` · ${sp.tokens} tokens` : ''}
                {sp.costMinor ? ` · ${money(sp.costMinor)}` : ''}
              </span>
            </li>
          ))}
        </ol>
      </details>
    </section>
  );
}

function FragmentPair({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}
