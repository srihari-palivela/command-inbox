import { expect, type Browser, type Page } from '@playwright/test';

export const USERS = {
  staff: 'p.sharma@bank.example',
  lead: 'r.menon@bank.example',
  admin: 'a.kapoor@bank.example',
} as const;

/** Sign in through the API (fast) in a fresh context, so each role has its own session cookie. */
export async function signedIn(browser: Browser, who: keyof typeof USERS): Promise<Page> {
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();
  const res = await page.request.post('/v1/auth/login', { data: { email: USERS[who] } });
  expect(res.ok()).toBeTruthy();
  return page;
}

export const gateway = (page: Page) => page.getByRole('region', { name: 'Approval gateway' });
export const toast = (page: Page) => page.getByRole('status');
