/**
 * New action wizard: Define → Risk & guardrails → Approval cycle. The risk group decides the approval
 * route; every new action starts suggest-only and pending Risk review.
 */
import type { ActionTemplateBody, ActionTemplateDTO, ActionsDTO, RiskCell } from '@ci/contracts';
import { useState } from 'react';
import { api } from '../../../lib/api';
import { keys, useAction, usePolicies } from '../../../lib/queries';
import { Button, Eyebrow, Input, Modal, Skeleton } from '../../../ui';
import { WarnNote, WizardSteps } from '../agents/WizardSteps';
import { Chain } from './Chain';
import { guardrails, ROUTES, SYSTEMS } from './cells';
import s from './actions.module.css';

const STEPS = ['Define', 'Risk & guardrails', 'Approval cycle'];

export function NewActionWizard({
  open,
  onClose,
  data,
  initialCell,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  data: ActionsDTO;
  initialCell: RiskCell;
  onCreated: (cell: RiskCell) => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title="New action template" width={600}>
      {open && <Body onClose={onClose} data={data} initialCell={initialCell} onCreated={onCreated} />}
    </Modal>
  );
}

function Body({
  onClose,
  data,
  initialCell,
  onCreated,
}: {
  onClose: () => void;
  data: ActionsDTO;
  initialCell: RiskCell;
  onCreated: (cell: RiskCell) => void;
}) {
  const [step, setStep] = useState(0);
  const [name, setName] = useState('');
  const [system, setSystem] = useState(SYSTEMS[0]!);
  const [cell, setCell] = useState<RiskCell>(initialCell);
  const policies = usePolicies();
  const title = (c: RiskCell) => data.cells.find((x) => x.cell === c)?.title ?? c;
  const chain = policies.data?.cycles.find((x) => x.cell === title(cell));

  const create = useAction(
    (b: ActionTemplateBody) => api.post<ActionTemplateDTO>('/v1/actions/templates', b),
    {
      invalidate: [keys.actions],
      success: (_r, b) => `${b.name} created as suggest-only in “${title(b.cell)}” — pending Risk review.`,
    },
  );

  const nameOk = name.trim().length >= 3;

  return (
    <>
      <WizardSteps steps={STEPS} current={step} />

      {step === 0 && (
        <>
          <label style={{ display: 'block', marginBottom: 13 }}>
            <span className={s.wizLabel}>Action name</span>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Waive locker rent for one year"
              maxLength={120}
              data-autofocus
            />
          </label>
          <span className={s.wizLabel} id="aw-sys">
            Executes via
          </span>
          <div className={s.systems} role="radiogroup" aria-labelledby="aw-sys">
            {SYSTEMS.map((x) => (
              <button
                key={x}
                type="button"
                role="radio"
                aria-checked={x === system}
                className={s.opt}
                onClick={() => setSystem(x)}
              >
                {x}
              </button>
            ))}
          </div>
        </>
      )}

      {step === 1 && (
        <>
          <span className={s.wizLabel} id="aw-cell">
            Risk group — decides the approval route automatically
          </span>
          <div className={s.cellOpts} role="radiogroup" aria-labelledby="aw-cell">
            {data.cells.map((c) => (
              <button
                key={c.cell}
                type="button"
                role="radio"
                aria-checked={c.cell === cell}
                className={s.opt}
                onClick={() => setCell(c.cell)}
                style={{ padding: '11px 13px', borderRadius: 10 }}
              >
                <span className={s.optTitle}>{c.title}</span>
                <span className={s.optSub}>{ROUTES[c.cell]}</span>
              </button>
            ))}
          </div>
          <Eyebrow>Guardrails · always on, not optional</Eyebrow>
          <ul className={s.guards}>
            {guardrails(cell).map((g) => (
              <li key={g} className={s.guard}>
                <span className={s.tick} aria-hidden>
                  ✓
                </span>
                <span>{g}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {step === 2 && (
        <>
          <div className={s.chainTitle}>{title(cell)}</div>
          <div className={s.chainRoute}>{ROUTES[cell]}</div>
          {policies.isLoading ? (
            <Skeleton h={30} style={{ marginBottom: 14 }} />
          ) : chain ? (
            <Chain steps={chain.chain} label="Approval chain" />
          ) : null}
          <WarnNote>
            New actions always start suggest-only, run against the golden set, and need Risk sign-off before
            the first real execution — whatever the risk group.
          </WarnNote>
        </>
      )}

      <div className={s.foot}>
        <Button onClick={onClose}>Cancel</Button>
        {step > 0 && <Button onClick={() => setStep(step - 1)}>← Back</Button>}
        <div className={s.footRight}>
          {step === 0 && !nameOk && <span className={s.footNote}>Name the action first.</span>}
          {step < 2 ? (
            <Button variant="dark" disabled={!nameOk} onClick={() => setStep(step + 1)}>
              Continue →
            </Button>
          ) : (
            <Button
              variant="primary"
              loading={create.isPending}
              onClick={() =>
                create.mutate(
                  { name: name.trim(), system, cell },
                  {
                    onSuccess: () => {
                      onCreated(cell);
                      onClose();
                    },
                  },
                )
              }
            >
              Create suggest-only
            </Button>
          )}
        </div>
      </div>
    </>
  );
}
