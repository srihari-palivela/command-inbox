import { formatMinutes } from '@ci/contracts';

export { formatMinutes };

/** "09:14" today, "yesterday 17:40", or "18 Jun 2026". */
export function clockTime(iso: string, now = new Date()): string {
  const d = new Date(iso);
  const hm = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86_400_000);
  if (diff === 0) return hm;
  if (diff === 1) return `yesterday ${hm}`;
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
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
  return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

export function minutesLeftLabel(mins: number | null): string {
  if (mins === null) return '—';
  if (mins < 0) return `${formatMinutes(-mins)} late`;
  return formatMinutes(mins);
}

export const inr = (minor: number, digits = 2) => '₹' + (minor / 100).toFixed(digits);

/** ₹2,14,000 → "₹2.14L" (Indian lakh notation, as bank staff read it). */
export function lakhs(minor: number): string {
  const rupees = minor / 100;
  if (rupees >= 1_00_000) return `₹${(rupees / 1_00_000).toFixed(2)}L`;
  return '₹' + rupees.toLocaleString('en-IN', { maximumFractionDigits: 0 });
}
