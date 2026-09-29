import type { ProblemDTO } from '@ci/contracts';

/** A failed API call, carrying the server's RFC 9457 problem details. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly problem: ProblemDTO,
  ) {
    super(problem.title);
  }
  get code() {
    return this.problem.code;
  }
}

let csrfToken = '';
export const setCsrfToken = (t: string) => {
  csrfToken = t;
};

export interface RequestOptions {
  idempotencyKey?: string;
  ifMatch?: number;
  signal?: AbortSignal;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = { accept: 'application/json' };
  if (body !== undefined) headers['content-type'] = 'application/json';
  if (method !== 'GET' && csrfToken) headers['x-csrf-token'] = csrfToken;
  if (opts.idempotencyKey) headers['idempotency-key'] = opts.idempotencyKey;
  if (opts.ifMatch !== undefined) headers['if-match'] = String(opts.ifMatch);
  const res = await fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: opts.signal,
  });
  if (!res.ok) {
    let problem: ProblemDTO;
    try {
      problem = (await res.json()) as ProblemDTO;
    } catch {
      problem = {
        type: 'about:blank',
        title: res.statusText || 'Request failed',
        status: res.status,
        code: 'http_error',
      };
    }
    throw new ApiError(res.status, problem);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string, opts?: RequestOptions) => request<T>('GET', path, undefined, opts),
  post: <T>(path: string, body: unknown = {}, opts?: RequestOptions) => request<T>('POST', path, body, opts),
  put: <T>(path: string, body: unknown = {}, opts?: RequestOptions) => request<T>('PUT', path, body, opts),
  patch: <T>(path: string, body: unknown = {}, opts?: RequestOptions) =>
    request<T>('PATCH', path, body, opts),
  del: <T>(path: string, opts?: RequestOptions) => request<T>('DELETE', path, undefined, opts),
};

export const newIdempotencyKey = () =>
  crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;

/** Build a query string from a filter object, dropping empty values. */
export function qs(params: Record<string, string | number | undefined | null>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params))
    if (v !== undefined && v !== null && v !== '') u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : '';
}
