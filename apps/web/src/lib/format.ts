import { formatMinutes } from '@ci/contracts';

export { formatMinutes };

/** How the signed-in tenant reads figures: BCP 47 locale, ISO 4217 currency, IANA time zone. */
export interface TenantLocale {
  locale: string;
  currency: string;
  timeZone: string;
}

const FALLBACK: TenantLocale = { locale: 'en-GB', currency: 'USD', timeZone: 'UTC' };
let tenant: TenantLocale = FALLBACK;

/** Set once the workspace is known (and again on a workspace switch); every formatter below reads it. */
export function setTenantLocale(next: TenantLocale): void {
  tenant = { locale: next.locale, currency: next.currency, timeZone: next.timeZone };
}

export function tenantLocale(): TenantLocale {
  return tenant;
}

/** A count in the tenant's digit grouping (12,34,567 for en-IN; 1,234,567 for en-GB). */
export function num(n: number): string {
  return n.toLocaleString(tenant.locale);
}

function currencyDigits(currency: string): number {
  return (
    new Intl.NumberFormat('en', { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2
  );
}

/** An amount held in minor units (paise, cents), in the tenant's currency. */
export function money(minor: number, digits?: number): string {
  const exp = currencyDigits(tenant.currency);
  return new Intl.NumberFormat(tenant.locale, {
    style: 'currency',
    currency: tenant.currency,
    minimumFractionDigits: digits ?? exp,
    maximumFractionDigits: digits ?? exp,
  }).format(minor / 10 ** exp);
}

/** A large amount, compact in the tenant's own notation (₹2.14L for en-IN, £214K for en-GB). */
export function moneyCompact(minor: number): string {
  const exp = currencyDigits(tenant.currency);
  return new Intl.NumberFormat(tenant.locale, {
    style: 'currency',
    currency: tenant.currency,
    notation: 'compact',
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(minor / 10 ** exp);
}

/** Calendar day of an instant in the tenant's time zone, as a sortable key. */
function dayKey(d: Date): string {
  return d.toLocaleDateString('en-CA', { timeZone: tenant.timeZone });
}

/** "09:14" today, "yesterday 17:40", or "18 Jun 2026". */
export function clockTime(iso: string, now = new Date()): string {
  const d = new Date(iso);
  const hm = d.toLocaleTimeString(tenant.locale, {
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZone: tenant.timeZone,
  });
  const diff = Math.round((Date.parse(dayKey(now)) - Date.parse(dayKey(d))) / 86_400_000);
  if (diff === 0) return hm;
  if (diff === 1) return `yesterday ${hm}`;
  return shortDate(iso);
}

export function ago(iso: string, now = new Date()): string {
  const m = Math.round((now.getTime() - new Date(iso).getTime()) / 60_000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  if (m < 1440) return `${Math.round(m / 60)}h ago`;
  if (m < 2880) return 'yesterday';
  return `${Math.round(m / 1440)}d ago`;
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(tenant.locale, {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    timeZone: tenant.timeZone,
  });
}

export function minutesLeftLabel(mins: number | null): string {
  if (mins === null) return '—';
  if (mins < 0) return `${formatMinutes(-mins)} late`;
  return formatMinutes(mins);
}

/** Full date and time for a tooltip, in the tenant's locale and time zone. */
export function fullDateTime(iso: string): string {
  return new Date(iso).toLocaleString(tenant.locale, { timeZone: tenant.timeZone });
}

/** "September 2026". */
export function monthYear(iso: string): string {
  return new Date(iso).toLocaleDateString(tenant.locale, {
    month: 'long',
    year: 'numeric',
    timeZone: tenant.timeZone,
  });
}
