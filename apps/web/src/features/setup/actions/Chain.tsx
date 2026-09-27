/** An approval chain drawn as numbered steps joined by arrows (Approval cycles, new-action wizard). */
import s from './actions.module.css';

export function Chain({ steps, label }: { steps: string[]; label: string }) {
  return (
    <ol className={s.chain} aria-label={label}>
      {steps.map((st, i) => (
        <li key={st + i} className={s.chainStep}>
          <span className={s.chainBox}>
            <span className={s.chainN} aria-hidden>
              {i + 1}
            </span>
            {st}
          </span>
          {i < steps.length - 1 && (
            <span className={s.arrow} aria-hidden>
              →
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}
