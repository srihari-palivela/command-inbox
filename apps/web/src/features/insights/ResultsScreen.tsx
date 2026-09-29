import type { Lane, ResultsDTO } from '@ci/contracts';
import type { ReactNode } from 'react';
import { useResults } from '../../lib/queries';
import { Card, EmptyState, Loadable, Page, PageHeader, Skeleton, cx } from '../../ui';
import { forbiddenText, Obs, stagger } from './bits';
import s from './Results.module.css';
import { num } from '../../lib/format';

export default function ResultsScreen() {
  return (
    <ResultsQuery>
      {(d) => (
        <>
          <Headline d={d} />
          <div className={s.section}>
            <Coverage coverage={d.coverage} />
          </div>
          <div className={s.pools}>
            {d.pools.map((p, i) => (
              <section key={p.label} className={s.pool} style={stagger(i, 0.06, 0.16)} aria-label={p.label}>
                <div className={s.poolHead}>
                  <span
                    className={s.poolDot}
                    style={{ background: POOL_COLORS[i % POOL_COLORS.length] }}
                    aria-hidden
                  />
                  <h3 className={s.poolTitle}>{p.label}</h3>
                </div>
                <div className={cx('mono', s.poolMetric)}>{p.metric}</div>
                <p className={s.poolNote}>{p.note}</p>
              </section>
            ))}
          </div>
        </>
      )}
    </ResultsQuery>
  );
}

function ResultsQuery({ children }: { children: (d: ResultsDTO) => ReactNode }) {
  const q = useResults();
  return (
    <Page>
      <PageHeader
        title="Results"
        subtitle="The last 14 days, against the baseline your team was hitting before the AI went live."
      />
      {forbiddenText(q.error) ? (
        <EmptyState
          title="Team lead or admin only"
          text={`${forbiddenText(q.error)} Ask your team lead if you need this view.`}
        />
      ) : (
        <Loadable
          query={q}
          skeleton={
            <div style={{ display: 'grid', gap: 13 }}>
              <Skeleton h={330} />
              <Skeleton h={260} />
            </div>
          }
        >
          {children}
        </Loadable>
      )}
    </Page>
  );
}

const POOL_COLORS = ['var(--accent)', 'var(--ok)', 'var(--warn)'];

// ── Headline strip + day-by-day chart ────────────────────────────────────────
function daysDigest(days: ResultsDTO['days']): string | null {
  if (days.length < 8) return null;
  const first = days[0]!.actual;
  const last = days.at(-1)!.actual;
  const wk = days[6]!.actual;
  const drop = first - last;
  if (drop <= 0)
    return `Time to resolve has not fallen over these ${days.length} days (${first}h → ${last}h).`;
  const firstWeekShare = Math.round(((first - wk) / drop) * 100);
  const tail = days.slice(-3);
  const tailMove = Math.abs(tail[0]!.actual - tail.at(-1)!.actual);
  return `Time to resolve fell from ${first}h to ${last}h; ${firstWeekShare}% of that drop came in the first week. The last three days moved ${tailMove.toFixed(1)}h${tailMove < 0.5 ? ' — the curve is flattening' : ''}.`;
}

function Headline({ d }: { d: ResultsDTO }) {
  const max = Math.max(1, ...d.days.flatMap((x) => [x.baseline, x.actual])) * 1.04;
  const digest = daysDigest(d.days);
  const deltaGood = d.deltaPct <= 0;
  return (
    <section className={s.headline} aria-label="Before and after">
      <div className={s.cells}>
        <Cell
          label="Before the AI"
          value={d.baselineHours.toFixed(1)}
          unit="h"
          note="measured across every query type"
          color="var(--text-2)"
        />
        <Cell
          label="Now"
          value={d.nowHours.toFixed(1)}
          unit="h"
          note="same queries, same day"
          color="var(--accent)"
        />
        <Cell
          label="Delta"
          value={`${d.deltaPct > 0 ? '+' : d.deltaPct < 0 ? '−' : ''}${Math.abs(d.deltaPct)}`}
          unit="%"
          note={deltaGood ? 'turnaround reduction' : 'turnaround increase'}
          color={deltaGood ? 'var(--ok)' : 'var(--bad)'}
        />
        <Cell
          label="North star · capacity multiple"
          value={d.capacityMultiple.toFixed(1)}
          unit="×"
          note="queries per FTE vs baseline"
          color="var(--ink)"
          star
        />
      </div>
      <div className={s.chartWrap}>
        <div className={s.chartHead}>
          <h3 className={s.h3}>Average time to resolve, day by day</h3>
          <span className={s.legend}>
            <span className={s.legendItem}>
              <span className={s.swatch} style={{ background: 'var(--disabled)' }} />
              old baseline
            </span>
            <span className={s.legendItem}>
              <span className={s.swatch} style={{ background: 'var(--accent)' }} />
              with the AI
            </span>
          </span>
        </div>
        {d.days.length === 0 ? (
          <EmptyState title="No daily data yet" text="Daily figures arrive with the overnight import." />
        ) : (
          <div
            className={s.chart}
            role="img"
            aria-label={`Average hours to resolve per day. ${d.days.map((x) => `${x.label}: ${x.baseline}h before, ${x.actual}h with the AI`).join('; ')}`}
          >
            {d.days.map((x, i) => (
              <div key={x.label} className={s.day}>
                <div className={s.pair}>
                  <div
                    className={s.barBefore}
                    title={`${x.label}: ${x.baseline}h before`}
                    style={{
                      height: `${(x.baseline / max) * 100}%`,
                      animationDelay: `${(i * 0.035).toFixed(2)}s`,
                    }}
                  />
                  <div
                    className={s.barNow}
                    title={`${x.label}: ${x.actual}h with the AI`}
                    style={{
                      height: `${(x.actual / max) * 100}%`,
                      animationDelay: `${(i * 0.035 + 0.06).toFixed(2)}s`,
                    }}
                  />
                </div>
                <span className={cx('mono', s.dayLabel)}>{x.label}</span>
              </div>
            ))}
          </div>
        )}
        {digest && (
          <Obs footer className={s.chartObs}>
            {digest}
          </Obs>
        )}
      </div>
    </section>
  );
}

