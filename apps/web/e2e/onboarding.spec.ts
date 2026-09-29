import { expect, test } from '@playwright/test';

/**
 * The first day of a new bank: an operator creates the tenant in the platform API, provisioning runs, the
 * bank's first admin opens the emailed link, accepts, and lands on the onboarding checklist.
 */
test('an operator creates a bank and its first admin accepts the emailed invitation', async ({
  browser,
  request,
}) => {
  const login = await request.post('/v1/platform/auth/dev-login', {
    data: { email: 'ops@platform.example' },
  });
  expect(login.ok()).toBeTruthy();
  const { csrfToken } = (await login.json()) as { csrfToken: string };
  const slug = `e2e-${Date.now().toString(36)}`;
  const adminEmail = `admin@${slug}.test`;
  const created = await request.post('/v1/platform/tenants', {
    headers: { 'x-csrf-token': csrfToken },
    data: {
      slug,
      name: 'Harbour Bank',
      legalName: 'Harbour Bank plc',
      region: 'uk-south',
      plan: 'Pilot',
      locale: 'en-GB',
      currency: 'GBP',
      timeZone: 'Europe/London',
      emailDomains: [`${slug}.test`],
      admin: { name: 'Hana Admin', email: adminEmail },
    },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const { id } = (await created.json()) as { id: string };

  await expect
    .poll(
      async () =>
        ((await (await request.get(`/v1/platform/tenants/${id}`)).json()) as { status: string }).status,
      {
        timeout: 20_000,
      },
    )
    .toBe('provisioned');

  let link = '';
  await expect
    .poll(async () => {
      const mail = (await (await request.get('/v1/dev/mailbox')).json()) as { to: string; text: string }[];
      link =
        mail.find((m) => m.to === adminEmail)?.text.match(/https?:\/\/\S+\/accept\?token=\S+/)?.[0] ?? '';
      return link;
    })
    .not.toBe('');

  const page = await (await browser.newContext()).newPage();
  await page.goto(new URL(link).pathname + new URL(link).search);
  await expect(page.getByRole('heading', { name: 'Join Harbour Bank' })).toBeVisible();
  await expect(page.getByText(adminEmail)).toBeVisible();
  await page.getByRole('button', { name: 'Accept and sign in' }).click();

  await expect(page).toHaveURL(/\/onboarding$/);
  await expect(page.getByRole('heading', { name: 'Getting started' })).toBeVisible();
  await expect(page.getByRole('listitem', { name: /People: In progress|People: Not started/ })).toBeVisible();

  // The link works once.
  const again = await (await browser.newContext()).newPage();
  await again.goto(new URL(link).pathname + new URL(link).search);
  await expect(again.getByRole('heading', { name: 'This invitation was accepted' })).toBeVisible();
});
