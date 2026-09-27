import { describe, expect, it } from 'vitest';
import { decideLane, type LaneInputs } from './lane.js';
import { parseNaturalFilters } from './nl-filter.js';
import { maskPii, unmask } from './pii.js';
import { rankPriority, type PrioritySignals } from './priority.js';
import { cellOf, chainFor, LOCKED_CELL, MAX_DIAL } from './risk.js';
import { atRisk, computeSla, slaBudget } from './sla.js';
import { allowedTransitions, canTransition, isOpen } from './transitions.js';

describe('risk matrix', () => {
  it('places actions by money movement (row) and reversibility (column)', () => {
    expect(cellOf(true, false)).toBe('0-0');
    expect(cellOf(false, false)).toBe('0-1');
    expect(cellOf(true, true)).toBe('1-0');
    expect(cellOf(false, true)).toBe('1-1');
  });

  it('never lets anything but the reversible, no-money cell auto-execute', () => {
    expect(MAX_DIAL['0-0']).toBe(2);
    for (const cell of ['0-1', '1-0', '1-1'] as const) expect(MAX_DIAL[cell]).toBeLessThan(2);
    expect(MAX_DIAL[LOCKED_CELL]).toBe(0);
  });

  it('requires maker + checker for anything irreversible, whatever the template or dial says', () => {
    for (const mode of ['auto', 'single', 'dual'] as const) {
      expect(chainFor('0-1', mode, 2)).toBe('dual');
      expect(chainFor('1-1', mode, 2)).toBe('dual');
    }
  });

  it('lets a template raise the bar above the cell minimum but never lower it', () => {
    expect(chainFor('1-0', 'auto', 2)).toBe('single_undo');
    expect(chainFor('1-0', 'dual', 0)).toBe('dual');
    expect(chainFor('0-0', 'dual', 2)).toBe('dual');
  });

  it('auto-executes only when both the template and the dial allow it', () => {
    expect(chainFor('0-0', 'auto', 2)).toBe('auto');
    expect(chainFor('0-0', 'auto', 1)).toBe('single_undo');
    expect(chainFor('0-0', 'single', 2)).toBe('single_undo');
  });
});

describe('lane decision', () => {
  const base: LaneInputs = {
    hardStop: null,
    confidence: 0.94,
    bar: 0.78,
    queryTypeOwned: true,
    multiIntent: false,
    hasTemplate: true,
    fieldsComplete: true,
    coverage: 'full',
    informational: false,
  };

  it('fills an action when a template matches, fields are complete and confidence clears the bar', () => {
    expect(decideLane(base).lane).toBe('auto');
  });

  it('checks safety before anything else', () => {
    expect(decideLane({ ...base, hardStop: 'ombudsman named' })).toEqual({ lane: 'manual', note: 'Held back — ombudsman named' });
  });

  it('stands down on multi-intent mail and on query types no team owns', () => {
    expect(decideLane({ ...base, multiIntent: true }).lane).toBe('manual');
    expect(decideLane({ ...base, queryTypeOwned: false }).lane).toBe('manual');
  });

  it('does not act below the bar or with missing fields', () => {
    expect(decideLane({ ...base, confidence: 0.7 }).lane).toBe('manual');
    expect(decideLane({ ...base, fieldsComplete: false }).lane).toBe('manual');
  });

  it('drafts informational answers only from approved content, and never from nothing', () => {
    const info = { ...base, hasTemplate: false, informational: true };
    expect(decideLane(info)).toEqual({ lane: 'draft', note: 'Cited draft ready to send' });
    expect(decideLane({ ...info, confidence: 0.6 }).note).toMatch(/read it before sending/);
    expect(decideLane({ ...info, coverage: 'none' }).lane).toBe('manual');
  });
});

describe('priority rules', () => {
  const calm: PrioritySignals = { regulatorNamed: false, vulnerable: false, minutesLeft: 1000, amountInr: null, contactCount: 1, informational: false };

  it('defaults to P3 and drops pure information requests to P4', () => {
    expect(rankPriority(calm, []).priority).toBe('P3');
    expect(rankPriority({ ...calm, informational: true }, []).priority).toBe('P4');
  });

  it('raises repeat contacts, large amounts and near deadlines to at least P2', () => {
    expect(rankPriority({ ...calm, contactCount: 3 }, []).priority).toBe('P2');
    expect(rankPriority({ ...calm, amountInr: 18_40_000 }, []).priority).toBe('P2');
    expect(rankPriority({ ...calm, minutesLeft: 300 }, []).priority).toBe('P2');
  });

  it('makes a named regulator or a vulnerable customer P1 even on an informational query', () => {
    expect(rankPriority({ ...calm, informational: true, regulatorNamed: true }, []).priority).toBe('P1');
    expect(rankPriority({ ...calm, vulnerable: true }, []).fired).toContain('p6');
  });

  it('ignores a disabled weighted rule but never a hard one', () => {
    expect(rankPriority({ ...calm, contactCount: 5 }, [{ key: 'p4', hard: false, enabled: false }]).priority).toBe('P3');
    expect(rankPriority({ ...calm, regulatorNamed: true }, [{ key: 'p1', hard: true, enabled: false }]).priority).toBe('P1');
  });
});

