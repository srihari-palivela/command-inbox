import { sql } from 'drizzle-orm';
import { db } from './db/client.js';
import { orgs } from './db/schema.js';
import { clock } from './platform/clock.js';
import { enqueue, Worker, type JobRow } from './platform/jobs.js';
import { logger } from './platform/logger.js';
import { jobsProcessed, triageDuration } from './platform/metrics.js';
import { runEscalateChecker, runExecuteAction, runSendDraft, runSendReply } from './modules/gateway/service.js';
import { runKnowledgeSync } from './modules/setup/policy-knowledge.js';
import { runTriage } from './modules/triage/pipeline.js';

const timed =
  (kind: string, fn: (job: JobRow) => Promise<void>) =>
  async (job: JobRow): Promise<void> => {
    const start = process.hrtime.bigint();
    try {
      await fn(job);
      jobsProcessed.inc({ kind, outcome: 'ok' });
      if (kind === 'triage') triageDuration.observe({ lane: 'all' }, Number(process.hrtime.bigint() - start) / 1e9);
    } catch (err) {
      jobsProcessed.inc({ kind, outcome: 'error' });
      throw err;
    }
  };

export function createWorker(): Worker {
  return new Worker()
    .register('triage', timed('triage', runTriage))
    .register('execute_action', timed('execute_action', runExecuteAction))
    .register('send_draft', timed('send_draft', runSendDraft))
    .register('send_reply', timed('send_reply', runSendReply))
    .register('escalate_checker', timed('escalate_checker', runEscalateChecker))
    .register('knowledge_sync', timed('knowledge_sync', runKnowledgeSync))
    .register('rerank', timed('rerank', async () => undefined))
    .register('sla_sweep', timed('sla_sweep', async () => undefined));
}

/**
 * Recurring work: the Priority Ranker re-ranks every five minutes. One job per org per window,
 * deduplicated, so any number of worker replicas enqueue it exactly once.
 */
export function startScheduler(): NodeJS.Timeout {
  const tick = async () => {
    try {
      const all = await db.select({ id: orgs.id }).from(orgs);
      const window = Math.floor(clock.now().getTime() / 300_000);
      for (const o of all) {
        await db.transaction(async (tx) => {
          await tx.execute(sql`select set_config('app.org_id', ${o.id}, true)`);
          await enqueue(tx, { orgId: o.id, kind: 'rerank', dedupeKey: `rerank:${window}`, maxAttempts: 1 });
        });
      }
    } catch (err) {
      logger.warn({ err: (err as Error).message }, 'scheduler tick failed');
    }
  };
  void tick();
  return setInterval(tick, 60_000);
}
