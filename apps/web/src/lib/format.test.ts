import { afterEach, describe, expect, it } from 'vitest';
import { clockTime, money, moneyCompact, num, setTenantLocale, shortDate } from './format';

const INDIA = { locale: 'en-IN', currency: 'INR', timeZone: 'Asia/Kolkata' };
const UK = { locale: 'en-GB', currency: 'GBP', timeZone: 'Europe/London' };

describe('tenant-aware formatting', () => {
  afterEach(() => setTenantLocale(INDIA));

  it('groups digits and formats money in the tenant locale and currency', () => {
    setTenantLocale(INDIA);
    expect(num(1234567)).toBe('12,34,567');
    expect(money(4512)).toBe('₹45.12');
    expect(moneyCompact(21_400_000)).toBe('₹2.14L');
    setTenantLocale(UK);
    expect(num(1234567)).toBe('1,234,567');
    expect(money(4512)).toBe('£45.12');
    expect(moneyCompact(21_400_000)).toBe('£214k');
  });

  it('reads minor units by the currency exponent', () => {
    setTenantLocale({ locale: 'en-GB', currency: 'JPY', timeZone: 'Asia/Tokyo' });
    expect(money(4512)).toBe('JP¥4,512');
  });

  it('decides "today" and the clock in the tenant time zone, not the browser', () => {
    // 20:00 UTC on 28 Sep is already 29 Sep 01:30 in Kolkata.
    const now = new Date('2026-09-28T20:30:00Z');
    setTenantLocale(INDIA);
    expect(clockTime('2026-09-28T20:00:00Z', now)).toBe('01:30');
    expect(clockTime('2026-09-28T10:00:00Z', now)).toBe('yesterday 15:30');
    setTenantLocale(UK);
    expect(clockTime('2026-09-28T10:00:00Z', now)).toBe('11:00');
    expect(shortDate('2026-01-05T12:00:00Z')).toBe('05 Jan 2026');
  });
});
