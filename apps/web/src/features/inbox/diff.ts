/**
 * Word-level diff between the AI's draft and the person's edit, used for the "Showing your edit" view.
 * Every edit is also training data (frontend review, W3), so the diff is computed on the same words the
 * server stores. LCS over tokens is fine at email length (a few hundred words).
 */
export interface DiffPart {
  text: string;
  op: 'same' | 'add' | 'del';
}

const tokenize = (s: string) => s.split(/(\s+)/).filter((t) => t.length > 0);

export function wordDiff(before: string, after: string): DiffPart[] {
  const a = tokenize(before);
  const b = tokenize(after);
  const n = a.length;
  const m = b.length;
  // Guard against pathological sizes: fall back to "all replaced".
  if (n * m > 4_000_000) return [{ text: before, op: 'del' }, { text: after, op: 'add' }];
  const dp: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i]![j] = a[i] === b[j] ? dp[i + 1]![j + 1]! + 1 : Math.max(dp[i + 1]![j]!, dp[i]![j + 1]!);
    }
  }
  const out: DiffPart[] = [];
  const push = (text: string, op: DiffPart['op']) => {
    const last = out[out.length - 1];
    if (last && last.op === op) last.text += text;
    else out.push({ text, op });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      push(a[i]!, 'same');
      i++;
      j++;
    } else if (dp[i + 1]![j]! >= dp[i]![j + 1]!) {
      push(a[i++]!, 'del');
    } else {
      push(b[j++]!, 'add');
    }
  }
  while (i < n) push(a[i++]!, 'del');
  while (j < m) push(b[j++]!, 'add');
  return out;
}

/** Split a body into paragraphs of parts, marking flagged phrases so they can be highlighted. */
export function highlight(body: string, flagged: string[]): { text: string; flag: boolean }[][] {
  const paras = body.split(/\n{2,}/);
  const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = flagged.length ? new RegExp(`(${flagged.map(esc).join('|')})`, 'gi') : null;
  return paras.map((p) => {
    if (!re) return [{ text: p, flag: false }];
    return p
      .split(re)
      .filter(Boolean)
      .map((t) => ({ text: t, flag: flagged.some((f) => f.toLowerCase() === t.toLowerCase()) }));
  });
}
