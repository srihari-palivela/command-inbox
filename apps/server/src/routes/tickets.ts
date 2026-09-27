import {
  ActionFieldsBody,
  ApproveBody,
  AssignBody,
  BatchApproveBody,
  CommentBody,
  DraftBody,
  InboxQuery,
  MergeBody,
  NlFilterBody,
  OverrideLaneBody,
  RejectBody,
  ReplyBody,
  SaveCallBody,
  SubtaskBody,
  TicketFilters,
  TransitionBody,
  WatchBody,
} from '@ci/contracts';
import type { FastifyPluginAsyncZod } from 'fastify-type-provider-zod';
import { z } from 'zod';
import { withTenant } from '../db/client.js';
import { ctxOf, tenant, tenantIdempotent } from '../platform/http.js';
import { gateDecisions } from '../platform/metrics.js';
import { endCall, getCall, saveCall, startCall } from '../modules/calls/service.js';
import {
  approve,
  editActionFields,
  editDraft,
  recallReply,
  reject,
  reply as sendReply,
  undo,
} from '../modules/gateway/service.js';
import { nlFilter } from '../modules/search/service.js';
import * as cmd from '../modules/tickets/commands.js';
import { getTicketDetail } from '../modules/tickets/detail.js';
import { findTicket, inbox, listTickets } from '../modules/tickets/queries.js';
import { notFound } from '../platform/errors.js';

const Id = z.object({ id: z.string().min(3).max(64) });

