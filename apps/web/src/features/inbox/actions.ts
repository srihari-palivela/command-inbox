/** Every write the Inbox makes. Each one refreshes the ticket, the queue and the chrome counts. */
import type { CallDTO, RejectReason, TicketDetailDTO, TicketStatus } from '@ci/contracts';
import { api, newIdempotencyKey } from '../../lib/api';
import { keys, useAction } from '../../lib/queries';
import { toast } from '../../lib/toast';

export const TICKET_KEYS = [keys.ticketAll, keys.inboxAll, keys.ticketsAll, keys.activity, keys.shift, keys.me];

export interface ApproveResult {
  outcome: 'awaiting_checker' | 'scheduled' | 'sending' | 'taken';
}

export function useTicketActions(t: TicketDetailDTO) {
  const id = t.id;
  const inv = { invalidate: TICKET_KEYS };

  // One idempotency key per approval attempt: a double click or a retried request replays, never repeats.
  const approve = useAction(
    (v: { openedEvidence: boolean; key: string }) =>
      api.post<ApproveResult>(`/v1/tickets/${id}/gate/approve`, { openedEvidence: v.openedEvidence }, { idempotencyKey: v.key }),
    {
      ...inv,
      success: (r) =>
        r.outcome === 'awaiting_checker'
          ? `Approved as maker. Waiting on ${t.gate.proposedChecker?.name ?? 'a second approver'} to counter-approve.`
          : r.outcome === 'sending'
            ? `Reply queued to ${t.fromName}. You can recall it for 60 seconds.`
            : r.outcome === 'taken'
              ? `${t.number} is yours now. The AI's brief stays on the ticket.`
              : null,
    },
  );
  const reject = useAction((reason: RejectReason) => api.post(`/v1/tickets/${id}/gate/reject`, { reason }), {
    ...inv,
    success: (_r, reason) =>
      reason === 'needs_human'
        ? 'Sent back. A hard stop rule was proposed so this pattern always comes to a person.'
        : 'Sent back to the AI. The correction is stored against this query type; the ticket is yours now.',
  });
  const undo = useAction(() => api.post(`/v1/tickets/${id}/gate/undo`), { ...inv, success: 'Undone. Nothing reached core systems.' });
  const amend = useAction((fields: { label: string; value: string }[]) => api.patch(`/v1/tickets/${id}/action/fields`, { fields }), {
    ...inv,
    success: 'Fields updated. Your edit is saved as a correction for the extractor.',
  });
  const saveDraft = useAction((body: string) => api.put(`/v1/tickets/${id}/draft`, { body }), { ...inv, success: 'Draft saved. Your edit is kept as a training example.' });
  const reply = useAction((body: string) => api.post<{ replyId: string; sendAfter: string }>(`/v1/tickets/${id}/replies`, { body }, { idempotencyKey: newIdempotencyKey() }), inv);
  const recall = useAction((replyId: string) => api.post(`/v1/replies/${replyId}/recall`), { ...inv, success: 'Recalled. Nothing was sent.' });
  const transition = useAction((to: TicketStatus) => api.post(`/v1/tickets/${id}/transition`, { to }, { ifMatch: t.version }), inv);
  const override = useAction((lane: TicketDetailDTO['lane']) => api.post(`/v1/tickets/${id}/override-lane`, { lane }), {
    ...inv,
    success: 'Handling changed. The AI logs this as a correction.',
  });
  const comment = useAction((v: { kind: 'note' | 'public'; body: string }) => api.post(`/v1/tickets/${id}/comments`, v), inv);
  const subtask = useAction((v: { key: string; done: boolean }) => api.patch(`/v1/tickets/${id}/subtasks/${v.key}`, { done: v.done }), inv);
  const watch = useAction((watching: boolean) => api.put(`/v1/tickets/${id}/watch`, { watching }), {
    ...inv,
    success: (_r, w) => (w ? `Watching ${t.number}. You'll hear about every change.` : `Stopped watching ${t.number}.`),
  });
  const escalate = useAction(() => api.post(`/v1/tickets/${id}/escalate`), { ...inv, success: `${t.number} escalated to the team lead.` });
  const split = useAction(() => api.post(`/v1/tickets/${id}/split`), { ...inv, success: 'Split into two tickets, one per question.' });
  const merge = useAction((intoNumber: string) => api.post(`/v1/tickets/${id}/merge`, { intoNumber }), { ...inv, success: (_r, n) => `Merged into ${n}.` });
  const assign = useAction((userId: string | null) => api.post(`/v1/tickets/${id}/assign`, { userId }), { ...inv, success: 'Reassigned.' });
  const startSuggestion = useAction((index: number) => api.post(`/v1/tickets/${id}/suggestions/${index}/start`), {
    ...inv,
    success: 'Started. It is on the ticket as a sub-task with you as owner.',
  });
  const startCall = useAction(() => api.post<CallDTO>(`/v1/tickets/${id}/calls`), {});

  return { approve, reject, undo, amend, saveDraft, reply, recall, transition, override, comment, subtask, watch, escalate, split, merge, assign, startSuggestion, startCall };
}

export type TicketActions = ReturnType<typeof useTicketActions>;

/** Show the "recall" affordance for a queued reply. */
export function offerRecall(actions: TicketActions, replyId: string, to: string) {
  toast.show(`Reply queued to ${to}. Sends in 60 seconds.`, { label: 'Recall', run: () => actions.recall.mutate(replyId) });
}
