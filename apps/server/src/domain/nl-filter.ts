import type { FilterKey, TicketFilters } from '@ci/contracts';

export interface Chip {
  key: FilterKey;
  value: string;
  text: string;
}

export interface ParsedFilters {
  filters: TicketFilters;
  chips: Chip[];
}

/**
 * Deterministic natural-language → filter parser. It is the offline/degraded path for the
 * NL query box and the reference behaviour the model path is evaluated against.
 */
export function parseNaturalFilters(raw: string, departments: { id: string; name: string }[]): ParsedFilters {
  const q = ' ' + raw.toLowerCase().trim() + ' ';
  const has = (...w: string[]) => w.some((x) => q.includes(x));
  const filters: TicketFilters = {};
  const chips: Chip[] = [];
  const add = <K extends FilterKey>(key: K, value: NonNullable<TicketFilters[K]>, text: string) => {
    (filters as Record<string, unknown>)[key] = value;
    chips.push({ key, value: String(value), text });
  };

  if (has('triaging', 'being sorted')) add('status', 'triage', 'Agent triaging');
  else if (has('approval', 'approve', 'sign off', 'waiting on me'))
    add('status', 'approval', 'Awaiting approval');
  else if (has('executing', 'in progress')) add('status', 'executing', 'Executing');
  else if (has('with a human', 'with a person', 'handed over')) add('status', 'human', 'With a human');
  else if (has('waiting on customer', 'waiting for the customer'))
    add('status', 'customer', 'Waiting on customer');
  else if (has('closed', 'resolved', ' done ')) add('status', 'resolved', 'Resolved');

  if (has(' auto ', 'automated', 'automatic')) add('lane', 'auto', 'Handled: Auto');
  else if (has('draft')) add('lane', 'draft', 'Handled: Draft');
  else if (has('manual', 'human-only', 'needs me', 'needs a person')) add('lane', 'manual', 'Handled: You');

  const dept = (needle: string) => departments.find((d) => d.name.toLowerCase().includes(needle));
  const team =
    (has('dispute', 'chargeback') && dept('dispute')) ||
    (has('trade', 'payment', 'remittance', 'swift') && dept('trade')) ||
    (has('lending', 'loan', 'foreclos', ' emi') && dept('lending')) ||
    (has('card') && dept('card')) ||
    (has('retail', 'service desk') && dept('retail')) ||
    null;
  if (team) add('team', team.id, `Team: ${team.name}`);

  if (has('assigned to me', 'my tickets', ' mine ', ' me ')) add('owner', 'mine', 'Owner: me');
  else if (has('unowned', 'unassigned', 'nobody', 'no owner')) add('owner', 'unassigned', 'Owner: nobody');
  else if (has('agent-owned', 'the ai owns', 'ai owned', 'ai-owned', 'owned by the ai'))
    add('owner', 'ai', 'Owner: the AI');

  if (has(' late', 'overdue', 'breach', 'at risk', 'urgent', 'running out'))
    add('due', 'risk', 'Running late');
  else if (has(' open ')) add('due', 'open', 'Open only');

  if (has('below the bar', 'low confidence', 'unsure', 'not confident')) add('conf', 'low', 'Below the bar');
  else if (has('high confidence', 'confident')) add('conf', 'high', 'High confidence');

  const pri = /\bp([1-4])\b/.exec(q);
  if (pri) add('pri', `P${pri[1]}` as 'P1', `Priority: P${pri[1]}`);

  return { filters, chips };
}
