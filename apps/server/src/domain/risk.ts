import type { ApprovalChain, ApprovalMode, RiskCell } from '@ci/contracts';

/** Row = money moves, column = cannot be undone. */
export function cellOf(reversible: boolean, moneyMoves: boolean): RiskCell {
  return `${moneyMoves ? 1 : 0}-${reversible ? 0 : 1}` as RiskCell;
}

export const CELL_TITLE: Record<RiskCell, string> = {
  '0-0': 'Can be undone · no money moves',
  '0-1': 'Cannot be undone · no money moves',
  '1-0': 'Can be undone · money moves',
  '1-1': 'Cannot be undone · money moves',
};

/** The irreversible + money cell is locked to suggest-only by policy, not configuration. */
export const LOCKED_CELL: RiskCell = '1-1';

/** Highest dial level each cell may ever reach. Only 0-0 may auto-execute. */
export const MAX_DIAL: Record<RiskCell, number> = { '0-0': 2, '0-1': 1, '1-0': 1, '1-1': 0 };

/**
 * Approval chain for an action: the template may raise the bar above the cell minimum, never lower it.
 * - Irreversible (0-1, 1-1): maker + checker, always.
 * - Reversible + money (1-0): one approver and an undo window, or dual if the template says so.
 * - Reversible, no money (0-0): auto only when the dial is at auto-execute and the template allows it.
 */
export function chainFor(cell: RiskCell, template: ApprovalMode, dialLevel: number): ApprovalChain {
  if (cell === '0-1' || cell === '1-1') return 'dual';
  if (cell === '1-0') return template === 'dual' ? 'dual' : 'single_undo';
  if (template === 'dual') return 'dual';
  if (template === 'auto' && dialLevel >= 2) return 'auto';
  return 'single_undo';
}

export const APPROVAL_CYCLES: { cell: string; chain: string[]; note: string }[] = [
  {
    cell: CELL_TITLE['0-0'],
    chain: ['AI fills & validates', 'AI executes (approved cell)', 'Sampled review weekly'],
    note: 'The only chain with no human before execution — post-hoc review, 5% sample.',
  },
  {
    cell: CELL_TITLE['1-0'],
    chain: ['AI fills & validates', 'Staff approves', 'Executes with 30s undo', 'Audit written'],
    note: 'Single approver plus an undo window. Reversal after the window needs its own approval.',
  },
  {
    cell: CELL_TITLE['0-1'],
    chain: ['AI fills & validates', 'Staff approves (maker)', 'Team lead approves (checker)', 'Executes · audit'],
    note: 'Two approvers always, because permanence.',
  },
  {
    cell: CELL_TITLE['1-1'],
    chain: ['AI fills & validates', 'Staff approves (maker)', 'Team lead approves (checker)', 'Executes · audit'],
    note: 'Locked to this chain by policy. Escalates to the lead if the checker is silent for 2 hours.',
  },
];

/** Seconds a reversible action waits before it reaches the core system (the undo window). */
export const UNDO_WINDOW_SEC = 30;
/** Seconds an approved reply waits before sending (the recall window). */
export const RECALL_WINDOW_SEC = 60;
/** Checker silence before escalation to the team lead. */
export const CHECKER_ESCALATION_MIN = 120;
