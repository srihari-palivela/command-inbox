/**
 * Agent detail drawer: model, cost and eval score; the system prompt (edit → new version pending evals);
 * evals; calibration (observed accuracy per confidence band); version history; attached boards.
 */
import type { AgentDTO, AgentsOverviewDTO } from '@ci/contracts';
import { useEffect, useState } from 'react';
import { api } from '../../../lib/api';
import { inr, shortDate } from '../../../lib/format';
import { keys, useAction, useBoards } from '../../../lib/queries';
import { Button, cx, Drawer, Eyebrow, Pill, Skeleton, TextArea } from '../../../ui';
import { Calibration } from './Calibration';
import { boardList, evalColor, evalToneColor, isGuard, stateLine } from './model';
import s from './agents.module.css';

export function AgentDrawer({
  agent,
  overview,
  canEdit,
  onClose,
}: {
  agent: AgentDTO | null;
  overview: AgentsOverviewDTO;
  canEdit: boolean;
  onClose: () => void;
}) {
  return (
    <Drawer
      open={!!agent}
      onClose={onClose}
      width={520}
      title={
        agent && (
          <span className={s.drawerTitle}>
            <span className={cx(s.icon, s.iconLg, isGuard(agent) && s.iconGuard)} aria-hidden>
              {agent.abbr}
            </span>
            {agent.name}
          </span>
        )
      }
      subtitle={
        agent &&
        `${overview.templates.find((t) => t.key === agent.template)?.label ?? agent.template} · ${agent.model} · ${stateLine(agent)}`
      }
    >
      {agent && <AgentBody key={agent.id} agent={agent} overview={overview} canEdit={canEdit} />}
    </Drawer>
  );
}

