import type { Capability, MeDTO, MembershipDTO, OrgChoiceDTO, OrgDTO, Role, SessionDTO } from '@ci/contracts';
import { and, count, desc, eq, gt, isNull, sql } from 'drizzle-orm';
import { env } from '../../config/env.js';
import { db, withTenant } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { audit } from '../../platform/audit.js';
import { clock } from '../../platform/clock.js';
import type { Ctx } from '../../platform/context.js';
import { randomToken, sha256 } from '../../platform/crypto.js';
import { deviceLabel, locationLabel } from '../../platform/device.js';
import { forbidden, notFound, unauthorized } from '../../platform/errors.js';
import { ROLE_CAPABILITIES } from '../../platform/rbac.js';
import { llmStatus } from '../triage/providers/index.js';

export const SESSION_COOKIE = 'ci_session';

export function toOrgDTO(o: typeof s.orgs.$inferSelect): OrgDTO {
  return {
    id: o.id,
    slug: o.slug,
    name: o.name,
    short: o.short,
    tint: o.tint,
    bg: o.bg,
    plan: o.plan,
    confidenceBar: o.confidenceBar,
  };
}

export interface ResolvedSession {
  ctx: Ctx;
  csrfToken: string;
}

/** Resolve a session token to a request context. Sliding expiry; last-seen is written at most once a minute. */
export async function resolveSession(token: string, requestId: string): Promise<ResolvedSession | null> {
  const now = clock.now();
  const rows = await db
    .select({ session: s.sessions, user: s.users, role: s.memberships.role })
    .from(s.sessions)
    .innerJoin(s.users, eq(s.users.id, s.sessions.userId))
    .innerJoin(
      s.memberships,
      and(eq(s.memberships.userId, s.sessions.userId), eq(s.memberships.orgId, s.sessions.orgId)),
    )
    .where(
      and(
        eq(s.sessions.tokenHash, sha256(token)),
        isNull(s.sessions.revokedAt),
        gt(s.sessions.expiresAt, now),
      ),
    )
    .limit(1);
  const row = rows[0];
  if (!row) return null;
  if (now.getTime() - row.session.lastSeenAt.getTime() > 60_000) {
    await db
      .update(s.sessions)
      .set({ lastSeenAt: now, expiresAt: new Date(now.getTime() + env.SESSION_TTL_HOURS * 3600_000) })
      .where(eq(s.sessions.id, row.session.id));
  }
  const role = row.role as Role;
  return {
    csrfToken: row.session.csrfToken,
    ctx: {
      orgId: row.session.orgId,
      sessionId: row.session.id,
      requestId,
      role,
      capabilities: ROLE_CAPABILITIES[role],
      user: { id: row.user.id, name: row.user.name, initials: row.user.initials, email: row.user.email },
    },
  };
}

export async function createSession(
  userId: string,
  orgId: string,
  meta: { userAgent: string; ip: string },
): Promise<{ token: string; csrfToken: string }> {
  const token = randomToken();
  const csrfToken = randomToken(18);
  const now = clock.now();
  await db.insert(s.sessions).values({
    userId,
    orgId,
    tokenHash: sha256(token),
    csrfToken,
    userAgent: meta.userAgent.slice(0, 400),
    device: deviceLabel(meta.userAgent),
    location: locationLabel(meta.ip),
    ip: meta.ip,
    expiresAt: new Date(now.getTime() + env.SESSION_TTL_HOURS * 3600_000),
  });
  return { token, csrfToken };
}

/**
 * Passwordless demo sign-in. In production this endpoint is disabled and sign-in goes through the
 * bank's OIDC provider (Entra ID / Google), which yields the same `createSession` call.
 */
export async function loginByEmail(email: string, meta: { userAgent: string; ip: string }) {
  if (!env.DEMO_MODE) throw forbidden('Sign in with your organisation’s single sign-on.', 'sso_required');
  const [user] = await db.select().from(s.users).where(eq(s.users.email, email)).limit(1);
  if (!user) throw unauthorized('No workspace access for that email. Ask your administrator.');
  const [m] = await db
    .select({ orgId: s.memberships.orgId })
    .from(s.memberships)
    .innerJoin(s.orgs, eq(s.orgs.id, s.memberships.orgId))
    .where(eq(s.memberships.userId, user.id))
    .orderBy(s.orgs.createdAt)
    .limit(1);
  if (!m) throw unauthorized('No workspace access for that email. Ask your administrator.');
  const session = await createSession(user.id, m.orgId, meta);
  await withTenant(m.orgId, (tx) =>
    audit(tx, m.orgId, {
      actor: { kind: 'user', id: user.id, name: user.name, initials: user.initials },
      action: 'session.created',
      entity: 'session',
      summary: `${user.name} signed in`,
      data: { device: deviceLabel(meta.userAgent) },
    }),
  );
  return session;
}

