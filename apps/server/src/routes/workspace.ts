import {
  ActionTemplateBody,
  AgentBody,
  AgentBoardsBody,
  AgentVersionBody,
  AskBody,
  BoardBody,
  ClearanceBody,
  CourseCompleteBody,
  DecideBody,
  DialBody,
  IntakeMessageBody,
  KnowledgeSourceBody,
  KpiBody,
  MarkReadBody,
  NotificationBody,
  OwnerBody,
  RuleToggleBody,
  SearchQuery,
} from '@ci/contracts';
import type { FastifyPluginAsyncZod } from 'fastify-type-provider-zod';
import { asc, eq } from 'drizzle-orm';
import { z } from 'zod';
import { env } from '../config/env.js';
import { mailboxes } from '../db/schema.js';
import { verifyAuditChain } from '../platform/audit.js';
import { hmac, safeEqual } from '../platform/crypto.js';
import { forbidden, unauthorized } from '../platform/errors.js';
import { ctxOf, tenant } from '../platform/http.js';
import { requireCap } from '../platform/rbac.js';
import { activity, actOnAlert, createKpi, deleteKpi, performance, results, shift } from '../modules/insights/service.js';
import { authorizeUrl, handleCallback, isConfigured, type OAuthProvider } from '../modules/intake/oauth.js';
import { ingest, SAMPLE_MAILS } from '../modules/intake/service.js';
import { completeCourse, learning, markRead, sendNotification } from '../modules/learning/service.js';
import { autoAssign, people, setClearance } from '../modules/people/service.js';
import { ask, search } from '../modules/search/service.js';
import { agentsOverview, createAgent, createBoard, listBoards, newAgentVersion, queueTuning, setAgentBoards } from '../modules/setup/agents-boards.js';
import {
  actionsOverview,
  actOnGap,
  adminOverview,
  connectSource,
  createActionTemplate,
  decideProposedRule,
  knowledge,
  policies,
  setDepartmentOwner,
  setDial,
  syncSource,
  taxonomy,
  togglePriorityRule,
} from '../modules/setup/policy-knowledge.js';

const Uuid = z.object({ id: z.string().uuid() });

