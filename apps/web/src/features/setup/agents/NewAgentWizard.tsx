/**
 * New agent wizard: Template → Name & model → Prompt → Boards & launch. Every new agent starts in
 * observe mode and scores nothing until the golden-set evals pass.
 */
import type { AgentBody, AgentsOverviewDTO, AgentTemplateDTO } from '@ci/contracts';
import { useState } from 'react';
import { api } from '../../../lib/api';
import { keys, useAction, useBoards } from '../../../lib/queries';
import { Button, Eyebrow, Input, Modal, Skeleton, TextArea } from '../../../ui';
import { WarnNote, WizardSteps } from './WizardSteps';
import s from './agents.module.css';

const STEPS = ['Template', 'Name & model', 'Prompt', 'Boards & launch'];

const namePlaceholder = (t: AgentTemplateDTO | undefined) =>
  !t ? '' : t.key === 'bucketing' ? 'Cards Bucketer' : t.key === 'drafting' ? 'NRI Reply Drafter' : `My ${t.label.toLowerCase()}`;

export function NewAgentWizard({ open, onClose, overview }: { open: boolean; onClose: () => void; overview: AgentsOverviewDTO }) {
  return (
    <Modal open={open} onClose={onClose} title="New AI agent" width={640}>
      {open && <WizardBody onClose={onClose} overview={overview} />}
    </Modal>
  );
}

