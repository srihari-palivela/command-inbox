import pg from 'pg';
import { env } from '../config/env.js';
import { EVENTS_CHANNEL } from './outbox.js';
import { JOBS_CHANNEL } from './jobs.js';
import { logger } from './logger.js';

type Listener = (event: { topic: string; [k: string]: unknown }) => void;

/**
 * Fan-out of committed domain events to connected browsers. Each API node holds one LISTEN connection;
 * NOTIFY is delivered only on commit, so clients never see an event for a rolled-back change.
 */
export class EventHub {
  private client: pg.Client | null = null;
  private subs = new Map<string, Set<Listener>>();
  private jobListeners = new Set<() => void>();
  private stopped = false;

  async start(): Promise<void> {
    this.stopped = false;
    await this.connect();
  }

  private async connect(): Promise<void> {
    try {
      const c = new pg.Client({ connectionString: env.DATABASE_URL });
      await c.connect();
      c.on('notification', (msg) => {
        if (msg.channel === JOBS_CHANNEL) {
          for (const l of this.jobListeners) l();
          return;
        }
        if (!msg.payload) return;
        try {
          const evt = JSON.parse(msg.payload) as { orgId: string; topic: string };
          for (const l of this.subs.get(evt.orgId) ?? []) l(evt);
        } catch {
          /* ignore malformed payloads */
        }
      });
      c.on('error', (err) => {
        logger.warn({ err: err.message }, 'event hub connection lost; reconnecting');
        this.client = null;
        if (!this.stopped) setTimeout(() => void this.connect(), 1000);
      });
      await c.query(`LISTEN ${EVENTS_CHANNEL}`);
      await c.query(`LISTEN ${JOBS_CHANNEL}`);
      this.client = c;
    } catch (err) {
      logger.warn({ err: (err as Error).message }, 'event hub failed to connect; retrying');
      if (!this.stopped) setTimeout(() => void this.connect(), 2000);
    }
  }

  subscribe(orgId: string, l: Listener): () => void {
    const set = this.subs.get(orgId) ?? new Set();
    set.add(l);
    this.subs.set(orgId, set);
    return () => set.delete(l);
  }

  onJob(l: () => void): void {
    this.jobListeners.add(l);
  }

  clients(): number {
    let n = 0;
    for (const s of this.subs.values()) n += s.size;
    return n;
  }

  async stop(): Promise<void> {
    this.stopped = true;
    await this.client?.end().catch(() => undefined);
    this.client = null;
  }
}

export const hub = new EventHub();