export const workspaceRoutes: FastifyPluginAsyncZod = async (app) => {
  // ── Activity, shift, search, copilot ─────────────────────────────────
  app.get('/v1/activity', (req) => tenant(req, (tx, ctx) => activity(tx, ctx)));
  app.get('/v1/shift', (req) => tenant(req, (tx, ctx) => shift(tx, ctx)));
  app.get('/v1/search', { schema: { querystring: SearchQuery } }, (req) => tenant(req, (tx, ctx) => search(tx, ctx, req.query.q)));
  app.post('/v1/copilot/ask', { schema: { body: AskBody } }, (req) => tenant(req, (tx, ctx) => ask(tx, ctx, req.body.question)));

  // ── People ────────────────────────────────────────────────────────────
  app.get('/v1/people', (req) => tenant(req, (tx, ctx) => people(tx, ctx)));
  app.put('/v1/clearances', { schema: { body: ClearanceBody } }, async (req) => {
    await tenant(req, (tx, ctx) => setClearance(tx, ctx, req.body.userId, req.body.departmentId, req.body.level));
    return { ok: true };
  });
  app.post('/v1/assignments/auto', (req) => tenant(req, (tx, ctx) => autoAssign(tx, ctx)));

  // ── Insights ──────────────────────────────────────────────────────────
  app.get('/v1/insights/performance', (req) => tenant(req, (tx, ctx) => performance(tx, ctx)));
  app.get('/v1/insights/results', (req) => tenant(req, (tx, ctx) => results(tx, ctx)));
  app.post('/v1/kpis', { schema: { body: KpiBody } }, async (req) => {
    await tenant(req, (tx, ctx) => createKpi(tx, ctx, req.body));
    return { ok: true };
  });
  app.delete('/v1/kpis/:id', { schema: { params: Uuid } }, async (req) => {
    await tenant(req, (tx, ctx) => deleteKpi(tx, ctx, req.params.id));
    return { ok: true };
  });
  app.post('/v1/alerts/:id/:mode', { schema: { params: Uuid.extend({ mode: z.enum(['act', 'notify']) }) } }, (req) =>
    tenant(req, (tx, ctx) => actOnAlert(tx, ctx, req.params.id, req.params.mode)),
  );

  // ── Learning & notifications ─────────────────────────────────────────
  app.get('/v1/learning', (req) => tenant(req, (tx, ctx) => learning(tx, ctx)));
  app.post('/v1/notifications', { schema: { body: NotificationBody } }, (req) => tenant(req, (tx, ctx) => sendNotification(tx, ctx, req.body)));
  app.post('/v1/notifications/read', { schema: { body: MarkReadBody } }, async (req) => {
    await tenant(req, (tx, ctx) => markRead(tx, ctx, req.body.ids, !!req.body.all));
    return { ok: true };
  });
  app.post('/v1/courses/:id/complete', { schema: { params: Uuid, body: CourseCompleteBody } }, (req) =>
    tenant(req, (tx, ctx) => completeCourse(tx, ctx, req.params.id, req.body.answers)),
  );

  // ── Boards & mailbox OAuth ───────────────────────────────────────────
  app.get('/v1/boards', (req) => tenant(req, (tx, ctx) => listBoards(tx, ctx)));
  app.post('/v1/boards', { schema: { body: BoardBody } }, async (req) => {
    const { board, mailboxId } = await tenant(req, (tx, ctx) => createBoard(tx, ctx, req.body));
    const provider = req.body.provider;
    // With OAuth configured, the browser is sent to the provider to grant read access.
    const oauth = (provider === 'microsoft' || provider === 'google') && isConfigured(provider);
    return { board, authorizeUrl: oauth ? authorizeUrl(provider as OAuthProvider, ctxOf(req).orgId, mailboxId) : null };
  });
  app.get(
    '/v1/oauth/:provider/callback',
    { config: { public: true }, schema: { params: z.object({ provider: z.enum(['microsoft', 'google']) }), querystring: z.object({ code: z.string(), state: z.string() }) } },
    async (req, reply) => {
      await handleCallback(req.params.provider, req.query.code, req.query.state);
      return reply.redirect(`${env.WEB_ORIGIN}/boards?connected=1`);
    },
  );

  // ── AI agents ─────────────────────────────────────────────────────────
  app.get('/v1/agents', (req) => tenant(req, (tx, ctx) => agentsOverview(tx, ctx)));
  app.post('/v1/agents', { schema: { body: AgentBody } }, async (req) => {
    await tenant(req, (tx, ctx) => createAgent(tx, ctx, req.body));
    return { ok: true };
  });
  app.post('/v1/agents/:id/versions', { schema: { params: Uuid, body: AgentVersionBody } }, (req) =>
    tenant(req, (tx, ctx) => newAgentVersion(tx, ctx, req.params.id, req.body.prompt, req.body.model)),
  );
  app.put('/v1/agents/:id/boards', { schema: { params: Uuid, body: AgentBoardsBody } }, async (req) => {
    await tenant(req, (tx, ctx) => setAgentBoards(tx, ctx, req.params.id, req.body.boardIds));
    return { ok: true };
  });
  app.post('/v1/feedback/:id/queue', { schema: { params: Uuid } }, async (req) => {
    await tenant(req, (tx, ctx) => queueTuning(tx, ctx, req.params.id));
    return { ok: true };
  });

  // ── Actions, rules, knowledge, taxonomy, admin ───────────────────────
  app.get('/v1/actions', (req) => tenant(req, (tx, ctx) => actionsOverview(tx, ctx)));
  app.put('/v1/actions/dial', { schema: { body: DialBody } }, async (req) => {
    await tenant(req, (tx, ctx) => setDial(tx, ctx, req.body.cell, req.body.level));
    return { ok: true };
  });
  app.post('/v1/actions/templates', { schema: { body: ActionTemplateBody } }, (req) => tenant(req, (tx, ctx) => createActionTemplate(tx, ctx, req.body)));
  app.get('/v1/policies', (req) => tenant(req, (tx, ctx) => policies(tx, ctx)));
  app.patch('/v1/policies/priority-rules/:id', { schema: { params: Uuid, body: RuleToggleBody } }, async (req) => {
    await tenant(req, (tx, ctx) => togglePriorityRule(tx, ctx, req.params.id, req.body.enabled));
    return { ok: true };
  });
  app.post('/v1/policies/proposed/:id/decide', { schema: { params: Uuid, body: DecideBody } }, async (req) => {
    await tenant(req, (tx, ctx) => decideProposedRule(tx, ctx, req.params.id, req.body.approve));
    return { ok: true };
  });
  app.get('/v1/knowledge', (req) => tenant(req, (tx, ctx) => knowledge(tx, ctx)));
  app.post('/v1/knowledge/sources', { schema: { body: KnowledgeSourceBody } }, async (req) => {
    await tenant(req, (tx, ctx) => connectSource(tx, ctx, req.body));
    return { ok: true };
  });
  app.post('/v1/knowledge/sources/:id/sync', { schema: { params: Uuid } }, (req) => tenant(req, (tx, ctx) => syncSource(tx, ctx, req.params.id)));
  app.post('/v1/knowledge/gaps/:id/act', { schema: { params: Uuid } }, (req) => tenant(req, (tx, ctx) => actOnGap(tx, ctx, req.params.id)));
  app.get('/v1/taxonomy', (req) => tenant(req, (tx, ctx) => taxonomy(tx, ctx)));
  app.put('/v1/taxonomy/departments/:id/owner', { schema: { params: Uuid, body: OwnerBody.partial() } }, (req) =>
    tenant(req, (tx, ctx) => setDepartmentOwner(tx, ctx, req.params.id, req.body.userId)),
  );
  app.get('/v1/admin', (req) => tenant(req, (tx, ctx) => adminOverview(tx, ctx)));

  app.get('/v1/audit/verify', (req) =>
    tenant(req, async (tx, ctx) => {
      requireCap(ctx, 'audit.verify', 'verify the audit log');
      return verifyAuditChain(tx, ctx.orgId);
    }),
  );

  // ── Intake ────────────────────────────────────────────────────────────
  /** Provider push / relay endpoint. Authenticated by an HMAC over the raw body, not a session. */
  app.post('/v1/intake/messages', { config: { public: true, csrfExempt: true }, schema: { body: IntakeMessageBody } }, async (req) => {
    const sig = req.headers['x-ci-signature'];
    if (typeof sig !== 'string' || !safeEqual(sig, hmac(env.INTAKE_WEBHOOK_SECRET, JSON.stringify(req.body)))) throw unauthorized('Invalid signature');
    return ingest(req.body);
  });

  /** Demo: deliver a sample email to this workspace, as if it had just arrived. */
  app.post('/v1/dev/simulate-mail', { schema: { body: z.object({ index: z.number().int().min(0).optional() }) } }, async (req) => {
    if (!env.DEMO_MODE) throw forbidden('Only available in demo mode.');
    const ctx = ctxOf(req);
    requireCap(ctx, 'ticket.work', 'simulate mail');
    const i = req.body.index ?? Math.floor(Math.random() * SAMPLE_MAILS.length);
    const sample = SAMPLE_MAILS[i % SAMPLE_MAILS.length]!;
    // Deliver to the sample's mailbox when this workspace has it, else to the workspace's first mailbox.
    const mailbox = await tenant(req, async (tx) => {
      const rows = await tx.select({ address: mailboxes.address }).from(mailboxes).where(eq(mailboxes.orgId, ctx.orgId)).orderBy(asc(mailboxes.sort));
      return rows.find((m) => m.address === sample.mailbox)?.address ?? rows[0]?.address ?? sample.mailbox;
    });
    return ingest({ ...sample, mailbox, messageId: `sim-${Date.now()}-${i}` }, ctx.orgId);
  });
};
