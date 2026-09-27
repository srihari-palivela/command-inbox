/**
 * The approval gateway: the one place a person commits the AI's work. It shows exactly what the server
 * will enforce — reversibility, who the second approver is, the customer's wait, the duplicate check —
 * and offers undo only when the server says the action is still inside its undo window (review C1).
 */
import type { MeDTO, RejectReason, TicketDetailDTO } from '@ci/contracts';
import { useEffect, useState } from 'react';
import { formatMinutes } from '../../lib/format';
import { Button, Eyebrow, Field, Input, Modal, Pill } from '../../ui';
import type { TicketActions } from './actions';
import s from './Inbox.module.css';

const CHAIN_NOTE = {
  dual: 'Needs two approvers',
  single: 'Needs one approver',
  single_undo: 'Needs one approver',
  auto: 'Runs inside the rules',
} as const;

function useSecondsLeft(until: string | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!until) return;
    const iv = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(iv);
  }, [until]);
  return until ? Math.max(0, Math.ceil((new Date(until).getTime() - now) / 1000)) : 0;
}

export function primaryLabel(t: TicketDetailDTO, me: MeDTO): string {
  const g = t.gate;
  if (g.mode === 'manual') return 'Take it on';
  if (g.state === 'awaiting_checker') return g.maker?.id === me.user.id ? 'Waiting on checker' : 'Counter-approve';
  return g.mode === 'draft' ? 'Approve & send' : 'Approve & execute';
}

export function Gateway({ t, me, actions, onApprove, approving, onEdit, onReply, onNext }: {
  t: TicketDetailDTO;
  me: MeDTO;
  actions: TicketActions;
  onApprove: () => void;
  approving: boolean;
  onEdit: () => void;
  onReply: () => void;
  onNext: () => void;
}) {
  const g = t.gate;
  const [rejecting, setRejecting] = useState(false);
  const secs = useSecondsLeft(g.canUndo ? g.undoUntil : null);
  const wait = g.customerWaitingMinutes;
  const waitColor = wait === null ? 'var(--muted)' : wait > 600 ? 'var(--bad)' : wait > 60 ? 'var(--warn)' : 'var(--text-2)';
  const closed = g.state === 'done' || g.state === 'rejected' || g.state === 'taken';
  const who = (u: { id: string; name: string } | null) => (u ? (u.id === me.user.id ? `You · ${u.name}` : u.name) : '—');

  let line1: React.ReactNode;
  if (g.mode === 'manual') {
    line1 = (
      <span>
        Owner: <b>{who(g.owner)}</b>
      </span>
    );
  } else if (g.mode === 'draft') {
    line1 = (
      <span>
        Sent under your name: <b>{who(g.maker ?? { id: me.user.id, name: me.user.name })}</b>
      </span>
    );
  } else if (g.chain === 'dual') {
    const checker = g.checker ?? g.proposedChecker;
    line1 =
      g.state === 'awaiting_checker' ? (
        <span>
          Maker <b>{who(g.maker)}</b> approved · waiting on <b>{checker ? `${who(checker)} (Checker)` : 'an eligible checker'}</b>
        </span>
      ) : (
        <span>
          Second approver: <b>{checker ? `${who(checker)} (Checker)` : 'next eligible team lead'}</b>
        </span>
      );
  } else {
    line1 = (
      <span>
        Approver: <b>{who(g.maker ?? { id: me.user.id, name: me.user.name })}</b>
      </span>
    );
  }

  const reversibility =
    g.mode === 'draft' ? (
      <span style={{ color: 'var(--ok)' }}>Recallable for 60s after send</span>
    ) : g.mode === 'action' && g.reversible !== null ? (
      <span style={{ color: g.reversible ? 'var(--ok)' : 'var(--bad)' }}>{g.reversible ? 'Can be undone for 30s after approval' : 'Cannot be undone'}</span>
    ) : null;

  return (
    <>
      <div className={s.gate} role="region" aria-label="Approval gateway">
        <div className={s.gateInfo}>
          <div className={s.gateLine}>
            <Eyebrow>Approval gateway</Eyebrow>
            <Pill fg={g.mode === 'manual' ? 'var(--muted)' : 'var(--accent)'} bg={g.mode === 'manual' ? 'var(--surface-4)' : 'var(--accent-bg-2)'} line={g.mode === 'manual' ? 'var(--line)' : 'var(--accent-line)'}>
              {g.mode !== 'manual' ? CHAIN_NOTE[g.chain ?? 'single'] : t.lane === 'manual' ? 'The AI has stepped back' : 'Nothing prepared yet'}
            </Pill>
          </div>
          <div className={s.gateLine}>
            {line1}
            {wait !== null && !closed && <span style={{ color: waitColor }}>Customer waiting {formatMinutes(wait)}</span>}
            {reversibility}
          </div>
          {(g.duplicateClear !== null || g.blockedReason || g.note) && (
            <div className={s.gateLine} style={{ fontSize: 12 }}>
              {g.duplicateClear !== null && (
                <span style={{ color: g.duplicateClear ? 'var(--ok)' : 'var(--warn)' }}>
                  {g.duplicateClear ? '✓ No similar action on this account in 90 days' : `⚠ ${t.action?.duplicate.text ?? 'A similar action ran recently'}`}
                </span>
              )}
              {g.note && <span style={{ color: 'var(--muted)' }}>{g.note}</span>}
              {g.blockedReason && !closed && <span style={{ color: 'var(--muted)' }}>{g.blockedReason}</span>}
            </div>
          )}
        </div>

        <div className={s.gateBtns}>
          {g.canUndo && secs > 0 ? (
            <>
              <div style={{ display: 'grid', gap: 5, justifyItems: 'end' }}>
                <span style={{ fontSize: 12, color: 'var(--text-2)' }}>
                  {g.mode === 'draft' ? 'Sending' : 'Executing'} in <b className="mono">{secs}s</b>
                </span>
                <div className={s.undoBar} aria-hidden>
                  <span style={{ width: `${(secs / (g.mode === 'draft' ? 60 : 30)) * 100}%` }} />
                </div>
              </div>
              <Button size="lg" onClick={() => actions.undo.mutate(undefined)} loading={actions.undo.isPending} kbd="U">
                {g.mode === 'draft' ? 'Recall' : 'Undo'}
              </Button>
            </>
          ) : closed ? (
            <>
              <span style={{ fontSize: 12.5, color: g.state === 'rejected' ? 'var(--warn)' : 'var(--ok)', fontWeight: 600 }}>
                {g.state === 'done' ? (g.mode === 'draft' ? 'Sent and logged' : 'Done and audited') : g.state === 'rejected' ? 'Sent back to the AI' : 'Yours now'}
              </span>
              <Button size="lg" variant="dark" onClick={onNext} kbd="J">
                Next ticket
              </Button>
            </>
          ) : (
            <>
              {g.mode !== 'manual' && (
                <Button size="lg" onClick={() => setRejecting(true)} disabled={!t.permissions.canWork || g.state !== 'open'}>
                  Reject
                </Button>
              )}
              {g.mode === 'action' && t.action && (
                <Button size="lg" onClick={onEdit} disabled={!t.permissions.canWork || g.state !== 'open'}>
                  Amend fields
                </Button>
              )}
              {g.mode === 'draft' && (
                <Button size="lg" onClick={onEdit} disabled={!t.permissions.canWork || g.state !== 'open'}>
                  Edit draft
                </Button>
              )}
              {g.mode === 'manual' && (
                <Button size="lg" onClick={onReply} disabled={!t.permissions.canReply}>
                  Reply
                </Button>
              )}
              <Button
                size="lg"
                variant={g.mode === 'manual' ? 'dark' : 'primary'}
                onClick={onApprove}
                loading={approving}
                disabled={!g.canApprove}
                title={g.canApprove ? undefined : (g.blockedReason ?? undefined)}
                kbd="A"
              >
                {primaryLabel(t, me)}
              </Button>
            </>
          )}
        </div>
      </div>
      <RejectModal open={rejecting} onClose={() => setRejecting(false)} actions={actions} />
    </>
  );
}

