/**
 * The new-tenant form works on strings (what the inputs hold); these helpers turn it into a
 * `CreateTenantBody` and turn the schema's issues back into one plain sentence per field.
 */
import { CreateTenantBody } from '@ci/contracts';

export interface TenantForm {
  slug: string;
  name: string;
  legalName: string;
  region: string;
  dataResidency: string;
  plan: 'Pilot' | 'Standard' | 'Enterprise';
  locale: string;
  currency: string;
  timeZone: string;
  supportEmail: string;
  emailDomains: string;
  mailboxes: string;
  seats: string;
  monthlyMail: string;
  /** Major units (e.g. pounds); sent as minor units. */
  modelSpendCap: string;
  storageGb: string;
  adminName: string;
  adminEmail: string;
  provision: boolean;
}

export const EMPTY_FORM: TenantForm = {
  slug: '',
  name: '',
  legalName: '',
  region: 'uk-south',
  dataResidency: '',
  plan: 'Pilot',
  locale: 'en-GB',
  currency: 'GBP',
  timeZone: 'Europe/London',
  supportEmail: '',
  emailDomains: '',
  mailboxes: '1',
  seats: '25',
  monthlyMail: '20000',
  modelSpendCap: '50000',
  storageGb: '50',
  adminName: '',
  adminEmail: '',
  provision: true,
};

/** "bank.example, @Mail.Bank.example\nbank.example" → ["bank.example", "mail.bank.example"]. */
export function parseDomains(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split(/[\s,;]+/)) {
    const d = raw.trim().replace(/^@/, '').toLowerCase();
    if (d && !out.includes(d)) out.push(d);
  }
  return out;
}

const plainNumber = (text: string) => {
  const t = text.replace(/[\s,_]/g, '');
  return t === '' ? NaN : Number(t);
};

/** "50,000.5" → 5000050. Anything that isn't a number stays NaN for the schema to reject. */
export const majorToMinor = (text: string) => Math.round(plainNumber(text) * 100);

export const minorToMajor = (minor: number) => minor / 100;

export function toBody(f: TenantForm): unknown {
  return {
    slug: f.slug,
    name: f.name,
    legalName: f.legalName,
    region: f.region,
    dataResidency: f.dataResidency,
    plan: f.plan,
    locale: f.locale,
    currency: f.currency.trim().toUpperCase(),
    timeZone: f.timeZone,
    supportEmail: f.supportEmail.trim() || undefined,
    emailDomains: parseDomains(f.emailDomains),
    limits: {
      mailboxes: plainNumber(f.mailboxes),
      seats: plainNumber(f.seats),
      monthlyMail: plainNumber(f.monthlyMail),
      modelSpendCapMinor: majorToMinor(f.modelSpendCap),
      storageGb: plainNumber(f.storageGb),
    },
    admin: { name: f.adminName, email: f.adminEmail },
    provision: f.provision,
  };
}

export type FieldKey = keyof TenantForm;

const MESSAGE: Partial<Record<FieldKey, string>> = {
  slug: 'Lower-case letters, digits and hyphens; 3–40 characters, starting with a letter.',
  name: 'Enter the name people will see.',
  legalName: 'Enter the registered legal name.',
  region: 'Enter the hosting region.',
  dataResidency: 'Keep it under 200 characters.',
  locale: 'Use a locale such as en-GB or en-IN.',
  currency: 'Use a three-letter ISO code such as GBP.',
  timeZone: 'Enter an IANA time zone such as Europe/London.',
  supportEmail: 'Enter a valid email address, or leave it empty.',
  emailDomains: 'List 1 to 20 domains such as bank.example.',
  mailboxes: 'A whole number from 1 to 50.',
  seats: 'A whole number from 1 to 5,000.',
  monthlyMail: 'A whole number from 100 to 10,000,000.',
  modelSpendCap: 'An amount from 0 to 10,000,000.',
  storageGb: 'A whole number from 1 to 10,000.',
  adminName: 'Enter the first admin’s name.',
  adminEmail: 'Enter the first admin’s work email.',
};

const FIELD_OF_PATH: Record<string, FieldKey> = {
  'limits.mailboxes': 'mailboxes',
  'limits.seats': 'seats',
  'limits.monthlyMail': 'monthlyMail',
  'limits.modelSpendCapMinor': 'modelSpendCap',
  'limits.storageGb': 'storageGb',
  'admin.name': 'adminName',
  'admin.email': 'adminEmail',
};

export type FieldErrors = Partial<Record<FieldKey, string>>;

/** Validate with the shared schema; on failure, one message per form field. */
export function validate(
  f: TenantForm,
): { ok: true; body: CreateTenantBody } | { ok: false; errors: FieldErrors } {
  const parsed = CreateTenantBody.safeParse(toBody(f));
  if (parsed.success) return { ok: true, body: parsed.data };
  const errors: FieldErrors = {};
  for (const issue of parsed.error.issues) {
    const [head, second] = issue.path.map(String);
    if (!head) continue;
    const field = FIELD_OF_PATH[`${head}.${second}`] ?? (head as FieldKey);
    errors[field] ??= MESSAGE[field] ?? issue.message;
  }
  return { ok: false, errors };
}
