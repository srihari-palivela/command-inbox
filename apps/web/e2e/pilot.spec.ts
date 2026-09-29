import { expect, test } from '@playwright/test';
import { signedIn, toast } from './helpers';

// The pilot screen on the live demo workspace. It never changes the stage (other specs send replies), and the
// incident it logs is closed again.

test('the pilot: stage, KPIs, the comparison with people, and the incident log', async ({ browser }) => {
  const page = await signedIn(browser, 'admin');
  await page.goto('/admin/pilot');
  await expect(page.getByRole('heading', { name: 'Pilot', exact: true })).toBeVisible();

  const stages = page.getByRole('list', { name: 'Pilot stages' });
  await expect(stages.locator('[aria-current="step"]')).toContainText('Live');
  await expect(page.getByText('The pilot is complete')).toBeVisible();
  await expect(
    page.getByText('Replies are sent from Command Inbox after a person approves them'),
  ).toBeVisible();
  await expect(page.getByText('Pilot KPIs')).toBeVisible();
  await expect(page.getByRole('table', { name: 'Lanes: AI against final' })).toBeVisible();

  const title = `Slow draft on a card query ${Date.now()}`;
  await page.getByRole('button', { name: 'Log an incident' }).click();
  await page.getByLabel('Title').fill(title);
  await page.getByRole('button', { name: 'Log it' }).click();
  await expect(toast(page)).toContainText('Incident logged');
  const row = page.getByRole('table', { name: 'Incidents' }).getByRole('row', { name: new RegExp(title) });
  await expect(row).toContainText('P3');

  await row.getByRole('button', { name: 'Close' }).click();
  await page.getByLabel('Resolution').fill('Knowledge article updated');
  await page.getByRole('button', { name: 'Close incident' }).click();
  await expect(toast(page)).toContainText('Incident closed');
  await expect(row).toContainText('Closed');
});
