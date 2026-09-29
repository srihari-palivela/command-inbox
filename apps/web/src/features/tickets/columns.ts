import type { TicketSummaryDTO } from '@ci/contracts';
import type { StatusGroup } from '@ci/contracts';
import { STATUS_TONE, STATUS_WORD, statusGroup } from '../../lib/presentation';

export const GROUPS: StatusGroup[] = ['triage', 'approval', 'executing', 'human', 'customer', 'resolved'];

/** Board column label: the board only shows what closed in the last 24 h, so the last column reads "today". */
export const COLUMN_LABEL: Record<StatusGroup, string> = { ...STATUS_WORD, resolved: 'Resolved today' };

export const COLUMN_BG: Partial<Record<StatusGroup, string>> = {
  approval: 'color-mix(in srgb, var(--warn-bg) 40%, var(--surface))',
  resolved: 'color-mix(in srgb, var(--ok-bg-3) 60%, var(--surface))',
};

export interface Column {
  key: string;
  label: string;
  dot: string;
  meta: string;
  bg?: string;
  loadPct: number;
  cards: TicketSummaryDTO[];
}

export function statusColumns(items: TicketSummaryDTO[]): Column[] {
  return GROUPS.map((g) => {
    const cards = items.filter((t) => statusGroup(t.status) === g);
    return {
      key: g,
      label: COLUMN_LABEL[g],
      dot: STATUS_TONE[g].dot,
      meta: STATUS_TONE[g].meta,
      bg: COLUMN_BG[g],
      loadPct: Math.min(100, (cards.length / 7) * 100),
      cards,
    };
  });
}

export function bucketColumns(items: TicketSummaryDTO[]): Column[] {
  const names: string[] = [];
  for (const t of items) if (!names.includes(t.bucket)) names.push(t.bucket);
  return names.map((name) => {
    const cards = items.filter((t) => t.bucket === name);
    const late = cards.some((c) => c.sla.tone === 'late' || c.sla.tone === 'almost_late');
    return {
      key: name,
      label: name,
      dot: late ? 'var(--bad)' : 'var(--accent)',
      meta: cards[0]?.department ?? '',
      loadPct: Math.min(100, (cards.length / 5) * 100),
      cards,
    };
  });
}
