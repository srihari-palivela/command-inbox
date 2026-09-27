/**
 * Step indicator shared by the setup wizards (new agent, new action): numbered dots, done steps in
 * green, the current one in indigo — as the prototype draws them.
 */
import type { ReactNode } from 'react';
import s from './WizardSteps.module.css';

export function WizardSteps({ steps, current }: { steps: string[]; current: number }) {
  return (
    <ol className={s.steps} aria-label="Steps">
      {steps.map((label, i) => {
        const state = i < current ? 'done' : i === current ? 'current' : 'todo';
        return (
          <li
            key={label}
            className={s.step}
            data-state={state}
            aria-current={i === current ? 'step' : undefined}
          >
            <span className={s.dot} aria-hidden>
              {i < current ? '✓' : i + 1}
            </span>
            <span className={s.label}>
              {label}
              {state === 'done' && <span className="sr-only"> (done)</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** A warm note box (amber dot + text), used for "what happens next" copy in the wizards. */
export function WarnNote({ children }: { children: ReactNode }) {
  return (
    <div className={s.note}>
      <span className={s.noteDot} aria-hidden />
      <span>{children}</span>
    </div>
  );
}
