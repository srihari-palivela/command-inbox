import type { BoardDTO, BoardState } from '@ci/contracts';
import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useBoards, useMe } from '../../lib/queries';
import { toast } from '../../lib/toast';
import { Button, Dot, EmptyState, Eyebrow, Loadable, Page, PageHeader, Skeleton } from '../../ui';
import { filtersToSearch } from '../tickets/filter-url';
import { NewBoardWizard } from './NewBoardWizard';
import s from './Boards.module.css';

export const BOARD_STATE: Record<BoardState, { word: string; fg: string; bg: string; dot: string }> = {
  live: { word: 'Live', fg: 'var(--ok)', bg: 'var(--ok-bg)', dot: 'var(--ok-dot)' },
  triage_only: { word: 'Triage only', fg: 'var(--accent)', bg: 'var(--accent-bg)', dot: 'var(--accent)' },
  observe: { word: 'Observe', fg: 'var(--text-2)', bg: 'var(--surface-3)', dot: 'var(--dot-idle)' },
  paused: { word: 'Paused', fg: 'var(--warn)', bg: 'var(--warn-bg)', dot: 'var(--warn-dot)' },
};

const autoTone = (pct: number) => (pct >= 60 ? 'var(--ok)' : pct >= 30 ? 'var(--accent)' : 'var(--warn)');

function BoardCard({ b, i, onOpen }: { b: BoardDTO; i: number; onOpen: () => void }) {
  const st = BOARD_STATE[b.state];
  return (
    <article className={s.card} style={{ animationDelay: `${i * 0.05}s` }} aria-labelledby={`board-${b.id}`}>
      <div className={s.cardHead}>
        <div className={s.nameRow}>
          <Dot color={st.dot} />
          <h2 id={`board-${b.id}`} className={s.name}>
            {b.name}
          </h2>
          <span className={s.state} style={{ color: st.fg, background: st.bg }}>
            {st.word}
          </span>
        </div>
        <div className={`${s.source} mono`} title={b.source}>
          {b.source || 'No mailbox connected'}
        </div>
      </div>
      <dl className={s.stats}>
        <div>
          <dt>mails / day</dt>
          <dd className="mono">{b.volume24h.toLocaleString('en-IN')}</dd>
        </div>
        <div>
          <dt>open now</dt>
          <dd className="mono">{b.open}</dd>
        </div>
        <div>
          <dt>handled by AI</dt>
          <dd className="mono" style={{ color: autoTone(b.autoRatePct) }}>
            {b.autoRatePct}%
          </dd>
        </div>
      </dl>
      <div className={s.cardFoot}>
        <Eyebrow className={s.eyebrow}>AGENTS ON THIS BOARD</Eyebrow>
        <div className={s.agents}>
          {b.agents.length ? (
            b.agents.map((a) => (
              <span key={a.id} className={s.agent}>
                {a.name}
              </span>
            ))
          ) : (
            <span className={s.none}>No agents yet</span>
          )}
        </div>
        <div className={s.teamRow}>
          <span className={s.team}>Team {b.team}</span>
          <Button size="sm" onClick={onOpen} aria-label={`Open board ${b.name}`}>
            Open board
          </Button>
        </div>
      </div>
    </article>
  );
}

export default function BoardsScreen() {
  const boards = useBoards();
  const me = useMe().data;
  const navigate = useNavigate();
  const [sp, setSp] = useSearchParams();
  const [wizard, setWizard] = useState(false);
  const canCreate = !!me?.capabilities.includes('setup.edit');

  // Back from the mail provider's consent screen.
  useEffect(() => {
    if (sp.get('connected') === '1') {
      toast.show('Mailbox connected · read access confirmed. The board starts in observe mode.');
      setSp(
        (p) => {
          const n = new URLSearchParams(p);
          n.delete('connected');
          return n;
        },
        { replace: true },
      );
    }
  }, [sp, setSp]);

  return (
    <Page>
      <div className={s.head}>
        <PageHeader
          title="Boards"
          subtitle="Each board is one source of mail with its own AI agents, team and rules."
          actions={
            <Button variant="dark" disabled={!canCreate} title={canCreate ? undefined : 'Only Admin can create a board.'} onClick={() => setWizard(true)}>
              + New board
            </Button>
          }
        />
      </div>
      <Loadable
        query={boards}
        skeleton={
          <div className={s.grid}>
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} h={240} style={{ borderRadius: 12 }} />
            ))}
          </div>
        }
      >
        {(list) =>
          list.length ? (
            <div className={s.grid}>
              {list.map((b, i) => (
                <BoardCard key={b.id} b={b} i={i} onOpen={() => navigate(`/tickets${filtersToSearch({ board: b.key })}`)} />
              ))}
            </div>
          ) : (
            <EmptyState
              title="No boards yet"
              text="A board reads one shared mailbox. Connect one to start sorting mail."
              action={
                canCreate ? (
                  <Button variant="dark" onClick={() => setWizard(true)}>
                    + New board
                  </Button>
                ) : undefined
              }
            />
          )
        }
      </Loadable>
      <NewBoardWizard open={wizard} onClose={() => setWizard(false)} />
    </Page>
  );
}
