import type { TicketSummaryDTO } from '@ci/contracts';
import { confTone, LANE_TONE, STATUS_WORD, statusGroup } from '../../lib/presentation';
import { Dot, EmptyState } from '../../ui';
import { LanePill, OwnerDot, SlaChip } from './bits';
import { bucketColumns, statusColumns } from './columns';
import s from './Tickets.module.css';

export function TicketBoard({ items, groupBy, bar, onOpen }: { items: TicketSummaryDTO[]; groupBy: 'status' | 'bucket'; bar: number; onOpen: (t: TicketSummaryDTO) => void }) {
  const columns = groupBy === 'status' ? statusColumns(items) : bucketColumns(items);
  if (groupBy === 'bucket' && !columns.length)
    return (
      <div className={s.boardEmpty}>
        <EmptyState title="No tickets match these filters" text="Clear a filter or pick another board to see more." />
      </div>
    );
  return (
    <div className={s.board} role="list" aria-label={groupBy === 'status' ? 'Tickets by status' : 'Tickets by query type'}>
      {columns.map((col, ci) => (
        <section key={col.key} className={s.col} style={{ background: col.bg, animationDelay: `${ci * 0.05}s` }} aria-label={`${col.label}, ${col.cards.length}`} role="listitem">
          <header className={s.colHead}>
            <div className={s.colTitleRow}>
              <Dot color={col.dot} />
              <h2 className={s.colTitle}>{col.label}</h2>
              <span className={`${s.colCount} mono`}>{col.cards.length}</span>
            </div>
            <div className={s.colMetaRow}>
              <div className={s.load} aria-hidden>
                <div className={s.loadFill} style={{ width: `${col.loadPct}%`, background: col.dot, animationDelay: `${ci * 0.05}s` }} />
              </div>
              <span className={s.colMeta}>{col.meta}</span>
            </div>
          </header>
          <div className={s.colBody}>
            {col.cards.map((t, i) => {
              const lane = LANE_TONE[t.lane];
              const breach = t.sla.tone === 'late' || t.sla.tone === 'almost_late';
              return (
                <button
                  key={t.id}
                  type="button"
                  className={s.card}
                  style={{ borderColor: breach ? 'var(--bad-line)' : undefined, animationDelay: `${0.1 + i * 0.03}s` }}
                  onClick={() => onOpen(t)}
                  aria-label={`${t.number}: ${t.subject}`}
                >
                  <span className={s.cardTop}>
                    <LanePill fg={lane.fg} bg={lane.bg} word={lane.word} />
                    <span className={`${s.cardId} mono`}>{t.number}</span>
                    <span style={{ marginLeft: 'auto' }}>
                      <SlaChip sla={t.sla} />
                    </span>
                  </span>
                  <span className={s.cardSubj}>{t.subject}</span>
                  <span className={s.cardSub}>{groupBy === 'status' ? t.bucket : STATUS_WORD[statusGroup(t.status)]}</span>
                  <span className={s.cardFoot}>
                    <OwnerDot t={t} />
                    <span className={s.cardNext}>{t.nextMove}</span>
                    <span className={`${s.cardConf} mono`} style={{ color: confTone(t.confidence, bar) }} title="Confidence">
                      {t.confidence.toFixed(2)}
                    </span>
                  </span>
                </button>
              );
            })}
            {!col.cards.length && <div className={s.nothing}>Nothing here</div>}
          </div>
        </section>
      ))}
    </div>
  );
}
