import type { ClearanceBody, ClearanceLevel, PeopleDTO, StaffDTO } from '@ci/contracts';
import { useQueryClient } from '@tanstack/react-query';
import type { CSSProperties } from 'react';
import { api } from '../../lib/api';
import { loadTone, TEAM_ROLE_LABEL } from '../../lib/presentation';
import { keys, useAction, usePeople } from '../../lib/queries';
import { Avatar, Card, Dot, EmptyState, Loadable, Meter, Page, PageHeader, cx } from '../../ui';
import { AVAIL, forbiddenText } from '../insights/bits';
import s from './People.module.css';

/** Clearance levels, as the prototype tints them. The server stores 0–3. */
const CLEAR_LEVELS: Record<ClearanceLevel, { label: string; short: string; bg: string; fg: string }> = {
  0: { label: 'None', short: '—', bg: 'var(--surface-4)', fg: 'var(--dot-idle)' },
  1: { label: 'Can read', short: 'Read', bg: 'var(--surface-3)', fg: 'var(--text-2)' },
  2: { label: 'Can resolve', short: 'Resolve', bg: 'var(--accent-bg)', fg: 'var(--accent)' },
  3: { label: 'Can approve', short: 'Approve', bg: 'var(--ok-bg)', fg: 'var(--ok)' },
};
const nextLevel = (l: ClearanceLevel) => ((l + 1) % 4) as ClearanceLevel;

export default function PeopleScreen() {
  const q = usePeople();
  return (
    <Page>
      <PageHeader
        title="Skills & clearance"
        subtitle="What each person is cleared to handle. Tickets are only ever routed to someone cleared for that team's work."
        actions={
          <ul className={s.legend} aria-label="Clearance levels">
            {([1, 2, 3] as const).map((n) => (
              <li key={n} className={s.legendItem}>
                <span
                  className={s.legendBox}
                  style={{ background: CLEAR_LEVELS[n].bg, borderColor: CLEAR_LEVELS[n].fg }}
                  aria-hidden
                />
                {CLEAR_LEVELS[n].label}
              </li>
            ))}
          </ul>
        }
      />
      {forbiddenText(q.error) ? (
        <EmptyState
          title="Team lead or admin only"
          text={`${forbiddenText(q.error)} Ask your team lead if you need this view.`}
        />
      ) : (
        <Loadable query={q}>{(d) => <Matrix d={d} />}</Loadable>
      )}
    </Page>
  );
}

function Matrix({ d }: { d: PeopleDTO }) {
  const qc = useQueryClient();
  const set = useAction(
    (b: ClearanceBody & { name: string; dept: string }) =>
      api.put('/v1/clearances', { userId: b.userId, departmentId: b.departmentId, level: b.level }),
    {
      invalidate: [keys.people, keys.performance],
      success: (_r, b) => `${b.name} · ${b.dept} → ${CLEAR_LEVELS[b.level as ClearanceLevel].label}`,
    },
  );

  const cycle = (p: StaffDTO, dept: { id: string; name: string }, current: ClearanceLevel) => {
    const level = nextLevel(current);
    void qc.cancelQueries({ queryKey: keys.people });
    const prev = qc.getQueryData<PeopleDTO>(keys.people);
    // Optimistic: show the new level straight away, roll back if the server refuses.
    qc.setQueryData<PeopleDTO>(
      keys.people,
      (old) =>
        old && {
          ...old,
          staff: old.staff.map((x) =>
            x.id === p.id ? { ...x, clearances: { ...x.clearances, [dept.id]: level } } : x,
          ),
        },
    );
    set.mutate(
      { userId: p.id, departmentId: dept.id, level, name: p.name, dept: dept.name },
      { onError: () => prev && qc.setQueryData(keys.people, prev) },
    );
  };

  const cols: CSSProperties = {
    gridTemplateColumns: `minmax(210px, 250px) repeat(${d.departments.length}, minmax(0, 1fr)) 140px`,
  };

  return (
    <Card
      flush
      className={s.card}
      title="Clearance matrix"
      actions={
        <span className={s.hint}>
          {d.editable
            ? 'Click any cell to raise or lower clearance.'
            : 'Read-only for staff — switch to Team lead to edit.'}
        </span>
      }
    >
      {d.staff.length === 0 ? (
        <EmptyState title="No one on this team yet" text="People appear here once they join the workspace." />
      ) : (
        <div className={s.scroll}>
          <div role="table" aria-label="Clearance matrix" className={s.table}>
            <div role="row" className={cx(s.row, s.head)} style={cols}>
              <span role="columnheader" className={s.eyebrow}>
                Person
              </span>
              {d.departments.map((dep) => (
                <span role="columnheader" key={dep.id} className={s.dept}>
                  {dep.name}
                </span>
              ))}
              <span role="columnheader" className={s.eyebrow}>
                Load
              </span>
            </div>
            {d.staff.map((p, i) => {
              const av = AVAIL[p.availability];
              const tone = loadTone(p.open, p.capacity);
              const pct = Math.round((p.open / Math.max(1, p.capacity)) * 100);
              return (
                <div
                  role="row"
                  key={p.id}
                  className={s.row}
                  style={{ ...cols, animationDelay: `${(i * 0.04).toFixed(2)}s` }}
                >
                  <div role="rowheader" className={s.person}>
                    <Avatar
                      initials={p.initials}
                      size={26}
                      fg={p.isMe ? 'var(--accent)' : 'var(--text-2)'}
                      bg={p.isMe ? 'var(--accent-bg)' : 'var(--surface-3)'}
                    />
                    <div style={{ minWidth: 0 }}>
                      <div className={s.nameRow}>
                        <Dot color={av.dot} size={6} />
                        <span className={s.name}>{p.name}</span>
                        {p.isMe && <span className={s.you}>· you</span>}
                        <span className="sr-only">, {av.label}</span>
                      </div>
                      <div className={s.sub} title={`${p.title} · ${TEAM_ROLE_LABEL[p.role]} · ${av.label}`}>
                        {p.title} · {p.checkin || av.label}
                      </div>
                    </div>
                  </div>
                  {d.departments.map((dep) => {
                    const lvl = (p.clearances[dep.id] ?? 0) as ClearanceLevel;
                    const m = CLEAR_LEVELS[lvl];
                    const style = { background: m.bg, color: m.fg };
                    return (
                      <div role="cell" key={dep.id}>
                        {d.editable ? (
                          <button
                            type="button"
                            className={cx(s.cell, s.cellBtn)}
                            style={style}
                            onClick={() => cycle(p, dep, lvl)}
                            aria-label={`${p.name}, ${dep.name}: ${m.label}. Change to ${CLEAR_LEVELS[nextLevel(lvl)].label}`}
                            title={`${m.label} — click for ${CLEAR_LEVELS[nextLevel(lvl)].label}`}
                          >
                            {m.short}
                          </button>
                        ) : (
                          <span className={s.cell} style={style} title={m.label}>
                            <span aria-hidden>{m.short}</span>
                            <span className="sr-only">{m.label}</span>
                          </span>
                        )}
                      </div>
                    );
                  })}
                  <div role="cell" className={s.load}>
                    <div className={s.loadTop}>
                      <span className={cx('mono', s.loadNum)} style={{ color: tone }}>
                        {p.open} / {p.capacity}
                      </span>
                      <span className={s.pod}>{p.pod}</span>
                    </div>
                    <Meter pct={pct} color={tone} height={4} label={`${p.name} load ${pct}%`} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </Card>
  );
}
