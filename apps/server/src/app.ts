import cookie from '@fastify/cookie';
import cors from '@fastify/cors';
import helmet from '@fastify/helmet';
import rateLimit from '@fastify/rate-limit';
import swagger from '@fastify/swagger';
import Fastify, { type FastifyError } from 'fastify';
import { hasZodFastifySchemaValidationErrors, jsonSchemaTransform, serializerCompiler, validatorCompiler, type ZodTypeProvider } from 'fastify-type-provider-zod';
import { randomUUID } from 'node:crypto';
import { sql } from 'drizzle-orm';
import { env } from './config/env.js';
import { db } from './db/client.js';
import { SESSION_COOKIE, resolveSession } from './modules/auth/service.js';
import { AppError } from './platform/errors.js';
import { safeEqual } from './platform/crypto.js';
import { logger } from './platform/logger.js';
import { httpDuration, registry, sseClients } from './platform/metrics.js';
import { hub } from './platform/sse.js';
import { authRoutes } from './routes/auth.js';
import { ticketRoutes } from './routes/tickets.js';
import { workspaceRoutes } from './routes/workspace.js';

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

export async function buildApp() {
  const app = Fastify({
    loggerInstance: logger,
    genReqId: (req) => (typeof req.headers['x-request-id'] === 'string' ? req.headers['x-request-id'].slice(0, 64) : randomUUID()),
    trustProxy: true,
    bodyLimit: 1_048_576,
  }).withTypeProvider<ZodTypeProvider>();

  app.setValidatorCompiler(validatorCompiler);
  app.setSerializerCompiler(serializerCompiler);

  await app.register(helmet, { contentSecurityPolicy: false });
  await app.register(cors, { origin: env.WEB_ORIGIN, credentials: true });
  await app.register(cookie);
  await app.register(rateLimit, {
    max: 600,
    timeWindow: '1 minute',
    keyGenerator: (req) => (req.cookies?.[SESSION_COOKIE] ? `s:${req.cookies[SESSION_COOKIE]!.slice(0, 16)}` : `ip:${req.ip}`),
  });
  await app.register(swagger, {
    openapi: { info: { title: 'Command Inbox API', version: '1.0.0', description: 'AI triage & resolution platform for bank customer queries.' } },
    transform: jsonSchemaTransform,
  });

  // Identity + CSRF on every request.
  app.addHook('onRequest', async (req) => {
    req.startedAt = process.hrtime.bigint();
    req.headers['x-request-id'] ??= req.id;
    const token = req.cookies?.[SESSION_COOKIE];
    if (token) {
      const resolved = await resolveSession(token, req.id);
      if (resolved) {
        req.ctx = resolved.ctx;
        req.csrfToken = resolved.csrfToken;
      }
    }
    const cfg = req.routeOptions.config ?? {};
    if (!cfg.public && req.url.startsWith('/v1/') && !req.ctx) throw new AppError(401, 'unauthenticated', 'Sign in to continue');
    if (UNSAFE.has(req.method) && req.ctx && !cfg.csrfExempt) {
      const header = req.headers['x-csrf-token'];
      if (typeof header !== 'string' || !req.csrfToken || !safeEqual(header, req.csrfToken)) {
        throw new AppError(403, 'csrf', 'Missing or invalid CSRF token');
      }
    }
  });

  app.addHook('onResponse', async (req, reply) => {
    if (!req.startedAt) return;
    const seconds = Number(process.hrtime.bigint() - req.startedAt) / 1e9;
    httpDuration.observe({ method: req.method, route: req.routeOptions.url ?? 'unknown', status: String(reply.statusCode) }, seconds);
  });

  // RFC 9457 problem details for every error.
  app.setErrorHandler((err: FastifyError, req, reply) => {
    if (hasZodFastifySchemaValidationErrors(err)) {
      return reply.status(400).type('application/problem+json').send({
        type: 'about:blank',
        title: 'Invalid request',
        status: 400,
        code: 'validation',
        detail: err.validation.map((v) => `${v.instancePath || '(body)'} ${v.message}`).join('; '),
        requestId: req.id,
      });
    }
    if (err instanceof AppError) {
      return reply.status(err.status).type('application/problem+json').send({
        type: 'about:blank',
        title: err.message,
        status: err.status,
        code: err.code,
        detail: err.detail,
        requestId: req.id,
      });
    }
    if (err.statusCode && err.statusCode < 500) {
      return reply.status(err.statusCode).type('application/problem+json').send({ type: 'about:blank', title: err.message, status: err.statusCode, code: err.code ?? 'error', requestId: req.id });
    }
    req.log.error({ err }, 'unhandled error');
    return reply.status(500).type('application/problem+json').send({ type: 'about:blank', title: 'Something went wrong', status: 500, code: 'internal', requestId: req.id });
  });

  app.get('/healthz', { config: { public: true } }, async () => ({ ok: true }));
  app.get('/readyz', { config: { public: true } }, async (_req, reply) => {
    try {
      await db.execute(sql`select 1`);
      return { ok: true };
    } catch {
      return reply.status(503).send({ ok: false });
    }
  });
  app.get('/metrics', { config: { public: true } }, async (req, reply) => {
    // Metrics are for the internal scrape network only; the ingress does not route /metrics.
    sseClients.set(hub.clients());
    reply.type(registry.contentType);
    return registry.metrics();
  });
  app.get('/v1/openapi.json', { config: { public: true } }, async () => app.swagger());

  // Server-sent events: committed domain events for the caller's workspace.
  app.get('/v1/stream', async (req, reply) => {
    const ctx = req.ctx!;
    reply.hijack();
    const res = reply.raw;
    res.writeHead(200, {
      'content-type': 'text/event-stream',
      'cache-control': 'no-cache, no-transform',
      connection: 'keep-alive',
      'x-accel-buffering': 'no',
      'access-control-allow-origin': env.WEB_ORIGIN,
      'access-control-allow-credentials': 'true',
    });
    res.write(`event: ready\ndata: {"orgId":"${ctx.orgId}"}\n\n`);
    const unsubscribe = hub.subscribe(ctx.orgId, (evt) => {
      res.write(`event: ${evt.topic}\ndata: ${JSON.stringify(evt)}\n\n`);
    });
    const keepAlive = setInterval(() => res.write(': keep-alive\n\n'), 25_000);
    req.raw.on('close', () => {
      clearInterval(keepAlive);
      unsubscribe();
    });
  });

  await app.register(authRoutes);
  await app.register(ticketRoutes);
  await app.register(workspaceRoutes);
  return app;
}