export const ticketRoutes: FastifyPluginAsyncZod = async (app) => {
  app.get('/v1/inbox', { schema: { querystring: InboxQuery } }, (req) =>
    tenant(req, (tx, ctx) => inbox(tx, ctx, req.query.filter)),
  );

  app.get('/v1/tickets', { schema: { querystring: TicketFilters } }, (req) =>
    tenant(req, (tx, ctx) => listTickets(tx, ctx, req.query)),
  );

  app.post('/v1/tickets/nl-filter', { schema: { body: NlFilterBody } }, (req) =>
    tenant(req, (tx, ctx) => nlFilter(tx, ctx, req.body.query)),
  );

  app.get('/v1/tickets/:id', { schema: { params: Id } }, (req) =>
    tenant(req, (tx, ctx) => getTicketDetail(tx, ctx, req.params.id)),
  );

  /** Resolve a human ticket number or id to the canonical id (used by deep links). */
  const resolve = async (orgId: string, id: string) => {
    const t = await withTenant(orgId, (tx) => findTicket(tx, orgId, id));
    if (!t) throw notFound('Ticket');
    return t.id;
  };

  app.post('/v1/tickets/:id/transition', { schema: { params: Id, body: TransitionBody } }, async (req) => {
    const ifMatch = req.headers['if-match'];
    const version = typeof ifMatch === 'string' && /^\d+$/.test(ifMatch) ? Number(ifMatch) : undefined;
    await tenant(req, (tx, ctx) => cmd.transition(tx, ctx, req.params.id, req.body.to, version));
    return { ok: true };
  });
  app.post('/v1/tickets/:id/assign', { schema: { params: Id, body: AssignBody } }, (req) =>
    tenant(req, (tx, ctx) => cmd.assign(tx, ctx, req.params.id, req.body.userId)),
  );
  app.post(
    '/v1/tickets/:id/override-lane',
    { schema: { params: Id, body: OverrideLaneBody } },
    async (req) => {
      await tenant(req, (tx, ctx) => cmd.overrideLane(tx, ctx, req.params.id, req.body.lane));
      return { ok: true };
    },
  );
  app.post('/v1/tickets/:id/comments', { schema: { params: Id, body: CommentBody } }, async (req) => {
    await tenant(req, (tx, ctx) => cmd.comment(tx, ctx, req.params.id, req.body.kind, req.body.body));
    return { ok: true };
  });
  app.patch(
    '/v1/tickets/:id/subtasks/:key',
    { schema: { params: Id.extend({ key: z.string().max(20) }), body: SubtaskBody } },
    async (req) => {
      await tenant(req, (tx, ctx) =>
        cmd.toggleSubtask(tx, ctx, req.params.id, req.params.key, req.body.done),
      );
      return { ok: true };
    },
  );
  app.put('/v1/tickets/:id/watch', { schema: { params: Id, body: WatchBody } }, async (req) => {
    await tenant(req, (tx, ctx) => cmd.watch(tx, ctx, req.params.id, req.body.watching));
    return { ok: true };
  });
  app.post('/v1/tickets/:id/escalate', { schema: { params: Id } }, (req) =>
    tenant(req, (tx, ctx) => cmd.escalate(tx, ctx, req.params.id)),
  );
  app.post('/v1/tickets/:id/split', { schema: { params: Id } }, (req) =>
    tenant(req, (tx, ctx) => cmd.split(tx, ctx, req.params.id)),
  );
  app.post('/v1/tickets/:id/merge', { schema: { params: Id, body: MergeBody } }, async (req) => {
    await tenant(req, (tx, ctx) => cmd.merge(tx, ctx, req.params.id, req.body.intoNumber));
    return { ok: true };
  });
  app.post(
    '/v1/tickets/:id/suggestions/:index/start',
    { schema: { params: Id.extend({ index: z.coerce.number().int().min(0).max(10) }) } },
    async (req) => {
      await tenant(req, (tx, ctx) => cmd.startSuggestion(tx, ctx, req.params.id, req.params.index));
      return { ok: true };
    },
  );

  // ── Approval gateway ─────────────────────────────────────────────────
  app.post(
    '/v1/tickets/:id/gate/approve',
    { schema: { params: Id, body: ApproveBody } },
    async (req, reply) => {
      const id = await resolve(ctxOf(req).orgId, req.params.id);
      const r = await tenantIdempotent(req, reply, (tx, ctx) =>
        approve(tx, ctx, id, req.body.openedEvidence),
      );
      gateDecisions.inc({
        mode: r.outcome,
        outcome: 'approved',
        opened_evidence: String(req.body.openedEvidence),
      });
      return r;
    },
  );
  app.post('/v1/tickets/:id/gate/reject', { schema: { params: Id, body: RejectBody } }, async (req) => {
    const id = await resolve(ctxOf(req).orgId, req.params.id);
    await tenant(req, (tx, ctx) => reject(tx, ctx, id, req.body.reason));
    gateDecisions.inc({ mode: 'reject', outcome: req.body.reason, opened_evidence: 'true' });
    return { ok: true };
  });
  app.post('/v1/tickets/:id/gate/undo', { schema: { params: Id } }, async (req) => {
    const id = await resolve(ctxOf(req).orgId, req.params.id);
    await tenant(req, (tx, ctx) => undo(tx, ctx, id));
    return { ok: true };
  });
  app.post('/v1/gate/batch-approve', { schema: { body: BatchApproveBody } }, async (req) => {
    // Each approval is its own transaction with its own audit entry. Approving from the list never opens the
    // evidence, so these count toward the approve-without-open canary (review W1) — that is the point of it.
    const ctx = ctxOf(req);
    const out: { ticketId: string; ok: boolean; outcome?: string; error?: string }[] = [];
    for (const ticketId of req.body.ticketIds) {
      try {
        const r = await withTenant(ctx.orgId, (tx) => approve(tx, ctx, ticketId, false));
        out.push({ ticketId, ok: true, outcome: r.outcome });
      } catch (err) {
        out.push({ ticketId, ok: false, error: err instanceof Error ? err.message : 'failed' });
      }
    }
    return { results: out };
  });
  app.patch(
    '/v1/tickets/:id/action/fields',
    { schema: { params: Id, body: ActionFieldsBody } },
    async (req) => {
      const id = await resolve(ctxOf(req).orgId, req.params.id);
      await tenant(req, (tx, ctx) => editActionFields(tx, ctx, id, req.body.fields));
      return { ok: true };
    },
  );
  app.put('/v1/tickets/:id/draft', { schema: { params: Id, body: DraftBody } }, async (req) => {
    const id = await resolve(ctxOf(req).orgId, req.params.id);
    await tenant(req, (tx, ctx) => editDraft(tx, ctx, id, req.body.body));
    return { ok: true };
  });
  app.post('/v1/tickets/:id/replies', { schema: { params: Id, body: ReplyBody } }, async (req, reply) => {
    const id = await resolve(ctxOf(req).orgId, req.params.id);
    return tenantIdempotent(req, reply, (tx, ctx) => sendReply(tx, ctx, id, req.body.body));
  });
  app.post(
    '/v1/replies/:id/recall',
    { schema: { params: z.object({ id: z.string().uuid() }) } },
    async (req) => {
      await tenant(req, (tx, ctx) => recallReply(tx, ctx, req.params.id));
      return { ok: true };
    },
  );

  // ── Calls ─────────────────────────────────────────────────────────────
  app.post('/v1/tickets/:id/calls', { schema: { params: Id } }, (req) =>
    tenant(req, (tx, ctx) => startCall(tx, ctx, req.params.id)),
  );
  app.get('/v1/calls/:id', { schema: { params: z.object({ id: z.string().uuid() }) } }, (req) =>
    tenant(req, (tx, ctx) => getCall(tx, ctx, req.params.id)),
  );
  app.post('/v1/calls/:id/end', { schema: { params: z.object({ id: z.string().uuid() }) } }, (req) =>
    tenant(req, (tx, ctx) => endCall(tx, ctx, req.params.id)),
  );
  app.post(
    '/v1/calls/:id/save',
    { schema: { params: z.object({ id: z.string().uuid() }), body: SaveCallBody } },
    async (req) => {
      await tenant(req, (tx, ctx) => saveCall(tx, ctx, req.params.id, req.body.discard));
      return { ok: true };
    },
  );
};
