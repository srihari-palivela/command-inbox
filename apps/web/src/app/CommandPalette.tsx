import type { CopilotAnswerDTO, MeDTO, TicketFilters } from '@ci/contracts';
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { LANE_TONE } from '../lib/presentation';
import { useAsk, useSearch } from '../lib/queries';
import { SCREENS } from './routes';
import { filtersToSearch } from '../features/tickets/filter-url';

interface Item {
  id: string;
  group: string;
  tag: string;
  tagFg: string;
  tagBg: string;
  label: string;
  meta?: string;
  run: () => void;
}

const SUGGESTED = [
  { label: 'Which tickets will miss their deadline today?', meta: 'deadlines' },
  { label: 'What is waiting on my approval?', meta: 'right now' },
  { label: 'Why is trade finance confidence falling?', meta: 'confidence' },
  { label: 'How is the AI doing this week?', meta: 'results' },
];

/**
 * ⌘K: jump to a screen, open a ticket, find a customer, policy or knowledge document, or ask the
 * copilot a question about the live queue. Arrow keys move, Enter runs.
 */
export function CommandPalette({ open, onClose, me }: { open: boolean; onClose: () => void; me: MeDTO }) {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [debounced, setDebounced] = useState('');
  const [answer, setAnswer] = useState<CopilotAnswerDTO | null>(null);
  const [active, setActive] = useState(0);
  const ask = useAsk();
  const search = useSearch(debounced);
  const inputRef = useRef<HTMLInputElement>(null);
  const caps = new Set(me.capabilities);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 160);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    if (open) {
      setQ('');
      setAnswer(null);
      setActive(0);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  const go = (path: string) => {
    onClose();
    navigate(path);
  };
  const runAsk = (question: string) => {
    setQ(question);
    ask.mutate(question, { onSuccess: setAnswer });
  };
  const applyFilters = (f?: TicketFilters, to?: string) => {
    if (to) return go(to);
    go(`/tickets${filtersToSearch(f ?? {}, 'list')}`);
  };

  const items = useMemo<Item[]>(() => {
    const term = q.trim().toLowerCase();
    const out: Item[] = [];
    const neutral = { tagFg: 'var(--muted)', tagBg: 'var(--surface-3)' };
    Object.values(SCREENS)
      .filter((sc) => (!sc.cap || caps.has(sc.cap)) && (!term || `${sc.label} ${sc.meta}`.toLowerCase().includes(term)))
      .slice(0, term ? 6 : 5)
      .forEach((sc) => out.push({ id: `s:${sc.key}`, group: 'Go to', tag: 'Go', ...neutral, label: sc.label, meta: sc.meta, run: () => go(sc.path) }));
    const r = search.data;
    if (term.length >= 2 && r) {
      r.tickets.forEach((t) =>
        out.push({ id: `t:${t.id}`, group: 'Tickets', tag: LANE_TONE[t.lane].word, tagFg: LANE_TONE[t.lane].fg, tagBg: LANE_TONE[t.lane].bg, label: t.subject, meta: t.number, run: () => go(`/inbox/${t.number}`) }),
      );
      r.customers.forEach((c) =>
        out.push({ id: `c:${c.id}`, group: 'Customers', tag: 'Customer', ...neutral, label: c.name, meta: `${c.cif} · ${c.tickets} ticket${c.tickets === 1 ? '' : 's'}`, run: () => go(`/tickets${filtersToSearch({ q: c.name }, 'list')}`) }),
      );
      r.knowledge.forEach((k) =>
        out.push({ id: `k:${k.id}`, group: 'Knowledge', tag: k.status === 'approved' ? 'Approved' : k.status, tagFg: 'var(--ok)', tagBg: 'var(--ok-bg)', label: `${k.title} ${k.section}`, meta: 'source', run: () => go(caps.has('setup.view') ? SCREENS.knowledge.path : `/tickets${filtersToSearch({ q: k.title }, 'list')}`) }),
      );
      r.policies.forEach((p) =>
        out.push({ id: `p:${p.id}`, group: 'Policies', tag: p.kind.split(' ')[0]!, tagFg: 'var(--warn)', tagBg: 'var(--warn-bg)', label: p.text, run: () => go(caps.has('setup.view') ? (p.kind === 'Action template' ? SCREENS.actions.path : SCREENS.policies.path) : '/inbox') }),
      );
    }
    const asks = term.length > 2 ? [{ label: q.trim(), meta: 'ask' }] : [];
    [...asks, ...SUGGESTED.filter((a) => !term || a.label.toLowerCase().includes(term))]
      .slice(0, 5)
      .forEach((a, i) => out.push({ id: `a:${i}:${a.label}`, group: 'Ask the copilot', tag: 'Ask', tagFg: 'var(--accent)', tagBg: 'var(--accent-bg)', label: a.label, meta: a.meta, run: () => runAsk(a.label) }));
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, search.data, me.capabilities]);

  useEffect(() => setActive(0), [q]);
  if (!open) return null;

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((a) => Math.min(items.length - 1, a + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const it = items[active];
      if (it) it.run();
      else if (q.trim().length > 2) runAsk(q.trim());
    }
  };

  const groups = [...new Set(items.map((i) => i.group))];
  return createPortal(
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(25,26,29,.22)', backdropFilter: 'blur(2px)', zIndex: 90, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', paddingTop: 96, animation: 'fadeIn .16s ease both' }}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onClick={(e) => e.stopPropagation()}
        style={{ width: 640, maxWidth: '92vw', background: 'var(--surface)', border: '1px solid var(--line)', borderRadius: 13, boxShadow: '0 24px 60px rgba(25,26,29,.2)', overflow: 'hidden', animation: 'popIn .22s var(--ease-out) both' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '13px 16px', borderBottom: '1px solid var(--line-soft)' }}>
          <span aria-hidden style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--accent)', animation: 'breathe 2.2s ease-in-out infinite', flex: '0 0 auto' }} />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setAnswer(null);
            }}
            onKeyDown={onKey}
            placeholder="Ask the copilot, jump to a ticket or customer, or open a screen"
            aria-label="Search or ask"
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
            aria-activedescendant={items[active] ? `pi-${items[active]!.id}` : undefined}
            style={{ flex: 1, border: 0, outline: 'none', fontSize: 14, background: 'transparent' }}
          />
          <span className="mono" style={{ fontSize: 10, color: 'var(--muted-2)', border: '1px solid var(--line)', borderRadius: 4, padding: '2px 5px' }}>
            ESC
          </span>
        </div>
        <div id="palette-list" role="listbox" style={{ maxHeight: 440, overflowY: 'auto', padding: 6 }}>
          {(answer || ask.isPending) && (
            <div aria-live="polite" style={{ margin: '4px 4px 8px', border: '1px solid var(--accent-line)', borderRadius: 11, background: 'var(--accent-bg-4)', overflow: 'hidden', animation: 'riseIn .28s var(--ease-out) both' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 13px', borderBottom: '1px solid var(--accent-bg)' }}>
                <span aria-hidden style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--accent)', animation: 'breathe 2.2s ease-in-out infinite' }} />
                <span style={{ fontSize: 11, fontWeight: 600, letterSpacing: '.04em', color: 'var(--accent)' }}>COPILOT</span>
                {answer && (
                  <span className="mono" style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--muted)' }}>
                    from the live queue{answer.provider === 'claude' ? ' · phrased by Claude' : ''}
                  </span>
                )}
              </div>
              <div style={{ padding: '12px 13px 13px' }}>
                {ask.isPending && !answer ? (
                  <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>Reading the queue…</div>
                ) : (
                  answer && (
                    <>
                      <div style={{ fontSize: 14, fontWeight: 600, lineHeight: 1.4, marginBottom: 9 }}>{answer.headline}</div>
                      <div style={{ display: 'grid', gap: 6, marginBottom: 12 }}>
                        {answer.lines.map((l, i) => (
                          <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                            <span aria-hidden style={{ width: 4, height: 4, borderRadius: '50%', background: 'var(--accent)', marginTop: 7, flex: '0 0 auto' }} />
                            <span style={{ fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-2)' }}>{l}</span>
                          </div>
                        ))}
                      </div>
                      <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
                        {answer.actions.map((a) => (
                          <button key={a.label} type="button" onClick={() => applyFilters(a.filters, a.to)} style={{ border: 0, background: 'var(--accent)', color: '#fff', borderRadius: 7, padding: '7px 13px', fontSize: 12.5, fontWeight: 600 }}>
                            {a.label}
                          </button>
                        ))}
                      </div>
                    </>
                  )
                )}
              </div>
            </div>
          )}
          {groups.map((g) => (
            <div key={g} style={{ padding: '6px 4px 2px' }} role="group" aria-label={g}>
              <div className="mono" style={{ fontSize: 9, fontWeight: 600, letterSpacing: '.13em', color: 'var(--muted-2)', padding: '0 8px 5px', textTransform: 'uppercase' }}>
                {g}
              </div>
              {items
                .filter((i) => i.group === g)
                .map((it) => {
                  const idx = items.indexOf(it);
                  return (
                    <button
                      key={it.id}
                      id={`pi-${it.id}`}
                      role="option"
                      aria-selected={idx === active}
                      type="button"
                      onMouseEnter={() => setActive(idx)}
                      onClick={it.run}
                      style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 10, padding: '8px 9px', border: 0, borderRadius: 8, background: idx === active ? 'var(--canvas)' : 'transparent', textAlign: 'left' }}
                    >
                      <span style={{ height: 17, padding: '0 6px', borderRadius: 4, background: it.tagBg, color: it.tagFg, fontSize: 9.5, fontWeight: 600, display: 'inline-flex', alignItems: 'center', whiteSpace: 'nowrap', flex: '0 0 auto' }}>{it.tag}</span>
                      <span style={{ fontSize: 13, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.label}</span>
                      {it.meta && <span style={{ fontSize: 11.5, color: 'var(--muted)', flex: '0 0 auto' }}>{it.meta}</span>}
                    </button>
                  );
                })}
            </div>
          ))}
          {!items.length && !answer && <div style={{ padding: '26px 16px', textAlign: 'center', fontSize: 12.5, color: 'var(--muted)' }}>Nothing matches that. Press Enter to ask the copilot.</div>}
        </div>
      </div>
    </div>,
    document.body,
  );
}
