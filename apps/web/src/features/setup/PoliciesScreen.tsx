/**
 * Rules & policies: bucketing and priority rules, hard stops proposed by staff (an Admin decides),
 * the permission matrix, and the approval cycle for each risk group. Only `rules.edit` may change rules.
 */
import type { PoliciesDTO } from '@ci/contracts';
import { api } from '../../lib/api';
import { ago } from '../../lib/format';
import { keys, useAction, useMe, usePolicies } from '../../lib/queries';
import { Button, cx, EmptyState, Loadable, Page, PageHeader, Pill, Skeleton } from '../../ui';
import { Chain } from './actions/Chain';
import { isForbidden, NoAccess } from './agents/NoAccess';
import s from './policies/policies.module.css';

const TITLE = 'Rules & policies';
const SUB = 'The policy engine: how tickets get bucketed and prioritised, who may do what, and how approvals flow.';

const VALUE: Record<PoliciesDTO['matrix']['rows'][number]['values'][number], { word: string; fg: string; bg: string }> = {
  yes: { word: 'Allowed', fg: 'var(--ok)', bg: 'var(--ok-bg)' },
  no: { word: 'Never', fg: 'var(--bad-text)', bg: 'var(--bad-bg)' },
  appr: { word: 'With approval', fg: 'var(--warn)', bg: 'var(--warn-bg)' },
  cell: { word: 'One cell only', fg: 'var(--accent)', bg: 'var(--accent-bg)' },
  auto: { word: 'By rules', fg: 'var(--accent)', bg: 'var(--accent-bg)' },
};

const HARD_TITLE = 'Hard rules cannot be switched off — they are policy, not preference.';

export default function PoliciesScreen() {
  const q = usePolicies();
  if (isForbidden(q.error))
    return (
      <Page>
        <PageHeader title={TITLE} subtitle={SUB} />
        <NoAccess error={q.error} what="rules and policies" />
      </Page>
    );
  return (
    <Page>
      <Loadable
        query={q}
        skeleton={
          <>
            <PageHeader title={TITLE} subtitle={SUB} />
            <div className={s.two}>
              <Skeleton h={340} />
              <Skeleton h={340} />
            </div>
            <Skeleton h={300} />
          </>
        }
      >
        {(data) => <Policies data={data} />}
      </Loadable>
    </Page>
  );
}

