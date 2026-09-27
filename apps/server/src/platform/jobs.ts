import { and, eq, sql } from 'drizzle-orm';
import { hostname } from 'node:os';
import type { Executor } from '../db/client.js';
import { db } from '../db/client.js';
import { jobs } from '../db/schema.js';
import { clock } from './clock.js';
import { logger } from './logger.js';

export const JOBS_CHANNEL = 'ci_jobs';

export type JobKind =
  | 'triage'
  | 'execute_action'
  | 'send_draft'
  | 'send_reply'
  | 'escalate_checker'
  | 'rerank'
  | 'sla_sweep'
  | 'knowledge_sync'
  | 'call_progress';

export interface JobRow {
  id: number;
  orgId: string;
  kind: JobKind;
  payload: Record<string, unknown>;
  attempts: number;
  maxAttempts: number;
}

export interface EnqueueInput {
  orgId: string;
  kind: JobKind;
  payload?: Record<string, unknown>;
  runAt?: Date;
  dedupeKey?: string;
  maxAttempts?: number;
}

/** Enqueue inside the caller's transaction, so the job exists iff the change commits. */
export async function enqueue(tx: Executor, input: EnqueueInput): Promise<void> {
  await tx
    .insert(jobs)
    .values({
      orgId: input.orgId,
      kind: input.kind,
      payload: input.payload ?? {},
      runAt: input.runAt ?? clock.now(),
      dedupeKey: input.dedupeKey ?? null,
      maxAttempts: input.maxAttempts ?? 5,
    })
    .onConflictDoNothing();
  await tx.execute(sql`select pg_notify(${JOBS_CHANNEL}, ${input.kind})`);
}

/** Cancel a queued job (e.g. an undo inside the window). Returns true if a job was cancelled. */
export async function cancelJob(tx: Executor, orgId: string, dedupeKey: string): Promise<boolean> {
  const rows = await tx
    .update(jobs)
    .set({ state: 'cancelled' })
    .where(and(eq(jobs.orgId, orgId), eq(jobs.dedupeKey, dedupeKey), eq(jobs.state, 'queued')))
    .returning({ id: jobs.id });
  return rows.length > 0;
}

export type JobHandler = (job: JobRow) => Promise<void>;

/**
 * Postgres-backed job runner. Jobs are claimed with FOR UPDATE SKIP LOCKED, so any number of workers
 * can run side by side; handlers must be idempotent (a crash after the effect but before `done`
 * re-runs the job).
 */
export class Worker {
  private handlers = new Map<JobKind, JobHandler>();
  private running = false;
  private timer: NodeJS.Timeout | null = null;
  private readonly id = `${hostname()}:${process.pid}:${Math.random().toString(36).slice(2, 7)}`;

  register(kind: JobKind, handler: JobHandler): this {
    this.handlers.set(kind, handler);
    return this;
  }

  /** Claim and run one due job. Returns false when nothing was due. */
  async tick(): Promise<boolean> {
    const claimed = await db.execute<{
      id: string;
      org_id: string;
      kind: JobKind;
      payload: Record<string, unknown>;
      attempts: number;
      max_attempts: number;
    }>(sql`
      update jobs set state = 'running', locked_by = ${this.id}, locked_at = now(), attempts = attempts + 1
       where id = (
         select id from jobs
          where state = 'queued' and run_at <= ${clock.now()}
          order by run_at, id
          for update skip locked
          limit 1)
      returning id, org_id, kind, payload, attempts, max_attempts`);
    const row = claimed.rows[0];
    if (!row) return false;
    const job: JobRow = {
      id: Number(row.id),
      orgId: row.org_id,
      kind: row.kind,
      payload: row.payload,
      attempts: row.attempts,
      maxAttempts: row.max_attempts,
    };
    const handler = this.handlers.get(job.kind);
    try {
      if (!handler) throw new Error(`no handler for job kind ${job.kind}`);
      await handler(job);
      await db.update(jobs).set({ state: 'done', lastError: null }).where(eq(jobs.id, job.id));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const final = job.attempts >= job.maxAttempts;
      const backoffMs = Math.min(300_000, 1000 * 2 ** job.attempts) * (0.75 + Math.random() * 0.5);
      logger.warn(
        { jobId: job.id, kind: job.kind, attempt: job.attempts, final, err: message },
        'job failed',
      );
      await db
        .update(jobs)
        .set({
          state: final ? 'failed' : 'queued',
          lastError: message.slice(0, 2000),
          runAt: new Date(clock.now().getTime() + backoffMs),
          lockedBy: null,
        })
        .where(eq(jobs.id, job.id));
    }
    return true;
  }

  /** Drain everything currently due (tests and the embedded dev worker). */
  async drain(max = 500): Promise<number> {
    let n = 0;
    while (n < max && (await this.tick())) n++;
    return n;
  }

  private pollMs = 500;

  private schedule(delay: number): void {
    if (!this.running) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.loop(), delay);
  }

  private inFlight = false;
  private wakeRequested = false;

  private async loop(): Promise<void> {
    if (!this.running || this.inFlight) return;
    this.inFlight = true;
    this.wakeRequested = false;
    let didWork = false;
    try {
      didWork = await this.tick();
    } catch (err) {
      logger.error({ err }, 'worker tick crashed');
    } finally {
      this.inFlight = false;
    }
    this.schedule(didWork || this.wakeRequested ? 0 : this.pollMs);
  }

  start(pollMs = 500): void {
    if (this.running) return;
    this.running = true;
    this.pollMs = pollMs;
    this.schedule(0);
    logger.info({ worker: this.id }, 'job worker started');
  }

  /** Wake immediately (called on NOTIFY ci_jobs) instead of waiting for the next poll. */
  poke(): void {
    if (this.inFlight) this.wakeRequested = true;
    else this.schedule(0);
  }

  stop(): void {
    this.running = false;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }
}