describe('deadlines', () => {
  const now = new Date('2026-09-27T10:00:00Z');
  const due = (mins: number) => new Date(now.getTime() + mins * 60_000);

  it('grades pressure against the budget', () => {
    expect(computeSla({ status: 'with_human', dueAt: due(600), slaMinutes: 1440, pausedAt: null }, now).tone).toBe('due_soon');
    expect(computeSla({ status: 'with_human', dueAt: due(1000), slaMinutes: 1440, pausedAt: null }, now).tone).toBe('on_track');
    expect(computeSla({ status: 'with_human', dueAt: due(45), slaMinutes: 1440, pausedAt: null }, now).tone).toBe('almost_late');
    expect(computeSla({ status: 'with_human', dueAt: due(-5), slaMinutes: 240, pausedAt: null }, now)).toMatchObject({ tone: 'late', minutesLeft: -5 });
  });

  it('pauses the clock while waiting on the customer and stops it when closed', () => {
    const paused = computeSla({ status: 'waiting_customer', dueAt: due(300), slaMinutes: 1440, pausedAt: new Date(now.getTime() - 60 * 60_000) }, now);
    expect(paused).toMatchObject({ tone: 'paused', minutesLeft: 360 });
    expect(computeSla({ status: 'resolved', dueAt: due(-500), slaMinutes: 240, pausedAt: null }, now).tone).toBe('closed');
  });

  it('counts due-soon, almost-late and late as at risk', () => {
    expect(['on_track', 'due_soon', 'almost_late', 'late', 'paused', 'closed'].filter((t) => atRisk(t as never))).toEqual(['due_soon', 'almost_late', 'late']);
  });

  it('sets budgets by priority, segment and escalation', () => {
    expect(slaBudget('P1', 'Corporate')).toBe(240);
    expect(slaBudget('P1', 'Retail')).toBe(480);
    expect(slaBudget('P3', 'Retail')).toBe(1440);
    expect(slaBudget('P4', 'Retail', true)).toBe(480);
  });
});

describe('status transitions', () => {
  it('only lets the gateway move a ticket out of executing', () => {
    expect(allowedTransitions('executing')).toEqual([]);
    expect(canTransition('awaiting_approval', 'resolved')).toBe(false);
  });

  it('allows reopening closed work', () => {
    expect(canTransition('closed', 'with_human')).toBe(true);
    expect(isOpen('closed')).toBe(false);
    expect(isOpen('waiting_customer')).toBe(true);
  });
});

describe('PII masking', () => {
  it('replaces identifiers with typed tokens and restores them from the vault', () => {
    const raw = 'Mail m.raghavan@sundaramtextiles.in or call +91 9840014471; PAN ABCDE1234F, card 4111 1111 1111 1111, A/C 123456789012.';
    const { text, vault } = maskPii(raw);
    expect(text).not.toMatch(/raghavan|ABCDE1234F|4111|123456789012|9840014471/);
    expect(text).toContain('[EMAIL_1]');
    expect(text).toContain('[PAN_1]');
    expect(text).toContain('[CARD_1]');
    expect(unmask(text, vault)).toBe(raw);
  });

  it('gives a repeated value the same token', () => {
    const { text } = maskPii('a@b.co wrote; reply to a@b.co');
    expect(text).toBe('[EMAIL_1] wrote; reply to [EMAIL_1]');
  });
});

describe('natural-language ticket filter', () => {
  const depts = [
    { id: 'd1', name: 'Chargeback & Disputes' },
    { id: 'd2', name: 'Trade & Payments' },
  ];

  it('reads a sentence into filters and removable chips', () => {
    const r = parseNaturalFilters('late disputes assigned to me', depts);
    expect(r.filters).toEqual({ team: 'd1', owner: 'mine', due: 'risk' });
    expect(r.chips.map((c) => c.text)).toEqual(['Team: Chargeback & Disputes', 'Owner: me', 'Running late']);
  });

  it('understands lanes, confidence and priority', () => {
    const r = parseNaturalFilters('P1 drafts below the bar waiting for approval', depts);
    expect(r.filters).toMatchObject({ lane: 'draft', conf: 'low', pri: 'P1', status: 'approval' });
  });

  it('returns nothing for text it does not understand', () => {
    expect(parseNaturalFilters('hello there', depts).chips).toEqual([]);
  });
});
