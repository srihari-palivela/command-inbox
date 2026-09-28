import { expect, test, type Page } from '@playwright/test';
import { apiAs, signedIn, toast } from './helpers';

// Tenant administration. These tests only touch data they create (a draft they discard at the end, an
// invitation they revoke), so they stay independent of the other specs and of a retry.

interface Deployment {
  id: string;
  key: string;
  draftVersionId: string | null;
}

/** Discard a draft of the default deployment left behind by an interrupted earlier run. */
async function defaultDeploymentWithoutDraft(page: Page): Promise<Deployment> {
  const api = await apiAs(page);
  const list = await api.get<Deployment[]>('/v1/deployments');
  const d = list.find((x) => x.key === 'default')!;
  if (d.draftVersionId) {
    const res = await api.post(`/v1/deployments/${d.id}/versions/${d.draftVersionId}/withdraw`);
    expect(res.ok()).toBeTruthy();
  }
  return d;
}

async function runEvals(page: Page) {
  await page.getByRole('button', { name: 'Run evals' }).click();
  const dialog = page.getByRole('dialog', { name: 'Run evals' });
  await dialog.getByRole('button', { name: 'Start run' }).click();
  await expect(page).toHaveURL(/\/admin\/evals\/runs\//);
}

test('admin sees deployments, with the default deployment published at v1', async ({ browser }) => {
  const page = await signedIn(browser, 'admin');
  await page.goto('/inbox');
  const nav = page.getByRole('navigation', { name: 'Main' });
  await expect(nav.getByText('Administration')).toBeVisible();
  await nav.getByRole('link', { name: 'Deployments' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Deployments');
  const row = page.getByRole('table', { name: 'Deployments' }).getByRole('row', { name: /Default/ });
  await expect(row).toContainText('Published v1');
  await expect(row).toContainText('customercare@bank.example');
  await row.getByRole('link', { name: 'Default' }).click();
  await expect(page.getByRole('list', { name: 'Version history' })).toContainText('v1');
  await expect(page.getByRole('list', { name: 'Version history' })).toContainText('Published');
});

test('a draft cannot be published until an eval run on its exact configuration passes', async ({
  browser,
}) => {
  const page = await signedIn(browser, 'admin');
  const d = await defaultDeploymentWithoutDraft(page);
  await page.goto(`/admin/deployments/${d.id}`);

  await page.getByRole('button', { name: 'Start a new draft' }).click();
  await expect(toast(page)).toContainText('Draft v');
  const check = page.getByRole('region', { name: 'Publish check' });
  await expect(check).toContainText('Needs a passed eval run on this exact configuration.');
  const publish = page.getByRole('button', { name: /^Publish v\d+$/ });
  await expect(publish).toBeDisabled();

  // Edit a threshold. An out-of-range value comes back from the server with its path, shown on the field.
  await page.getByRole('tab', { name: 'Thresholds' }).click();
  const autoBar = page.getByLabel('Auto lane — minimum confidence');
  await autoBar.fill('0.3');
  await page.getByRole('button', { name: 'Save draft' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'thresholds.autoMinConfidence' })).toBeVisible();
  await expect(autoBar).toHaveAttribute('aria-invalid', 'true');
  await autoBar.fill('0.9');
  await page.getByRole('button', { name: 'Save draft' }).click();
  await expect(toast(page)).toContainText('saved');
  await expect(page.getByLabel('Auto lane — minimum confidence')).toHaveValue('0.9');

  // A run with the default gates fails: the verdict per gate says why.
  await runEvals(page);
  await expect(page.getByText(/Failed \d of 7 gates/)).toBeVisible({ timeout: 20_000 });
  const gates = page.getByRole('table', { name: 'Gates' });
  await expect(gates.getByRole('row', { name: /Hard-stop recall/ })).toContainText('Pass');
  await expect(gates.getByRole('row', { name: /Macro-F1/ })).toContainText('Fail');
  await expect(gates.getByRole('row', { name: /Macro-F1/ })).toContainText('≥ 85.0%');
  await page.getByRole('button', { name: /Wrong only/ }).click();
  await expect(page).toHaveURL(/wrong=1/);
  const results = page.getByRole('table', { name: 'Case results' });
  await expect(results.getByRole('row').nth(1)).toContainText('Wrong');
  await expect(results.getByText('Right', { exact: true })).toHaveCount(0);

  // Still blocked.
  await page.getByRole('link', { name: /Default v\d+/ }).click();
  await expect(check).toContainText('Needs a passed eval run on this exact configuration.');
  await expect(publish).toBeDisabled();

  // Loosen the gates (a real admin would add cases instead); the passing run unblocks the version.
  await page.getByRole('tab', { name: 'Eval gates' }).click();
  await page.getByLabel('Category accuracy at least').fill('0.8');
  await page.getByLabel('Macro-F1 at least').fill('0.7');
  await page.getByLabel('Expected calibration error at most').fill('0.15');
  await page.getByLabel('Selective accuracy at least').fill('0.9');
  await page.getByRole('button', { name: 'Save draft' }).click();
  await expect(toast(page)).toContainText('saved');

  await runEvals(page);
  await expect(page.getByText('Passed every gate.')).toBeVisible({ timeout: 20_000 });
  await page.getByRole('link', { name: /Default v\d+/ }).click();
  await expect(check).toContainText('A passed eval run covers this exact configuration');
  // Apex has one admin, who also made the edit: publishing needs the audited acknowledgement.
  await expect(publish).toBeDisabled();
  await check.getByRole('checkbox', { name: /I am the only admin/ }).check();
  await expect(publish).toBeEnabled();

  // Leave the deployment as it was: discard the draft.
  await page.getByRole('button', { name: 'Discard draft' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Discard draft' }).click();
  await expect(toast(page)).toContainText('discarded');
  await expect(page.getByRole('button', { name: 'Start a new draft' })).toBeVisible();
});

test('members lists the seeded people, and an invitation shows as pending', async ({ browser }) => {
  const page = await signedIn(browser, 'admin');
  await page.goto('/admin/members');
  const members = page.getByRole('table', { name: 'Members' });
  for (const name of ['P. Sharma', 'R. Menon', 'A. Kapoor', 'S. Qureshi'])
    await expect(members).toContainText(name);
  // Nobody changes their own role.
  await expect(page.getByLabel('Role for A. Kapoor')).toBeDisabled();
  await expect(page.getByLabel('Role for R. Menon')).toHaveValue('lead');

  const email = `e2e.${Date.now()}@bank.example`;
  const form = page.getByRole('form', { name: 'Invite someone' });
  await form.getByLabel('Email').fill(email);
  await form.getByLabel('Invited role').selectOption('lead');
  await form.getByRole('button', { name: 'Send invitation' }).click();
  await expect(toast(page)).toContainText(`Invited ${email}`);
  const row = page.getByRole('table', { name: 'Invitations' }).getByRole('row', { name: new RegExp(email) });
  await expect(row).toContainText('Pending');
  await expect(row).toContainText('Team lead');
  await expect(row).toContainText('in 7 days');

  await row.getByRole('button', { name: `Revoke the invitation for ${email}` }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Revoke invitation' }).click();
  await expect(row).toContainText('Revoked');
});

test('the permission matrix shows locked rows with the reason', async ({ browser }) => {
  const page = await signedIn(browser, 'admin');
  await page.goto('/admin/permissions');
  const matrix = page.getByRole('table', { name: 'Permission matrix' });
  const publish = matrix.getByRole('row', { name: 'Publish deployments' });
  await expect(publish).toContainText('Locked');
  await expect(publish).toContainText('Admin only');
  await expect(publish.getByRole('switch')).toHaveCount(0);
  await expect(matrix.getByRole('row', { name: 'Counter-approve (checker)' })).toContainText(
    'Separation of duties',
  );
  // Delegable capabilities are switches for staff and team leads.
  await expect(matrix.getByRole('switch', { name: 'Staff: Reassign tickets' })).toHaveAttribute(
    'aria-checked',
    'false',
  );
  await expect(matrix.getByRole('switch', { name: 'Team lead: Reassign tickets' })).toBeEnabled();
});

test('staff do not see the admin area', async ({ browser }) => {
  const page = await signedIn(browser, 'staff');
  await page.goto('/inbox');
  const nav = page.getByRole('navigation', { name: 'Main' });
  await expect(nav.getByRole('link', { name: 'Inbox' })).toBeVisible();
  await expect(nav.getByText('Administration')).toHaveCount(0);
  await expect(nav.getByRole('link', { name: /Deployments|Evals|Members|Permissions/ })).toHaveCount(0);
  // A deep link explains instead of showing a broken page.
  await page.goto('/admin/members');
  await expect(page.getByText('You can’t view members')).toBeVisible();
});
