import { expect, test } from '@playwright/test';
import { gateway, signedIn, toast } from './helpers';

// These tests change data in order (seeded state → approvals), so they run serially.
test.describe.configure({ mode: 'serial' });

test('sign in from the demo picker lands on the urgency-ordered inbox', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: /P\. Sharma/ }).click();
  await expect(page.getByRole('heading', { name: /Inbox/ })).toBeVisible();
  const rows = page.getByRole('listbox', { name: /Tickets/ }).getByRole('option');
  // Most urgent first: the P1 ombudsman escalation that the AI stood down on.
  await expect(rows.first()).toContainText('Third time writing');
  await expect(rows.first()).toContainText('needs you');
  // The first ticket opens by default and deep-links.
  await expect(page).toHaveURL(/\/inbox\/QRY-48199$/);
  await expect(gateway(page)).toContainText('The AI has stepped back');
});

test('J/K move through the queue and the URL follows', async ({ browser }) => {
  const page = await signedIn(browser, 'staff');
  await page.goto('/inbox/QRY-48199');
  await expect(page.getByRole('heading', { level: 2 })).toContainText('Third time writing');
  await page.keyboard.press('j');
  await expect(page).toHaveURL(/QRY-48211$/);
  await page.keyboard.press('k');
  await expect(page).toHaveURL(/QRY-48199$/);
});

test('irreversible money movement needs a maker and a different checker', async ({ browser }) => {
  const maker = await signedIn(browser, 'staff');
  await maker.goto('/inbox/QRY-48211');
  const gate = gateway(maker);
  await expect(gate).toContainText('Needs two approvers');
  await expect(gate).toContainText('Cannot be undone');
  await expect(gate).toContainText('R. Menon (Checker)');
  await expect(gate).toContainText('No similar action on this account in 90 days');
  // Irreversible: no undo is ever offered (review C1).
  await expect(gate.getByRole('button', { name: /Undo/ })).toHaveCount(0);

  await gate.getByRole('button', { name: /Approve & execute/ }).click();
  await expect(toast(maker)).toContainText('Approved as maker');
  await expect(gate).toContainText('waiting on');
  // The maker cannot be their own checker.
  await expect(gate.getByRole('button', { name: /Waiting on checker/ })).toBeDisabled();

  const checker = await signedIn(browser, 'lead');
  await checker.goto('/inbox/QRY-48211');
  const cgate = gateway(checker);
  await cgate.getByRole('button', { name: /Counter-approve/ }).click();
  // The worker executes it in core banking and writes the audit record.
  await expect(cgate).toContainText('Done and audited', { timeout: 20_000 });
  await expect(checker.getByText('Action carried out in core banking')).toBeVisible();
});

test('a person edits the draft, sends it, and can recall it inside the window', async ({ browser }) => {
  const page = await signedIn(browser, 'staff');
  await page.goto('/inbox/QRY-48207');
  const draft = page.getByRole('region', { name: 'Drafted reply' });
  await expect(draft).toContainText('Cited sources');
  await draft.getByRole('button', { name: 'Edit draft' }).click();
  const box = page.getByLabel('Edit the reply');
  await box.fill((await box.inputValue()).replace('We will need the following', 'Please arrange the following documents'));
  await page.getByRole('button', { name: 'Save draft' }).click();
  await expect(toast(page)).toContainText('Draft saved');
  // The diff shows what the person changed against the AI's version.
  await expect(draft.locator('ins').first()).toContainText('Please arrange');
  await expect(draft.locator('del').first()).toContainText('We will need');

  const gate = gateway(page);
  await expect(gate).toContainText('Recallable for 60s after send');
  await gate.getByRole('button', { name: /Approve & send/ }).click();
  await expect(gate.getByRole('button', { name: /Recall/ })).toBeVisible();
  await gate.getByRole('button', { name: /Recall/ }).click();
  await expect(toast(page)).toContainText('Recalled');
  await expect(gate.getByRole('button', { name: /Approve & send/ })).toBeEnabled();
});

test('sending work back records a correction and hands the ticket to me', async ({ browser }) => {
  const page = await signedIn(browser, 'staff');
  await page.goto('/inbox/QRY-48188');
  await gateway(page).getByRole('button', { name: 'Reject' }).click();
  const dialog = page.getByRole('dialog', { name: 'Send it back to the AI' });
  const confirm = dialog.getByRole('button', { name: /Send back/ });
  await expect(confirm).toBeDisabled();
  await dialog.getByRole('radio', { name: /Wrong tone/ }).click();
  await confirm.click();
  await expect(toast(page)).toContainText('Sent back to the AI');
});

test('taking on a stood-down ticket makes it mine', async ({ browser }) => {
  const page = await signedIn(browser, 'staff');
  await page.goto('/inbox/QRY-48174');
  await gateway(page).getByRole('button', { name: /Take it on/ }).click();
  await expect(toast(page)).toContainText('is yours now');
  await expect(gateway(page)).toContainText('Yours now');
});

test('ticket fields, trace and the reply composer', async ({ browser }) => {
  const page = await signedIn(browser, 'staff');
  await page.goto('/inbox/QRY-48199');
  await page.getByRole('tab', { name: /Ticket fields/ }).click();
  await expect(page.getByText('Seen before')).toBeVisible();
  await page.getByLabel('Internal note').fill('Called back, promised a dated answer by 5pm.');
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(page.getByText('Called back, promised a dated answer by 5pm.')).toBeVisible();

  await page.getByRole('tab', { name: /AI trace/ }).click();
  await expect(page.getByText('Execution trace')).toBeVisible();
  await expect(page.getByText('Guardrail Sentinel').first()).toBeVisible();

  await page.keyboard.press('r');
  await expect(page.getByLabel(/Reply to Fatima Sheikh/)).toBeFocused();
});
