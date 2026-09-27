import type { StatusGroup, TicketDetailDTO, TicketSummaryDTO } from '@ci/contracts';
import { useNavigate } from 'react-router-dom';
import { confTone, confWord, LANE_TONE, statusGroup } from '../../lib/presentation';
import { useTicket } from '../../lib/queries';
import { Button, Drawer, Eyebrow, Loadable, Skeleton } from '../../ui';
import { LanePill, ownerOf, SlaChip } from './bits';
import s from './Tickets.module.css';

const ORDER: StatusGroup[] = ['triage', 'approval', 'executing', 'human', 'customer', 'resolved'];

const AGENT_NOTES: Record<StatusGroup, string> = {
  triage: 'Still working. It has classified the query and is pulling the fields it needs — nothing is sent until it is done and, where required, a person approves.',
  approval: 'It has done everything it can without a person. The draft or the filled action is ready and waiting on a named approver.',
  executing: 'Approved. It is writing to the core system now and will log the result whether it succeeds or fails.',
  human: 'It has stood down on purpose. It will not generate customer-facing text here, but it has assembled the context so you are not starting cold.',
  customer: 'Paused. It is watching the thread and will pick the work back up the moment the customer replies.',
  resolved: 'Finished and logged, with the actor, the timestamp and the source recorded against the thread.',
};

function pathFor(g: StatusGroup): StatusGroup[] {
  if (g === 'resolved') return ['triage', 'approval', 'executing', 'resolved'];
  if (g === 'human') return ['triage', 'human'];
  if (g === 'customer') return ['triage', 'approval', 'customer'];
  return ORDER.slice(0, ORDER.indexOf(g) + 1);
}

function approvalNote(t: TicketDetailDTO): string {
  const g = t.gate;
  if (g.state === 'awaiting_checker' && g.maker) return `${g.maker.name} approved as maker · waiting for a checker${g.proposedChecker ? ` (${g.proposedChecker.name})` : ''}`;
  if (t.assignee) return `${t.assignee.name} holds this`;
  return 'Unassigned approver';
}

function timeline(t: TicketDetailDTO, bar: number) {
  const g = statusGroup(t.status);
  const copy: Record<StatusGroup, { label: string; note: string }> = {
    triage: { label: 'The AI read and classified it', note: `Sorted into ${t.bucket.toLowerCase()} with ${confWord(t.confidence, bar).toLowerCase()} confidence (${t.confidence.toFixed(2)})` },
    approval: { label: 'Waiting for a person to approve', note: approvalNote(t) },
    executing: { label: 'Being carried out', note: 'Writing to core banking, then the audit log' },
    human: { label: 'Handed to a person', note: `The AI stepped back — ${t.nextMove.toLowerCase()}` },
    customer: { label: 'Waiting on the customer', note: 'The deadline clock is paused' },
    resolved: { label: t.status === 'closed' ? 'Closed' : 'Resolved', note: t.nextMove },
  };
  const path = pathFor(g);
  return path.map((k, i) => {
    const last = i === path.length - 1;
    const done = !last || g === 'resolved';
    return { key: k, ...copy[k], last, done };
  });
}

function DrawerBody({ t, bar }: { t: TicketDetailDTO; bar: number }) {
  const g = statusGroup(t.status);
  const own = ownerOf(t);
  const steps = timeline(t, bar);
  const facts = [
    { l: 'Owner', v: own.name, color: t.ownerKind === 'unassigned' ? 'var(--warn)' : 'var(--ink)' },
    { l: 'Confidence', v: `${confWord(t.confidence, bar)} · ${t.confidence.toFixed(2)}`, color: confTone(t.confidence, bar), mono: true },
    { l: 'How it is handled', v: t.lane === 'auto' ? 'AI acts' : t.lane === 'draft' ? 'AI drafts, you send' : 'You handle it', color: 'var(--ink)' },
    { l: 'Department', v: t.department, color: 'var(--ink)' },
  ];
  return (
    <>
      <Eyebrow style={{ marginBottom: 11 }}>WHERE IT HAS GOT TO</Eyebrow>
      <ol className={s.timeline}>
        {steps.map((st) => (
          <li key={st.key} className={s.step}>
            <span className={s.stepRail} aria-hidden>
              <span className={s.stepDot} data-done={st.done || undefined}>
                {st.done ? '✓' : ''}
              </span>
              {!st.last && <span className={s.stepLine} />}
            </span>
            <span className={s.stepText}>
              <span className={s.stepLabel} data-last={st.last || undefined}>
                {st.label}
                <span className="sr-only">{st.done ? ' (done)' : ' (now)'}</span>
              </span>
              <span className={s.stepNote}>{st.note}</span>
            </span>
          </li>
        ))}
      </ol>

      <dl className={s.facts}>
        {facts.map((f) => (
          <div key={f.l} className={s.fact}>
            <dt>{f.l}</dt>
            <dd style={{ color: f.color }}>{f.v}</dd>
          </div>
        ))}
      </dl>

      <div className={s.agentNote}>
        <div className={s.agentNoteHead}>
          <span className={s.agentDot} aria-hidden />
          What the AI is doing about it
        </div>
        <p>{AGENT_NOTES[g]}</p>
      </div>
    </>
  );
}

export function TicketDrawer({ number, summary, bar, onClose }: { number: string | null; summary: TicketSummaryDTO | undefined; bar: number; onClose: () => void }) {
  const navigate = useNavigate();
  const q = useTicket(number);
  const t = q.data ?? summary;
  const lane = t ? LANE_TONE[t.lane] : null;
  return (
    <Drawer
      open={!!number}
      onClose={onClose}
      width={460}
      title={
        <>
          <span className={s.drawerMeta}>
            {lane && <LanePill fg={lane.fg} bg={lane.bg} word={lane.word} large />}
            <span className={`${s.drawerId} mono`}>{number}</span>
            {t && <SlaChip sla={t.sla} suffix="left" />}
          </span>
          <span className={s.drawerSubj}>{t?.subject ?? <Skeleton h={18} w="80%" />}</span>
        </>
      }
      subtitle={t ? `${t.bucket} · ${t.department}` : undefined}
      footer={
        <>
          <span className={s.nextMove}>
            Next move · <b>{t?.nextMove ?? '—'}</b>
          </span>
          <Button
            variant="primary"
            style={{ marginLeft: 'auto' }}
            onClick={() => {
              onClose();
              navigate(`/inbox/${number}`);
            }}
          >
            Open in Inbox →
          </Button>
        </>
      }
    >
      <Loadable
        query={q}
        skeleton={
          <div style={{ display: 'grid', gap: 10, paddingTop: 8 }}>
            <Skeleton h={12} w="40%" />
            <Skeleton h={40} />
            <Skeleton h={40} />
            <Skeleton h={90} />
          </div>
        }
      >
        {(d) => <DrawerBody t={d} bar={bar} />}
      </Loadable>
    </Drawer>
  );
}
