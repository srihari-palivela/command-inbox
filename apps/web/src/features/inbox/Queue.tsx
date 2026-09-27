import type { InboxDTO, TicketSummaryDTO } from '@ci/contracts';
import { clockTime } from '../../lib/format';
import { LANE_TONE, SLA_TONE } from '../../lib/presentation';
import { EmptyState, ErrorState, Kbd, Pill, Skeleton, cx } from '../../ui';
import s from './Inbox.module.css';

export type InboxFilter = 'all' | 'auto' | 'draft' | 'manual' | 'late';

const FILTERS: { key: InboxFilter; label: string; count: keyof InboxDTO['counts'] }[] = [
  { key: 'all', label: 'All', count: 'all' },
  { key: 'auto', label: 'Auto', count: 'auto' },
  { key: 'draft', label: 'Draft', count: 'draft' },
  { key: 'manual', label: 'Needs you', count: 'manual' },
  { key: 'late', label: 'Running late', count: 'late' },
];

/** What the row promises next, in the prototype's words. */
function nextWord(t: TicketSummaryDTO): { text: string; color: string } {
  if (t.pendingGate === 'checker') return { text: 'needs checker', color: 'var(--warn)' };
  if (t.lane === 'manual') return { text: 'needs you', color: 'var(--warn)' };
  if (t.lane === 'draft') return { text: 'check it', color: 'var(--ok)' };
  return { text: 'ready', color: 'var(--ok)' };
}

export function Queue({
  query,
  filter,
  onFilter,
  selectedId,
  onSelect,
}: {
  query: { data: InboxDTO | undefined; isLoading: boolean; error: unknown; refetch: () => unknown };
  filter: InboxFilter;
  onFilter: (f: InboxFilter) => void;
  selectedId: string | null;
  onSelect: (t: TicketSummaryDTO) => void;
}) {
  const d = query.data;
  return (
    <aside className={s.queue} aria-label="Your queue">
      <div className={s.queueHead}>
        <h1 className={s.queueTitle}>
          Inbox <span>{d ? `${d.counts.all} for you` : ''}</span>
        </h1>
        <div className={s.filters} role="group" aria-label="Filter the queue">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={s.filter}
              aria-pressed={filter === f.key}
              onClick={() => onFilter(f.key)}
            >
              {f.label}
              {f.key === 'all' && d ? ` ${d.counts.all}` : ''}
              {f.key !== 'all' && d && d.counts[f.count] ? (
                <span className="mono" style={{ marginLeft: 4, opacity: 0.7 }}>
                  {d.counts[f.count]}
                </span>
              ) : null}
            </button>
          ))}
        </div>
      </div>
      <div className={s.queueList} role="listbox" aria-label="Tickets, most urgent first">
        {query.error ? (
          <div style={{ padding: 12 }}>
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          </div>
        ) : !d ? (
          <div style={{ padding: 13, display: 'grid', gap: 14 }}>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} style={{ display: 'grid', gap: 7 }}>
                <Skeleton h={12} w="40%" />
                <Skeleton h={30} />
                <Skeleton h={10} w="70%" />
              </div>
            ))}
          </div>
        ) : d.items.length === 0 ? (
          <EmptyState
            title={filter === 'all' ? 'Nothing waiting on you' : 'Nothing in this view'}
            text={
              filter === 'all'
                ? 'The AI is handling everything that came in. New mail lands here as soon as it needs a person.'
                : 'Try another filter.'
            }
          />
        ) : (
          d.items.map((t, i) => {
            const lane = LANE_TONE[t.lane];
            const sla = SLA_TONE[t.sla.tone];
            const nx = nextWord(t);
            const on = t.id === selectedId;
            return (
              <button
                key={t.id}
                type="button"
                role="option"
                aria-selected={on}
                className={cx(s.row, on && s.rowOn)}
                style={{ animationDelay: `${Math.min(i, 10) * 0.03}s` }}
                onClick={() => onSelect(t)}
              >
                <div className={s.rowTop}>
                  <Pill fg={lane.fg} bg={lane.bg}>
                    {lane.word}
                  </Pill>
                  <Pill fg={sla.fg} bg={sla.bg}>
                    {sla.label}
                  </Pill>
                  <span className={s.rowTime}>{clockTime(t.receivedAt)}</span>
                </div>
                <div className={s.rowSubject}>{t.subject}</div>
                <div className={s.rowMeta}>
                  <span>{t.fromName}</span>
                  <span aria-hidden>·</span>
                  <span>{t.bucket}</span>
                  <span className={s.rowNext} style={{ color: nx.color }}>
                    {nx.text}
                  </span>
                </div>
              </button>
            );
          })
        )}
      </div>
      <div className={s.queueFoot} aria-hidden>
        <span>
          <Kbd>J</Kbd> <Kbd>K</Kbd> move
        </span>
        <span>
          <Kbd>A</Kbd> approve
        </span>
        <span>
          <Kbd>R</Kbd> reply
        </span>
        <span>
          <Kbd>?</Kbd> all keys
        </span>
      </div>
    </aside>
  );
}
