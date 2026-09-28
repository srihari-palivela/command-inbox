import { expect, type Browser, type Page } from '@playwright/test';

export const USERS = {
  staff: 'p.sharma@bank.example',
  lead: 'r.menon@bank.example',
  admin: 'a.kapoor@bank.example',
} as const;

/**
 * Sign in through the API (fast) in a fresh context, so each role has its own session cookie.
 * `prefs` sets personal preferences first, e.g. `{ autoAdvance: false }` for a test that checks a ticket's
 * state after an approval (auto-advance would otherwise move on 500 ms later and race the assertion).
 */
export async function signedIn(
  browser: Browser,
  who: keyof typeof USERS,
  prefs?: Record<string, boolean>,
): Promise<Page> {
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();
  const res = await page.request.post('/v1/auth/login', { data: { email: USERS[who] } });
  expect(res.ok()).toBeTruthy();
  if (prefs) {
    const { csrfToken } = (await res.json()) as { csrfToken: string };
    const set = await page.request.put('/v1/settings', {
      data: { prefs },
      headers: { 'x-csrf-token': csrfToken },
    });
    expect(set.ok()).toBeTruthy();
  }
  return page;
}

export const gateway = (page: Page) => page.getByRole('region', { name: 'Approval gateway' });
export const toast = (page: Page) => page.getByRole('status');
