/**
 * Claude provider: every stage is one structured-output call (`beta.messages.parse` + Zod), so the
 * pipeline only ever receives schema-valid data. The agent's own prompt is the system prompt; the
 * stable prefix is cache-marked. Refusals and API errors throw, and the pipeline degrades to the
 * heuristic provider and a human lane.
 */
import Anthropic from '@anthropic-ai/sdk';
import { betaZodOutputFormat } from '@anthropic-ai/sdk/helpers/beta/zod';
import { TicketFilters } from '@ci/contracts';
import { z } from 'zod';
import { env } from '../../../config/env.js';
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

/** USD per million tokens (input, output). */
const PRICES: Record<string, [number, number]> = {
  'claude-opus-5': [5, 25],
  'claude-sonnet-5': [2, 10],
  'claude-haiku-4-5': [1, 5],
};
const INR_PER_USD = 84;

export function costMinor(model: string, input: number, output: number): number {
  const [pin, pout] = PRICES[model] ?? [5, 25];
  // paise
  return Math.round(((input * pin + output * pout) / 1e6) * INR_PER_USD * 100);
}

/** "rules + claude-haiku-4-5" → "claude-haiku-4-5". */
export function apiModel(model: string): string {
  const m = /claude-[a-z0-9-]+/.exec(model);
  return m ? m[0] : 'claude-sonnet-5';
}

export class RefusalError extends Error {}

const threadText = (t: ThreadInput) =>
  [`Subject: ${t.subject}`, ...t.messages.map((m, i) => `--- Message ${i + 1} from ${m.from}\n${m.body}`)].join('\n\n') +
  `\n\nCustomer segment: ${t.customer.segment}. Prior contacts: ${t.customer.priorContacts} (${t.customer.priorSameTopic} on the same topic).`;

const Guard = z.object({
  regulator_named: z.boolean(),
  vulnerable_customer: z.boolean(),
  legal_or_fraud: z.boolean(),
  repeat_contact: z.boolean(),
  sentiment: z.enum(['neutral', 'upset', 'escalating', 'vulnerable']),
  stop_reason: z.string().describe('Short reason if any hard stop fires, else empty string'),
});

const Classify = z.object({
  query_type: z.string().describe('Exactly one name from the taxonomy, or "ambiguous"'),
  confidence: z.number().min(0).max(1),
  phrases: z.array(z.string()).max(3).describe('Verbatim phrases that drove the choice'),
  multi_intent: z.boolean().describe('True if the email asks for two or more things owned by different teams'),
  informational: z.boolean().describe('True if the customer asks for information rather than an action'),
  intents: z.array(z.string()),
});

const Extract = z.object({
  fields: z.array(
    z.object({
      label: z.string(),
      value: z.string().describe('Verbatim from the thread, or empty string if not present'),
      source: z.string().describe('"email body", or "inferred" when not stated verbatim'),
      inferred: z.boolean(),
    }),
  ),
  amount_inr: z.number().nullable(),
});

const Draft = z.object({
  body: z.string().describe('The reply, citing sources as [n]. Plain text paragraphs separated by blank lines.'),
  citations: z.array(z.number().int()),
  flagged: z.array(z.string()).describe('Verbatim sentences from the body that state a gap or need checking'),
  coverage: z.enum(['full', 'partial', 'none']),
  gap_question: z.string().describe('The part of the question with no approved source, or empty string'),
});

const Brief = z.object({
  summary: z.string(),
  context: z.array(z.object({ label: z.string(), value: z.string() })).max(6),
  suggestions: z.array(z.object({ label: z.string(), meta: z.string() })).max(4),
});

const Answer = z.object({ headline: z.string(), lines: z.array(z.string()).max(4) });

export class ClaudeProvider implements LlmProvider {
  readonly name = 'claude' as const;
  private readonly client: Anthropic;

  constructor(client?: Anthropic) {
    this.client = client ?? new Anthropic({ apiKey: env.ANTHROPIC_API_KEY, maxRetries: 2, timeout: 60_000 });
  }

  private async call<T extends z.ZodType>(
    model: string,
    system: string,
    user: string,
    schema: T,
    opts: { effort?: 'low' | 'medium' | 'high'; maxTokens?: number } = {},
  ): Promise<Staged<z.infer<T>>> {
    const started = Date.now();
    const id = apiModel(model);
    const isOpus5 = id === 'claude-opus-5';
    const response = await this.client.beta.messages.parse({
      model: id,
      max_tokens: opts.maxTokens ?? 4000,
      system: [{ type: 'text', text: system, cache_control: { type: 'ephemeral' } }],
      messages: [{ role: 'user', content: user }],
      output_config: {
        format: betaZodOutputFormat(schema),
        // Haiku 4.5 does not accept effort; the other current models do.
        ...(id.startsWith('claude-haiku') ? {} : { effort: opts.effort ?? 'low' }),
      },
      // Server-side refusal fallback on Opus-tier calls (routed by refusal category).
      ...(isOpus5 ? { betas: ['server-side-fallback-2026-07-01'], fallbacks: 'default' as const } : {}),
    });
    if (response.stop_reason === 'refusal') throw new RefusalError(`${id} declined the request`);
    if (!response.parsed_output) throw new Error(`${id} returned no parseable output (stop: ${response.stop_reason})`);
    const input = response.usage.input_tokens + (response.usage.cache_read_input_tokens ?? 0);
    const output = response.usage.output_tokens;
    return {
      result: response.parsed_output as z.infer<T>,
      usage: { model: id, tokens: input + output, costMinor: costMinor(id, input, output), latencyMs: Date.now() - started },
    };
  }

