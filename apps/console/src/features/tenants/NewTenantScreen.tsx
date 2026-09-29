/** Create a bank tenant: who it is, where it runs, what it may use, and who its first admin is. */
import { Button, Card, cx, Input, Page, PageHeader, TextArea } from '@web/ui';
import { useState, type InputHTMLAttributes } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useCan, useCreateTenant } from '../../lib/queries';
import {
  EMPTY_FORM,
  validate,
  type FieldErrors,
  type FieldKey,
  type TenantForm,
} from '../../lib/tenant-form';
import { css as s, FormField, ProblemAlert, Select } from '../../ui/bits';

const REGIONS = [
  'uk-south',
  'eu-west',
  'eu-central',
  'in-west',
  'in-south',
  'me-central',
  'ap-southeast',
  'us-east',
];
const LOCALES = ['en-GB', 'en-IN', 'en-US', 'en-AE', 'en-SG', 'en-IE'];
const CURRENCIES = ['GBP', 'INR', 'USD', 'EUR', 'AED', 'SGD', 'CHF', 'HKD'];
const TIME_ZONES = [
  'Europe/London',
  'Europe/Dublin',
  'Europe/Berlin',
  'Asia/Kolkata',
  'Asia/Dubai',
  'Asia/Singapore',
  'Asia/Hong_Kong',
  'America/New_York',
];

const slugify = (name: string) =>
  name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^[^a-z]+/, '')
    .slice(0, 40)
    .replace(/-+$/, '');

