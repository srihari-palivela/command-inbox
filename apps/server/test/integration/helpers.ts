import type { MeDTO, TicketDetailDTO } from '@ci/contracts';
import type { InjectOptions, LightMyRequestResponse } from 'fastify';
import { buildApp } from '../../src/app.js';

export type App = Awaited<ReturnType<typeof buildApp>>;

export interface Session {
  me: MeDTO;
  cookie: string;
  req: (
    method: InjectOptions['method'],
    url: string,
    body?: unknown,
    headers?: Record<string, string>,
  ) => Promise<LightMyRequestResponse>;
  ticket: (idOrNumber: string) => Promise<TicketDetailDTO>;
}

export async function signIn(app: App, email: string): Promise<Session> {
  const res = await app.inject({ method: 'POST', url: '/v1/auth/login', payload: { email } });
  if (res.statusCode !== 200) throw new Error(`login failed for ${email}: ${res.statusCode} ${res.body}`);
  const setCookie = res.headers['set-cookie'];
  const raw = Array.isArray(setCookie) ? setCookie.join('; ') : String(setCookie);
  const cookie = /ci_session=[^;]+/.exec(raw)![0];
  let me = res.json() as MeDTO;
  const s: Session = {
    get me() {
      return me;
    },
    set me(v) {
      me = v;
    },
    cookie,
    req: (method, url, body, headers = {}) =>
      app.inject({
        method,
        url,
        payload: body === undefined ? undefined : (body as InjectOptions['payload']),
        headers: { cookie, 'x-csrf-token': me.csrfToken, ...headers },
      }),
    ticket: async (idOrNumber) => {
      const r = await s.req('GET', `/v1/tickets/${idOrNumber}`);
      if (r.statusCode !== 200) throw new Error(`GET ticket ${idOrNumber}: ${r.statusCode} ${r.body}`);
      return r.json() as TicketDetailDTO;
    },
  };
  return s;
}
