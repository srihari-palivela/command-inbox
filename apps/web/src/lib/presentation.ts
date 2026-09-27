/**
 * Presentation mapping: domain values → words and colours. The server never sends colours; every
 * status carries a word as well as a colour so colour is never the only signal.
 */
import type { Lane, Priority, SlaTone, TicketStatus } from '@ci/contracts';
import { STATUS_GROUP, type StatusGroup } from '@ci/contracts';

export interface Tone {
  fg: string;
  bg: string;
  line?: string;
}

export const LANE_TONE: Record<Lane, Tone & { word: string }> = {
  auto: { fg: 'var(--accent)', bg: 'var(--accent-bg)', word: 'Auto' },
  draft: { fg: 'var(--ok)', bg: 'var(--ok-bg)', word: 'Draft' },
  manual: { fg: 'var(--warn)', bg: 'var(--warn-bg-2)', word: 'You' },
};

export const SLA_TONE: Record<SlaTone, Tone & { label: string }> = {
  on_track: { fg: 'var(--ok)', bg: 'var(--ok-bg-2)', label: 'On track' },
  due_soon: { fg: 'var(--warn)', bg: 'var(--warn-bg)', label: 'Due soon' },
  almost_late: { fg: 'var(--bad)', bg: 'var(--bad-bg)', label: 'Almost late' },
  late: { fg: 'var(--bad-text)', bg: 'var(--bad-bg)', label: 'Late' },
  paused: { fg: 'var(--text-2)', bg: 'var(--surface-3)', label: 'Paused' },
  closed: { fg: 'var(--ok)', bg: 'var(--ok-bg-2)', label: 'Closed' },
};

export const PRIORITY_TONE: Record<Priority, Tone & { label: string; note: string }> = {
  P1: {
    fg: 'var(--bad-text)',
    bg: 'var(--bad-bg)',
    label: 'P1 · Critical',
    note: 'Regulator, fraud, or money at risk today',
  },
  P2: {
    fg: 'var(--warn)',
    bg: 'var(--warn-bg)',
    label: 'P2 · High',
    note: 'Deadline inside 8 hours, or a repeat contact',
  },
  P3: {
    fg: 'var(--accent)',
    bg: 'var(--accent-bg)',
    label: 'P3 · Normal',
    note: 'Standard servicing, inside the day',
  },
  P4: {
    fg: 'var(--text-2)',
    bg: 'var(--surface-3)',
    label: 'P4 · Low',
    note: 'Informational, no deadline pressure',
  },
};

export const STATUS_WORD: Record<StatusGroup, string> = {
  triage: 'Agent triaging',
  approval: 'Awaiting approval',
  executing: 'Executing',
  human: 'With a human',
  customer: 'Waiting on customer',
  resolved: 'Resolved',
};

export const STATUS_TONE: Record<StatusGroup, Tone & { dot: string; meta: string }> = {
  triage: { fg: 'var(--accent)', bg: 'var(--accent-bg)', dot: 'var(--accent)', meta: 'agent owns' },
  approval: { fg: 'var(--warn)', bg: 'var(--warn-bg)', dot: 'var(--warn-dot)', meta: 'you owe a decision' },
  executing: { fg: 'var(--accent)', bg: 'var(--accent-bg)', dot: 'var(--accent)', meta: 'in core systems' },
  human: { fg: 'var(--bad-text)', bg: 'var(--bad-bg)', dot: 'var(--bad)', meta: 'agent stood down' },
  customer: { fg: 'var(--text-2)', bg: 'var(--surface-3)', dot: 'var(--dot-idle)', meta: 'clock paused' },
  resolved: { fg: 'var(--ok)', bg: 'var(--ok-bg)', dot: 'var(--ok-dot)', meta: 'audit written' },
};

export const statusGroup = (s: TicketStatus): StatusGroup => STATUS_GROUP[s];

export function confTone(c: number, bar = 0.78): string {
  return c >= 0.85 ? 'var(--ok)' : c >= bar ? 'var(--accent)' : 'var(--warn)';
}

export function confWord(c: number, bar = 0.78): 'High' | 'Moderate' | 'Low' {
  return c >= 0.9 ? 'High' : c >= bar ? 'Moderate' : 'Low';
}

/** Load bar colour: over capacity red, near capacity amber. */
export function loadTone(open: number, cap: number): string {
  const pct = (open / Math.max(cap, 1)) * 100;
  return pct > 100 ? 'var(--bad)' : pct > 85 ? 'var(--warn)' : 'var(--ok)';
}

export const TEAM_ROLE_LABEL = { staff: 'Staff', lead: 'Team lead', admin: 'Admin' } as const;
