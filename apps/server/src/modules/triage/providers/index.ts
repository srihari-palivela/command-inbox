import { env } from '../../../config/env.js';
import { logger } from '../../../platform/logger.js';
import { ClaudeProvider } from './claude.js';
import { HeuristicProvider } from './heuristic.js';
import type { LlmProvider } from './types.js';

export const heuristic = new HeuristicProvider();

let primary: LlmProvider | null = null;
function configured(): LlmProvider {
  if (primary) return primary;
  const useClaude = env.LLM_PROVIDER === 'claude' || (env.LLM_PROVIDER === 'auto' && !!env.ANTHROPIC_API_KEY);
  primary = useClaude ? new ClaudeProvider() : heuristic;
  return primary;
}

/**
 * Circuit breaker: after 3 consecutive provider failures, route to the heuristic provider for 60 s.
 * "Agent live" in the chrome shows degraded while open.
 */
const breaker = { failures: 0, openUntil: 0 };

export function provider(): LlmProvider {
  const p = configured();
  if (p.name === 'claude' && Date.now() < breaker.openUntil) return heuristic;
  return p;
}

export function recordSuccess(): void {
  breaker.failures = 0;
}

export function recordFailure(err: unknown): void {
  breaker.failures++;
  logger.warn(
    { err: err instanceof Error ? err.message : String(err), failures: breaker.failures },
    'llm provider failure',
  );
  if (breaker.failures >= 3) {
    breaker.openUntil = Date.now() + 60_000;
    breaker.failures = 0;
    logger.error('llm circuit open for 60s — degrading to heuristic provider and human lanes');
  }
}

/** Run a model call; on failure record it and use the heuristic fallback instead. */
export async function withFallback<T>(
  fn: (p: LlmProvider) => Promise<T>,
): Promise<{ value: T; degraded: boolean }> {
  const p = provider();
  if (p.name === 'heuristic') return { value: await fn(p), degraded: configured().name === 'claude' };
  try {
    const value = await fn(p);
    recordSuccess();
    return { value, degraded: false };
  } catch (err) {
    recordFailure(err);
    return { value: await fn(heuristic), degraded: true };
  }
}

export function llmStatus(): { provider: 'claude' | 'heuristic'; degraded: boolean } {
  const p = configured();
  return { provider: p.name, degraded: p.name === 'claude' && Date.now() < breaker.openUntil };
}

/** Tests only. */
export function setProviderForTests(p: LlmProvider | null): void {
  primary = p;
  breaker.failures = 0;
  breaker.openUntil = 0;
}