export default function NewTenantScreen() {
  const can = useCan();
  const navigate = useNavigate();
  const create = useCreateTenant();
  const [form, setForm] = useState<TenantForm>(EMPTY_FORM);
  const [slugTouched, setSlugTouched] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [attempted, setAttempted] = useState(false);

  const set = <K extends FieldKey>(key: K, value: TenantForm[K]) => {
    const next = { ...form, [key]: value };
    if (key === 'name' && !slugTouched) next.slug = slugify(String(value));
    setForm(next);
    if (attempted) {
      const r = validate(next);
      setErrors(r.ok ? {} : r.errors);
    }
  };

  const submit = () => {
    setAttempted(true);
    const r = validate(form);
    if (!r.ok) {
      setErrors(r.errors);
      const first = Object.keys(r.errors)[0];
      if (first) document.getElementById(`t-${first}`)?.focus();
      return;
    }
    setErrors({});
    create.mutate(r.body, { onSuccess: (t) => navigate(`/tenants/${t.id}`) });
  };

  /** Props shared by every text input bound to a form field. */
  const bind = (key: Exclude<FieldKey, 'provision' | 'plan'>): InputHTMLAttributes<HTMLInputElement> => ({
    id: `t-${key}`,
    value: form[key],
    onChange: (e) => set(key, e.target.value),
    'aria-invalid': errors[key] ? true : undefined,
    'aria-describedby': errors[key] ? `t-${key}-error` : `t-${key}-hint`,
    className: cx(errors[key] && s.invalid),
  });

  if (!can('tenants.create'))
    return (
      <Page narrow>
        <PageHeader title="New tenant" subtitle="Your role can look at tenants but not create them." />
      </Page>
    );

  return (
    <Page narrow>
      <div className={s.crumbs}>
        <Link to="/tenants">Tenants</Link>
        <span aria-hidden>/</span>
        <span>New tenant</span>
      </div>
      <div className="rise">
        <PageHeader
          title="New tenant"
          subtitle="The bank gets its own data key, identity realm and starter deployment. Its first admin receives an invitation once provisioning finishes."
        />
      </div>
      <form
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Card title="The bank" className={s.card}>
          <div className={s.fields}>
            <FormField
              id="t-name"
              label="Display name"
              error={errors.name}
              hint="What people see in the product."
            >
              <Input {...bind('name')} placeholder="Harbour Bank" autoFocus maxLength={120} />
            </FormField>
            <FormField id="t-legalName" label="Legal name" error={errors.legalName} hint="As registered.">
              <Input {...bind('legalName')} placeholder="Harbour Bank plc" maxLength={200} />
            </FormField>
            <FormField
              id="t-slug"
              label="Slug"
              error={errors.slug}
              hint="Lower-case letters, digits and hyphens. It can't be changed later."
            >
              <Input
                {...bind('slug')}
                className={cx('mono', errors.slug && s.invalid)}
                onChange={(e) => {
                  setSlugTouched(true);
                  set('slug', e.target.value);
                }}
                placeholder="harbour-bank"
                maxLength={40}
              />
            </FormField>
            <FormField id="t-plan" label="Plan">
              <Select
                id="t-plan"
                value={form.plan}
                onChange={(e) => set('plan', e.target.value as TenantForm['plan'])}
                style={{ height: 34 }}
              >
                <option value="Pilot">Pilot</option>
                <option value="Standard">Standard</option>
                <option value="Enterprise">Enterprise</option>
              </Select>
            </FormField>
          </div>
        </Card>

        <Card title="Where it runs" className={s.card}>
          <div className={s.fields}>
            <FormField id="t-region" label="Region" error={errors.region} hint="Where its data is hosted.">
              <Input {...bind('region')} list="regions" className={cx('mono', errors.region && s.invalid)} />
            </FormField>
            <FormField
              id="t-dataResidency"
              label="Data residency"
              error={errors.dataResidency}
              hint="Optional. Any residency commitment in the contract."
            >
              <Input {...bind('dataResidency')} placeholder="UK only" maxLength={200} />
            </FormField>
            <FormField id="t-locale" label="Locale" error={errors.locale} hint="Number and date formats.">
              <Input {...bind('locale')} list="locales" className={cx('mono', errors.locale && s.invalid)} />
            </FormField>
            <FormField id="t-currency" label="Currency" error={errors.currency} hint="ISO 4217 code.">
              <Input
                {...bind('currency')}
                list="currencies"
                maxLength={3}
                className={cx('mono', errors.currency && s.invalid)}
              />
            </FormField>
            <FormField
              id="t-timeZone"
              label="Time zone"
              error={errors.timeZone}
              hint="Decides the bank's working day."
            >
              <Input
                {...bind('timeZone')}
                list="time-zones"
                className={cx('mono', errors.timeZone && s.invalid)}
              />
            </FormField>
            <FormField
              id="t-supportEmail"
              label="Support email"
              error={errors.supportEmail}
              hint="Optional. Where the bank's staff ask for help."
            >
              <Input {...bind('supportEmail')} type="email" placeholder="it-support@harbour.example" />
            </FormField>
            <FormField
              id="t-emailDomains"
              label="Email domains"
              error={errors.emailDomains}
              hint="The bank's own domains, separated by commas or new lines. Staff sign in with addresses on these."
              wide
            >
              <TextArea
                id="t-emailDomains"
                value={form.emailDomains}
                onChange={(e) => set('emailDomains', e.target.value)}
                aria-invalid={errors.emailDomains ? true : undefined}
                aria-describedby={errors.emailDomains ? 't-emailDomains-error' : 't-emailDomains-hint'}
                className={cx('mono', errors.emailDomains && s.invalid)}
                placeholder={'harbour.example\nmail.harbour.example'}
                rows={3}
              />
            </FormField>
          </div>
        </Card>

        <Card title="Limits" meta="What the tenant may use each month" className={s.card}>
          <div className={s.fields}>
            <FormField id="t-mailboxes" label="Mailboxes" error={errors.mailboxes} hint="1 to 50.">
              <Input {...bind('mailboxes')} inputMode="numeric" />
            </FormField>
            <FormField id="t-seats" label="Seats" error={errors.seats} hint="People who can sign in.">
              <Input {...bind('seats')} inputMode="numeric" />
            </FormField>
            <FormField
              id="t-monthlyMail"
              label="Monthly mail"
              error={errors.monthlyMail}
              hint="Messages a month."
            >
              <Input {...bind('monthlyMail')} inputMode="numeric" />
            </FormField>
            <FormField
              id="t-modelSpendCap"
              label={`Model spend cap (${form.currency.trim().toUpperCase() || 'major units'})`}
              error={errors.modelSpendCap}
              hint="A month, in whole currency units."
            >
              <Input {...bind('modelSpendCap')} inputMode="decimal" />
            </FormField>
            <FormField
              id="t-storageGb"
              label="Storage (GB)"
              error={errors.storageGb}
              hint="Mail and documents."
            >
              <Input {...bind('storageGb')} inputMode="numeric" />
            </FormField>
          </div>
        </Card>

        <Card title="First admin" meta="Invited by email; they set up the rest" className={s.card}>
          <div className={s.fields}>
            <FormField id="t-adminName" label="Name" error={errors.adminName}>
              <Input {...bind('adminName')} placeholder="Hana Ito" maxLength={120} />
            </FormField>
            <FormField
              id="t-adminEmail"
              label="Work email"
              error={errors.adminEmail}
              hint="Usually on one of the bank's domains."
            >
              <Input {...bind('adminEmail')} type="email" placeholder="hana.ito@harbour.example" />
            </FormField>
          </div>
        </Card>

        <div className={s.stack}>
          <ProblemAlert error={create.error} />
          <div className={s.formFoot}>
            <label className={s.check}>
              <input
                type="checkbox"
                checked={form.provision}
                onChange={(e) => set('provision', e.target.checked)}
              />
              <span>
                Start provisioning now
                <span className={s.sub} style={{ display: 'block' }}>
                  Otherwise the tenant stays a draft until you provision it.
                </span>
              </span>
            </label>
            <Button type="button" variant="ghost" onClick={() => navigate('/tenants')}>
              Cancel
            </Button>
            <Button type="submit" variant="dark" loading={create.isPending}>
              Create tenant
            </Button>
          </div>
        </div>
      </form>

      <datalist id="regions">
        {REGIONS.map((v) => (
          <option key={v} value={v} />
        ))}
      </datalist>
      <datalist id="locales">
        {LOCALES.map((v) => (
          <option key={v} value={v} />
        ))}
      </datalist>
      <datalist id="currencies">
        {CURRENCIES.map((v) => (
          <option key={v} value={v} />
        ))}
      </datalist>
      <datalist id="time-zones">
        {TIME_ZONES.map((v) => (
          <option key={v} value={v} />
        ))}
      </datalist>
    </Page>
  );
}
