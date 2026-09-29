import { expect, test } from '@playwright/test';
import { signedIn, toast } from './helpers';

test('an uploaded document is read, approved and then found by retrieval', async ({ browser }) => {
  const page = await signedIn(browser, 'admin');
  const stamp = Date.now();
  const title = `Locker rent waiver ${stamp}`;
  await page.goto('/setup/knowledge');

  await page.getByRole('button', { name: 'Upload documents' }).click();
  const dialog = page.getByRole('dialog', { name: 'Upload a document' });
  await dialog.locator('input[type=file]').setInputFiles({
    name: `locker-${stamp}.md`,
    mimeType: 'text/markdown',
    buffer: Buffer.from(
      `# Locker rent waiver\n\nLocker rent zebrafinch${stamp} is waived for the first year for customers aged ` +
        'over seventy. The waiver is applied automatically at the branch on renewal.\n',
    ),
  });
  await dialog.getByLabel('Title').fill(title);
  await dialog.getByRole('button', { name: 'Upload' }).click();
  await expect(toast(page).getByText(/not citable until approved/)).toBeVisible();

  const row = page.getByRole('row').filter({ hasText: title });
  await expect(row.getByText(/Ready · \d+ passages?/)).toBeVisible({ timeout: 20_000 });
  await expect(row.getByText('Awaiting approval')).toBeVisible();

  // Not citable before approval.
  const search = page.getByLabel('Customer question');
  await search.fill(`zebrafinch${stamp} locker rent`);
  await page.getByRole('button', { name: 'Find sources' }).click();
  await expect(page.getByText(/No approved source answers this/)).toBeVisible();

  await row.click();
  const drawer = page.getByRole('dialog', { name: title });
  await expect(drawer.getByText(/waived for the first year/)).toBeVisible();
  await drawer.getByRole('button', { name: 'Approve for citation' }).click();
  await expect(toast(page).getByText(/may now cite/)).toBeVisible();
  await drawer.getByRole('button', { name: 'Close' }).click();
  await expect(row.getByText('Approved · citable')).toBeVisible();

  await page.getByRole('button', { name: 'Find sources' }).click();
  await expect(page.getByRole('button', { name: new RegExp(`1\\. ${title}`) })).toBeVisible();
});
