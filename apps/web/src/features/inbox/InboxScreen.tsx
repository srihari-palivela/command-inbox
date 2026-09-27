/**
 * The copilot workspace: the queue (most urgent first), the selected ticket with the AI's work and the
 * approval gateway, and the AI activity rail. The selected ticket lives in the URL (/inbox/QRY-48211).
 */
import type { TicketSummaryDTO } from '@ci/contracts';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { pref } from '../../lib/prefs';
import { useInbox, useMe, useTicket } from '../../lib/queries';
import { EmptyState, ErrorState, Kbd, Modal, Skeleton, cx } from '../../ui';
import type { ApproveResult } from './actions';
import { Detail, type DetailHandle } from './Detail';
import s from './Inbox.module.css';
import { Queue, type InboxFilter } from './Queue';
import { Rail } from './Rail';

const FILTERS: InboxFilter[] = ['all', 'auto', 'draft', 'manual', 'late'];
const RAIL_KEY = 'ci.inbox.rail';

const readRail = () => {
  try {
    return localStorage.getItem(RAIL_KEY) !== 'closed';
  } catch {
    return true;
  }
};

const isTyping = (el: EventTarget | null) => {
  const n = el as HTMLElement | null;
  return !!n && (n.tagName === 'INPUT' || n.tagName === 'TEXTAREA' || n.tagName === 'SELECT' || n.isContentEditable);
};

export default function InboxScreen() {
  const me = useMe().data!;
  const navigate = useNavigate();
  const { ticket: param } = useParams();
  const [search, setSearch] = useSearchParams();
  const filter: InboxFilter = FILTERS.includes(search.get('filter') as InboxFilter) ? (search.get('filter') as InboxFilter) : 'all';
  const inbox = useInbox(filter);
  const items = useMemo(() => inbox.data?.items ?? [], [inbox.data]);
  const [railOpen, setRailOpen] = useState(readRail);
  const [help, setHelp] = useState(false);
  const handle = useRef<DetailHandle | null>(null);

  // Default selection: the first ticket in the queue.
  const selectedRef = param ?? items[0]?.number ?? null;
  const detail = useTicket(selectedRef);
  const t = detail.data;

  const go = useCallback(
    (number: string | null | undefined, replace = false) => {
      if (!number) return;
      navigate({ pathname: `/inbox/${number}`, search: search.toString() ? `?${search.toString()}` : '' }, { replace });
    },
    [navigate, search],
  );

  // Reflect the default selection in the URL so what's on screen is always shareable.
  const firstNumber = items[0]?.number;
  useEffect(() => {
    if (!param && firstNumber) go(firstNumber, true);
  }, [param, firstNumber, go]);

  const move = useCallback(
    (delta: 1 | -1) => {
      if (!items.length) return;
      const i = items.findIndex((x) => x.number === selectedRef);
      const next = i < 0 ? items[0] : items[(i + delta + items.length) % items.length];
      go(next?.number);
    },
    [items, selectedRef, go],
  );

  const onApproved = (r: ApproveResult) => {
    // Stay put while an undo or recall window is open ('scheduled', 'sending') so the person can use it;
    // once the work has left their hands, move on if they want to.
    if ((r.outcome === 'awaiting_checker' || r.outcome === 'taken') && pref(me, 'autoAdvance')) setTimeout(() => move(1), 500);
  };

  // Keyboard: J/K move, A approve, R reply, E edit, ? help. Only when the person has shortcuts on.
  useEffect(() => {
    if (!pref(me, 'keyboard')) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return;
      if (document.querySelector('[aria-modal="true"]')) return;
      const k = e.key.toLowerCase();
      if (k === 'j') move(1);
      else if (k === 'k') move(-1);
      else if (k === 'a') handle.current?.approve();
      else if (k === 'r') handle.current?.reply();
      else if (k === 'e') handle.current?.edit();
      else if (e.key === '?') setHelp(true);
      else return;
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [me, move]);

  const toggleRail = () => {
    setRailOpen((v) => {
      try {
        localStorage.setItem(RAIL_KEY, v ? 'closed' : 'open');
      } catch {
        /* per-viewer convenience only */
      }
      return !v;
    });
  };

  const setFilter = (f: InboxFilter) => {
    const next = new URLSearchParams(search);
    if (f === 'all') next.delete('filter');
    else next.set('filter', f);
    setSearch(next, { replace: true });
  };

  return (
    <div className={cx(s.layout, !railOpen && s.layoutRailClosed)}>
      <Queue query={inbox} filter={filter} onFilter={setFilter} selectedId={t?.id ?? null} onSelect={(x: TicketSummaryDTO) => go(x.number)} />

      {detail.error ? (
        <section className={s.detail} style={{ padding: 24 }}>
          <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
        </section>
      ) : t ? (
        <Detail t={t} me={me} onNext={() => move(1)} onApproved={onApproved} onOpenTicket={(n) => go(n)} handle={handle} />
      ) : inbox.data && !items.length && !param ? (
        <section className={s.detail} style={{ justifyContent: 'center' }}>
          <EmptyState title="You're clear" text="Nothing needs you right now. The AI keeps working the rest, and anything that needs a person lands here." />
        </section>
      ) : (
        <section className={s.detail} style={{ padding: 24 }} aria-busy>
          <div style={{ maxWidth: 900, margin: '0 auto', width: '100%', display: 'grid', gap: 14 }}>
            <Skeleton h={14} w="30%" />
            <Skeleton h={52} w="80%" />
            <Skeleton h={160} />
            <Skeleton h={220} />
          </div>
        </section>
      )}

      <Rail open={railOpen} onToggle={toggleRail} live={me.worker.state === 'live'} onOpenTicket={(n) => go(n)} />

      <Modal open={help} onClose={() => setHelp(false)} title="Keyboard shortcuts" width={380}>
        <div className={s.shortcuts}>
          {[
            ['J / K', 'Next / previous ticket'],
            ['A', 'Approve (focuses the gateway first)'],
            ['R', 'Reply to the customer'],
            ['E', 'Edit the draft or amend fields'],
            ['⌘K', 'Search or ask the copilot'],
            ['?', 'This sheet'],
          ].map(([k, v]) => (
            <div key={k} style={{ display: 'contents' }}>
              <Kbd>{k}</Kbd>
              <span>{v}</span>
            </div>
          ))}
        </div>
        <p style={{ fontSize: 12, color: 'var(--muted)', marginTop: 14 }}>Turn shortcuts off in Settings if they get in the way of a screen reader.</p>
      </Modal>
    </div>
  );
}
