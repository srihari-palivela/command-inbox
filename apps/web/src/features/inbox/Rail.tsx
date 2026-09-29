import type { ActivityDTO } from '@ci/contracts';
import { clockTime, formatMinutes } from '../../lib/format';
import { useActivity, useShift } from '../../lib/queries';
import { Dot, EmptyState, Eyebrow, Skeleton } from '../../ui';
import s from './Inbox.module.css';

const TONE: Record<ActivityDTO['tone'], string> = {
  ok: 'var(--ok-dot)',
  stop: 'var(--bad)',
  flag: 'var(--warn-dot)',
  info: 'var(--accent)',
  muted: 'var(--dot-idle)',
};

export function Rail({
  open,
  onToggle,
  live,
  onOpenTicket,
}: {
  open: boolean;
  onToggle: () => void;
  live: boolean;
  onOpenTicket: (number: string) => void;
}) {
  const act = useActivity();
  const shift = useShift();
  if (!open) {
    return (
      <aside className={s.rail} aria-label="AI activity (collapsed)">
        <div style={{ padding: '14px 8px' }}>
          <button
            type="button"
            className={s.railToggle}
            onClick={onToggle}
            aria-label="Show AI activity"
            aria-expanded={false}
          >
            ‹
          </button>
        </div>
      </aside>
    );
  }
  return (
    <aside className={s.rail} aria-label="AI activity">
      <div className={s.railHead}>
        AI activity
        <span
          style={{
            marginLeft: 'auto',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 5,
            fontSize: 11,
            fontWeight: 400,
            color: 'var(--muted)',
          }}
        >
          <Dot color={live ? 'var(--ok-dot)' : 'var(--warn-dot)'} pulse={live} size={6} />{' '}
          {live ? 'live' : 'reconnecting'}
        </span>
        <button
          type="button"
          className={s.railToggle}
          style={{ marginLeft: 6 }}
          onClick={onToggle}
          aria-label="Hide AI activity"
          aria-expanded
        >
          ›
        </button>
      </div>
      <div className={s.railList} aria-live="polite">
        {act.isLoading ? (
          <div style={{ display: 'grid', gap: 12, paddingTop: 10 }}>
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} h={34} />
            ))}
          </div>
        ) : !act.data?.length ? (
          <EmptyState title="Quiet so far" text="What the AI does on its own shows up here." />
        ) : (
          act.data.map((a, i) => (
            <div key={a.id} className={s.act} style={{ animationDelay: `${Math.min(i, 8) * 0.03}s` }}>
              <span style={{ paddingTop: 5 }}>
                <Dot color={TONE[a.tone]} size={6} />
              </span>
              <div style={{ minWidth: 0 }}>
                <div>{a.text}</div>
                <div className={s.actMeta}>
                  {a.ticketNumber ? (
                    <button
                      type="button"
                      onClick={() => onOpenTicket(a.ticketNumber!)}
                      className="mono"
                      style={{
                        border: 0,
                        background: 'none',
                        padding: 0,
                        color: 'var(--accent)',
                        fontSize: 10,
                      }}
                    >
                      {a.ticketNumber}
                    </button>
                  ) : null}
                  {a.ticketNumber ? ' · ' : ''}
                  {clockTime(a.at)} · {a.meta}
                </div>
              </div>
            </div>
          ))
        )}
      </div>
      <div className={s.shift}>
        <Eyebrow>Your shift</Eyebrow>
        {shift.data ? (
          <div className={s.shiftGrid}>
            <div>
              <div className={s.shiftNum}>{shift.data.closedToday}</div>
              <div className={s.small} style={{ marginTop: 0 }}>
                closed today
              </div>
            </div>
            <div>
              <div className={s.shiftNum}>{shift.data.sentAsDraftedPct}%</div>
              <div className={s.small} style={{ marginTop: 0 }}>
                sent as drafted
              </div>
            </div>
            <div>
              <div className={s.shiftNum} style={{ color: 'var(--ok)' }}>
                {formatMinutes(shift.data.savedMinutes)}
              </div>
              <div className={s.small} style={{ marginTop: 0 }}>
                handling time saved
              </div>
            </div>
            <div>
              <div
                className={s.shiftNum}
                style={{ color: shift.data.missedDeadlines ? 'var(--bad)' : 'var(--ok)' }}
              >
                {shift.data.missedDeadlines}
              </div>
              <div className={s.small} style={{ marginTop: 0 }}>
                missed deadlines
              </div>
            </div>
          </div>
        ) : (
          <Skeleton h={80} style={{ marginTop: 10 }} />
        )}
      </div>
    </aside>
  );
}
