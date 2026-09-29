import type { Capability, StaffDTO } from '@ci/contracts';
import type { CSSProperties, ReactNode } from 'react';
import { ApiError } from '../../lib/api';
import { useMe } from '../../lib/queries';
import { cx } from '../../ui';
import s from './bits.module.css';

/** The signed-in person's capabilities; the server still enforces every one of them. */
export function useCan(): (cap: Capability) => boolean {
  const me = useMe().data;
  const caps = new Set(me?.capabilities ?? []);
  return (cap) => caps.has(cap);
}

/** "OBS" digest line — the agent's one-sentence reading of a chart. */
export function Obs({
  children,
  footer,
  className,
  style,
}: {
  children: ReactNode;
  footer?: boolean;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <div className={cx(s.obs, footer && s.obsFooter, className)} style={style}>
      <span className={s.obsTag} aria-label="Observation">
        OBS
      </span>
      <span className={s.obsText}>{children}</span>
    </div>
  );
}

/** Compact age for feed items: "now", "32m", "1h", "3d". */
export function sinceShort(iso: string, now = Date.now()): string {
  const m = Math.round((now - new Date(iso).getTime()) / 60_000);
  if (m < 1) return 'now';
  if (m < 60) return `${m}m`;
  if (m < 1440) return `${Math.round(m / 60)}h`;
  return `${Math.round(m / 1440)}d`;
}

/** Staggered animation delay, as the prototype does it. */
export const stagger = (i: number, step = 0.05, base = 0): CSSProperties => ({
  animationDelay: `${(base + i * step).toFixed(2)}s`,
});

export function fmtNum(n: number): string {
  return Number.isInteger(n) ? n.toLocaleString('en-IN') : String(Math.round(n * 100) / 100);
}

export const AVAIL: Record<StaffDTO['availability'], { dot: string; fg: string; label: string }> = {
  available: { dot: 'var(--ok-dot)', fg: 'var(--ok)', label: 'Available' },
  busy: { dot: 'var(--warn-dot)', fg: 'var(--warn)', label: 'Busy' },
  away: { dot: 'var(--dot-idle)', fg: 'var(--muted)', label: 'Away' },
};

/** A 403 from the server: say who can see this instead of offering a pointless retry. */
export function forbiddenText(error: unknown): string | null {
  return error instanceof ApiError && error.status === 403 ? error.problem.title : null;
}