function WizardBody({ onClose, overview }: { onClose: () => void; overview: AgentsOverviewDTO }) {
  const [step, setStep] = useState(0);
  const [tplKey, setTplKey] = useState<AgentTemplateDTO['key'] | null>(null);
  const [name, setName] = useState('');
  const [model, setModel] = useState(overview.models[1]?.id ?? overview.models[0]?.id ?? '');
  const [prompt, setPrompt] = useState('');
  const [boardIds, setBoardIds] = useState<string[]>([]);
  const boards = useBoards();
  const tpl = overview.templates.find((t) => t.key === tplKey);
  const taken = overview.agents.some((a) => a.name.toLowerCase() === name.trim().toLowerCase());

  const launch = useAction((body: AgentBody) => api.post('/v1/agents', body), {
    invalidate: [keys.agents, keys.boards, keys.me],
    success: (_r, v) => `${v.name} created in observe mode — it scores nothing until the golden-set evals pass.`,
  });

  const pick = (t: AgentTemplateDTO) => {
    if (t.key !== tplKey) {
      setTplKey(t.key);
      setPrompt(t.prompt);
      setModel(t.recommendedModel);
    }
    setStep(1);
  };

  const canNext = step === 0 ? !!tpl : step === 1 ? name.trim().length >= 2 && !taken && !!model : step === 2 ? prompt.trim().length >= 20 : false;
  const nextHint = step === 1 ? (taken ? 'An agent with that name already exists.' : !name.trim() ? 'Give the agent a name first.' : '') : step === 2 && prompt.trim().length < 20 ? 'Write at least a sentence of prompt.' : '';
  const summary = `${name.trim() || 'Unnamed agent'} · ${tpl?.label ?? '—'} · ${model} · ${boardIds.length || 'no'} board${boardIds.length === 1 ? '' : 's'}`;

  return (
    <>
      <WizardSteps steps={STEPS} current={step} />

      {step === 0 && (
        <div className={s.choices} role="radiogroup" aria-label="Template">
          {overview.templates.map((t) => (
            <button key={t.key} type="button" role="radio" aria-checked={t.key === tplKey} className={s.choice} onClick={() => pick(t)}>
              <span className={s.choiceAbbr} aria-hidden>
                {t.abbr}
              </span>
              <span style={{ minWidth: 0 }}>
                <span className={s.choiceTitle} style={{ display: 'block' }}>
                  {t.label}
                </span>
                <span className={s.choiceSub} style={{ display: 'block' }}>
                  {t.what}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}

      {step === 1 && (
        <>
          <label className={s.wizBlock} style={{ display: 'block' }}>
            <span className={s.wizLabel}>Agent name — yours, not ours</span>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={namePlaceholder(tpl)} maxLength={60} data-autofocus style={{ height: 40, fontSize: 13.5 }} />
          </label>
          <span className={s.wizLabel} id="wiz-model">
            Model
          </span>
          <div className={s.choices} role="radiogroup" aria-labelledby="wiz-model">
            {overview.models.map((m) => (
              <button key={m.id} type="button" role="radio" aria-checked={m.id === model} className={s.choice} onClick={() => setModel(m.id)}>
                <span style={{ minWidth: 0, flex: '1 1 auto' }}>
                  <span className={s.choiceMono} style={{ display: 'block' }}>
                    {m.id}
                  </span>
                  <span className={s.choiceSub} style={{ display: 'block', marginTop: 2 }}>
                    {m.note}
                  </span>
                </span>
                {tpl?.recommendedModel === m.id && <span className={s.rec}>recommended for this template</span>}
              </button>
            ))}
          </div>
        </>
      )}

      {step === 2 && (
        <>
          <label style={{ display: 'block', marginBottom: 10 }}>
            <span className={s.wizLabel}>System prompt</span>
            <TextArea className={s.wizPrompt} value={prompt} onChange={(e) => setPrompt(e.target.value)} maxLength={8000} data-autofocus />
          </label>
          <WarnNote>
            Starts from the {tpl?.label ?? 'template'} base. Your edits ship as v1 and every change after that is versioned. Guardrail lines — no recall answers, no unsourced amounts — are
            enforced outside the prompt and cannot be edited away.
          </WarnNote>
        </>
      )}

      {step === 3 && (
        <>
          <fieldset style={{ border: 0, padding: 0, margin: '0 0 4px' }}>
            <legend className={s.wizLabel}>Attach to boards</legend>
            {boards.isLoading ? (
              <Skeleton h={160} />
            ) : (
              <div className={s.checkList}>
                {(boards.data ?? []).map((b) => (
                  <label key={b.id} className={s.check}>
                    <input type="checkbox" checked={boardIds.includes(b.id)} onChange={(e) => setBoardIds((cur) => (e.target.checked ? [...cur, b.id] : cur.filter((x) => x !== b.id)))} />
                    <span className={s.checkName}>{b.name}</span>
                    <span className={s.checkSrc}>{b.source}</span>
                  </label>
                ))}
              </div>
            )}
          </fieldset>
          <div className={s.plan}>
            <Eyebrow>Launch plan</Eyebrow>
            <div className={s.planSummary}>{summary}</div>
            <div className={s.planSteps}>
              <span>1 · Runs against the golden set before it sees live mail.</span>
              <span>2 · Starts in observe mode — outputs recorded, nothing acted on.</span>
              <span>3 · Goes live per board only after evals clear the bar and Risk signs off.</span>
            </div>
          </div>
        </>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 18, paddingTop: 12, borderTop: '1px solid var(--line-soft)' }}>
        <Button onClick={onClose}>Cancel</Button>
        {step > 0 && <Button onClick={() => setStep(step - 1)}>← Back</Button>}
        <div className={s.footRight}>
          {step < 3 && nextHint && <span className={s.footNote}>{nextHint}</span>}
          {step === 3 && boardIds.length === 0 && <span className={s.footNote}>Attach at least one board.</span>}
          {step > 0 && step < 3 && (
            <Button variant="dark" disabled={!canNext} onClick={() => setStep(step + 1)}>
              {step === 1 ? 'Write the prompt →' : 'Continue →'}
            </Button>
          )}
          {step === 3 && (
            <Button
              variant="primary"
              disabled={!boardIds.length || !tpl}
              loading={launch.isPending}
              onClick={() => tpl && launch.mutate({ name: name.trim(), template: tpl.key, model, prompt: prompt.trim(), boardIds }, { onSuccess: onClose })}
            >
              Launch in observe mode
            </Button>
          )}
        </div>
      </div>
    </>
  );
}
