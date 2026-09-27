import type { Lane, TicketDetailDTO } from '@ci/contracts';
import { LANE_AUTONOMY, LANE_NAME } from '@ci/contracts';
import { useEffect, useState } from 'react';
import { confTone, confWord, LANE_TONE } from '../../lib/presentation';
import { Button, Card, Dot, Eyebrow, MenuItem, Pill, Popover } from '../../ui';
import type { TicketActions } from './actions';
import s from './Inbox.module.css';

const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** Reveal the reasoning as it would stream from the model; instant when the user prefers it or motion is reduced. */
function useStreamed(text: string, key: string, enabled: boolean): { shown: string; typing: boolean } {
  const animate = enabled && !reducedMotion();
  const [n, setN] = useState(animate ? 0 : text.length);
  useEffect(() => {
    if (!animate) {
      setN(text.length);
      return;
    }
    setN(0);
    const step = Math.max(2, Math.ceil(text.length / 70)); // ~1.2s regardless of length
    const iv = setInterval(() => {
      setN((v) => {
        if (v + step >= text.length) {
          clearInterval(iv);
          return text.length;
        }
        return v + step;
      });
    }, 16);
    return () => clearInterval(iv);
    // Re-stream only when the ticket changes, not on every refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, animate]);
  return { shown: text.slice(0, n), typing: n < text.length };
}

function laneSub(t: TicketDetailDTO): string {
  if (t.lane !== t.originalLane) return `Overridden · was ${LANE_TONE[t.originalLane].word}`;
  return t.laneNote;
}

export function Triage({ t, actions, stream, showWhy, onToggleWhy, confidenceBar }: {
  t: TicketDetailDTO;
  actions: TicketActions;
  stream: boolean;
  showWhy: boolean;
  onToggleWhy: () => void;
  confidenceBar: number;
}) {
  const tri = t.triage;
  const { shown, typing } = useStreamed(tri?.reasoning ?? '', t.id, stream);
  const [ovOpen, setOvOpen] = useState(false);
  const lane = LANE_TONE[t.lane];
  const conf = t.confidence;
  const above = conf >= confidenceBar;

  const choose = (l: Lane) => {
    setOvOpen(false);
    actions.override.mutate(l);
  };

  return (
    <Card
      title={
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <Dot color="var(--accent)" pulse={typing} /> Agent triage
        </span>
      }
      meta={tri ? `classified in ${(tri.latencyMs / 1000).toFixed(1)}s · ${tri.provider}` : 'not classified yet'}
      actions={
        <>
          <div style={{ position: 'relative' }}>
            <Button size="sm" onClick={() => setOvOpen((v) => !v)} aria-haspopup="menu" aria-expanded={ovOpen} disabled={!t.permissions.canWork}>
              Override
            </Button>
            <Popover open={ovOpen} onClose={() => setOvOpen(false)} label="Change how this is handled" style={{ top: 32, right: 0, width: 290 }}>
              <div style={{ padding: '10px 12px 6px' }}>
                <Eyebrow>Change how it's handled</Eyebrow>
                <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 4, lineHeight: 1.45 }}>
                  Giving the AI more room needs a team lead. Taking it back is always allowed.
                </div>
              </div>
              <div style={{ padding: 6 }} role="menu">
                {(['auto', 'draft', 'manual'] as Lane[])
                  .filter((l) => l !== t.lane)
                  .map((l) => {
                    const up = LANE_AUTONOMY[l] > LANE_AUTONOMY[t.lane];
                    const blocked = up && !t.permissions.canOverrideUp;
                    const tone = LANE_TONE[l];
                    return blocked ? (
                      <div key={l} className="mono" style={{ padding: '8px 10px', fontSize: 11.5, color: 'var(--muted)' }} title="Only a team lead can give the AI more autonomy on a ticket.">
                        <Pill fg={tone.fg} bg={tone.bg}>
                          {tone.word}
                        </Pill>{' '}
                        {LANE_NAME[l]} — team lead only
                      </div>
                    ) : (
                      <MenuItem key={l} onClick={() => choose(l)}>
                        <Pill fg={tone.fg} bg={tone.bg}>
                          {tone.word}
                        </Pill>
                        <span style={{ marginLeft: 8 }}>{LANE_NAME[l]}</span>
                      </MenuItem>
                    );
                  })}
              </div>
            </Popover>
          </div>
          <Button size="sm" onClick={onToggleWhy} aria-expanded={showWhy}>
            {showWhy ? 'Hide reasoning' : 'Show reasoning'}
          </Button>
        </>
      }
      flush
    >
      <div className={s.triGrid}>
        <div className={s.triCell}>
          <div className={s.label}>What kind of query</div>
          <div className={s.big}>{t.bucket}</div>
          <div className={s.small}>{t.department}</div>
        </div>
        <div className={s.triCell}>
          <div className={s.label}>How it gets handled</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <Pill fg={lane.fg} bg={lane.bg}>
              {lane.word}
            </Pill>
            <span className={s.big}>{LANE_NAME[t.lane]}</span>
          </div>
          <div className={s.small}>{laneSub(t)}</div>
        </div>
        <div className={s.triCell}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
            <span className={s.label}>Confidence</span>
            <span className="mono" style={{ fontSize: 15, fontWeight: 600, color: confTone(conf, confidenceBar) }}>
              {conf.toFixed(2)}
            </span>
          </div>
          <div className={s.confTrack} role="meter" aria-valuenow={Math.round(conf * 100)} aria-valuemin={0} aria-valuemax={100} aria-label={`Confidence ${confWord(conf, confidenceBar)}, ${conf.toFixed(2)}; bar ${confidenceBar}`}>
            <div className={s.confFill} style={{ width: `${conf * 100}%`, background: confTone(conf, confidenceBar) }} />
            <div className={s.confBar} style={{ left: `${confidenceBar * 100}%` }} />
          </div>
          <div className="mono" style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--muted)', gap: 8 }}>
            <span>bar for acting alone · {confidenceBar.toFixed(2)}</span>
            <span>{above ? 'above the bar' : 'below — a person must handle it'}</span>
          </div>
        </div>
      </div>
      {showWhy && tri && (
        <div className={s.why}>
          <Eyebrow>Why this routing</Eyebrow>
          <p className={s.reason} aria-live="off">
            {shown}
            {typing && <span className={s.caret} aria-hidden />}
          </p>
          {!typing && (
            <div className={s.evidence}>
              {tri.evidence.map((e, i) => (
                <div key={i} className={s.ev} style={{ animationDelay: `${i * 0.06}s` }}>
                  <span className={s.evTag}>{e.tag.toUpperCase()}</span>
                  <span style={{ minWidth: 0 }}>{e.quote}</span>
                  <span className={s.evWhy}>{e.why}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
