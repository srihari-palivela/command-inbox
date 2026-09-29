import { describe, expect, it } from 'vitest';
import { highlight, wordDiff } from './diff';

describe('wordDiff', () => {
  it('returns one unchanged part for identical text', () => {
    expect(wordDiff('a b c', 'a b c')).toEqual([{ text: 'a b c', op: 'same' }]);
  });

  it('groups an edited phrase into one deletion and one insertion', () => {
    const parts = wordDiff(
      'We will need the following, all of which',
      'Please arrange the following documents, all of which',
    );
    expect(parts.filter((p) => p.op === 'del').map((p) => p.text.trim())).toEqual([
      'We will need',
      'following,',
    ]);
    expect(parts.filter((p) => p.op === 'add').map((p) => p.text.trim())).toEqual([
      'Please arrange',
      'following documents,',
    ]);
    // Reassembling the kept and added parts gives the new text back exactly.
    expect(
      parts
        .filter((p) => p.op !== 'del')
        .map((p) => p.text)
        .join(''),
    ).toBe('Please arrange the following documents, all of which');
  });

  it('keeps the original recoverable from kept and deleted parts', () => {
    const before = 'Dear Mr Krishnan,\n\nThank you for writing in.';
    const after = 'Dear Mr Krishnan,\n\nThanks for writing in today.';
    expect(
      wordDiff(before, after)
        .filter((p) => p.op !== 'add')
        .map((p) => p.text)
        .join(''),
    ).toBe(before);
  });
});

describe('highlight', () => {
  it('marks flagged phrases case-insensitively and splits paragraphs', () => {
    const paras = highlight('Operate as Former or Survivor.\n\nNext para', ['former or survivor']);
    expect(paras).toHaveLength(2);
    expect(paras[0]!.find((p) => p.flag)?.text).toBe('Former or Survivor');
  });
});
