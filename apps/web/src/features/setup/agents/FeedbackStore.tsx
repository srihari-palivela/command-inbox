/**
 * Feedback store: every override, draft edit and rejection staff made, kept as training signal. Each
 * one can be queued for prompt or context tuning; the case joins the golden set.
 */
import type { FeedbackDTO, FeedbackKind } from '@ci/contracts';
import { useState } from 'react';
import { api } from '../../../lib/api';
import { ago } from '../../../lib/format';
import { keys, useAction } from '../../../lib/queries';
import { Button, Card, Chip, EmptyState, Pill } from '../../../ui';
import s from './agents.module.css';

const KIND: Record<FeedbackKind, { word: string; fg: string; bg: string }> = {
  override: { word: 'Override', fg: 'var(--warn)', bg: 'var(--warn-bg)' },
  edit: { word: 'Edit', fg: 'var(--text-2)', bg: 'var(--surface-3)' },
  rejection: { word: 'Rejection', fg: 'var(--bad-text)', bg: 'var(--bad-bg)' },
};

const FIX = {
  prompt: { label: 'Prompt tuning', note: 'behaviour, tone, caution' },
  context: { label: 'Context tuning', note: 'examples, taxonomy, sources' },
} as const;

export function FeedbackStore({ feedback, canQueue }: { feedback: FeedbackDTO[]; canQueue: boolean }) {
  const [agent, setAgent] = useState<string | null>(null);
  const counts = new Map<string, number>();
  for (const f of feedback) counts.set(f.agentName, (counts.get(f.agentName) ?? 0) + 1);
  const rows = agent ? feedback.filter((f) => f.agentName === agent) : feedback;

  const queue = useAction((f: FeedbackDTO) => api.post(`/v1/feedback/${f.id}/queue`), {
    invalidate: [keys.agents],
    success: (_r, f) => `${FIX[f.fix].label} queued for ${f.agentName} — ships as a new version after evals, with this case added to the golden set.`,
  });

  return (
    <Card className={s.fbCard} flush title="Feedback store" meta={`${feedback.length} stored`}>
      <p className={s.fbIntro}>Every override, draft edit and rejection is kept against the agent that made the call. Queue one for tuning and the case joins that agent’s golden set.</p>
      {counts.size > 0 && (
        <div className={s.fbChips} role="group" aria-label="Filter by agent">
          <Chip on={agent === null} count={feedback.length} onClick={() => setAgent(null)}>
            All agents
          </Chip>
          {[...counts].map(([name, n]) => (
            <Chip key={name} on={agent === name} count={n} onClick={() => setAgent(agent === name ? null : name)}>
              {name}
            </Chip>
          ))}
        </div>
      )}
      {rows.length === 0 ? (
        <EmptyState title="Nothing stored yet" text="When staff override a route, edit a draft or reject a suggestion, it lands here as tuning material." />
      ) : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {rows.map((f, i) => {
            const k = KIND[f.kind];
            const fix = FIX[f.fix];
            const busy = queue.isPending && queue.variables?.id === f.id;
            return (
              <li key={f.id} className={s.fbRow} style={{ animationDelay: `${Math.min(i, 12) * 0.03}s` }}>
                <div style={{ minWidth: 0 }}>
                  <div className={`${s.fbAgent} ${s.ellipsis}`}>{f.agentName}</div>
                  <div className={s.fbTicket}>{f.ticketNumber ?? '—'}</div>
                </div>
                <Pill fg={k.fg} bg={k.bg} style={{ justifySelf: 'start' }}>
                  {k.word}
                </Pill>
                <div className={s.fbText}>{f.text}</div>
                <div>
                  <div className={s.fbFix}>{fix.label}</div>
                  <div className={s.fbFixNote}>{fix.note}</div>
                </div>
                <time className={s.fbWhen} dateTime={f.at} title={new Date(f.at).toLocaleString('en-GB')}>
                  {ago(f.at)}
                </time>
                {f.status === 'queued' ? (
                  <span className={s.queued}>
                    <span aria-hidden>✓</span> Queued
                  </span>
                ) : (
                  <Button
                    size="sm"
                    variant="soft"
                    className={s.fbAction}
                    loading={busy}
                    disabled={!canQueue}
                    title={canQueue ? `Queue ${fix.label.toLowerCase()} for ${f.agentName}` : 'Only team leads and admins can queue tuning.'}
                    onClick={() => queue.mutate(f)}
                  >
                    Queue tuning
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
