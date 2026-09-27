/** The AI's pre-work for each lane: the filled action (Auto), the grounded draft (Draft), the brief (You). */
import type { ActionDTO, BriefDTO, DraftDTO, GateDTO, TicketDetailDTO } from '@ci/contracts';
import { forwardRef, useMemo, useState } from 'react';
import { ago, clockTime } from '../../lib/format';
import { Button, Card, Dot, Eyebrow, Pill, cx } from '../../ui';
import type { TicketActions } from './actions';
import { highlight, wordDiff } from './diff';
import s from './Inbox.module.css';

// ── Filled action ─────────────────────────────────────────────────────────────
export const FilledAction = forwardRef<HTMLElement, { a: ActionDTO }>(function FilledAction({ a }, ref) {
  const approvers = a.chain === 'dual' ? 'needs you and a second approver' : a.chain === 'auto' ? 'runs on its own inside the rules' : 'needs your approval';
  return (
    <section ref={ref} aria-label="Filled action">
      <Card
        title="Filled action"
        meta={<span className="mono">{a.templateCode}</span>}
        actions={
          <>
            <Pill fg={a.reversible ? 'var(--ok)' : 'var(--bad-text)'} bg={a.reversible ? 'var(--ok-bg)' : 'var(--bad-bg)'} line={a.reversible ? 'var(--ok-line)' : 'var(--bad-line)'}>
              {a.reversible ? 'Can be undone' : 'Cannot be undone'}
            </Pill>
            {a.moneyMoves && (
              <Pill fg="var(--warn)" bg="var(--warn-bg)" line="var(--warn-line)">
                Money moves
              </Pill>
            )}
          </>
        }
      >
        <div className={s.actName}>{a.name}</div>
        <div className={s.small}>
          Executes via {a.system} · <span className="mono">{a.endpoint}</span> · {approvers}
        </div>
        <div className={s.fields}>
          {a.fields.map((f) => (
            <div key={f.label} className={s.fieldCell}>
              <div className={s.fieldTop}>
                <span>{f.label}</span>
                <span style={{ color: f.inferred ? 'var(--warn)' : 'var(--ok)' }}>{f.inferred ? 'inferred' : 'verified'}</span>
              </div>
              <div className={s.fieldVal}>{f.value}</div>
              <div className={s.small} style={{ marginTop: 0 }}>
                from {f.source}
              </div>
            </div>
          ))}
        </div>
        {a.validation && (
          <div className={s.check}>
            <span className={s.tick} aria-hidden>
              ✓
            </span>
            <span>{a.validation}</span>
          </div>
        )}
      </Card>
    </section>
  );
});

