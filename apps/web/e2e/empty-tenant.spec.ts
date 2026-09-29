import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

/**
 * A new bank before onboarding: one admin and nothing else. Every screen must render (empty states, not
 * errors or crashes). The demo seed hides this class of bug, so this runs against a workspace with no seed.
 */
const SCREENS = [
  '/inbox',
  '/tickets',
  '/boards',
  '/performance',
  '/results',
  '/people',
  '/learning',
  '/setup/agents',
  '/setup/actions',
  '/setup/policies',
  '/setup/knowledge',
  '/setup/ownership',
  '/setup/mailboxes',
  '/admin/deployments',
  '/admin/evals',
  '/admin/members',
  '/admin/permissions',
  '/settings',
];

let adminEmail = '';

test.beforeAll(() => {
  const api = fileURLToPath(new URL('../../api', import.meta.url));
  adminEmail = execSync('uv run python scripts/empty_tenant.py', {
    cwd: api,
    env: { ...process.env, APP_ENV: 'development' },
  })
    .toString()
    .trim()
    .split('\n')
    .pop()!;
});

test('every screen renders on an empty workspace', async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();
  const res = await page.request.post('/v1/auth/login', { data: { email: adminEmail } });
  expect(res.ok()).toBeTruthy();
  expect(((await res.json()) as { org: { slug: string } }).org.slug).toBe('empty-smoke');

  const problems: string[] = [];
  page.on('pageerror', (e) => problems.push(`page error: ${e.message}`));
  page.on('response', (r) => {
    if (r.url().includes('/v1/') && r.status() >= 500) problems.push(`${r.status()} ${r.url()}`);
  });

  for (const path of SCREENS) {
    await page.goto(path);
    await expect(page.getByRole('main').getByRole('heading').first(), path).toBeVisible();
    // Loaded: no skeletons left (the live event stream keeps the network busy, so no "networkidle").
    await expect(page.getByRole('main').locator('[class*="skel"]'), `${path} never loads`).toHaveCount(0);
    await expect(page.locator('[role="alert"]'), `${path} shows an error`).toHaveCount(0);
  }
  expect(problems).toEqual([]);
  await ctx.close();
});
