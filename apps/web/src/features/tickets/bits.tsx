/**
 * Small presentational pieces shared by the Tickets board, list and drawer.
 */
import type { SlaDTO, TicketSummaryDTO } from '@ci/contracts';
import { minutesLeftLabel } from '../../lib/format';
import { SLA_TONE, type Tone } from '../../lib/presentation';
import s from './Tickets.module.css';

/** The prototype shows an on-track deadline in neutral grey; only pressure gets colour. */
export function slaChipTone(sla: SlaDTO): Tone {
  switch (sla.tone) {
    case 'closed':
      return { fg: 'var(--ok)', bg: 'var(--ok-bg-2)' };
    case 'late':
    case 'almost_late':
      return { fg: 'var(--bad)', bg: 'var(--bad-bg)' };
    case 'due_soon':
      return { fg: 'var(--warn)', bg: 'var(--warn-bg)' };
    default:
      return { fg: 'var(--muted)', bg: 'var(--surface-4)' };
  }
}

export function slaLabel(sla: SlaDTO): string {
  if (sla.tone === 'closed') return 'Closed';
  if (sla.tone === 'paused') return 'Paused';
  return minutesLeftLabel(sla.minutesLeft);
}

export function SlaChip({ sla, suffix }: { sla: SlaDTO; suffix?: string }) {
  const t = slaChipTone(sla);
  const label = slaLabel(sla);
  const withSuffix = suffix && sla.tone !== 'closed' && sla.tone !== 'paused' && sla.minutesLeft !== null && sla.minutesLeft >= 0 ? `${label} ${suffix}` : label;
  return (
    <span className={s.sla} style={{ color: t.fg, background: t.bg }} title={`Deadline: ${SLA_TONE[sla.tone].label}`}>
      {withSuffix}
    </span>
  );
}

export function LanePill({ fg, bg, word, large }: { fg: string; bg: string; word: string; large?: boolean }) {
  return (
    <span className={large ? s.laneLg : s.lane} style={{ color: fg, background: bg }}>
      {word}
    </span>
  );
}

export function ownerOf(t: Pick<TicketSummaryDTO, 'ownerKind' | 'assignee'>): { name: string; init: string; fg: string; bg: string } {
  if (t.ownerKind === 'ai') return { name: 'Agent', init: 'AI', fg: 'var(--accent)', bg: 'var(--accent-bg)' };
  if (t.ownerKind === 'unassigned' || !t.assignee) return { name: 'Unassigned', init: '?', fg: 'var(--warn)', bg: 'var(--warn-bg)' };
  return { name: t.assignee.name, init: t.assignee.initials, fg: 'var(--text-2)', bg: 'var(--surface-3)' };
}

export function OwnerDot({ t, size = 18 }: { t: Pick<TicketSummaryDTO, 'ownerKind' | 'assignee'>; size?: number }) {
  const o = ownerOf(t);
  return (
    <span className={s.ownerDot} style={{ width: size, height: size, color: o.fg, background: o.bg }} title={`Owner: ${o.name}`} aria-label={`Owner: ${o.name}`}>
      {o.init}
    </span>
  );
}
