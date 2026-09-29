import { describe, expect, it } from 'vitest';
import { EMPTY_FORM, majorToMinor, minorToMajor, parseDomains, validate } from './tenant-form';

describe('parseDomains', () => {
  it('splits on commas, spaces and new lines, normalises and drops duplicates', () => {
    expect(parseDomains('bank.example, @Mail.Bank.example\nbank.example ;  ')).toEqual([
      'bank.example',
      'mail.bank.example',
    ]);
    expect(parseDomains('  \n ')).toEqual([]);
  });
});

describe('spend cap units', () => {
  it('sends major units as minor units and back', () => {
    expect(majorToMinor('50,000')).toBe(5_000_000);
    expect(majorToMinor('12.345')).toBe(1235);
    expect(majorToMinor('')).toBeNaN();
    expect(majorToMinor('lots')).toBeNaN();
    expect(minorToMajor(5_000_000)).toBe(50_000);
  });
});

describe('validate', () => {
  const filled = {
    ...EMPTY_FORM,
    slug: 'harbour-bank',
    name: 'Harbour Bank',
    legalName: 'Harbour Bank plc',
    emailDomains: 'harbour.example',
    adminName: 'Hana Admin',
    adminEmail: 'Hana@Harbour.example',
  };

  it('builds the request body from a complete form', () => {
    const r = validate(filled);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.body.limits.modelSpendCapMinor).toBe(5_000_000);
    expect(r.body.admin.email).toBe('hana@harbour.example');
    expect(r.body.supportEmail).toBeUndefined();
  });

  it('reports one plain message per form field', () => {
    const r = validate({ ...filled, slug: 'Bad Slug', seats: '0', emailDomains: '', adminEmail: 'nope' });
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(Object.keys(r.errors).sort()).toEqual(['adminEmail', 'emailDomains', 'seats', 'slug']);
  });
});
