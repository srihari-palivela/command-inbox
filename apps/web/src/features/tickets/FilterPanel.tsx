import type { FilterKey, NlFilterDTO, TicketFilters, TicketListDTO } from '@ci/contracts';
import { useState } from 'react';
import { PRIORITY_TONE } from '../../lib/presentation';
import { Button, Popover } from '../../ui';
import s from './Tickets.module.css';

export const NL_EXAMPLES = ['late disputes assigned to me', 'auto tickets waiting on approval', 'unowned trade tickets below the bar', 'everything closed today'];

type FacetKey = 'pri' | 'bucket' | 'status' | 'lane' | 'team' | 'owner' | 'due' | 'conf';

interface FacetDef {
  key: FacetKey;
  label: string;
  opts: [string, string][];
}

export function facetDefs(data: TicketListDTO): FacetDef[] {
  return [
    { key: 'pri', label: 'Priority', opts: (['P1', 'P2', 'P3', 'P4'] as const).map((p) => [p, PRIORITY_TONE[p].label]) },
    { key: 'bucket', label: 'Bucket', opts: data.buckets.map((b) => [b, b]) },
    {
      key: 'status',
      label: 'Status',
      opts: [
        ['triage', 'Agent triaging'],
        ['approval', 'Awaiting approval'],
        ['executing', 'Executing'],
        ['human', 'With a human'],
        ['customer', 'Waiting on customer'],
        ['resolved', 'Resolved'],
      ],
    },
    {
      key: 'lane',
      label: 'Handled by',
      opts: [
        ['auto', 'Auto — the AI does it'],
        ['draft', 'Draft — you send it'],
        ['manual', 'You — the AI steps back'],
      ],
    },
    { key: 'team', label: 'Team', opts: data.departments.map((d) => [d.id, d.name]) },
    {
      key: 'owner',
      label: 'Owner',
      opts: [
        ['mine', 'Assigned to me'],
        ['ai', 'Owned by the AI'],
        ['unassigned', 'Nobody'],
      ],
    },
    {
      key: 'due',
      label: 'State',
      opts: [
        ['risk', 'Running late'],
        ['open', 'Open only'],
        ['closed', 'Closed only'],
      ],
    },
    {
      key: 'conf',
      label: 'Confidence',
      opts: [
        ['low', 'Below the bar'],
        ['high', 'High confidence'],
      ],
    },
  ];
}

/** Human-readable value of an active filter, for the dropdown button. */
export function filterValueLabel(data: TicketListDTO, key: FilterKey, value: string): string {
  const def = facetDefs(data).find((d) => d.key === key);
  return def?.opts.find((o) => o[0] === value)?.[1] ?? value;
}

function FacetDropdown({ def, value, counts, onSet }: { def: FacetDef; value: string | undefined; counts: Record<string, number> | undefined; onSet: (v: string | null) => void }) {
  const [open, setOpen] = useState(false);
  const active = !!value;
  const label = active ? def.opts.find((o) => o[0] === value)?.[1] ?? value : def.label;
  return (
    <div className={s.facet}>
      <button
        type="button"
        className={s.facetBtn}
        data-on={active || undefined}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        aria-label={active ? `${def.label}: ${label}. Change` : `Filter by ${def.label.toLowerCase()}`}
      >
        <span className={s.facetLabel}>{label}</span>
        {!active && (
          <span aria-hidden className={s.caret}>
            ▾
          </span>
        )}
      </button>
      {active && (
        <button type="button" className={s.facetClear} onClick={() => onSet(null)} aria-label={`Clear ${def.label.toLowerCase()} filter`}>
          ×
        </button>
      )}
      <Popover open={open} onClose={() => setOpen(false)} label={def.label} className={s.facetPop} style={{ top: 30, left: 0 }}>
        <div className={s.facetList}>
          {[['', `Any ${def.label.toLowerCase()}`] as [string, string], ...def.opts].map(([v, l]) => {
            const n = v ? counts?.[v] ?? 0 : undefined;
            const sel = v ? value === v : !active;
            return (
              <button
                key={v || 'any'}
                type="button"
                className={s.facetOpt}
                aria-pressed={sel}
                style={{ opacity: n === 0 && !sel ? 0.45 : 1 }}
                onClick={() => {
                  onSet(v || null);
                  setOpen(false);
                }}
              >
                <span className={s.box} data-on={sel || undefined} aria-hidden>
                  {sel ? '✓' : ''}
                </span>
                <span className={s.facetOptLabel}>{l}</span>
                {n !== undefined && <span className={`${s.facetCount} mono`}>{n}</span>}
              </button>
            );
          })}
        </div>
      </Popover>
    </div>
  );
}

export function FilterPanel({
  data,
  filters,
  chips,
  nlText,
  nlPending,
  onNl,
  onSet,
  onRemoveChip,
  onClearAll,
}: {
  data: TicketListDTO;
  filters: TicketFilters;
  chips: NlFilterDTO['chips'];
  nlText: string;
  nlPending: boolean;
  onNl: (q: string) => void;
  onSet: (key: FilterKey, v: string | null) => void;
  onRemoveChip: (c: NlFilterDTO['chips'][number]) => void;
  onClearAll: () => void;
}) {
  const [q, setQ] = useState(nlText);
  const [lastNl, setLastNl] = useState(nlText);
  if (nlText !== lastNl) {
    // The URL changed underneath us (Back, a chip removed, Clear all): follow it.
    setLastNl(nlText);
    setQ(nlText);
  }
  const hasFilters = Object.entries(filters).some(([k, v]) => k !== 'board' && !!v);
  const run = (text: string) => {
    const t = text.trim();
    if (t) onNl(t);
  };
  return (
    <div className={s.panel}>
      <form
        className={s.nlRow}
        onSubmit={(e) => {
          e.preventDefault();
          run(q);
        }}
      >
        <label className={s.nlBox}>
          <span className={s.nlDot} aria-hidden />
          <span className="sr-only">Describe what you want to see</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Describe what you want to see — “late disputes assigned to me”" className={s.nlInput} maxLength={300} />
        </label>
        <Button type="submit" variant="primary" loading={nlPending} disabled={!q.trim()}>
          Apply
        </Button>
        {hasFilters && (
          <Button type="button" onClick={onClearAll}>
            Clear all
          </Button>
        )}
      </form>

      {chips.length > 0 && (
        <div className={s.chipRow}>
          <span className={s.hint}>Read as</span>
          {chips.map((c) => (
            <button key={`${c.key}:${c.value}`} type="button" className={s.nlChip} onClick={() => onRemoveChip(c)} aria-label={`Remove filter: ${c.text}`}>
              {c.text} <span aria-hidden>×</span>
            </button>
          ))}
        </div>
      )}

      <div className={s.facetRow}>
        {facetDefs(data).map((d) => (
          <FacetDropdown key={d.key} def={d} value={filters[d.key]} counts={data.facets[d.key]} onSet={(v) => onSet(d.key, v)} />
        ))}
        <span className={s.listCount}>
          <span className="mono">{data.total}</span> of <span className="mono">{data.all}</span> tickets
        </span>
      </div>

      {chips.length === 0 && (
        <div className={s.examples}>
          <span className={s.hint}>Try</span>
          {NL_EXAMPLES.map((x) => (
            <button
              key={x}
              type="button"
              className={s.example}
              onClick={() => {
                setQ(x);
                run(x);
              }}
            >
              {x}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
