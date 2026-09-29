/**
 * What it can do: the action library by risk group (money moves × can be undone) and the autonomy
 * dial per group. The selected group lives in the URL (?cell=1-1).
 */
import type { ActionsDTO, RiskCell } from '@ci/contracts';
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useActions, useMe } from '../../lib/queries';
import { Button, cx, EmptyState, Loadable, Page, PageHeader, Pill, Skeleton } from '../../ui';
import { APPROVAL_TONE, isCell } from './actions/cells';
import { NewActionWizard } from './actions/NewActionWizard';
import { RiskMatrix } from './actions/RiskMatrix';
import { isForbidden, NoAccess } from './agents/NoAccess';
import s from './actions/actions.module.css';

const TITLE = 'What the AI can do';
const SUB = 'Pick a risk group below to see its actions and how much freedom the AI has there.';

export default function ActionsScreen() {
  const q = useActions();
  if (isForbidden(q.error))
    return (
      <Page>
        <PageHeader title={TITLE} subtitle={SUB} />
        <NoAccess error={q.error} what="the action library" />
      </Page>
    );
  return (
    <Page>
      <Loadable
        query={q}
        skeleton={
          <>
            <PageHeader title={TITLE} subtitle={SUB} />
            <div className={s.layout}>
              <Skeleton h={480} />
              <Skeleton h={420} />
            </div>
          </>
        }
      >
        {(data) => <Actions data={data} />}
      </Loadable>
    </Page>
  );
}

function Actions({ data }: { data: ActionsDTO }) {
  const caps = useMe().data?.capabilities ?? [];
  const canEdit = caps.includes('setup.edit');
  const [params, setParams] = useSearchParams();
  const [wizard, setWizard] = useState(false);
  const raw = params.get('cell');
  const key: RiskCell = isCell(raw) ? raw : '0-0';
  const selected = data.cells.find((c) => c.cell === key) ?? data.cells[0];
  const select = (c: RiskCell) =>
    setParams(
      (p) => {
        p.set('cell', c);
        return p;
      },
      { replace: true },
    );

  if (!selected) return <EmptyState title="No risk groups configured" />;
  const rows = data.templates.filter((t) => t.cell === selected.cell);

  return (
    <>
      <div className="rise">
        <PageHeader
          title={TITLE}
          subtitle={SUB}
          actions={<span className={s.ownerPill}>Policy owner · Risk &amp; Compliance</span>}
        />
      </div>

      <div className={s.layout}>
        <RiskMatrix
          data={data}
          selected={selected}
          onSelect={select}
          canDial={caps.includes('autonomy.change')}
        />

        <section className={s.listCard} aria-labelledby="cell-actions-title">
          <header className={s.listHead}>
            <h3 className={s.listTitle} id="cell-actions-title">
              {selected.title}
            </h3>
            <span className={s.listCount}>{selected.count} actions</span>
            <Button
              size="sm"
              variant="soft"
              className={s.newBtn}
              onClick={() => setWizard(true)}
              disabled={!canEdit}
              title={canEdit ? undefined : 'Only an Admin can create an action template.'}
            >
              + New action
            </Button>
          </header>
          <div className={cx(s.tGrid, s.tHead)} aria-hidden>
            <span>ACTION</span>
            <span>RUNS ON</span>
            <span>APPROVAL</span>
            <span>NO HELP</span>
            <span>VOLUME</span>
          </div>
          {rows.length === 0 ? (
            <EmptyState
              title="No action templates here yet"
              text="Add one with “+ New action” — it starts suggest-only and waits for Risk review."
            />
          ) : (
            <ul
              style={{ listStyle: 'none', margin: 0, padding: 0 }}
              aria-label={`Actions in ${selected.title}`}
            >
              {rows.map((a, i) => {
                const ap = APPROVAL_TONE[a.approval];
                return (
                  <li key={a.id} className={cx(s.tGrid, s.tRow)} style={{ animationDelay: `${i * 0.04}s` }}>
                    <div style={{ minWidth: 0, paddingRight: 10 }}>
                      <div className={s.tName}>
                        <span title={a.name}>{a.name}</span>
                        {a.state === 'pending_review' && (
                          <Pill
                            fg="var(--warn)"
                            bg="var(--warn-bg)"
                            title="New actions stay suggest-only until Risk signs off"
                          >
                            Pending Risk review
                          </Pill>
                        )}
                      </div>
                      <div className={s.tCode}>
                        {a.code} · {a.owner}
                      </div>
                    </div>
                    <span className={s.tSys}>{a.system}</span>
                    <span>
                      <Pill fg={ap.fg} bg={ap.bg}>
                        {ap.word}
                      </Pill>
                    </span>
                    <span
                      className={s.tNum}
                      style={a.stpPct === null ? { color: 'var(--muted-2)' } : undefined}
                      title="Handled with no human help (straight-through)"
                    >
                      {a.stpPct === null ? '—' : `${a.stpPct}%`}
                    </span>
                    <span className={s.tNum}>{a.volume.toLocaleString('en-IN')}</span>
                  </li>
                );
              })}
            </ul>
          )}
          <div className={s.audit}>
            <span className={s.info} aria-hidden>
              i
            </span>
            <span>{selected.audit}</span>
          </div>
        </section>
      </div>

      <NewActionWizard
        open={wizard}
        onClose={() => setWizard(false)}
        data={data}
        initialCell={selected.cell}
        onCreated={select}
      />
    </>
  );
}
