import { DemoRoleBody, LoginBody, SettingsBody, SwitchOrgBody } from '@ci/contracts';
import type { FastifyPluginAsyncZod } from 'fastify-type-provider-zod';
import { z } from 'zod';
import { env } from '../config/env.js';
import { ctxOf, tenant } from '../platform/http.js';
import {
  buildMe,
  demoSwitchRole,
  demoUsers,
  listOrgChoices,
  listSessions,
  loginByEmail,
  logout,
  resolveSession,
  revokeOtherSessions,
  revokeSession,
  SESSION_COOKIE,
  switchOrg,
} from '../modules/auth/service.js';
import { updateSettings } from '../modules/learning/service.js';

export const authRoutes: FastifyPluginAsyncZod = async (app) => {
  const cookieOpts = {
    httpOnly: true,
    secure: env.COOKIE_SECURE,
    sameSite: 'lax' as const,
    path: '/',
    maxAge: env.SESSION_TTL_HOURS * 3600,
  };

  app.get('/v1/auth/demo', { config: { public: true } }, async () => ({
    demoMode: env.DEMO_MODE,
    users: await demoUsers(),
    orgs: await listOrgChoices('p.sharma@bank.example'),
  }));

  app.post(
    '/v1/auth/login',
    { config: { public: true, csrfExempt: true }, schema: { body: LoginBody } },
    async (req, reply) => {
      const session = await loginByEmail(req.body.email, {
        userAgent: req.headers['user-agent'] ?? '',
        ip: req.ip,
      });
      reply.setCookie(SESSION_COOKIE, session.token, cookieOpts);
      const resolved = await resolveSession(session.token, req.id);
      return buildMe(resolved!.ctx, session.csrfToken);
    },
  );

  app.post('/v1/auth/logout', async (req, reply) => {
    await logout(ctxOf(req));
    reply.clearCookie(SESSION_COOKIE, { path: '/' });
    return { ok: true };
  });

  app.get('/v1/me', async (req) => buildMe(ctxOf(req), req.csrfToken!));

  app.post('/v1/session/org', { schema: { body: SwitchOrgBody } }, async (req) => {
    await switchOrg(ctxOf(req), req.body.orgId);
    return { ok: true };
  });

  app.post('/v1/session/demo-role', { schema: { body: DemoRoleBody } }, async (req) => {
    await demoSwitchRole(ctxOf(req), req.body.role);
    return { ok: true };
  });

  app.get('/v1/sessions', async (req) => listSessions(ctxOf(req)));
  app.delete('/v1/sessions/:id', { schema: { params: z.object({ id: z.string().uuid() }) } }, async (req) => {
    await revokeSession(ctxOf(req), req.params.id);
    return { ok: true };
  });
  app.post('/v1/sessions/revoke-others', async (req) => ({ revoked: await revokeOtherSessions(ctxOf(req)) }));

  app.put('/v1/settings', { schema: { body: SettingsBody } }, async (req) => {
    await tenant(req, (tx, ctx) => updateSettings(tx, ctx, req.body));
    return { ok: true };
  });
};