export async function switchOrg(ctx: Ctx, orgId: string): Promise<void> {
  const [m] = await db
    .select()
    .from(s.memberships)
    .where(and(eq(s.memberships.userId, ctx.user.id), eq(s.memberships.orgId, orgId)));
  if (!m) throw forbidden('You are not a member of that workspace.');
  await db.update(s.sessions).set({ orgId }).where(eq(s.sessions.id, ctx.sessionId));
}

export async function logout(ctx: Ctx): Promise<void> {
  await db.update(s.sessions).set({ revokedAt: clock.now() }).where(eq(s.sessions.id, ctx.sessionId));
}

export async function listSessions(ctx: Ctx): Promise<SessionDTO[]> {
  const rows = await db
    .select()
    .from(s.sessions)
    .where(
      and(
        eq(s.sessions.userId, ctx.user.id),
        isNull(s.sessions.revokedAt),
        gt(s.sessions.expiresAt, clock.now()),
      ),
    )
    .orderBy(desc(s.sessions.lastSeenAt));
  return rows.map((r) => ({
    id: r.id,
    device: r.device || 'Browser',
    location: r.location,
    lastSeenAt: r.lastSeenAt.toISOString(),
    current: r.id === ctx.sessionId,
  }));
}

export async function revokeSession(ctx: Ctx, id: string): Promise<void> {
  const rows = await db
    .update(s.sessions)
    .set({ revokedAt: clock.now() })
    .where(and(eq(s.sessions.id, id), eq(s.sessions.userId, ctx.user.id)))
    .returning({ id: s.sessions.id });
  if (!rows.length) throw notFound('Session');
}

export async function revokeOtherSessions(ctx: Ctx): Promise<number> {
  const rows = await db
    .update(s.sessions)
    .set({ revokedAt: clock.now() })
    .where(
      and(
        eq(s.sessions.userId, ctx.user.id),
        isNull(s.sessions.revokedAt),
        sql`${s.sessions.id} <> ${ctx.sessionId}`,
      ),
    )
    .returning({ id: s.sessions.id });
  return rows.length;
}

/** Demo only: re-point this session at the seeded person holding `role` in the current workspace. */
export async function demoSwitchRole(ctx: Ctx, role: Role): Promise<void> {
  if (!env.DEMO_MODE) throw forbidden('Role switching is only available in demo mode.');
  const [target] = await db
    .select({ userId: s.memberships.userId })
    .from(s.memberships)
    .innerJoin(s.users, eq(s.users.id, s.memberships.userId))
    .where(and(eq(s.memberships.orgId, ctx.orgId), eq(s.memberships.role, role)))
    .orderBy(s.memberships.joinedAt)
    .limit(1);
  if (!target) throw notFound(`A ${role} in this workspace`);
  await db.update(s.sessions).set({ userId: target.userId }).where(eq(s.sessions.id, ctx.sessionId));
}

export async function listOrgChoices(email?: string): Promise<OrgChoiceDTO[]> {
  const orgRows = await db.select().from(s.orgs).orderBy(s.orgs.createdAt);
  const user = email ? (await db.select().from(s.users).where(eq(s.users.email, email)))[0] : undefined;
  const out: OrgChoiceDTO[] = [];
  for (const o of orgRows) {
    const [m] = user
      ? await db
          .select()
          .from(s.memberships)
          .where(and(eq(s.memberships.orgId, o.id), eq(s.memberships.userId, user.id)))
      : [];
    const counts = await withTenant(o.id, async (tx) => {
      const [b] = await tx.select({ n: count() }).from(s.boards);
      return b?.n ?? 0;
    });
    out.push({ ...toOrgDTO(o), role: (m?.role as Role) ?? null, boards: counts, people: o.headcount });
  }
  return out;
}

