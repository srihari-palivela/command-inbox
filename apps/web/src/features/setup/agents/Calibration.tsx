/**
 * Calibration (watchlist: overconfidence drift). For each confidence band, the accuracy the agent
 * claimed (band midpoint) next to the accuracy it actually achieved, with the sample size.
 */
import type { CalibrationBandDTO } from '@ci/contracts';
import { cx, Eyebrow, Meter, Pill } from '../../../ui';
import { CAL_TOLERANCE, calibrationVerdict, MIN_BAND_N, pct } from './model';
import s from './agents.module.css';

const PREDICTED = 'var(--line-strong)';

export function Calibration({ bands }: { bands: CalibrationBandDTO[] }) {
  const v = calibrationVerdict(bands);
  const tone = v.tone === 'ok' ? { fg: 'var(--ok)', bg: 'var(--ok-bg)', word: 'Calibrated' } : v.tone === 'warn' ? { fg: 'var(--warn)', bg: 'var(--warn-bg)', word: 'Drifting' } : { fg: 'var(--text-2)', bg: 'var(--surface-3)', word: 'Not enough data' };
  return (
    <section className={s.section} aria-labelledby="cal-head">
      <div className={s.sectionHead}>
        <Eyebrow>
          <span id="cal-head">Calibration</span>
        </Eyebrow>
        <span className={s.hint}>does its confidence mean what it says?</span>
      </div>
      <div className={s.calVerdict}>
        <Pill fg={tone.fg} bg={tone.bg}>
          {tone.word}
        </Pill>
        <span>{v.word}</span>
      </div>
      {bands.length > 0 && (
        <div className={s.calBox}>
          <div className={s.calLegend} aria-hidden>
            <span>
              <i className={s.swatch} style={{ background: PREDICTED }} /> stated confidence
            </span>
            <span>
              <i className={s.swatch} style={{ background: 'var(--ok)' }} /> observed accuracy
            </span>
            <span style={{ marginLeft: 'auto' }}>outcomes</span>
          </div>
          {bands.map((b, i) => {
            const thin = b.n < MIN_BAND_N || b.observed === null;
            const off = b.observed !== null && Math.abs(b.observed - b.predicted) > CAL_TOLERANCE;
            const obsColor = off ? 'var(--warn-dot)' : 'var(--ok)';
            return (
              <div key={b.band} className={cx(s.calRow, thin && s.calThin)}>
                <span className={s.calBand}>{b.band}</span>
                <div className={s.calBars}>
                  <div className={s.calBar}>
                    <Meter pct={b.predicted * 100} color={PREDICTED} height={5} label={`Stated confidence ${pct(b.predicted)}`} delay={i * 0.04} />
                    <span>{pct(b.predicted)}</span>
                  </div>
                  <div className={s.calBar}>
                    <Meter pct={(b.observed ?? 0) * 100} color={obsColor} height={5} label={`Observed accuracy ${b.observed === null ? 'unknown' : pct(b.observed)}`} delay={i * 0.04 + 0.02} />
                    <span style={{ color: off ? 'var(--warn)' : 'var(--ok)', fontWeight: 600 }}>{b.observed === null ? '—' : pct(b.observed)}</span>
                  </div>
                </div>
                <span className={s.calN} title={thin ? `Fewer than ${MIN_BAND_N} outcomes — not counted in the verdict` : undefined}>
                  n = {b.n}
                  {thin ? ' · thin' : ''}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
