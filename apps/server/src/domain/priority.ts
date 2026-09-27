import type { Priority } from '@ci/contracts';

export interface PrioritySignals {
  regulatorNamed: boolean;
  vulnerable: boolean;
  minutesLeft: number | null;
  amountInr: number | null;
  contactCount: number;
  informational: boolean;
}

export interface PriorityRuleRow {
  key: string;
  hard: boolean;
  enabled: boolean;
}

const RANK: Record<Priority, number> = { P1: 1, P2: 2, P3: 3, P4: 4 };
const atLeast = (cur: Priority, floor: Priority): Priority => (RANK[cur] > RANK[floor] ? floor : cur);

/**
 * Deterministic priority rules (Rules & policies → Priority rules). Hard rules always fire — they are
 * policy, not preference — even if a stale row says disabled. Weighted rules can be switched off.
 */
export function rankPriority(
  s: PrioritySignals,
  rules: PriorityRuleRow[],
): { priority: Priority; fired: string[] } {
  const on = (key: string) => {
    const r = rules.find((x) => x.key === key);
    return !r || r.hard || r.enabled;
  };
  const fired: string[] = [];
  let p: Priority = 'P3';
  const fire = (key: string, cond: boolean, apply: () => void) => {
    if (cond && on(key)) {
      fired.push(key);
      apply();
    }
  };
  fire('p7', s.informational && !s.regulatorNamed && !s.vulnerable, () => (p = 'P4'));
  fire('p5', s.minutesLeft !== null && s.minutesLeft <= 8 * 60, () => (p = atLeast(p, 'P2')));
  fire('p4', s.contactCount >= 3, () => (p = atLeast(p, 'P2')));
  fire('p3', (s.amountInr ?? 0) >= 10_00_000, () => (p = atLeast(p, 'P2')));
  fire('p2', s.minutesLeft !== null && s.minutesLeft <= 120, () => (p = 'P1'));
  fire('p1', s.regulatorNamed, () => (p = 'P1'));
  fire('p6', s.vulnerable, () => (p = 'P1'));
  return { priority: p, fired };
}