function AgentBody({
  agent,
  overview,
  canEdit,
}: {
  agent: AgentDTO;
  overview: AgentsOverviewDTO;
  canEdit: boolean;
}) {
  const noEdit = canEdit ? undefined : 'Only an Admin can change an agent.';
  return (
    <>
      <div className={s.statGrid}>
        <div className={s.stat}>
          <div className={s.statLbl}>Model</div>
          <div className={s.statVal}>{agent.model}</div>
        </div>
        <div className={s.stat}>
          <div className={s.statLbl}>Cost per 1k</div>
          <div className={s.statVal}>{inr(agent.costPer1kMinor)}</div>
        </div>
        <div className={s.stat}>
          <div className={s.statLbl}>Eval score</div>
          <div className={s.statVal} style={{ color: evalColor(agent.evalScore), fontWeight: 600 }}>
            {agent.evalScore === null ? 'pending' : `${agent.evalScore}%`}
          </div>
        </div>
      </div>

      <PromptSection agent={agent} models={overview.models} canEdit={canEdit} noEdit={noEdit} />

      <section className={s.section}>
        <div className={s.sectionHead}>
          <Eyebrow>Evals · last 30 days</Eyebrow>
        </div>
        {agent.evals.length ? (
          <div className={cx(s.statGrid, s.evalGrid)} style={{ margin: 0 }}>
            {agent.evals.map((e) => (
              <div key={e.label} className={s.stat}>
                <div className={s.statLbl}>{e.label}</div>
                <div className={cx(s.statVal, s.statValLg)} style={{ color: evalToneColor(e.tone) }}>
                  {e.value}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className={s.hint}>No eval runs yet.</p>
        )}
      </section>

      <Calibration bands={agent.calibration} />

      <section className={s.section}>
        <div className={s.sectionHead}>
          <Eyebrow>Versions</Eyebrow>
          <span className={s.hint}>every prompt or model change ships as a new version</span>
        </div>
        {agent.versions.length ? (
          <div className={s.versions}>
            {agent.versions.map((v) => (
              <div key={v.version} className={s.version}>
                <span className={s.versionNo}>v{v.version}</span>
                <span className={cx(s.versionMeta, s.ellipsis)}>
                  <span className="mono">{v.model}</span> · {v.createdBy} · {shortDate(v.createdAt)}
                </span>
                <VersionStatus live={v.version === agent.version} status={v.evalStatus} />
              </div>
            ))}
          </div>
        ) : (
          <p className={s.hint}>No versions recorded.</p>
        )}
      </section>

      <BoardsSection agent={agent} canEdit={canEdit} noEdit={noEdit} />
    </>
  );
}

function VersionStatus({ live, status }: { live: boolean; status: string }) {
  if (live)
    return (
      <Pill fg="var(--ok)" bg="var(--ok-bg)">
        Running
      </Pill>
    );
  const passed = status === 'passed';
  const failed = status === 'failed';
  return (
    <Pill
      fg={passed ? 'var(--text-2)' : failed ? 'var(--bad-text)' : 'var(--warn)'}
      bg={passed ? 'var(--surface-3)' : failed ? 'var(--bad-bg)' : 'var(--warn-bg)'}
    >
      {passed ? 'Evals passed' : status.charAt(0).toUpperCase() + status.slice(1)}
    </Pill>
  );
}

function PromptSection({
  agent,
  models,
  canEdit,
  noEdit,
}: {
  agent: AgentDTO;
  models: AgentsOverviewDTO['models'];
  canEdit: boolean;
  noEdit?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [prompt, setPrompt] = useState(agent.prompt);
  const [model, setModel] = useState(agent.model);
  const create = useAction(
    (v: { prompt: string; model?: string }) =>
      api.post<{ version: number }>(`/v1/agents/${agent.id}/versions`, v),
    {
      invalidate: [keys.agents],
      success: (r) => `v${r.version} created — pending golden-set evals; v${agent.version} keeps running`,
    },
  );
  const modelOptions = models.some((m) => m.id === agent.model)
    ? models
    : [{ id: agent.model, note: 'current' }, ...models];
  const changed = prompt.trim() !== agent.prompt.trim() || model !== agent.model;

  const start = () => {
    setPrompt(agent.prompt);
    setModel(agent.model);
    setEditing(true);
  };
  const save = () =>
    create.mutate(
      { prompt: prompt.trim(), ...(model !== agent.model ? { model } : {}) },
      { onSuccess: () => setEditing(false) },
    );

  return (
    <section className={s.section}>
      <div className={s.sectionHead}>
        <Eyebrow>System prompt</Eyebrow>
        {!editing && (
          <Button size="sm" onClick={start} disabled={!canEdit} title={noEdit}>
            Edit prompt
          </Button>
        )}
      </div>
      {editing ? (
        <div className={s.editGrid}>
          <label>
            <span className="sr-only">System prompt</span>
            <TextArea
              className={s.promptEdit}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              maxLength={8000}
              data-autofocus
            />
          </label>
          <label>
            <span className={s.wizLabel}>Model</span>
            <select className={s.select} value={model} onChange={(e) => setModel(e.target.value)}>
              {modelOptions.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.id} — {m.note}
                </option>
              ))}
            </select>
          </label>
          <div className={s.editActions}>
            <span className={s.hint}>
              Ships as a new version after golden-set evals; v{agent.version} keeps running meanwhile.
            </span>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              variant="primary"
              onClick={save}
              loading={create.isPending}
              disabled={!changed || prompt.trim().length < 20}
            >
              Create new version
            </Button>
          </div>
        </div>
      ) : (
        <div className={s.prompt}>{agent.prompt}</div>
      )}
    </section>
  );
}

function BoardsSection({ agent, canEdit, noEdit }: { agent: AgentDTO; canEdit: boolean; noEdit?: string }) {
  const [editing, setEditing] = useState(false);
  const [sel, setSel] = useState<string[]>(agent.boards.map((b) => b.id));
  const boards = useBoards();
  useEffect(() => {
    if (!editing) setSel(agent.boards.map((b) => b.id));
  }, [agent.boards, editing]);
  const save = useAction((boardIds: string[]) => api.put(`/v1/agents/${agent.id}/boards`, { boardIds }), {
    invalidate: [keys.agents, keys.boards],
    success: (_r, ids) =>
      `${agent.name} now on ${boardList((boards.data ?? []).filter((b) => ids.includes(b.id)))}`,
  });
  const same = sel.length === agent.boards.length && agent.boards.every((b) => sel.includes(b.id));

  return (
    <section className={s.section}>
      <div className={s.sectionHead}>
        <Eyebrow>Attached boards</Eyebrow>
        {!editing && (
          <Button size="sm" onClick={() => setEditing(true)} disabled={!canEdit} title={noEdit}>
            Attach to boards
          </Button>
        )}
      </div>
      {editing ? (
        <div className={s.editGrid}>
          {boards.isLoading ? (
            <Skeleton h={120} />
          ) : (
            <fieldset className={s.checkList} style={{ border: 0, padding: 0, margin: 0 }}>
              <legend className="sr-only">Boards this agent works on</legend>
              {(boards.data ?? []).map((b) => (
                <label key={b.id} className={s.check}>
                  <input
                    type="checkbox"
                    checked={sel.includes(b.id)}
                    onChange={(e) =>
                      setSel((cur) => (e.target.checked ? [...cur, b.id] : cur.filter((x) => x !== b.id)))
                    }
                  />
                  <span className={s.checkName}>{b.name}</span>
                  <span className={s.checkSrc}>{b.source}</span>
                </label>
              ))}
            </fieldset>
          )}
          <div className={s.editActions}>
            <span className={s.hint}>
              {sel.length === 0
                ? 'With no boards the agent sees no mail.'
                : `${sel.length} board${sel.length === 1 ? '' : 's'} selected`}
            </span>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={same}
              loading={save.isPending}
              onClick={() => save.mutate(sel, { onSuccess: () => setEditing(false) })}
            >
              Save boards
            </Button>
          </div>
        </div>
      ) : agent.boards.length ? (
        <div className={s.boardPills}>
          {agent.boards.map((b) => (
            <span key={b.id} className={s.boardPill}>
              {b.name}
            </span>
          ))}
        </div>
      ) : (
        <p className={s.hint}>Not attached to any board — it sees no mail.</p>
      )}
    </section>
  );
}
