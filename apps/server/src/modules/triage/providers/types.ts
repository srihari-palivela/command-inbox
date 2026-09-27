import type { TicketFilters } from '@ci/contracts';

export interface ThreadInput {
  subject: string;
  /** PII-masked message bodies, oldest first. */
  messages: { from: string; body: string }[];
  customer: { segment: string; priorContacts: number; priorSameTopic: number };
}

export interface AgentConfig {
  name: string;
  model: string;
  prompt: string;
}

export interface TaxonomyEntry {
  name: string;
  department: string | null;
  lane: string;
  owned: boolean;
  templateCode: string | null;
}

export interface GuardResult {
  stop: string | null;
  regulatorNamed: boolean;
  vulnerable: boolean;
  legalOrFraud: boolean;
  repeatContact: boolean;
  sentiment: 'neutral' | 'upset' | 'escalating' | 'vulnerable';
}

export interface ClassifyResult {
  queryType: string | null;
  confidence: number;
  phrases: string[];
  multiIntent: boolean;
  informational: boolean;
  intents: string[];
}

export interface ExtractedField {
  label: string;
  value: string;
  source: string;
  inferred: boolean;
}

export interface ExtractResult {
  fields: ExtractedField[];
  complete: boolean;
  amountInr: number | null;
}

export interface GroundingDoc {
  n: number;
  title: string;
  section: string;
  body: string;
}

export interface DraftResult {
  body: string;
  citations: number[];
  flagged: string[];
  coverage: 'full' | 'partial' | 'none';
  gapQuestion: string | null;
}

export interface BriefResult {
  summary: string;
  context: { label: string; value: string }[];
  suggestions: { label: string; meta: string }[];
}

export interface Usage {
  model: string;
  tokens: number | null;
  costMinor: number | null;
  latencyMs: number;
}

export interface Staged<T> {
  result: T;
  usage: Usage;
}

export interface TemplateSpec {
  code: string;
  name: string;
  fields: string[];
}

export interface LlmProvider {
  readonly name: 'claude' | 'heuristic';
  guard(t: ThreadInput, agent: AgentConfig): Promise<Staged<GuardResult>>;
  classify(t: ThreadInput, taxonomy: TaxonomyEntry[], agent: AgentConfig): Promise<Staged<ClassifyResult>>;
  extract(t: ThreadInput, template: TemplateSpec, agent: AgentConfig): Promise<Staged<ExtractResult>>;
  draft(
    t: ThreadInput,
    docs: GroundingDoc[],
    agent: AgentConfig,
    customerName: string,
  ): Promise<Staged<DraftResult>>;
  brief(t: ThreadInput, facts: string, agent: AgentConfig): Promise<Staged<BriefResult>>;
  nlFilter(
    query: string,
    departments: { id: string; name: string }[],
  ): Promise<{ filters: TicketFilters; understood: boolean } | null>;
  answer(question: string, facts: string): Promise<{ headline: string; lines: string[] } | null>;
}

/** Field specs per action template (what the extractor must fill). */
export const TEMPLATE_FIELDS: Record<string, string[]> = {
  'ACT-STP-014': [
    'Account number',
    'Cheque number',
    'Amount',
    'Instrument date',
    'Reason code',
    'Requested by',
  ],
  'ACT-STM-002': ['Account number', 'Period from', 'Period to', 'Format', 'Delivery', 'Charge'],
  'ACT-CRT-004': ['Account number', 'Financial year', 'Delivery'],
  'ACT-LTR-011': ['Account number', 'Purpose', 'Delivery'],
  'ACT-CHQ-001': ['Account number', 'Leaves', 'Collection branch'],
  'ACT-KYC-021': ['Account number', 'New mobile number', 'OTP verification'],
  'ACT-PAY-025': ['Account number', 'Amount', 'Beneficiary', 'Transfer date'],
  'ACT-CRD-008': ['Card number', 'Hold type', 'Location'],
  'ACT-FEE-017': ['Card number', 'Fee', 'Reason'],
  'ACT-LON-007': ['Loan account', 'Quote date'],
  'ACT-SI-009': ['Account number', 'Payee', 'Frequency'],
};
