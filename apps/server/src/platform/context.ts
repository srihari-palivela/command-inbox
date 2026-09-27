import type { Capability, Role } from '@ci/contracts';

export interface Actor {
  kind: 'user' | 'ai' | 'system';
  id: string | null;
  name: string;
  initials: string;
}

export const AI_ACTOR: Actor = { kind: 'ai', id: null, name: 'Command Inbox', initials: 'AI' };
export const SYSTEM_ACTOR: Actor = { kind: 'system', id: null, name: 'System', initials: 'SY' };

/** Per-request identity, resolved from the session cookie. */
export interface Ctx {
  orgId: string;
  sessionId: string;
  requestId: string;
  role: Role;
  capabilities: ReadonlySet<Capability>;
  user: { id: string; name: string; initials: string; email: string };
}

export function actorOf(ctx: Ctx): Actor {
  return { kind: 'user', id: ctx.user.id, name: ctx.user.name, initials: ctx.user.initials };
}