// ── Draft ─────────────────────────────────────────────────────────────────────
function Cited({ text }: { text: string }) {
  const parts = text.split(/(\[\d+\])/);
  return (
    <>
      {parts.map((p, i) =>
        /^\[\d+\]$/.test(p) ? (
          <span key={i} className="mono" style={{ color: 'var(--accent)', fontSize: 11.5 }}>
            {p}
          </span>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

export const DraftCard = forwardRef<
  HTMLElement,
  { d: DraftDTO; t: TicketDetailDTO; actions: TicketActions; editing: boolean; onEditing: (v: boolean) => void }
>(function DraftCard({ d, t, actions, editing, onEditing }, ref) {
  const edited = d.currentBody.trim() !== d.originalBody.trim();
  const [showDiff, setShowDiff] = useState(true);
  const [text, setText] = useState(d.currentBody);
  const locked = d.state !== 'draft';
  const diff = useMemo(() => (edited && showDiff ? wordDiff(d.originalBody, d.currentBody) : null), [edited, showDiff, d.originalBody, d.currentBody]);
  const paras = useMemo(() => highlight(d.currentBody, d.flagged), [d.currentBody, d.flagged]);

  const startEdit = () => {
    setText(d.currentBody);
    onEditing(true);
  };
  const save = () =>
    actions.saveDraft.mutate(text, {
      onSuccess: () => {
        onEditing(false);
        setShowDiff(true);
      },
    });

  return (
    <section ref={ref} aria-label="Drafted reply">
      <Card
        title="Drafted reply"
        meta={`grounded in ${d.citations.length} verified source${d.citations.length === 1 ? '' : 's'}${d.state === 'sent' && d.sentAt ? ` · sent ${clockTime(d.sentAt)}` : ''}`}
        actions={
          editing ? null : (
            <>
              {edited && (
                <Button size="sm" variant="soft" aria-pressed={showDiff} onClick={() => setShowDiff((v) => !v)}>
                  {showDiff ? 'Showing your edit' : 'Show your edit'}
                </Button>
              )}
              {!locked && t.permissions.canWork && (
                <Button size="sm" onClick={startEdit}>
                  Edit draft
                </Button>
              )}
            </>
          )
        }
      >
        <div className={s.draftHead}>
          <span>
            To <b>{d.to}</b>
          </span>
          <span>
            Subject <b>{d.subject}</b>
          </span>
        </div>
        {editing ? (
          <>
            <label className="sr-only" htmlFor="draft-edit">
              Edit the reply
            </label>
            <textarea id="draft-edit" className={s.editArea} value={text} onChange={(e) => setText(e.target.value)} autoFocus />
            <div className={s.composerFoot}>
              <span className={s.small} style={{ marginTop: 0 }}>
                Your edit is kept next to the AI's version and used to teach the drafter.
              </span>
              <span style={{ flex: 1 }} />
              <Button size="sm" onClick={() => onEditing(false)}>
                Cancel
              </Button>
              <Button size="sm" variant="primary" loading={actions.saveDraft.isPending} disabled={!text.trim() || text === d.currentBody} onClick={save}>
                Save draft
              </Button>
            </div>
          </>
        ) : (
          <div className={s.draftBody}>
            {diff ? (
              <p>
                {diff.map((p, i) => (
                  <span key={i} className={cx(p.op === 'add' && s.add, p.op === 'del' && s.del)}>
                    {p.op === 'del' ? <del>{p.text}</del> : p.op === 'add' ? <ins style={{ textDecoration: 'none' }}>{p.text}</ins> : p.text}
                  </span>
                ))}
              </p>
            ) : (
              paras.map((para, i) => (
                <p key={i}>
                  {para.map((seg, j) =>
                    seg.flag ? (
                      <mark key={j} className={s.flag} title="The policy says this must be stated">
                        {seg.text}
                      </mark>
                    ) : (
                      <Cited key={j} text={seg.text} />
                    ),
                  )}
                </p>
              ))
            )}
          </div>
        )}
        {d.citations.length > 0 && (
          <div className={s.cites}>
            <Eyebrow>Cited sources</Eyebrow>
            {d.citations.map((c) => (
              <div key={c.n} className={s.cite}>
                <span className="mono" style={{ color: 'var(--accent)', fontSize: 11 }}>
                  [{c.n}]
                </span>
                <span style={{ fontWeight: 500 }}>{c.doc}</span>
                <span style={{ color: 'var(--muted)' }}>{c.section}</span>
                <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
                  <Pill fg="var(--ok)" bg="var(--ok-bg)" line="var(--ok-line)">
                    Verified {ago(c.verifiedAt)}
                  </Pill>
                  <span style={{ color: 'var(--muted)', fontSize: 11.5 }}>{c.owner}</span>
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </section>
  );
});

// ── Brief ─────────────────────────────────────────────────────────────────────
export const BriefCard = forwardRef<HTMLElement, { b: BriefDTO; t: TicketDetailDTO; actions: TicketActions }>(function BriefCard({ b, t, actions }, ref) {
  const [started, setStarted] = useState<number | null>(null);
  return (
    <section ref={ref} aria-label="Brief for you">
      <Card
        title="Handed to you, pre-worked"
        actions={
          <Pill fg="var(--warn)" bg="var(--warn-bg)" line="var(--warn-line)">
            {b.why}
          </Pill>
        }
      >
        <div className={s.label}>Agent summary</div>
        <p style={{ fontSize: 13.5, lineHeight: 1.62 }}>{b.summary}</p>
        <div className={s.briefGrid}>
          <div className={s.briefBox}>
            <Eyebrow style={{ marginBottom: 8 }}>Context pulled for you</Eyebrow>
            {b.context.map((c) => (
              <div key={c.label} className={s.ctxRow}>
                <span>{c.label}</span>
                <span>{c.value}</span>
              </div>
            ))}
          </div>
          <div className={s.briefBox}>
            <Eyebrow>Suggested next moves</Eyebrow>
            {b.suggestions.map((sg, i) => (
              <button
                key={i}
                type="button"
                className={s.sug}
                disabled={!t.permissions.canWork || actions.startSuggestion.isPending}
                onClick={() => {
                  setStarted(i);
                  actions.startSuggestion.mutate(i);
                }}
                title="Start this as a sub-task you own"
              >
                <Dot color={started === i ? 'var(--ok-dot)' : 'var(--accent)'} size={6} />
                <span>{sg.label}</span>
                <span className={s.sugMeta}>{sg.meta}</span>
              </button>
            ))}
          </div>
        </div>
      </Card>
    </section>
  );
});

// ── Progress after approval ───────────────────────────────────────────────────
type StepState = 'done' | 'active' | 'todo';

function stepsFor(g: GateDTO): { label: string; state: StepState; note?: string }[] {
  const after = (states: GateDTO['state'][]) => states.includes(g.state);
  if (g.mode === 'draft') {
    return [
      { label: 'Check the draft', state: 'done' },
      { label: 'Your approval', state: 'done' },
      { label: 'Send to the customer', state: after(['done']) ? 'done' : 'active', note: g.state === 'scheduled' ? 'recallable' : undefined },
      { label: 'Write audit record', state: after(['done']) ? 'done' : 'todo' },
    ];
  }
  const dual = g.chain === 'dual';
  return [
    { label: 'Check the fields', state: 'done' },
    {
      label: dual ? 'Second approver' : 'Your approval',
      state: g.state === 'awaiting_checker' ? 'active' : 'done',
      note: g.state === 'awaiting_checker' ? (g.proposedChecker?.name ?? g.checker?.name ?? 'waiting') : undefined,
    },
    {
      label: 'Execute in core banking',
      state: after(['done']) ? 'done' : after(['scheduled', 'executing']) ? 'active' : 'todo',
      note: g.state === 'scheduled' ? 'undo window open' : undefined,
    },
    { label: 'Write audit record', state: after(['done']) ? 'done' : 'todo' },
  ];
}

export function Progress({ t }: { t: TicketDetailDTO }) {
  const g = t.gate;
  if (g.mode === 'manual' || !['awaiting_checker', 'scheduled', 'executing', 'done'].includes(g.state)) return null;
  const steps = stepsFor(g);
  const done = g.state === 'done';
  const title = done ? (g.mode === 'draft' ? 'Reply sent' : 'Action carried out in core banking') : g.mode === 'draft' ? 'Sending the reply' : 'Carrying out the action';
  return (
    <Card
      title={
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <Dot color={done ? 'var(--ok-dot)' : 'var(--accent)'} pulse={!done} /> {title}
        </span>
      }
      meta={t.action?.externalRef ? <span className="mono">ref {t.action.externalRef}</span> : <span className="mono">{t.number}</span>}
    >
      <ol className={s.steps} style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {steps.map((st, i) => (
          <li key={st.label} className={cx(s.step, st.state === 'active' && s.stepOn)} aria-current={st.state === 'active' ? 'step' : undefined}>
            <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span
                aria-hidden
                style={{
                  width: 15,
                  height: 15,
                  borderRadius: '50%',
                  flex: 'none',
                  display: 'grid',
                  placeItems: 'center',
                  fontSize: 9,
                  color: '#fff',
                  background: st.state === 'done' ? 'var(--ok-dot)' : 'transparent',
                  border: st.state === 'done' ? 0 : `1.5px solid ${st.state === 'active' ? 'var(--accent)' : 'var(--line-strong)'}`,
                }}
              >
                {st.state === 'done' ? '✓' : ''}
              </span>
              <span style={{ fontWeight: st.state === 'active' ? 600 : 500 }}>{st.label}</span>
              <span className="sr-only">{st.state === 'done' ? '(done)' : st.state === 'active' ? '(in progress)' : '(not started)'}</span>
            </span>
            {st.note && <span className={s.small} style={{ marginTop: 0 }}>{st.note}</span>}
            <span className={s.stepBar}>
              <span
                style={{
                  width: st.state === 'done' ? '100%' : st.state === 'active' ? '45%' : 0,
                  background: st.state === 'done' ? 'var(--ok-dot)' : 'var(--accent)',
                  animation: `growX .5s var(--ease-out) ${i * 0.08}s both`,
                }}
              />
            </span>
          </li>
        ))}
      </ol>
    </Card>
  );
}
