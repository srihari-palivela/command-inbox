import type { SlaDTO, TicketStatus } from '@ci/contracts';

export interface SlaInput {
  status: TicketStatus | string;
  dueAt: Date | null;
  slaMinutes: number;
  pausedAt: Date | null;
}

/**
 * Deadline pressure. Late if past due; almost late inside the last hour or last 10% of budget;
 * due soon inside the last half of budget. The clock is paused while waiting on the customer.
 */
export function computeSla(t: SlaInput, now: Date): SlaDTO {
  const budget = t.slaMinutes;
  if (t.status === 'resolved' || t.status === 'closed') {
    return { tone: 'closed', minutesLeft: null, budgetMinutes: budget, dueAt: null };
  }
  if (!t.dueAt) return { tone: 'on_track', minutesLeft: null, budgetMinutes: budget, dueAt: null };
  const ref = t.pausedAt ?? now;
  const left = Math.round((t.dueAt.getTime() - ref.getTime()) / 60_000);
  const dueAt = t.dueAt.toISOString();
  if (t.pausedAt) return { tone: 'paused', minutesLeft: left, budgetMinutes: budget, dueAt };
  if (left < 0) return { tone: 'late', minutesLeft: left, budgetMinutes: budget, dueAt };
  if (left <= 60 || left <= budget * 0.1)
    return { tone: 'almost_late', minutesLeft: left, budgetMinutes: budget, dueAt };
  if (left <= budget * 0.5) return { tone: 'due_soon', minutesLeft: left, budgetMinutes: budget, dueAt };
  return { tone: 'on_track', minutesLeft: left, budgetMinutes: budget, dueAt };
}

export const atRisk = (tone: SlaDTO['tone']): boolean =>
  tone === 'due_soon' || tone === 'almost_late' || tone === 'late';

/** SLA budget in minutes by priority and segment. */
export function slaBudget(priority: string, segment: string, escalation = false): number {
  if (escalation) return 8 * 60;
  if (priority === 'P1' && segment === 'Corporate') return 4 * 60;
  if (priority === 'P1') return 8 * 60;
  return 24 * 60;
}
