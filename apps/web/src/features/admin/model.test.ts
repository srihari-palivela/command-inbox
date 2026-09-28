import { describe, expect, it } from 'vitest';
import { errorsFor, errorsUnder, formatMetric, numberOrRaw, parseConfigErrors, slugify } from './model';

describe('parseConfigErrors', () => {
  it('splits the server detail into paths and messages', () => {
    const errs = parseConfigErrors(
      "taxonomy.categories.0.key: String should match pattern '^[a-z][a-z0-9_]{0,47}$'; thresholds.autoMinConfidence: Input should be greater than or equal to 0.5",
    );
    expect(errs).toEqual([
      { path: 'taxonomy.categories.0.key', message: "String should match pattern '^[a-z][a-z0-9_]{0,47}$'" },
      { path: 'thresholds.autoMinConfidence', message: 'Input should be greater than or equal to 0.5' },
    ]);
    expect(errorsFor(errs, 'thresholds.autoMinConfidence')).toHaveLength(1);
    expect(errorsUnder(errs, 'taxonomy')).toHaveLength(1);
  });

  it('keeps section-level rules and path-less messages', () => {
    expect(parseConfigErrors('thresholds: draftMinConfidence must not exceed autoMinConfidence')).toEqual([
      { path: 'thresholds', message: 'draftMinConfidence must not exceed autoMinConfidence' },
    ]);
    expect(parseConfigErrors("fallback category 'x' is not in the taxonomy")).toEqual([
      { path: '', message: "fallback category 'x' is not in the taxonomy" },
    ]);
    expect(parseConfigErrors(undefined)).toEqual([]);
  });
});

describe('formatting', () => {
  it('reads metrics the way people do', () => {
    expect(formatMetric('accuracy', 0.8182)).toBe('81.8%');
    expect(formatMetric('ece', 0.1191)).toBe('0.119');
    expect(formatMetric('laneSafetyViolations', 0)).toBe('0');
    expect(formatMetric('macroF1', null)).toBe('—');
  });

  it('keeps unparseable numbers as text so the server can name them', () => {
    expect(numberOrRaw('0.9')).toBe(0.9);
    expect(numberOrRaw('')).toBe('');
    expect(numberOrRaw('abc')).toBe('abc');
  });

  it('derives a deployment key from its name', () => {
    expect(slugify('Trade ops — Mumbai')).toBe('trade_ops_mumbai');
    expect(slugify('2nd desk')).toBe('nd_desk');
  });
});
