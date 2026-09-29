import type { SpanStatus, TraceDTO } from '@ci/contracts';
import { Card, EmptyState, Pill } from '../../ui';
import s from './Inbox.module.css';
import { money } from '../../lib/format';

const STATUS: Record<SpanStatus, { word: string; fg: string; bg: string; ring: string }> = {
  ok: { word: 'OK', fg: 'var(--ok)', bg: 'var(--ok-bg)', ring: 'var(--ok-dot)' },
  flag: { word: 'FLAGGED', fg: 'var(--warn)', bg: 'var(--warn-bg)', ring: 'var(--warn-dot)' },
  stop: { word: 'STOPPED', fg: 'var(--bad-text)', bg: 'var(--bad-bg)', ring: 'var(--bad)' },
};

const ms = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${v}ms`);
const tokens = (n: number | null) => (n === null ? '—' : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

export function Trace({ trace }: { trace: TraceDTO | null }) {
  if (!trace)
    return (
      <EmptyState
        title="No trace yet"
        text="The trace appears as soon as the agents have looked at this ticket."
      />
    );
  return (
    <Card
      title="Execution trace"
      meta={<span className="mono">{trace.traceId}</span>}
      actions={
        <span style={{ fontSize: 11.5, color: 'var(--muted)' }}>
          classified in{' '}
          <b className="mono" style={{ color: 'var(--ink)' }}>
            {ms(trace.totalMs)}
          </b>{' '}
          · model cost{' '}
          <b className="mono" style={{ color: 'var(--ink)' }}>
            {money(trace.costMinor)}
          </b>
        </span>
      }
      flush
    >
      <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {trace.spans.map((sp, i) => {
          const st = STATUS[sp.status];
          return (
            <li key={sp.seq} className={s.span} style={{ animationDelay: `${i * 0.04}s` }}>
              <span className={s.spanN} style={{ borderColor: st.ring, color: st.fg }} aria-hidden>
                {sp.seq}
              </span>
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <b style={{ fontSize: 13.5 }}>{sp.agent}</b>
                  <span
                    className="mono"
                    style={{
                      fontSize: 10,
                      color: 'var(--muted)',
                      border: '1px solid var(--line)',
                      borderRadius: 4,
                      padding: '1px 5px',
                    }}
                  >
                    {sp.model}
                  </span>
                  <Pill fg={st.fg} bg={st.bg} mono>
                    {st.word}
                  </Pill>
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--text-2)', marginTop: 3 }}>{sp.action}</div>
                <div className={s.spanOut}>
                  <b>OUT</b> {sp.output}
                </div>
                <div className="mono" style={{ fontSize: 10, color: 'var(--muted)', marginTop: 4 }}>
                  tokens {tokens(sp.tokens)} · cost {sp.costMinor === null ? '—' : money(sp.costMinor)}
                </div>
              </div>
              <span className="mono" style={{ fontSize: 11, color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                +{ms(sp.offsetMs)} · {ms(sp.latencyMs)}
              </span>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
