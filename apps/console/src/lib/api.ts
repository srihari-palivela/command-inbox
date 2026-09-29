import type { ProblemDTO } from '@ci/contracts';
import { ApiError } from '@web/lib/api';

/**
 * The console's API client. `ApiError` is the web app's own class, so the shared UI kit (`ErrorState`)
 * recognises the console's failures too; the CSRF token is the operator session's, kept here.
 */
export { ApiError };

let csrfToken = '';
export const setCsrfToken = (t: string) => {
  csrfToken = t;
};

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { accept: 'application/json' };
  if (body !== undefined) headers['content-type'] = 'application/json';
  if (method !== 'GET' && csrfToken) headers['x-csrf-token'] = csrfToken;
  const res = await fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
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
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
};

/** Build a query string from a filter object, dropping empty values. */
export function qs(params: Record<string, string | number | undefined | null>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params))
    if (v !== undefined && v !== null && v !== '') u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : '';
}
