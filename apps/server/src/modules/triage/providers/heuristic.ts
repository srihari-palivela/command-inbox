/**
 * Deterministic provider. Used in tests, offline development, and as the degraded path when the model
 * provider is unavailable. It is intentionally conservative: when unsure it lowers confidence, which
 * sends the ticket to a person.
 */
import { parseNaturalFilters } from '../../../domain/nl-filter.js';
import type {
  AgentConfig,
  BriefResult,
  ClassifyResult,
  DraftResult,
  ExtractResult,
  GroundingDoc,
  GuardResult,
  LlmProvider,
  Staged,
  TaxonomyEntry,
  TemplateSpec,
  ThreadInput,
} from './types.js';

const text = (t: ThreadInput) => (t.subject + '\n' + t.messages.map((m) => m.body).join('\n')).toLowerCase();

const staged = <T>(result: T, agent: AgentConfig | string, latencyMs: number): Staged<T> => ({
  result,
  usage: { model: typeof agent === 'string' ? agent : 'rules', tokens: null, costMinor: null, latencyMs },
});

interface Signature {
  queryType: string;
  any: string[];
  strong?: RegExp;
  informational?: boolean;
  intent: string;
}

/** Keyword signatures per query type, strongest first. */
const SIGNATURES: Signature[] = [
  {
    queryType: 'Stop payment instruction',
    any: ['stop payment', 'stop on cheque', 'place a stop', 'immediate stop'],
    strong: /cheque\s*(no\.?|number)?\s*\d{6}/,
    intent: 'stop a cheque',
  },
  {
    queryType: 'Disputed transactions',
    any: [
      'unauthorised',
      'unauthorized',
      'never transacted',
      'dispute',
      'fraudulent',
      'duplicate neft',
      'sent twice',
    ],
    intent: 'dispute a debit',
  },
  {
    queryType: 'Chargeback status',
    any: ['chargeback status', 'status of my chargeback', 'chargeback dsp'],
    intent: 'chargeback status',
  },
  {
    queryType: 'Statement re-issue',
    any: ['statement for', 're-issue the account statement', 'send me the statement', 'account statement'],
    informational: false,
    intent: 'statement copy',
  },
  {
    queryType: 'Certificate requests',
    any: ['interest certificate', 'balance confirmation', 'certificate for'],
    intent: 'certificate',
  },
  { queryType: 'Foreclosure quotes', any: ['foreclosure', 'foreclose'], intent: 'loan foreclosure' },
  {
    queryType: 'EMI reschedule requests',
    any: ['emi reschedule', 'reschedule my emi', 'emis be rescheduled', 'moratorium'],
    intent: 'EMI reschedule',
  },
  {
    queryType: 'Credit limit explanations',
    any: ['credit limit', 'limit was reduced', 'limit dropped'],
    intent: 'credit limit',
  },
  {
    queryType: 'Trade finance advisory',
    any: ['forward contract', 'forward cover', 'receivables', 'letter of credit', ' lc '],
    informational: true,
    intent: 'trade finance advice',
  },
  {
    queryType: 'SWIFT trace requests',
    any: ['swift', 'trace the payment', 'has not arrived', 'uetr'],
    informational: true,
    intent: 'trace a remittance',
  },
  { queryType: 'Locker rent waiver', any: ['locker'], intent: 'locker' },
  {
    queryType: 'Balance & charge queries',
    any: ['annual fee', 'charges', 'fee schedule', 'debited as', 'why was', 'maintenance charges'],
    informational: true,
    intent: 'fee question',
  },
  {
    queryType: 'Account maintenance',
    any: [
      'joint holder',
      'nomination',
      'registered mobile',
      'change my address',
      'standing instruction',
      'cheque book',
    ],
    informational: true,
    intent: 'account change',
  },
];

