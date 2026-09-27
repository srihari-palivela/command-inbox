/**
 * Execution connectors. Each real connector (Finacle, cards, payments hub, SWIFT) implements this
 * interface per bank. The sandbox connector gives the same idempotency semantics: executing the same
 * key twice returns the first result and changes nothing.
 */
import { sha256 } from '../../platform/crypto.js';

export interface ExecutionRequest {
  idempotencyKey: string;
  system: string;
  endpoint: string;
  code: string;
  fields: { label: string; value: string }[];
}

export interface ExecutionResult {
  externalRef: string;
  alreadyApplied: boolean;
}

export interface ExecutionConnector {
  execute(req: ExecutionRequest): Promise<ExecutionResult>;
  /** Has an effect with this key already reached the core system? */
  lookup(idempotencyKey: string): Promise<ExecutionResult | null>;
}

export class SandboxConnector implements ExecutionConnector {
  private readonly applied = new Map<string, string>();

  async lookup(key: string): Promise<ExecutionResult | null> {
    const ref = this.applied.get(key);
    return ref ? { externalRef: ref, alreadyApplied: true } : null;
  }

  async execute(req: ExecutionRequest): Promise<ExecutionResult> {
    const existing = await this.lookup(req.idempotencyKey);
    if (existing) return existing;
    const ref = `${req.endpoint}-${sha256(req.idempotencyKey).slice(0, 8).toUpperCase()}`;
    this.applied.set(req.idempotencyKey, ref);
    return { externalRef: ref, alreadyApplied: false };
  }
}

export const connector: ExecutionConnector = new SandboxConnector();
