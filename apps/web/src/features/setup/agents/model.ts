/**
 * Presentation logic for the AI agents screen: eval colours, state words, and the calibration verdict.
 */
import type { AgentDTO, AgentState, CalibrationBandDTO } from '@ci/contracts';

export const STATE_WORD: Record<AgentState, string> = { live: 'Live', observe: 'Observe', paused: 'Paused' };

export const stateLine = (a: Pick<AgentDTO, 'state' | 'version'>) => `${STATE_WORD[a.state]} · v${a.version}`;

/** ≥93 green, ≥88 indigo, else amber — the prototype's eval bands. */
export function evalColor(score: number | null): string {
  if (score === null) return 'var(--muted)';
  return score >= 93 ? 'var(--ok)' : score >= 88 ? 'var(--accent)' : 'var(--warn)';
}

export const evalToneColor = (t: 'ok' | 'warn' | 'neutral') =>
  t === 'ok' ? 'var(--ok)' : t === 'warn' ? 'var(--warn)' : 'var(--text-2)';

export const boardList = (boards: { name: string }[]) =>
  boards.length === 0
    ? 'No boards'
    : boards.length > 2
      ? `${boards.length} boards`
      : boards.map((b) => b.name).join(', ');

export const isGuard = (a: Pick<AgentDTO, 'template'>) => a.template === 'policy_guard';

/** Bands with fewer outcomes than this are shown but don't count towards the verdict. */
export const MIN_BAND_N = 10;
/** Observed accuracy within this distance of the stated confidence counts as calibrated. */
export const CAL_TOLERANCE = 0.05;

export interface CalibrationVerdict {
  word: string;
  tone: 'ok' | 'warn' | 'neutral';
  /** The band that drove a warning verdict, if any. */
  band?: string;
}

/**
 * One line that says whether the agent's confidence can be trusted: "Well calibrated" when every
 * band with enough outcomes is within 5 points of its stated confidence, otherwise the worst band.
 */
export function calibrationVerdict(bands: CalibrationBandDTO[]): CalibrationVerdict {
  const scored = bands.filter(
    (b): b is CalibrationBandDTO & { observed: number } => b.observed !== null && b.n >= MIN_BAND_N,
  );
  if (!scored.length)
    return {
      word: bands.length ? 'Too few scored outcomes to judge yet' : 'No scored outcomes yet',
      tone: 'neutral',
    };
  let worst = scored[0]!;
  for (const b of scored)
    if (Math.abs(b.observed - b.predicted) > Math.abs(worst.observed - worst.predicted)) worst = b;
  const gap = worst.observed - worst.predicted;
  if (Math.abs(gap) <= CAL_TOLERANCE) return { word: 'Well calibrated', tone: 'ok' };
  return {
    word: `${gap < 0 ? 'Overconfident' : 'Underconfident'} in the ${worst.band} band`,
    tone: 'warn',
    band: worst.band,
  };
}

export const pct = (x: number) => `${Math.round(x * 100)}%`;
