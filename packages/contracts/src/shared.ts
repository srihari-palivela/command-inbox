import type { Lane, TicketStatus } from './enums.js';

/** Board column key for a ticket status (the Tickets board groups by these). */
export type StatusGroup = 'triage' | 'approval' | 'executing' | 'human' | 'customer' | 'resolved';

export const STATUS_GROUP: Record<TicketStatus, StatusGroup> = {
  triaging: 'triage',
  awaiting_approval: 'approval',
  executing: 'executing',
  with_human: 'human',
  waiting_customer: 'customer',
  resolved: 'resolved',
  closed: 'resolved',
};

export const STATUS_LABEL: Record<TicketStatus, string> = {
  triaging: 'Triaging',
  awaiting_approval: 'Waiting on approval',
  executing: 'In progress',
  with_human: 'With a human',
  waiting_customer: 'Waiting on customer',
  resolved: 'Resolved',
  closed: 'Closed',
};

export const LANE_WORD: Record<Lane, string> = { auto: 'Auto', draft: 'Draft', manual: 'You' };
export const LANE_NAME: Record<Lane, string> = {
  auto: 'Auto — the AI does it',
  draft: 'Draft — you send it',
  manual: 'You — the AI steps back',
};

/** Autonomy order: moving toward a higher number gives the AI more freedom. */
export const LANE_AUTONOMY: Record<Lane, number> = { manual: 0, draft: 1, auto: 2 };

export const DEFAULT_CONFIDENCE_BAR = 0.78;

export function confidenceWord(c: number): 'High' | 'Moderate' | 'Low' {
  return c >= 0.9 ? 'High' : c >= DEFAULT_CONFIDENCE_BAR ? 'Moderate' : 'Low';
}

export function ticketNumber(n: number): string {
  return `QRY-${n}`;
}

export function initialsOf(name: string): string {
  return name
    .replace(/[^A-Za-z .]/g, '')
    .split(/[\s.]+/)
    .filter(Boolean)
    .map((p) => p[0]!.toUpperCase())
    .join('')
    .slice(0, 2);
}

/** Minor units (paise) → "₹0.19" style label. */
export function rupees(minor: number, digits = 2): string {
  return '₹' + (minor / 100).toFixed(digits);
}

/** Minutes → "2h 48m" / "26m". */
export function formatMinutes(mins: number): string {
  const m = Math.max(0, Math.round(mins));
  const h = Math.floor(m / 60);
  const r = m % 60;
  if (!h) return `${r}m`;
  return r ? `${h}h ${String(r).padStart(2, '0')}m` : `${h}h`;
}