const HARD_STOPS: {
  re: RegExp;
  reason: string;
  kind: keyof Pick<GuardResult, 'regulatorNamed' | 'vulnerable' | 'legalOrFraud'>;
}[] = [
  {
    re: /ombudsman|rbi complaint|regulator|consumer forum/,
    reason: 'regulator named',
    kind: 'regulatorNamed',
  },
  { re: /legal notice|my lawyer|advocate|court/, reason: 'legal notice', kind: 'legalOrFraud' },
  { re: /fraud|scam|phishing|hacked/, reason: 'suspected fraud', kind: 'legalOrFraud' },
  {
    re: /passed away|deceased|bereave|died|terminal|serious illness|hospitali[sz]ed|can(?:no|')t afford|financial distress/,
    reason: 'vulnerable-customer signal',
    kind: 'vulnerable',
  },
];

export class HeuristicProvider implements LlmProvider {
  readonly name = 'heuristic' as const;

  async guard(t: ThreadInput): Promise<Staged<GuardResult>> {
    const body = text(t);
    const r: GuardResult = {
      stop: null,
      regulatorNamed: false,
      vulnerable: false,
      legalOrFraud: false,
      repeatContact: false,
      sentiment: 'neutral',
    };
    const reasons: string[] = [];
    for (const h of HARD_STOPS) {
      if (h.re.test(body)) {
        r[h.kind] = true;
        reasons.push(h.reason);
      }
    }
    r.repeatContact =
      /third (time|email)|3rd (time|email)|written three times|chasing|again and again/.test(body) ||
      t.customer.priorSameTopic >= 2;
    if (r.repeatContact) reasons.push('third contact');
    if (reasons.length) r.stop = reasons.join(' + ');
    r.sentiment = r.vulnerable
      ? 'vulnerable'
      : r.regulatorNamed || r.repeatContact
        ? 'escalating'
        : /urgent|disappointed|unacceptable|angry/.test(body)
          ? 'upset'
          : 'neutral';
    return staged(r, 'rules', 40);
  }

  async classify(t: ThreadInput, taxonomy: TaxonomyEntry[]): Promise<Staged<ClassifyResult>> {
    const body = ' ' + text(t) + ' ';
    const known = new Set(taxonomy.map((x) => x.name));
    const hits = SIGNATURES.filter((sig) => known.has(sig.queryType))
      .map((sig) => {
        const phrases = sig.any.filter((w) => body.includes(w));
        const strong = sig.strong ? sig.strong.test(body) : false;
        return { sig, phrases, score: phrases.length + (strong ? 2 : 0) };
      })
      .filter((h) => h.score > 0)
      .sort((a, b) => b.score - a.score);

    if (!hits.length) {
      return staged(
        {
          queryType: null,
          confidence: 0.35,
          phrases: [],
          multiIntent: false,
          informational: true,
          intents: [],
        },
        'rules',
        60,
      );
    }
    const top = hits[0]!;
    const topDept = taxonomy.find((x) => x.name === top.sig.queryType)?.department ?? null;
    const others = hits.slice(1).filter((h) => {
      const d = taxonomy.find((x) => x.name === h.sig.queryType)?.department ?? null;
      return d !== topDept && h.score >= 1;
    });
    const joined = /\b(two things|separately|also|and also)\b/.test(body);
    const multiIntent = others.length > 0 && joined;
    let confidence = top.score >= 3 ? 0.95 : top.score === 2 ? 0.88 : 0.8;
    if (multiIntent) confidence = 0.55;
    else if (hits.length > 1 && hits[1]!.score === top.score) confidence -= 0.12;
    return staged(
      {
        queryType: top.sig.queryType,
        confidence: Math.round(confidence * 100) / 100,
        phrases: top.phrases.slice(0, 3),
        multiIntent,
        informational: top.sig.informational ?? false,
        intents: [top.sig.intent, ...others.map((o) => o.sig.intent)],
      },
      'rules',
      80,
    );
  }

  async extract(t: ThreadInput, template: TemplateSpec): Promise<Staged<ExtractResult>> {
    const raw = t.subject + '\n' + t.messages.map((m) => m.body).join('\n');
    const fields: ExtractResult['fields'] = [];
    const amount = /₹\s?([\d,]+(?:\.\d{2})?)/.exec(raw);
    const amountInr = amount ? Number(amount[1]!.replace(/,/g, '')) : null;
    const acct = /(?:account|a\/c)[^\d]{0,30}(\d{4})\b/i.exec(raw) ?? /ending\s+(\d{4})/i.exec(raw);
    const cheque = /cheque\s*(?:no\.?|number)?\s*(\d{6})/i.exec(raw);
    const date = /(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*)/i.exec(raw);
    for (const label of template.fields) {
      let value: string | null = null;
      let source = 'email body';
      let inferred = false;
      if (/account|loan account|card number/i.test(label)) {
        value = acct ? `••••${acct[1]}` : null;
        source = 'customer record';
      } else if (/cheque number/i.test(label)) value = cheque?.[1] ?? null;
      else if (/amount/i.test(label))
        value = amountInr !== null ? `₹${amountInr.toLocaleString('en-IN')}.00` : null;
      else if (/date/i.test(label)) value = date?.[1] ?? null;
      else if (/reason code/i.test(label)) {
        value = /terminat|cancel/i.test(raw)
          ? 'CONTRACT_TERMINATED'
          : /lost|stolen/i.test(raw)
            ? 'INSTRUMENT_LOST'
            : null;
        source = 'inferred · 0.80';
        inferred = true;
      } else if (/requested by/i.test(label)) {
        value = t.messages[0]?.from ?? null;
        source = 'sender';
      } else if (/delivery/i.test(label)) {
        value = 'Registered email';
        source = 'policy default';
      } else if (/charge/i.test(label)) {
        value = 'Waived · first request';
        source = 'fee schedule';
      } else if (/format/i.test(label)) {
        value = /password|unlocked|unsecured/i.test(raw) ? 'PDF, unsecured' : 'PDF';
      } else if (/period from/i.test(label)) {
        const m =
          /(january|february|march|april|may|june|july|august|september|october|november|december)\s+to/i.exec(
            raw,
          );
        value = m ? `01-${m[1]!.slice(0, 3)}-2026` : null;
      } else if (/period to/i.test(label)) {
        const m =
          /to\s+(january|february|march|april|may|june|july|august|september|october|november|december)/i.exec(
            raw,
          );
        value = m ? `end of ${m[1]}` : null;
      } else if (/leaves/i.test(label)) value = /(\d+)\s+leaves/i.exec(raw)?.[1] ?? null;
      else if (/financial year/i.test(label)) value = /fy\s*(\d{4}-\d{2})/i.exec(raw)?.[1] ?? null;
      if (value !== null) fields.push({ label, value, source, inferred });
    }
    return staged({ fields, complete: fields.length === template.fields.length, amountInr }, 'rules', 70);
  }

  async draft(
    t: ThreadInput,
    docs: GroundingDoc[],
    _agent: AgentConfig,
    customerName: string,
  ): Promise<Staged<DraftResult>> {
    const body = text(t);
    const words = (s: string) => new Set(s.toLowerCase().match(/[a-z]{4,}/g) ?? []);
    const q = words(body);
    const scored = docs
      .map((d) => {
        const w = words(d.title + ' ' + d.section + ' ' + d.body);
        let overlap = 0;
        for (const x of q) if (w.has(x)) overlap++;
        return { d, overlap };
      })
      .filter((x) => x.overlap >= 3)
      .sort((a, b) => b.overlap - a.overlap)
      .slice(0, 2);
    const salutation = `Dear ${customerName},`;
    if (!scored.length) {
      return staged(
        {
          body: `${salutation}\n\nThank you for writing in. I do not yet have approved guidance I can quote on this, so I have asked the owning team and will come back to you with a definitive answer.\n\nWarm regards,`,
          citations: [],
          flagged: ['I do not yet have approved guidance I can quote on this'],
          coverage: 'none',
          gapQuestion: t.subject,
        },
        'rules',
        120,
      );
    }
    const paragraphs = scored.map((x) => {
      const firstSentences = x.d.body
        .split(/(?<=\.)\s+/)
        .slice(0, 2)
        .join(' ');
      return `${firstSentences} [${x.d.n}]`;
    });
    const questions = (body.match(/\?/g) ?? []).length;
    const partial = questions > scored.length;
    const gapLine =
      'On the remaining part of your question, I do not yet have an approved position I can commit to in writing; I have raised it with the owning team and will revert.';
    return staged(
      {
        body: [
          salutation,
          'Thank you for writing in.',
          ...paragraphs,
          ...(partial ? [gapLine] : []),
          'Warm regards,',
        ].join('\n\n'),
        citations: scored.map((x) => x.d.n),
        flagged: partial ? [gapLine] : [],
        coverage: partial ? 'partial' : 'full',
        gapQuestion: partial ? t.subject : null,
      },
      'rules',
      140,
    );
  }

  async brief(t: ThreadInput, facts: string): Promise<Staged<BriefResult>> {
    const first = t.messages[0]?.body ?? '';
    const summary = first.length > 280 ? first.slice(0, 277) + '…' : first;
    const context = facts
      .split('\n')
      .map((l) => l.split(': '))
      .filter((p) => p.length === 2)
      .map(([label, value]) => ({ label: label!, value: value! }));
    return staged(
      {
        summary: `${t.customer.priorContacts ? `Contact ${t.customer.priorContacts + 1} from this customer. ` : ''}${summary}`,
        context,
        suggestions: [
          { label: 'Call the customer and agree a dated next step', meta: 'recommended' },
          { label: 'Reply with an acknowledgement in your own words', meta: 'you write it' },
        ],
      },
      'rules',
      90,
    );
  }

  async nlFilter(query: string, departments: { id: string; name: string }[]) {
    const parsed = parseNaturalFilters(query, departments);
    return { filters: parsed.filters, understood: parsed.chips.length > 0 };
  }

  async answer(): Promise<null> {
    return null;
  }
}
