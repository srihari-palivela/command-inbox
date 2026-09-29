import type { TicketFilters } from '@ci/contracts';

const KEYS: (keyof TicketFilters)[] = [
  'board',
  'status',
  'lane',
  'team',
  'owner',
  'due',
  'conf',
  'pri',
  'bucket',
  'q',
];

/** Filters live in the URL so a filtered board can be shared, bookmarked, and survives navigation. */
export function filtersToSearch(f: TicketFilters, view?: 'board' | 'list'): string {
  const u = new URLSearchParams();
  for (const k of KEYS) {
    const v = f[k];
    if (v) u.set(k, String(v));
  }
  if (view) u.set('view', view);
  const s = u.toString();
  return s ? `?${s}` : '';
}

export function searchToFilters(sp: URLSearchParams): TicketFilters {
  const f: Record<string, string> = {};
  for (const k of KEYS) {
    const v = sp.get(k);
    if (v) f[k] = v;
  }
  return f as TicketFilters;
}
