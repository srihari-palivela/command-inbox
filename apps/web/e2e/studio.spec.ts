import { expect, test, type Page } from '@playwright/test';
import { apiAs, signedIn, toast } from './helpers';

// The studio and the admin's taxonomy: each test touches only what it creates (a draft it discards, a
// dataset, a team) so it stays independent of the other specs.

interface Deployment {
  id: string;
  key: string;
  draftVersionId: string | null;
}

async function freshDraft(page: Page): Promise<{ dep: Deployment; draftId: string }> {
  const api = await apiAs(page);
  const dep = (await api.get<Deployment[]>('/v1/deployments')).find((x) => x.key === 'default')!;
  if (dep.draftVersionId) {
    expect(
      (await api.post(`/v1/deployments/${dep.id}/versions/${dep.draftVersionId}/withdraw`)).ok(),
    ).toBeTruthy();
  }
  const res = await api.post(`/v1/deployments/${dep.id}/versions`, {});
  expect(res.ok()).toBeTruthy();
  return { dep, draftId: ((await res.json()) as { id: string }).id };
}

test('the studio: node agents, then the test bench runs the draft beside the live version', async ({
  browser,
}) => {
  const page = await signedIn(browser, 'admin');
  const { dep, draftId } = await freshDraft(page);
  await page.goto(`/admin/deployments/${dep.id}?version=${draftId}&section=agents`);

  const drafter = page.getByRole('region', { name: /Reply drafter agent|Reply Drafter agent/ });
  await expect(drafter).toBeVisible();
  await drafter.getByLabel('Sign-off').fill('Kind regards,\nCustomer Care');
  await page.getByRole('button', { name: 'Save draft' }).click();
  await expect(toast(page)).toContainText('saved');

  await page
    .getByLabel("Customer's email")
    .fill('Please send me a copy of my account statement for the last quarter. My number is 9876543210.');
  await page.getByRole('button', { name: /^Run v\d+$/ }).click();
  const results = page.locator('section[aria-label$=" result"]');
  await expect(results).toHaveCount(2);
  await expect(results.first()).toContainText('Lane');
  await expect(results.first()).not.toContainText('9876543210'); // masked: [PHONE_1]
  await expect(results.first()).toContainText('[PHONE_1]');

  // Leave the deployment as it was.
  const api = await apiAs(page);
  expect((await api.post(`/v1/deployments/${dep.id}/versions/${draftId}/withdraw`)).ok()).toBeTruthy();
});

test('labelling real mail adds masked cases to a dataset', async ({ browser }) => {
  const page = await signedIn(browser, 'admin');
  const api = await apiAs(page);
  const dep = (await api.get<Deployment[]>('/v1/deployments')).find((x) => x.key === 'default')!;
  const res = await api.post('/v1/evals/datasets', { deploymentId: dep.id, name: `Real mail ${Date.now()}` });
  expect(res.ok()).toBeTruthy();
  const ds = (await res.json()) as { id: string };

  await page.goto(`/admin/evals/datasets/${ds.id}`);
  await page.getByRole('button', { name: 'Label real mail' }).click();
  const dialog = page.getByRole('dialog', { name: 'Label real mail' });
  await expect(dialog).toContainText('Personal data is masked');
  const category = dialog.getByLabel('What is it?');
  await category.selectOption({ index: 1 });
  await dialog.getByLabel('What should happen?').selectOption('draft');
  await dialog.getByRole('button', { name: 'Save label and next' }).click();
  await expect(dialog).toContainText('1 labelled here');
  await dialog.getByRole('button', { name: 'Close' }).click();
  await expect(page.getByRole('main')).toContainText(/labelled|real-mail|1/);
});

test('an admin adds a team and sees reply-time targets and model providers', async ({ browser }) => {
  const page = await signedIn(browser, 'admin');
  const name = `Trade desk ${Date.now()}`;
  await page.goto('/setup/ownership');
  await page.getByRole('button', { name: '+ Add team' }).click();
  const dialog = page.getByRole('dialog', { name: 'Add a team' });
  await dialog.getByLabel('Name').fill(name);
  await dialog.getByRole('button', { name: 'Save' }).click();
  await expect(toast(page)).toContainText('Team added');
  const row = page.getByRole('table', { name: 'Teams' }).getByRole('row', { name: new RegExp(name) });
  await expect(row).toBeVisible();
  await expect(page.getByRole('table', { name: 'Reply-time targets' })).toBeVisible();

  // Clean up: the new team has no history, so it can be deleted.
  await row.getByRole('button', { name: 'Delete' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click();
  await expect(row).toHaveCount(0);

  await page.goto('/admin/organisation');
  await expect(page.getByRole('table', { name: 'Model providers' })).toContainText('Anthropic');
  await expect(page.getByRole('table', { name: 'Model providers' })).toContainText('OpenAI');
});