function Policies({ data }: { data: PoliciesDTO }) {
  const canEdit = (useMe().data?.capabilities ?? []).includes('rules.edit');
  const toggle = useAction((v: { id: string; enabled: boolean; description: string }) => api.patch(`/v1/policies/priority-rules/${v.id}`, { enabled: v.enabled }), {
    invalidate: [keys.policies],
    success: (_r, v) => `Priority rule “${v.description}” ${v.enabled ? 'switched on' : 'switched off'}.`,
  });
  const decide = useAction((v: { id: string; approve: boolean }) => api.post(`/v1/policies/proposed/${v.id}/decide`, { approve: v.approve }), {
    invalidate: [keys.policies],
    success: (_r, v) => (v.approve ? 'Approved — added to the bucketing rules as a hard stop.' : 'Proposal rejected — the rules stay as they are.'),
  });
  const pending = data.proposedRules.filter((p) => p.status === 'pending').length;

  return (
    <>
      <div className="rise">
        <PageHeader title={TITLE} subtitle={SUB} />
      </div>

      <div className={s.two}>
        <section className={s.card} style={{ animationDelay: '0.04s' }} aria-labelledby="bucket-h">
          <header className={s.head}>
            <h3 className={s.title} id="bucket-h">
              Bucketing rules
            </h3>
            <span className={s.meta}>checked in order · first match wins</span>
          </header>
          {data.bucketRules.length === 0 ? (
            <EmptyState title="No bucketing rules" text="Every mail goes to the model classifier." />
          ) : (
            <ol className={s.list}>
              {data.bucketRules.map((r, i) => (
                <li key={r.id} className={s.rule} style={{ animationDelay: `${i * 0.04}s` }}>
                  <span className={s.n} aria-hidden>
                    {i + 1}
                  </span>
                  <div className={s.ruleBody}>
                    <div className={s.ruleText}>{r.description}</div>
                    <div className={s.ruleMeta}>
                      <span className={s.target}>→ {r.target}</span>
                      <span className={cx(s.kind, s.push)}>{r.kind}</span>
                      <span className={s.hits}>{r.hits}</span>
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </section>

        <section className={s.card} style={{ animationDelay: '0.08s' }} aria-labelledby="pri-h">
          <header className={s.head}>
            <h3 className={s.title} id="pri-h">
              Priority rules
            </h3>
            <span className={s.meta}>applied by the Priority Ranker · re-ranked every 5 min</span>
          </header>
          {data.priorityRules.length === 0 ? (
            <EmptyState title="No priority rules" />
          ) : (
            <ol className={s.list}>
              {data.priorityRules.map((r, i) => {
                const on = r.enabled || r.hard;
                const busy = toggle.isPending && toggle.variables?.id === r.id;
                const why = r.hard ? HARD_TITLE : canEdit ? `Switch ${on ? 'off' : 'on'}: ${r.description}` : 'Only an Admin can change priority rules.';
                return (
                  <li key={r.id} className={cx(s.rule, !on && s.ruleOff)} style={{ animationDelay: `${i * 0.03}s`, padding: '10px 15px' }}>
                    <span className={s.n} aria-hidden>
                      {i + 1}
                    </span>
                    <div className={s.ruleBody}>
                      <div className={s.ruleText}>{r.description}</div>
                      <div className={s.ruleMeta}>
                        <span className={s.target}>→ {r.target}</span>
                        <span className={s.hard} style={r.hard ? { color: 'var(--bad-text)', background: 'var(--bad-bg)' } : { color: 'var(--text-2)', background: 'var(--surface-3)' }}>
                          {r.hard ? 'Hard rule' : 'Weighted'}
                        </span>
                        <span className={cx(s.hits, s.push)}>{r.hits}</span>
                        <button
                          type="button"
                          role="switch"
                          aria-checked={on}
                          aria-label={`${r.description}: ${on ? 'on' : 'off'}`}
                          className={s.switch}
                          disabled={r.hard || !canEdit || busy}
                          title={why}
                          onClick={() => toggle.mutate({ id: r.id, enabled: !on, description: r.description })}
                        >
                          {r.hard && <LockIcon />}
                          {on ? 'On' : 'Off'}
                        </button>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </section>
      </div>

      <section className={s.card} style={{ animationDelay: '0.1s' }} aria-labelledby="prop-h">
        <header className={s.head}>
          <h3 className={s.title} id="prop-h">
            Proposed rules
          </h3>
          {pending > 0 && (
            <Pill fg="var(--warn)" bg="var(--warn-bg)">
              {pending} awaiting decision
            </Pill>
          )}
          <span className={s.meta}>from staff rejections · a rule applies only once an Admin approves it</span>
        </header>
        {data.proposedRules.length === 0 ? (
          <EmptyState title="No proposals" text="When staff reject an AI action as “this should never be automated”, the proposed hard stop waits here for an Admin." />
        ) : (
          <ul className={s.list}>
            {data.proposedRules.map((p, i) => (
              <li key={p.id} className={s.prop} style={{ animationDelay: `${i * 0.04}s` }}>
                <div style={{ minWidth: 0 }}>
                  <div className={s.propText}>{p.text}</div>
                  <div className={s.propMeta}>
                    <span>from {p.proposedBy}</span>
                    {p.ticketNumber && (
                      <>
                        <span aria-hidden>·</span>
                        <span className="mono">{p.ticketNumber}</span>
                      </>
                    )}
                    <span aria-hidden>·</span>
                    <time dateTime={p.at} title={new Date(p.at).toLocaleString('en-GB')}>
                      {ago(p.at)}
                    </time>
                  </div>
                </div>
                <div className={s.propActions}>
                  <ProposalStatus status={p.status} />
                  {p.status === 'pending' && canEdit && (
                    <>
                      <Button size="sm" loading={decide.isPending && decide.variables?.id === p.id && !decide.variables.approve} disabled={decide.isPending} onClick={() => decide.mutate({ id: p.id, approve: false })}>
                        Reject
                      </Button>
                      <Button size="sm" variant="primary" loading={decide.isPending && decide.variables?.id === p.id && decide.variables.approve} disabled={decide.isPending} onClick={() => decide.mutate({ id: p.id, approve: true })}>
                        Approve
                      </Button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={s.card} style={{ animationDelay: '0.12s' }} aria-labelledby="who-h">
        <header className={s.head}>
          <h3 className={s.title} id="who-h">
            Who may do what
          </h3>
          <span className={s.meta}>enforced at execution time, not in the UI — an API call without the permission fails the same way</span>
        </header>
        <table className={s.table}>
          <thead>
            <tr>
              <th scope="col">CAPABILITY</th>
              {data.matrix.cols.map((c) => (
                <th key={c} scope="col">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.matrix.rows.map((r, i) => (
              <tr key={r.capability} style={{ animationDelay: `${i * 0.03}s` }}>
                <th scope="row" style={{ background: 'none', fontFamily: 'inherit', letterSpacing: 0, fontSize: 12.5, fontWeight: 500, color: 'var(--ink)', borderBottom: i === data.matrix.rows.length - 1 ? 0 : undefined }}>
                  {r.capability}
                </th>
                {r.values.map((v, j) => {
                  const t = VALUE[v];
                  return (
                    <td key={j}>
                      <span className={s.val} style={{ color: t.fg, background: t.bg }}>
                        {t.word}
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className={s.card} style={{ animationDelay: '0.16s' }} aria-labelledby="cyc-h">
        <header className={s.head}>
          <h3 className={s.title} id="cyc-h">
            Approval cycles
          </h3>
          <span className={s.meta}>the chain every action follows, set by its risk group</span>
        </header>
        <div className={s.cycles}>
          {data.cycles.map((c) => (
            <div key={c.cell} className={s.cycle}>
              <div className={s.cycleTitle}>{c.cell}</div>
              <Chain steps={c.chain} label={`Approval chain for ${c.cell}`} />
              <div className={s.cycleNote}>{c.note}</div>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}

function ProposalStatus({ status }: { status: string }) {
  if (status === 'approved')
    return (
      <Pill fg="var(--ok)" bg="var(--ok-bg)">
        Approved · now a hard stop
      </Pill>
    );
  if (status === 'rejected')
    return (
      <Pill fg="var(--text-2)" bg="var(--surface-3)">
        Rejected
      </Pill>
    );
  return (
    <Pill fg="var(--warn)" bg="var(--warn-bg)">
      Awaiting decision
    </Pill>
  );
}

function LockIcon() {
  return (
    <svg width="9" height="10" viewBox="0 0 9 10" aria-hidden focusable="false">
      <rect x="0.5" y="4.5" width="8" height="5" rx="1.2" fill="currentColor" />
      <path d="M2.3 4.6V3.2a2.2 2.2 0 0 1 4.4 0v1.4" fill="none" stroke="currentColor" strokeWidth="1.2" />
    </svg>
  );
}