function Cell({
  label,
  value,
  unit,
  note,
  color,
  star,
}: {
  label: string;
  value: string;
  unit: string;
  note: string;
  color: string;
  star?: boolean;
}) {
  return (
    <div className={cx(s.cell, star && s.cellStar)}>
      <div className={s.cellLabel}>{label}</div>
      <div className={s.cellValueRow}>
        <span className={cx('mono', s.cellValue)} style={{ color }}>
          {value}
        </span>
        <span className={s.cellUnit}>{unit}</span>
      </div>
      <div className={s.cellNote}>{note}</div>
    </div>
  );
}

// ── Automation coverage ──────────────────────────────────────────────────────
const LANE_COVER: Record<Lane, { label: string; note: string; color: string; text: string }> = {
  auto: {
    label: 'Auto — the AI does it',
    note: 'action executed end to end',
    color: 'var(--accent)',
    text: 'var(--surface)',
  },
  draft: {
    label: 'Draft — you send it',
    note: 'cited draft, human sends',
    color: 'var(--accent-soft-bar)',
    text: 'var(--ink)',
  },
  manual: {
    label: 'You — the AI steps back',
    note: 'pre-triaged, agent stands down',
    color: 'var(--line-strong)',
    text: 'var(--text-2)',
  },
};

function Coverage({ coverage }: { coverage: ResultsDTO['coverage'] }) {
  const manual = coverage.find((c) => c.lane === 'manual');
  const shown = coverage.filter((c) => c.pct > 0);
  return (
    <Card className={s.card} style={stagger(0, 0, 0.1)}>
      <h3 className={s.h3}>Automation coverage</h3>
      <p className={s.sub}>Share of volume the platform can carry today, by lane.</p>
      {shown.length === 0 ? (
        <EmptyState title="No volume yet" text="Coverage appears once query types carry monthly volume." />
      ) : (
        <>
          <div
            className={s.stack}
            role="img"
            aria-label={coverage.map((c) => `${LANE_COVER[c.lane].label}: ${c.pct}%`).join(', ')}
          >
            {shown.map((c, i) => (
              <div
                key={c.lane}
                className={s.stackSeg}
                style={{
                  width: `${c.pct}%`,
                  background: LANE_COVER[c.lane].color,
                  animationDelay: `${(i * 0.09).toFixed(2)}s`,
                }}
              >
                <span className="mono" style={{ color: LANE_COVER[c.lane].text }}>
                  {c.pct}%
                </span>
              </div>
            ))}
          </div>
          <ul className={s.coverList}>
            {coverage.map((c) => {
              const l = LANE_COVER[c.lane];
              return (
                <li key={c.lane} className={s.coverRow}>
                  <span className={s.swatch} style={{ background: l.color }} aria-hidden />
                  <span className={s.coverLabel}>{l.label}</span>
                  <span className={s.coverNote}>{l.note}</span>
                  <span className={cx('mono', s.coverVol)}>{num(c.volume)} q</span>
                </li>
              );
            })}
          </ul>
        </>
      )}
      {manual && (
        <Obs>
          Coverage grows by moving Draft volume to Auto, not by touching the human {manual.pct}% — that share
          stays with people by design.
        </Obs>
      )}
    </Card>
  );
}
