import { and, eq } from 'drizzle-orm';
import type { FastifyReply, FastifyRequest } from 'fastify';
import { withTenant, type Tx } from '../db/client.js';
import { idempotencyKeys } from '../db/schema.js';
import type { Ctx } from './context.js';
import { canonicalJson, sha256 } from './crypto.js';
import { conflict, unauthorized, unprocessable } from './errors.js';

declare module 'fastify' {
  interface FastifyRequest {
    ctx?: Ctx;
    csrfToken?: string;
    startedAt?: bigint;
  }
  interface FastifyContextConfig {
    /** No session required. */
    public?: boolean;
    /** Skip the CSRF header check (e.g. signed webhooks, login). */
    csrfExempt?: boolean;
  }
}

export function ctxOf(req: FastifyRequest): Ctx {
  if (!req.ctx) throw unauthorized();
  return req.ctx;
}

/** Run a handler inside a tenant transaction for the signed-in user. */
export function tenant<T>(req: FastifyRequest, fn: (tx: Tx, ctx: Ctx) => Promise<T>): Promise<T> {
  const ctx = ctxOf(req);
  return withTenant(ctx.orgId, (tx) => fn(tx, ctx));
}

/**
 * Same as `tenant`, but honours an `Idempotency-Key` header: a retried request with the same key and
 * body returns the stored response instead of acting twice. The key row is written in the same
 * transaction as the effect, so a crash cannot leave one without the other.
 */
export async function tenantIdempotent<T>(
  req: FastifyRequest,
  reply: FastifyReply,
  fn: (tx: Tx, ctx: Ctx) => Promise<T>,
): Promise<T> {
  const key = req.headers['idempotency-key'];
  if (typeof key !== 'string' || !key) return tenant(req, fn);
  if (key.length > 200) throw unprocessable('bad_idempotency_key', 'Idempotency-Key is too long.');
  const ctx = ctxOf(req);
  const route = `${req.method} ${req.routeOptions.url}`;
  const requestHash = sha256(canonicalJson({ route, params: req.params, body: req.body ?? null }));
  return withTenant(ctx.orgId, async (tx) => {
    const inserted = await tx
      .insert(idempotencyKeys)
      .values({
        orgId: ctx.orgId,
        userId: ctx.user.id,
        key,
        route,
        requestHash,
        statusCode: 0,
        response: null,
      })
      .onConflictDoNothing()
      .returning({ key: idempotencyKeys.key });
    if (!inserted.length) {
      const [prev] = await tx
        .select()
        .from(idempotencyKeys)
        .where(
          and(
            eq(idempotencyKeys.orgId, ctx.orgId),
            eq(idempotencyKeys.userId, ctx.user.id),
            eq(idempotencyKeys.key, key),
          ),
        );
      if (prev && prev.requestHash !== requestHash)
        throw conflict('idempotency_mismatch', 'This Idempotency-Key was used for a different request.');
      if (prev && prev.statusCode) {
        reply.header('idempotent-replay', 'true');
        return prev.response as T;
      }
      throw conflict('in_progress', 'The same request is already being processed.');
    }
    const result = await fn(tx, ctx);
    await tx
      .update(idempotencyKeys)
      .set({ statusCode: 200, response: (result ?? null) as unknown })
      .where(
        and(
          eq(idempotencyKeys.orgId, ctx.orgId),
          eq(idempotencyKeys.userId, ctx.user.id),
          eq(idempotencyKeys.key, key),
        ),
      );
    return result;
  });
}