export async function demoUsers() {
  if (!env.DEMO_MODE) return [];
  const rows = await db
    .select({
      email: s.users.email,
      name: s.users.name,
      role: s.memberships.role,
      title: s.memberships.title,
    })
    .from(s.users)
    .innerJoin(s.memberships, eq(s.memberships.userId, s.users.id))
    .innerJoin(s.orgs, and(eq(s.orgs.id, s.memberships.orgId), eq(s.orgs.slug, 'apex')));
  const order: Record<string, number> = { staff: 0, lead: 1, admin: 2 };
  return rows.sort((a, b) => order[a.role]! - order[b.role]! || a.name.localeCompare(b.name));
}

export async function buildMe(ctx: Ctx, csrfToken: string): Promise<MeDTO> {
  const [org] = await db.select().from(s.orgs).where(eq(s.orgs.id, ctx.orgId));
  const [membership] = await db
    .select()
    .from(s.memberships)
    .where(and(eq(s.memberships.orgId, ctx.orgId), eq(s.memberships.userId, ctx.user.id)));
  if (!org || !membership) throw unauthorized();

  const memberships: MembershipDTO[] = [];
  const mine = await db
    .select({ org: s.orgs, role: s.memberships.role })
    .from(s.memberships)
    .innerJoin(s.orgs, eq(s.orgs.id, s.memberships.orgId))
    .where(eq(s.memberships.userId, ctx.user.id))
    .orderBy(s.orgs.createdAt);
  for (const m of mine) {
    const stats = await withTenant(m.org.id, async (tx) => {
      const [b] = await tx.select({ n: count() }).from(s.boards);
      const [u] = await tx
        .execute<{ n: number }>(
          sql`
        select count(*)::int as n from notifications n
         where not exists (select 1 from notification_reads r where r.notification_id = n.id and r.user_id = ${ctx.user.id})`,
        )
        .then((r) => r.rows);
      return { boards: b?.n ?? 0, unread: u?.n ?? 0 };
    });
    memberships.push({
      org: toOrgDTO(m.org),
      role: m.role as Role,
      boards: stats.boards,
      people: m.org.headcount,
      unread: stats.unread,
    });
  }

  const nav = await withTenant(ctx.orgId, async (tx) => {
    const one = async (q: ReturnType<typeof sql>) =>
      Number((await tx.execute<{ n: number }>(q)).rows[0]?.n ?? 0);
    return {
      // Same rule as the Inbox's own count: open work assigned to me, plus maker-approved actions I can check.
      inbox:
        await one(sql`select count(*)::int n from tickets t where t.merged_into_id is null and t.status not in ('resolved','closed')
                            and (t.assignee_id = ${ctx.user.id}
                                 or (${ctx.capabilities.has('action.approve_checker')} and exists (
                                       select 1 from action_instances ai where ai.ticket_id = t.id
                                          and ai.state = 'awaiting_checker' and ai.maker_id <> ${ctx.user.id})))`),
      tickets: await one(
        sql`select count(*)::int n from tickets where status not in ('closed') and (resolved_at is null or resolved_at > now() - interval '24 hours') and merged_into_id is null`,
      ),
      boards: await one(sql`select count(*)::int n from boards`),
      alerts: await one(sql`select count(*)::int n from alerts where resolved_at is null`),
      learning: await one(sql`select count(*)::int n from notifications n where n.kind = 'learning'
                               and not exists (select 1 from notification_reads r where r.notification_id = n.id and r.user_id = ${ctx.user.id})`),
      agents: await one(sql`select count(*)::int n from agents`),
      gaps: await one(sql`select count(*)::int n from gap_tickets where closed_at is null`),
      autonomousCells: await one(sql`select count(*)::int n from autonomy_dial where level >= 2`),
    };
  });

  const settings = await withTenant(ctx.orgId, async (tx) => {
    const [row] = await tx
      .select()
      .from(s.userSettings)
      .where(and(eq(s.userSettings.orgId, ctx.orgId), eq(s.userSettings.userId, ctx.user.id)));
    return { prefs: row?.prefs ?? {}, signature: row?.signature ?? '' };
  });

  const status = llmStatus();
  return {
    user: {
      ...ctx.user,
      title: membership.title,
      pod: membership.pod,
      joinedAt: membership.joinedAt.toISOString(),
    },
    org: toOrgDTO(org),
    role: ctx.role,
    capabilities: [...ctx.capabilities] as Capability[],
    memberships,
    csrfToken,
    demoMode: env.DEMO_MODE,
    settings,
    nav,
    worker: { state: status.degraded ? 'degraded' : 'live', provider: status.provider },
  };
}
