import type { Capability, TicketSummaryDTO } from '@ci/contracts';
import { useEffect, useMemo, useState } from 'react';
import { api, newIdempotencyKey } from '../../lib/api';
import { confTone, LANE_TONE, PRIORITY_TONE, STATUS_TONE, STATUS_WORD, statusGroup } from '../../lib/presentation';
import { keys, useAction } from '../../lib/queries';
import { Button, EmptyState } from '../../ui';
import { LanePill, ownerOf, OwnerDot, slaChipTone, slaLabel } from './bits';
import s from './Tickets.module.css';

interface BatchResult {
  results: { ticketId: string; ok: boolean; outcome?: string; error?: string }[];
}

const OUTCOME_WORD: Record<string, string> = {
  awaiting_checker: 'waiting for a checker',
  scheduled: 'scheduled',
  sending: 'sending',
  taken: 'taken',
};

/** Which rows the signed-in person could approve in one step (the server still decides). */
function approvable(t: TicketSummaryDTO, caps: Set<Capability>): boolean {
  if (t.pendingGate === 'maker') return caps.has('action.approve_maker');
  if (t.pendingGate === 'send') return caps.has('ticket.reply');
  return false;
}

export function TicketList({ items, bar, caps, onOpen, activeNumber }: { items: TicketSummaryDTO[]; bar: number; caps: Set<Capability>; onOpen: (t: TicketSummaryDTO) => void; activeNumber: string | null }) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const selectable = useMemo(() => items.filter((t) => approvable(t, caps)), [items, caps]);

  // Drop selections that are no longer visible or no longer approvable (e.g. after a live update).
  useEffect(() => {
    setSelected((prev) => {
      const ok = new Set(selectable.map((t) => t.id));
      const next = new Set([...prev].filter((id) => ok.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [selectable]);

  const batch = useAction(
    (ticketIds: string[]) => api.post<BatchResult>('/v1/gate/batch-approve', { ticketIds }, { idempotencyKey: newIdempotencyKey() }),
    {
      invalidate: [keys.ticketsAll, keys.inboxAll, keys.ticketAll, keys.activity],
      success: (r) => {
        const num = (id: string) => items.find((t) => t.id === id)?.number ?? 'A ticket';
        const ok = r.results.filter((x) => x.ok);
        const failed = r.results.filter((x) => !x.ok);
        const okText = ok.length
          ? `Approved ${ok.length}: ${ok.map((x) => `${num(x.ticketId)} ${OUTCOME_WORD[x.outcome ?? ''] ?? ''}`.trim()).join(', ')}.`
          : '';
        const failText = failed.length ? ` ${failed.length} not approved — ${failed.map((x) => `${num(x.ticketId)}: ${x.error ?? 'failed'}`).join('; ')}` : '';
        return (okText + failText).trim() || 'Nothing was approved.';
      },
    },
  );

  const toggle = (id: string) =>
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const allOn = selectable.length > 0 && selectable.every((t) => selected.has(t.id));
  const toggleAll = () => setSelected(allOn ? new Set() : new Set(selectable.map((t) => t.id)));

  const chosen = items.filter((t) => selected.has(t.id));
  const teams = [...new Set(chosen.map((t) => t.department))];

  return (
    <div className={s.table}>
      <div className={s.thead}>
        <span className={s.checkCell}>
          <input
            type="checkbox"
            aria-label="Select every ticket waiting on your approval"
            checked={allOn}
            disabled={!selectable.length}
            onChange={toggleAll}
            title={selectable.length ? 'Select every ticket you can approve' : 'Nothing here is waiting on an approval you can give'}
          />
        </span>
        <span className={`${s.rowGrid} mono`}>
          <span>TICKET</span>
          <span>PRI</span>
          <span>HANDLED</span>
          <span>SUBJECT</span>
          <span>BUCKET</span>
          <span>STATUS</span>
          <span>OWNER</span>
          <span>DUE</span>
          <span>CONF.</span>
        </span>
      </div>
      {items.map((t, i) => {
        const lane = LANE_TONE[t.lane];
        const pri = PRIORITY_TONE[t.priority];
        const g = statusGroup(t.status);
        const st = STATUS_TONE[g];
        const can = approvable(t, caps);
        const own = ownerOf(t);
        return (
          <div key={t.id} className={s.tr} style={{ animationDelay: `${Math.min(i, 30) * 0.015}s` }} data-active={t.number === activeNumber || undefined}>
            <span className={s.checkCell}>
              {t.pendingGate === 'maker' || t.pendingGate === 'send' ? (
                <input
                  type="checkbox"
                  checked={selected.has(t.id)}
                  disabled={!can}
                  onChange={() => toggle(t.id)}
                  aria-label={`Select ${t.number} for batch approval`}
                  title={can ? (t.pendingGate === 'send' ? 'Draft ready to send' : 'Action ready for your approval') : 'You are not cleared to approve this'}
                />
              ) : null}
            </span>
            <button type="button" className={`${s.rowGrid} ${s.rowBtn}`} onClick={() => onOpen(t)} aria-label={`${t.number}: ${t.subject}`}>
              <span className={`${s.rowId} mono`}>{t.number}</span>
              <span className={`${s.pri} mono`} style={{ color: pri.fg, background: pri.bg }} title={pri.label}>
                {t.priority}
              </span>
              <LanePill fg={lane.fg} bg={lane.bg} word={lane.word} />
              <span className={s.rowSubj}>{t.subject}</span>
              <span className={s.rowMuted}>{t.bucket}</span>
              <span className={s.status} style={{ color: st.fg, background: st.bg }}>
                {STATUS_WORD[g]}
              </span>
              <span className={s.rowOwner}>
                <OwnerDot t={t} />
                <span>{own.name}</span>
              </span>
              <span className={s.rowSla} style={{ color: slaChipTone(t.sla).fg }}>
                {slaLabel(t.sla)}
              </span>
              <span className={`${s.rowConf} mono`} style={{ color: confTone(t.confidence, bar) }}>
                {t.confidence.toFixed(2)}
              </span>
            </button>
          </div>
        );
      })}
      {!items.length && <EmptyState title="No tickets match that search" text="Clear a filter, or try describing what you want to see in plain words." />}

      {selected.size > 0 && (
        <div className={s.batchBar} role="region" aria-label="Batch approval">
          <span className={s.batchText}>
            <b className="mono">{selected.size}</b> selected
            <span className={s.batchNote}>
              {teams.length === 1 ? ` · all ${teams[0]}` : ` · ${teams.length} teams`} · each approval is logged separately
            </span>
          </span>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
            Clear
          </Button>
          <Button
            size="sm"
            variant="primary"
            loading={batch.isPending}
            onClick={() =>
              batch.mutate([...selected], {
                // Anything that failed stays selected so the person can open it and see why.
                onSuccess: (r) => setSelected(new Set(r.results.filter((x) => !x.ok).map((x) => x.ticketId))),
              })
            }
          >
            Approve {selected.size} selected
          </Button>
        </div>
      )}
    </div>
  );
}
