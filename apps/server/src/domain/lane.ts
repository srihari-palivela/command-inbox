import type { Lane } from '@ci/contracts';

export interface LaneInputs {
  hardStop: string | null;
  confidence: number;
  bar: number;
  queryTypeOwned: boolean;
  multiIntent: boolean;
  hasTemplate: boolean;
  fieldsComplete: boolean;
  coverage: 'full' | 'partial' | 'none';
  informational: boolean;
}

export interface LaneDecision {
  lane: Lane;
  note: string;
}

/**
 * The lane decision is deterministic and ordered: safety stops first, then ownership, then
 * confidence. The model only supplies inputs; it never picks the lane.
 */
export function decideLane(i: LaneInputs): LaneDecision {
  if (i.hardStop) return { lane: 'manual', note: `Held back — ${i.hardStop}` };
  if (i.multiIntent) return { lane: 'manual', note: 'Two separate questions in one email' };
  if (!i.queryTypeOwned) return { lane: 'manual', note: 'No team owns this query type yet' };
  if (i.hasTemplate && !i.informational) {
    if (i.fieldsComplete && i.confidence >= i.bar)
      return { lane: 'auto', note: 'Filled in, waiting at the approval gate' };
    return { lane: 'manual', note: 'Action matched but fields or confidence fall short' };
  }
  if (i.coverage === 'full' && i.confidence >= i.bar)
    return { lane: 'draft', note: 'Cited draft ready to send' };
  if (i.coverage !== 'none') return { lane: 'draft', note: 'Not confident enough — read it before sending' };
  return { lane: 'manual', note: 'No approved content covers this — handed to a person' };
}
