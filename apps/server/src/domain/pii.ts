/**
 * PII masking applied before any text reaches a model. Values are replaced with typed tokens;
 * the vault lets the executor re-bind them at execution time, inside the bank's boundary.
 */
const PATTERNS: { kind: string; re: RegExp }[] = [
  { kind: 'EMAIL', re: /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi },
  { kind: 'PAN', re: /\b[A-Z]{5}\d{4}[A-Z]\b/g },
  // Cards before Aadhaar: a 16-digit card number starts with an Aadhaar-shaped 12 digits.
  { kind: 'CARD', re: /\b(?:\d{4}[ -]){3}\d{4}\b/g },
  { kind: 'AADHAAR', re: /\b\d{4}\s\d{4}\s\d{4}\b(?![ -]\d)/g },
  { kind: 'PHONE', re: /(?:\+91[\s-]?)?\b[6-9]\d{9}\b/g },
  { kind: 'ACCOUNT', re: /\b\d{11,18}\b/g },
];

export interface Masked {
  text: string;
  vault: Record<string, string>;
}

export function maskPii(input: string): Masked {
  const vault: Record<string, string> = {};
  const counters: Record<string, number> = {};
  let text = input;
  for (const { kind, re } of PATTERNS) {
    text = text.replace(re, (m) => {
      const existing = Object.entries(vault).find(([, v]) => v === m);
      if (existing) return existing[0];
      counters[kind] = (counters[kind] ?? 0) + 1;
      const token = `[${kind}_${counters[kind]}]`;
      vault[token] = m;
      return token;
    });
  }
  return { text, vault };
}

export function unmask(text: string, vault: Record<string, string>): string {
  return text.replace(/\[[A-Z]+_\d+\]/g, (t) => vault[t] ?? t);
}
