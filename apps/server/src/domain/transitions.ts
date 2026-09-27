import type { TicketStatus } from '@ci/contracts';

/** Status moves a person may make directly. Approval-driven moves happen through the gateway. */
const MANUAL: Record<TicketStatus, TicketStatus[]> = {
  triaging: ['with_human'],
  awaiting_approval: ['with_human', 'waiting_customer'],
  executing: [],
  with_human: ['waiting_customer', 'resolved'],
  waiting_customer: ['with_human', 'resolved'],
  resolved: ['closed', 'with_human'],
  closed: ['with_human'],
};

export function allowedTransitions(from: TicketStatus): TicketStatus[] {
  return MANUAL[from] ?? [];
}

export function canTransition(from: TicketStatus, to: TicketStatus): boolean {
  return allowedTransitions(from).includes(to);
}

export const isOpen = (s: string): boolean => s !== 'resolved' && s !== 'closed';
