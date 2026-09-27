/**
 * The 2×2 risk matrix, as the product explains it: rows = does money move, columns = can it be undone.
 * Limits mirror the server's policy (apps/server/src/domain/risk.ts); the server still enforces them.
 */
import type { RiskCell, RiskCellDTO } from '@ci/contracts';

export const ROWS: { label: string; money: 0 | 1 }[] = [
  { label: 'No money moves', money: 0 },
  { label: 'Money moves', money: 1 },
];
export const COLS: { label: string; irreversible: 0 | 1 }[] = [
  { label: 'Can be undone', irreversible: 0 },
  { label: 'Cannot be undone', irreversible: 1 },
];

export const cellKey = (money: 0 | 1, irreversible: 0 | 1) => `${money}-${irreversible}` as RiskCell;
export const isCell = (v: string | null): v is RiskCell =>
  v === '0-0' || v === '0-1' || v === '1-0' || v === '1-1';

/** Highest dial level each cell may reach: only 0-0 may auto-execute; 1-1 is locked to suggest-only. */
export const MAX_DIAL: Record<RiskCell, number> = { '0-0': 2, '0-1': 1, '1-0': 1, '1-1': 0 };

export const DIAL_LABELS = ['Suggest only', 'Suggest + approve', 'Auto-execute'] as const;

export function dialBlockedReason(cell: RiskCellDTO, level: number): string | null {
  if (level <= MAX_DIAL[cell.cell] && !(cell.locked && level > 0)) return null;
  if (cell.locked) return 'Locked by policy — this cell is always suggest-only.';
  return 'Auto-execute is only possible for actions that can be undone and move no money.';
}

export interface CellLook {
  state: string;
  dot: string;
  fg: string;
  tint: string;
  line: string;
}

/** The word and colours for a cell's current autonomy. */
export function cellLook(c: RiskCellDTO): CellLook {
  if (c.locked)
    return {
      state: 'Suggest only · locked',
      dot: 'var(--bad)',
      fg: 'var(--bad-text)',
      tint: 'var(--bad-bg-2)',
      line: 'var(--bad-line)',
    };
  if (c.dial >= 2)
    return {
      state: 'Auto-executing',
      dot: 'var(--ok-dot)',
      fg: 'var(--ok)',
      tint: 'var(--ok-bg-3)',
      line: 'var(--ok-line)',
    };
  if (c.dial === 1)
    return {
      state: c.cell === '1-0' ? 'Suggest + approve' : 'Suggest + dual approve',
      dot: 'var(--accent)',
      fg: 'var(--accent)',
      tint: 'var(--accent-bg-4)',
      line: 'var(--accent-line)',
    };
  return {
    state: 'Suggest only',
    dot: 'var(--dot-idle)',
    fg: 'var(--text-2)',
    tint: 'var(--surface-2)',
    line: 'var(--line)',
  };
}

const WORDS = ['No', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'];
const countWord = (n: number) => WORDS[n] ?? String(n);

export function cellNote(c: RiskCellDTO, autoActions: number): string {
  switch (c.cell) {
    case '0-0':
      return c.dial >= 2
        ? `${countWord(autoActions)} action${autoActions === 1 ? '' : 's'} the AI may do on its own, reviewed afterwards. The only place it acts unsupervised.`
        : 'The only place the AI may act on its own — today every action here waits for a person.';
    case '0-1':
      return 'Permanent but no money moves. Two approvers, always.';
    case '1-0':
      return 'Money moves but it can be reversed. One approver plus an undo window.';
    default:
      return 'Never automated. The agent fills the form; a human signs it.';
  }
}

export function gateLook(c: RiskCellDTO): { icon: string; fg: string; word: string } {
  if (c.locked) return { icon: '×', fg: 'var(--bad-text)', word: 'Locked' };
  if (c.cell === '0-0') return { icon: '✓', fg: 'var(--ok)', word: 'Signed off' };
  return { icon: '!', fg: 'var(--warn-strong)', word: 'Gate' };
}

/** The approval route each cell implies — shown when choosing a risk group for a new action. */
export const ROUTES: Record<RiskCell, string> = {
  '0-0': 'Eligible for auto-execution once evals pass and Risk signs off',
  '0-1': 'Two approvers, always',
  '1-0': 'One approver plus a 30-second undo window',
  '1-1': 'Maker–checker, locked — never automated',
};

export function guardrails(cell: RiskCell): string[] {
  return [
    'Every field verified against a system record or marked inferred',
    'Idempotency key — a duplicate approval cannot execute twice',
    'Hard stops (regulator, fraud, vulnerable customer) suspend the action',
    'Full trace written to the immutable audit log',
    cell.endsWith('0')
      ? 'Undo window while reversal is safe'
      : 'No undo — permanence is why approvals are doubled',
  ];
}

export const SYSTEMS = ['Finacle API', 'Cards system', 'Payments hub', 'Doc service', 'Branch workflow'];

export const APPROVAL_TONE = {
  auto: { word: 'Auto', fg: 'var(--ok)', bg: 'var(--ok-bg-2)' },
  single: { word: 'Single', fg: 'var(--text-2)', bg: 'var(--surface-4)' },
  dual: { word: 'Dual', fg: 'var(--accent)', bg: 'var(--accent-bg-2)' },
} as const;
