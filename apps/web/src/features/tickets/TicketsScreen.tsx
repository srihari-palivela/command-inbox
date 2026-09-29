import {
  DEFAULT_CONFIDENCE_BAR,
  type BoardState,
  type FilterKey,
  type NlFilterDTO,
  type TicketFilters,
  type TicketListDTO,
  type TicketSummaryDTO,
} from '@ci/contracts';
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useBoards, useMe, useNlFilter, useTickets } from '../../lib/queries';
import { toast } from '../../lib/toast';
import { Chip, Loadable, Segmented, Skeleton } from '../../ui';
import { filtersToSearch, searchToFilters } from './filter-url';
import { FilterPanel, filterValueLabel } from './FilterPanel';
import { TicketBoard } from './TicketBoard';
import { TicketDrawer } from './TicketDrawer';
import { TicketList } from './TicketList';
import s from './Tickets.module.css';

type View = 'board' | 'list';
type GroupBy = 'status' | 'bucket';

const FILTER_KEYS: FilterKey[] = [
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

const OWNER_CHIPS: { key: '' | 'mine' | 'ai' | 'unassigned'; label: string }[] = [
  { key: '', label: 'Everyone' },
  { key: 'mine', label: 'Mine' },
  { key: 'ai', label: 'AI-owned' },
  { key: 'unassigned', label: 'Unowned' },
];

const BOARD_STATE_WORD: Record<BoardState, string> = {
  live: 'Live',
  triage_only: 'Triage only',
  observe: 'Observe',
  paused: 'Paused',
};

/**
 * Every ticket across the workspace's boards, as a status board or a filterable list. All filters,
 * the view mode, the grouping and the open ticket live in the URL.
 */
export default function TicketsScreen() {
  const [sp, setSp] = useSearchParams();
  const me = useMe().data;
  const filters = useMemo(() => searchToFilters(sp), [sp]);
  const view: View = sp.get('view') === 'list' ? 'list' : 'board';
  const groupBy: GroupBy = sp.get('group') === 'bucket' ? 'bucket' : 'status';
  const openNumber = sp.get('ticket');
  const nlText = sp.get('nl') ?? '';

  const tickets = useTickets(filters);
  const boards = useBoards();
  const nl = useNlFilter();
  const [chips, setChips] = useState<NlFilterDTO['chips']>([]);
  const bar = me?.org.confidenceBar ?? DEFAULT_CONFIDENCE_BAR;
  const caps = useMemo(() => new Set(me?.capabilities ?? []), [me?.capabilities]);

  /** Patch the URL; `null` removes a key. Typing-driven changes replace history instead of pushing. */
  const patch = (changes: Record<string, string | null>, replace = false) => {
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(changes)) {
          if (v === null || v === '') n.delete(k);
          else n.set(k, v);
        }
        return n;
      },
      { replace },
    );
  };

  // Search box: local state, debounced into `q`.
  const [search, setSearch] = useState(filters.q ?? '');
  const [lastQ, setLastQ] = useState(filters.q ?? '');
  if ((filters.q ?? '') !== lastQ) {
    setLastQ(filters.q ?? '');
    setSearch(filters.q ?? '');
  }
  useEffect(() => {
    if (search === (filters.q ?? '')) return;
    const t = setTimeout(() => patch({ q: search.trim() || null }, true), 250);
    return () => clearTimeout(t);
    // Debounce on keystrokes only; `patch` and the URL value are read fresh when the timer fires.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const setFilter = (key: FilterKey, v: string | null) => patch({ [key]: v });

  const runNl = (query: string) =>
    nl.mutate(query, {
      onSuccess: (r) => {
        // The sentence replaces the structured filters (the board tab stays), and shows the result as a list.
        const next: TicketFilters = { board: filters.board, ...r.filters };
        const search = new URLSearchParams(filtersToSearch(next, 'list'));
        search.set('nl', query);
        if (groupBy === 'bucket') search.set('group', 'bucket');
        setSp(search);
        setChips(r.chips);
        toast.show(
          r.chips.length
            ? `Read that as ${r.chips.length} filter${r.chips.length > 1 ? 's' : ''}.`
            : 'Could not turn that into filters — try naming a team, a status or a deadline.',
        );
      },
    });

  const clearAll = () => {
    const changes: Record<string, null> = { nl: null };
    for (const k of FILTER_KEYS) if (k !== 'board') changes[k] = null;
    patch(changes);
    setChips([]);
    setSearch('');
  };

  // A chip is shown only while the filter it produced is still in force.
  const liveChips = chips.filter((c) => filters[c.key] === c.value);

  const data = tickets.data;
  const summary = data?.items.find((t) => t.number === openNumber);
  const openTicket = (t: TicketSummaryDTO) => patch({ ticket: t.number });

  const scopeBoard = data?.boards.find((b) => b.key === filters.board);
  const scopeSource = boards.data?.find((b) => b.key === filters.board)?.source;
  const scopeNote = !data
    ? ' '
    : scopeBoard
      ? `${scopeBoard.name}${scopeSource ? ` · ${scopeSource}` : ''} · ${BOARD_STATE_WORD[scopeBoard.state].toLowerCase()}.`
      : `Every ticket across your ${data.boards.length} boards.`;

  return (
    <div className={s.screen}>
      <div className={s.head}>
        <div className={s.headTop}>
          <div style={{ minWidth: 0 }}>
            <h1 className={s.title}>Tickets</h1>
            <p className={s.scope}>{scopeNote}</p>
          </div>
          <div className={s.controls}>
            <label className={s.search}>
              <span className={s.searchIcon} aria-hidden />
              <span className="sr-only">Search tickets</span>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search tickets, customers, IDs"
                maxLength={200}
              />
              {search && (
                <button
                  type="button"
                  className={s.searchClear}
                  onClick={() => setSearch('')}
                  aria-label="Clear search"
                >
                  ×
                </button>
              )}
            </label>
            <Segmented<View>
              label="View"
              value={view}
              onChange={(v) => patch({ view: v === 'board' ? null : v })}
              items={[
                { key: 'board', label: 'Board' },
                { key: 'list', label: 'List' },
              ]}
            />
            {view === 'board' && (
              <div className={s.groupBy}>
                <span className="mono" aria-hidden>
                  GROUP BY
                </span>
                <Segmented<GroupBy>
                  label="Group by"
                  value={groupBy}
                  onChange={(g) => patch({ group: g === 'status' ? null : g })}
                  items={[
                    { key: 'status', label: 'Status' },
                    { key: 'bucket', label: 'Query type' },
                  ]}
                />
              </div>
            )}
            <div className={s.ownerChips} role="group" aria-label="Owner">
              {OWNER_CHIPS.map((o) => (
                <Chip
                  key={o.key || 'all'}
                  on={(filters.owner ?? '') === o.key}
                  onClick={() => setFilter('owner', o.key || null)}
                >
                  {o.label}
                </Chip>
              ))}
            </div>
          </div>
        </div>

        {data ? (
          <>
            <BoardTabs
              data={data}
              active={filters.board ?? ''}
              onPick={(k) => setFilter('board', k || null)}
            />
            <Stats stats={data.stats} />
          </>
        ) : (
          <div style={{ display: 'grid', gap: 12 }}>
            <Skeleton h={28} w="60%" />
            <Skeleton h={20} w="50%" />
          </div>
        )}
      </div>

      <div className={s.body}>
        <Loadable query={tickets} skeleton={<BodySkeleton view={view} />}>
          {(d) =>
            view === 'list' ? (
              <div className={s.listWrap}>
                <FilterPanel
                  data={d}
                  filters={filters}
                  chips={liveChips}
                  nlText={nlText}
                  nlPending={nl.isPending}
                  onNl={runNl}
                  onSet={setFilter}
                  onRemoveChip={(c) => patch({ [c.key]: null })}
                  onClearAll={clearAll}
                />
                <TicketList
                  items={d.items}
                  bar={bar}
                  caps={caps}
                  onOpen={openTicket}
                  activeNumber={openNumber}
                />
              </div>
            ) : (
              <>
                <ActiveFilterStrip
                  data={d}
                  filters={filters}
                  onClear={setFilter}
                  onClearAll={clearAll}
                  onEdit={() => patch({ view: 'list' })}
                />
                <TicketBoard items={d.items} groupBy={groupBy} bar={bar} onOpen={openTicket} />
              </>
            )
          }
        </Loadable>
      </div>

      <TicketDrawer number={openNumber} summary={summary} bar={bar} onClose={() => patch({ ticket: null })} />
    </div>
  );
}

function BoardTabs({
  data,
  active,
  onPick,
}: {
  data: TicketListDTO;
  active: string;
  onPick: (key: string) => void;
}) {
  // Tab counts ignore the board filter; "All boards" is the same pool summed.
  const all = active ? data.boards.reduce((n, b) => n + b.count, 0) : data.total;
  const tabs = [
    { key: '', name: 'All boards', count: all },
    ...data.boards.map((b) => ({ key: b.key, name: b.name, count: b.count })),
  ];
  return (
    <div className={s.tabs} role="group" aria-label="Boards">
      {tabs.map((b) => (
        <button
          key={b.key || 'all'}
          type="button"
          className={s.tabBtn}
          aria-pressed={active === b.key}
          onClick={() => onPick(b.key)}
        >
          {b.name}
          <span className={`${s.tabN} mono`}>{b.count}</span>
        </button>
      ))}
    </div>
  );
}

function Stats({ stats }: { stats: TicketListDTO['stats'] }) {
  const items = [
    { v: stats.open, l: 'open tickets', c: 'var(--ink)' },
    { v: stats.awaitingDecision, l: 'waiting on a human decision', c: 'var(--warn)' },
    { v: stats.aiOwned, l: 'agent-owned right now', c: 'var(--accent)' },
    { v: stats.atRisk, l: 'close to their deadline', c: 'var(--bad)' },
    { v: stats.closedToday, l: 'closed today', c: 'var(--ok)' },
  ];
  return (
    <div className={s.stats}>
      {items.map((x) => (
        <div key={x.l} className={s.stat}>
          <span className={`${s.statV} mono`} style={{ color: x.c }}>
            {x.v}
          </span>
          <span className={s.statL}>{x.l}</span>
        </div>
      ))}
    </div>
  );
}

/** On the board, filters set from the list stay visible so the columns never look mysteriously short. */
function ActiveFilterStrip({
  data,
  filters,
  onClear,
  onClearAll,
  onEdit,
}: {
  data: TicketListDTO;
  filters: TicketFilters;
  onClear: (k: FilterKey, v: null) => void;
  onClearAll: () => void;
  onEdit: () => void;
}) {
  const active = (Object.entries(filters) as [FilterKey, string][]).filter(
    ([k, v]) => v && k !== 'board' && k !== 'owner' && k !== 'q',
  );
  if (!active.length) return null;
  return (
    <div className={s.strip}>
      <span className={s.hint}>Filtered by</span>
      {active.map(([k, v]) => (
        <button
          key={k}
          type="button"
          className={s.nlChip}
          onClick={() => onClear(k, null)}
          aria-label={`Remove filter ${filterValueLabel(data, k, v)}`}
        >
          {filterValueLabel(data, k, v)} <span aria-hidden>×</span>
        </button>
      ))}
      <button type="button" className={s.linkBtn} onClick={onEdit}>
        Edit in list
      </button>
      <button type="button" className={s.linkBtn} onClick={onClearAll}>
        Clear all
      </button>
    </div>
  );
}

function BodySkeleton({ view }: { view: View }) {
  if (view === 'list')
    return (
      <div className={s.listWrap} style={{ display: 'grid', gap: 10, alignContent: 'start' }}>
        <Skeleton h={110} />
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} h={38} />
        ))}
      </div>
    );
  return (
    <div className={s.board}>
      {Array.from({ length: 5 }, (_, i) => (
        <div
          key={i}
          className={s.col}
          style={{ padding: 10, gap: 8, display: 'flex', flexDirection: 'column' }}
        >
          <Skeleton h={18} w="70%" />
          <Skeleton h={96} />
          <Skeleton h={96} />
        </div>
      ))}
    </div>
  );
}
