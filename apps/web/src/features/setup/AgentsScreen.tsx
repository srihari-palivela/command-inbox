/**
 * AI agents: the roster of agents built from templates, their evals and calibration, and the feedback
 * store that tunes them. Editing needs `setup.edit`; the server enforces it either way.
 */
import type { AgentsOverviewDTO } from '@ci/contracts';
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAgents, useMe } from '../../lib/queries';
import { Button, cx, EmptyState, Loadable, Meter, Page, PageHeader, Skeleton } from '../../ui';
import { AgentDrawer } from './agents/AgentDrawer';
import { FeedbackStore } from './agents/FeedbackStore';
import { boardList, evalColor, isGuard, stateLine } from './agents/model';
import { NewAgentWizard } from './agents/NewAgentWizard';
import { isForbidden, NoAccess } from './agents/NoAccess';
import s from './agents/agents.module.css';
import { money, moneyCompact } from '../../lib/format';

export default function AgentsScreen() {
  const q = useAgents();
  if (isForbidden(q.error))
    return (
      <Page>
        <PageHeader
          title="AI agents"
          subtitle="Built from templates, given your own names, and attached to one or more boards."
        />
        <NoAccess error={q.error} what="AI agents" />
      </Page>
    );
  return (
    <Page>
      <Loadable
        query={q}
        skeleton={
          <div style={{ display: 'grid', gap: 12 }}>
            <PageHeader
              title="AI agents"
              subtitle="Built from templates, given your own names, and attached to one or more boards."
            />
            <Skeleton h={26} w="50%" />
            <Skeleton h={380} />
          </div>
        }
      >
        {(data) => <Agents data={data} />}
      </Loadable>
    </Page>
  );
}

function Agents({ data }: { data: AgentsOverviewDTO }) {
  const me = useMe().data;
  const caps = me?.capabilities ?? [];
  const canEdit = caps.includes('setup.edit');
  const [params, setParams] = useSearchParams();
  const [wizard, setWizard] = useState(false);
  const openId = params.get('agent');
  const open = data.agents.find((a) => a.id === openId) ?? null;
  const setOpen = (id: string | null) =>
    setParams(
      (p) => {
        if (id) p.set('agent', id);
        else p.delete('agent');
        return p;
      },
      { replace: true },
    );

  const label = (k: string) => data.templates.find((t) => t.key === k)?.label ?? k;
  const counts = data.templates
    .map((t) => ({ key: t.key, label: t.label, n: data.agents.filter((a) => a.template === t.key).length }))
    .filter((c) => c.n > 0);

  return (
    <>
      <div className="rise">
        <PageHeader
          title="AI agents"
          subtitle="Built from templates, given your own names, and attached to one or more boards."
          actions={
            <>
              <div className={s.spend}>
                <div className={s.spendVal}>{moneyCompact(data.spendMonthMinor)}</div>
                <div className={s.spendLbl}>spend this month</div>
              </div>
              <Button
                variant="dark"
                onClick={() => setWizard(true)}
                disabled={!canEdit}
                title={canEdit ? undefined : 'Only an Admin can add an agent.'}
                style={{ marginLeft: 8 }}
              >
                + New agent
              </Button>
            </>
          }
        />
      </div>

      <div className={s.chips} aria-label="Agents by template">
        {counts.map((c) => (
          <span key={c.key} className={s.tplChip}>
            {c.label}
            <b>{c.n}</b>
          </span>
        ))}
      </div>

      <section className={s.roster} aria-label="AI agents">
        <div className={cx(s.grid, s.thead)} aria-hidden>
          <span>AGENT</span>
          <span>TEMPLATE</span>
          <span>MODEL</span>
          <span>BOARDS</span>
          <span>COST / 1K</span>
          <span>EVAL SCORE</span>
        </div>
        {data.agents.length === 0 ? (
          <EmptyState
            title="No agents yet"
            text="Start from a template — bucketing, extraction, drafting, summarisation or a policy guard."
          />
        ) : (
          data.agents.map((a, i) => (
            <button
              key={a.id}
              type="button"
              className={cx(s.grid, s.row)}
              style={{ animationDelay: `${i * 0.04}s` }}
              onClick={() => setOpen(a.id)}
              aria-label={`${a.name}, ${label(a.template)}. Open details`}
            >
              <span className={s.agentCell}>
                <span className={cx(s.icon, isGuard(a) && s.iconGuard)} aria-hidden>
                  {a.abbr}
                </span>
                <span style={{ minWidth: 0 }}>
                  <span className={cx(s.agentName, s.ellipsis)} style={{ display: 'block' }}>
                    {a.name}
                  </span>
                  <span className={s.stateLine} style={{ display: 'block' }}>
                    {stateLine(a)}
                  </span>
                </span>
              </span>
              <span className={cx(s.tpl, s.ellipsis)}>{label(a.template)}</span>
              <span className={cx(s.model, s.ellipsis)} title={a.model}>
                {a.model}
              </span>
              <span className={cx(s.boards, s.ellipsis)} title={a.boards.map((b) => b.name).join(', ')}>
                {boardList(a.boards)}
              </span>
              <span className={s.cost}>{money(a.costPer1kMinor)}</span>
              <span className={s.evalCell}>
                <Meter
                  pct={a.evalScore ?? 0}
                  color={evalColor(a.evalScore)}
                  height={4}
                  label={`Eval score ${a.evalScore ?? 'pending'}`}
                  delay={i * 0.04}
                />
                <span className={s.evalPct} style={{ color: evalColor(a.evalScore) }}>
                  {a.evalScore === null ? 'pending' : `${a.evalScore}%`}
                </span>
              </span>
            </button>
          ))
        )}
      </section>

      <FeedbackStore feedback={data.feedback} canQueue={caps.includes('insights.view')} />

      <AgentDrawer agent={open} overview={data} canEdit={canEdit} onClose={() => setOpen(null)} />
      <NewAgentWizard open={wizard} onClose={() => setWizard(false)} overview={data} />
    </>
  );
}