  async guard(t: ThreadInput, agent: AgentConfig): Promise<Staged<GuardResult>> {
    const r = await this.call(agent.model, agent.prompt, `Screen this thread for hard stops.\n\n${threadText(t)}`, Guard);
    const stop = [
      r.result.regulator_named && 'regulator named',
      r.result.legal_or_fraud && 'legal notice or suspected fraud',
      r.result.vulnerable_customer && 'vulnerable-customer signal',
      r.result.repeat_contact && 'third contact',
    ].filter(Boolean) as string[];
    return {
      usage: r.usage,
      result: {
        stop: stop.length ? stop.join(' + ') : null,
        regulatorNamed: r.result.regulator_named,
        vulnerable: r.result.vulnerable_customer,
        legalOrFraud: r.result.legal_or_fraud,
        repeatContact: r.result.repeat_contact,
        sentiment: r.result.sentiment,
      },
    };
  }

  async classify(t: ThreadInput, taxonomy: TaxonomyEntry[], agent: AgentConfig): Promise<Staged<ClassifyResult>> {
    const system = `${agent.prompt}\n\nApproved taxonomy (query type — owning team):\n${taxonomy
      .map((q) => `- ${q.name} — ${q.department ?? 'no owner'}`)
      .join('\n')}`;
    const r = await this.call(agent.model, system, threadText(t), Classify);
    const known = taxonomy.some((q) => q.name === r.result.query_type);
    return {
      usage: r.usage,
      result: {
        queryType: known ? r.result.query_type : null,
        confidence: known ? r.result.confidence : Math.min(r.result.confidence, 0.4),
        phrases: r.result.phrases,
        multiIntent: r.result.multi_intent,
        informational: r.result.informational,
        intents: r.result.intents,
      },
    };
  }

  async extract(t: ThreadInput, template: TemplateSpec, agent: AgentConfig): Promise<Staged<ExtractResult>> {
    const user = `Action template ${template.code} — ${template.name}.\nFill exactly these fields: ${template.fields.join(', ')}.\n\n${threadText(t)}`;
    const r = await this.call(agent.model, agent.prompt, user, Extract);
    const fields = r.result.fields.filter((f) => f.value.trim() && template.fields.includes(f.label));
    return {
      usage: r.usage,
      result: {
        fields: fields.map((f) => ({ ...f, source: f.inferred ? 'inferred' : f.source })),
        complete: template.fields.every((l) => fields.some((f) => f.label === l)),
        amountInr: r.result.amount_inr,
      },
    };
  }

  async draft(t: ThreadInput, docs: GroundingDoc[], agent: AgentConfig, customerName: string): Promise<Staged<DraftResult>> {
    const sources = docs.map((d) => `[${d.n}] ${d.title} ${d.section}\n${d.body}`).join('\n\n');
    const user = `Approved sources (the only material you may state as fact):\n\n${sources || '(none)'}\n\nWrite the reply to ${customerName}.\n\n${threadText(t)}`;
    const r = await this.call(agent.model, agent.prompt, user, Draft, { effort: 'medium', maxTokens: 6000 });
    const valid = new Set(docs.map((d) => d.n));
    return {
      usage: r.usage,
      result: {
        body: r.result.body,
        // Never trust a citation to a source we did not supply.
        citations: r.result.citations.filter((n) => valid.has(n)),
        flagged: r.result.flagged.filter((f) => r.result.body.includes(f)),
        coverage: docs.length ? r.result.coverage : 'none',
        gapQuestion: r.result.gap_question || null,
      },
    };
  }

  async brief(t: ThreadInput, facts: string, agent: AgentConfig): Promise<Staged<BriefResult>> {
    const r = await this.call(agent.model, agent.prompt, `Records:\n${facts}\n\n${threadText(t)}`, Brief, { effort: 'medium' });
    return { usage: r.usage, result: r.result };
  }

  async nlFilter(query: string, departments: { id: string; name: string }[]) {
    const system = `Translate a support lead's request into ticket filters. Only use these keys and values:
status: triage | approval | executing | human | customer | resolved
lane: auto | draft | manual
team: one of these department ids: ${departments.map((d) => `${d.id} (${d.name})`).join(', ')}
owner: mine | ai | unassigned
due: risk | open | closed
conf: low | high
pri: P1 | P2 | P3 | P4
q: free text search, only if nothing else fits.
Omit keys the request does not mention.`;
    const r = await this.call(env.COPILOT_MODEL, system, query, TicketFilters, { effort: 'low', maxTokens: 1000 });
    const filters = TicketFilters.parse(r.result);
    return { filters, understood: Object.keys(filters).length > 0 };
  }

  async answer(question: string, facts: string) {
    const system =
      'You answer questions from a bank support team about their live queue. Use only the facts given; never invent numbers. Lead with a one-sentence headline, then up to three short supporting lines.';
    const r = await this.call(env.COPILOT_MODEL, system, `Facts:\n${facts}\n\nQuestion: ${question}`, Answer, {
      effort: 'medium',
      maxTokens: 2000,
    });
    return r.result;
  }
}
