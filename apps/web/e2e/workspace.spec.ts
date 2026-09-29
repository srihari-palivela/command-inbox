import { expect, test } from '@playwright/test';
import { signedIn } from './helpers';

test('every screen a team lead can reach renders without errors', async ({ browser }) => {
  const page = await signedIn(browser, 'lead');
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  for (const [path, heading] of [
    ['/tickets', /Tickets/],
    ['/boards', /Boards/],
    ['/performance', /Performance/],
    ['/results', /Results/],
    ['/monitoring', /Monitoring/],
    ['/people', /Skills/],
    ['/learning', /Learning/],
    ['/settings', /Settings/],
  ] as const) {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1 }).first()).toContainText(heading);
  }
  expect(errors).toEqual([]);
});

test('admin setup screens render', async ({ browser }) => {
  const page = await signedIn(browser, 'admin');
  for (const path of [
    '/setup/agents',
    '/setup/actions',
    '/setup/policies',
    '/setup/knowledge',
    '/setup/ownership',
    '/setup/mailboxes',
  ]) {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
  }
});

test('staff cannot open admin setup; the page says why instead of offering a retry', async ({ browser }) => {
  const page = await signedIn(browser, 'staff');
  await page.goto('/setup/agents');
  await expect(page.getByText(/can.t view|Not available for your role/i).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Try again' })).toHaveCount(0);
});

test('natural-language filter turns a sentence into removable chips', async ({ browser }) => {
  const page = await signedIn(browser, 'lead');
  await page.goto('/tickets?view=list');
  const box = page.getByLabel('Describe what you want to see');
  await box.fill('late disputes assigned to me');
  await box.press('Enter');
  await expect(page).toHaveURL(/nl=/);
  await expect(page.getByRole('button', { name: /Remove filter/ }).first()).toBeVisible();
});

test('command palette finds a ticket by number', async ({ browser }) => {
  const page = await signedIn(browser, 'staff');
  await page.goto('/inbox');
  await expect(page.getByRole('heading', { name: /Inbox/ })).toBeVisible();
  await page.keyboard.press('Control+k');
  await page.getByRole('combobox', { name: 'Search or ask' }).fill('48211');
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('QRY-48211').first()).toBeVisible();
});
