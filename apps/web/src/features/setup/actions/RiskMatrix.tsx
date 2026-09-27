/**
 * Left card: the 2×2 risk matrix and the autonomy dial for the selected cell. Levels a cell may never
 * reach, and every level of the locked cell, are disabled with the reason; the server enforces the
 * same limits.
 */
import type { ActionsDTO, RiskCell, RiskCellDTO } from '@ci/contracts';
import { api } from '../../../lib/api';
import { keys, useAction } from '../../../lib/queries';
import { Eyebrow } from '../../../ui';
import { cellKey, cellLook, cellNote, COLS, DIAL_LABELS, dialBlockedReason, gateLook, ROWS } from './cells';
import s from './actions.module.css';

export function RiskMatrix({ data, selected, onSelect, canDial }: { data: ActionsDTO; selected: RiskCellDTO; onSelect: (c: RiskCell) => void; canDial: boolean }) {
  const autoIn00 = data.templates.filter((t) => t.cell === '0-0' && t.approval === 'auto').length;
  const setDial = useAction((v: { cell: RiskCell; level: number }) => api.put('/v1/actions/dial', v), {
    invalidate: [keys.actions, keys.me],
    success: (_r, v) => `Autonomy for “${data.cells.find((c) => c.cell === v.cell)?.title ?? v.cell}” set to ${DIAL_LABELS[v.level]?.toLowerCase()}.`,
  });
  const gate = gateLook(selected);

  return (
    <div className={s.matrixCard}>
      <div className={s.mGrid} aria-hidden>
        <span />
        {COLS.map((c) => (
          <span key={c.label} className={s.colHead}>
            {c.label}
          </span>
        ))}
      </div>
      <div role="group" aria-label="Risk groups">
        {ROWS.map((row) => (
          <div key={row.label} className={s.mGrid}>
            <span className={s.rowHead} aria-hidden>
              {row.label}
            </span>
            {COLS.map((col) => {
              const key = cellKey(row.money, col.irreversible);
              const c = data.cells.find((x) => x.cell === key);
              if (!c) return <span key={key} />;
              const look = cellLook(c);
              const on = c.cell === selected.cell;
              return (
                <button
                  key={key}
                  type="button"
                  className={s.cell}
                  aria-pressed={on}
                  aria-label={`${c.title}: ${look.state}, ${c.count} actions`}
                  onClick={() => onSelect(c.cell)}
                  style={{ borderColor: on ? look.dot : look.line, background: on ? look.tint : undefined }}
                >
                  <div className={s.cellState} style={{ color: look.fg }}>
                    <span className={s.cellDot} style={{ background: look.dot }} />
                    {look.state}
                  </div>
                  <div className={s.cellCount}>{c.count}</div>
                  <div className={s.cellUnit}>actions</div>
                  <div className={s.cellNote}>{cellNote(c, autoIn00)}</div>
                </button>
              );
            })}
          </div>
        ))}
      </div>

      <div className={s.dialBox}>
        <div className={s.dialHead}>
          <span className={s.dialTitle}>{selected.title}</span>
          <span className={s.dialPhase}>{selected.phase}</span>
        </div>
        <Eyebrow>
          <span id="dial-label">Autonomy dial for this cell</span>
        </Eyebrow>
        <div className={s.dial} role="group" aria-labelledby="dial-label">
          {DIAL_LABELS.map((label, level) => {
            const on = selected.dial === level;
            const blocked = dialBlockedReason(selected, level);
            const reason = blocked ?? (canDial ? undefined : 'Only Admin · Risk can change the autonomy dial.');
            return (
              <button
                key={label}
                type="button"
                className={s.stop}
                aria-pressed={on}
                disabled={!!reason || setDial.isPending}
                title={on ? `Current setting${reason && blocked ? ` · ${blocked}` : ''}` : reason}
                onClick={() => !on && setDial.mutate({ cell: selected.cell, level })}
              >
                {label}
              </button>
            );
          })}
        </div>
        {!canDial && <div className={s.readOnly}>Read-only — only Admin · Risk can move the dial.</div>}
        <div className={s.gate} style={{ color: gate.fg }}>
          <span aria-hidden>{gate.icon}</span>
          <span>
            <span className="sr-only">{gate.word}: </span>
            {selected.gateNote}
          </span>
        </div>
      </div>
    </div>
  );
}