// ── Reject ────────────────────────────────────────────────────────────────────
const REASONS: { k: RejectReason; label: string; note: string }[] = [
  { k: 'wrong_type', label: 'Wrong query type', note: 'It picked the wrong bucket, so the whole routing is off.' },
  { k: 'bad_field', label: 'A field is wrong', note: 'The extracted values do not match the customer’s email.' },
  { k: 'tone', label: 'Wrong tone for this customer', note: 'Accurate, but not how we would say it.' },
  { k: 'needs_human', label: 'This should never be automated', note: 'Proposes a hard stop rule for this pattern. Risk & Compliance approves it.' },
];

function RejectModal({ open, onClose, actions }: { open: boolean; onClose: () => void; actions: TicketActions }) {
  const [pick, setPick] = useState<RejectReason | null>(null);
  useEffect(() => {
    if (open) setPick(null);
  }, [open]);
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Send it back to the AI"
      subtitle="Tell it what was wrong. The correction is stored against this query type, so the same mistake is less likely next time."
      width={500}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="dark" disabled={!pick} loading={actions.reject.isPending} onClick={() => pick && actions.reject.mutate(pick, { onSuccess: onClose })}>
            Send back &amp; reassign to me
          </Button>
        </>
      }
    >
      <div role="radiogroup" aria-label="What was wrong">
        {REASONS.map((r) => (
          <button key={r.k} type="button" role="radio" aria-checked={pick === r.k} className={s.rejectOpt} onClick={() => setPick(r.k)}>
            <span className={s.radio} aria-hidden />
            <span>
              <span style={{ display: 'block', fontSize: 13, fontWeight: 600 }}>{r.label}</span>
              <span style={{ display: 'block', fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>{r.note}</span>
            </span>
          </button>
        ))}
      </div>
    </Modal>
  );
}

// ── Amend fields ──────────────────────────────────────────────────────────────
export function AmendModal({ t, open, onClose, actions }: { t: TicketDetailDTO; open: boolean; onClose: () => void; actions: TicketActions }) {
  const a = t.action;
  const [vals, setVals] = useState<Record<string, string>>({});
  useEffect(() => {
    if (open && a) setVals(Object.fromEntries(a.fields.map((f) => [f.label, f.value])));
  }, [open, a]);
  if (!a) return null;
  const changed = a.fields.filter((f) => (vals[f.label] ?? f.value) !== f.value);
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Amend fields"
      subtitle={`${a.name} · ${a.templateCode}. The duplicate check and validation re-run on save; your change is recorded as a correction.`}
      width={560}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!changed.length}
            loading={actions.amend.isPending}
            onClick={() => actions.amend.mutate(changed.map((f) => ({ label: f.label, value: vals[f.label]!.trim() })), { onSuccess: onClose })}
          >
            Save {changed.length ? `${changed.length} change${changed.length > 1 ? 's' : ''}` : 'changes'}
          </Button>
        </>
      }
    >
      <div style={{ display: 'grid', gap: 12 }}>
        {a.fields.map((f) => (
          <Field key={f.label} label={f.label} hint={`${f.inferred ? 'Inferred' : 'Verified'} · from ${f.source}`}>
            <Input className="mono" value={vals[f.label] ?? f.value} onChange={(e) => setVals((v) => ({ ...v, [f.label]: e.target.value }))} />
          </Field>
        ))}
      </div>
    </Modal>
  );
}
