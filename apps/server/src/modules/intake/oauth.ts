/**
 * Mailbox OAuth (authorisation-code flow). Read-only scopes: the AI never holds send scope — replies go
 * out under a named approver. State is HMAC-signed and short-lived; tokens are encrypted at rest.
 */
import { withTenant } from '../../db/client.js';
import { env } from '../../config/env.js';
import * as s from '../../db/schema.js';
import { eq } from 'drizzle-orm';
import { encrypt, hmac, safeEqual } from '../../platform/crypto.js';
import { badRequest, unprocessable } from '../../platform/errors.js';
import { clock } from '../../platform/clock.js';

const PROVIDERS = {
  microsoft: {
    authorize: 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
    token: 'https://login.microsoftonline.com/common/oauth2/v2.0/token',
    scope: 'offline_access https://graph.microsoft.com/Mail.Read',
    clientId: () => env.MS_CLIENT_ID,
    secret: () => env.MS_CLIENT_SECRET,
  },
  google: {
    authorize: 'https://accounts.google.com/o/oauth2/v2/auth',
    token: 'https://oauth2.googleapis.com/token',
    scope: 'https://www.googleapis.com/auth/gmail.readonly',
    clientId: () => env.GOOGLE_CLIENT_ID,
    secret: () => env.GOOGLE_CLIENT_SECRET,
  },
} as const;

export type OAuthProvider = keyof typeof PROVIDERS;

interface State {
  orgId: string;
  mailboxId: string;
  exp: number;
}

const sign = (st: State) => {
  const body = Buffer.from(JSON.stringify(st)).toString('base64url');
  return `${body}.${hmac(env.ENCRYPTION_KEY, body)}`;
};

function verify(raw: string): State {
  const [body, sig] = raw.split('.');
  if (!body || !sig || !safeEqual(sig, hmac(env.ENCRYPTION_KEY, body))) throw badRequest('bad_state', 'Invalid OAuth state.');
  const st = JSON.parse(Buffer.from(body, 'base64url').toString()) as State;
  if (st.exp < clock.now().getTime()) throw badRequest('expired_state', 'The connection attempt expired. Start again.');
  return st;
}

export function isConfigured(p: OAuthProvider): boolean {
  return !!PROVIDERS[p].clientId() && !!PROVIDERS[p].secret();
}

export function authorizeUrl(p: OAuthProvider, orgId: string, mailboxId: string): string {
  const cfg = PROVIDERS[p];
  if (!isConfigured(p)) throw unprocessable('oauth_not_configured', `${p} OAuth is not configured for this deployment.`);
  const url = new URL(cfg.authorize);
  url.searchParams.set('client_id', cfg.clientId()!);
  url.searchParams.set('response_type', 'code');
  url.searchParams.set('redirect_uri', `${env.PUBLIC_API_URL}/v1/oauth/${p}/callback`);
  url.searchParams.set('scope', cfg.scope);
  url.searchParams.set('access_type', 'offline');
  url.searchParams.set('prompt', 'consent');
  url.searchParams.set('state', sign({ orgId, mailboxId, exp: clock.now().getTime() + 10 * 60_000 }));
  return url.toString();
}

/** Exchange the code, store encrypted tokens, and start streaming the mailbox. */
export async function handleCallback(p: OAuthProvider, code: string, rawState: string): Promise<void> {
  const st = verify(rawState);
  const cfg = PROVIDERS[p];
  const res = await fetch(cfg.token, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      client_id: cfg.clientId()!,
      client_secret: cfg.secret()!,
      grant_type: 'authorization_code',
      code,
      redirect_uri: `${env.PUBLIC_API_URL}/v1/oauth/${p}/callback`,
    }),
  });
  if (!res.ok) throw badRequest('token_exchange_failed', `The ${p} token exchange failed (${res.status}).`);
  const tokens = (await res.json()) as Record<string, unknown>;
  await withTenant(st.orgId, async (tx) => {
    await tx
      .update(s.mailboxes)
      .set({ credentialsEnc: encrypt(JSON.stringify(tokens)), state: 'streaming', lastSyncAt: clock.now() })
      .where(eq(s.mailboxes.id, st.mailboxId));
  });
}
