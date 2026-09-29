import type { CourseDTO } from '@ci/contracts';

/** Team completion colour: ≥80 done, ≥55 on the way, below that lagging. */
export const completionTone = (pct: number) =>
  pct >= 80 ? 'var(--ok)' : pct >= 55 ? 'var(--accent)' : 'var(--warn)';

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export const courseMeta = (c: CourseDTO) =>
  `${plural(c.cards.length, 'card')} · ${c.quiz.length}-question quiz · ${c.minutes} min`;
