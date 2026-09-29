/** Platform values → words and colours. Every status shows a word as well as a colour. */
import type { OperatorRole, StepState, TenantKeyDTO, TenantStatus, TenantInvitationDTO } from '@ci/contracts';

export interface Tone {
  label: string;
  fg: string;
  bg: string;
  line: string;
}

const ACCENT = { fg: 'var(--accent)', bg: 'var(--accent-bg)', line: 'var(--accent-line)' };
const OK = { fg: 'var(--ok)', bg: 'var(--ok-bg)', line: 'var(--ok-line)' };
const WARN = { fg: 'var(--warn)', bg: 'var(--warn-bg)', line: 'var(--warn-line)' };
const BAD = { fg: 'var(--bad-text)', bg: 'var(--bad-bg)', line: 'var(--bad-line)' };
const IDLE = { fg: 'var(--text-2)', bg: 'var(--surface-3)', line: 'var(--line)' };
const GONE = { fg: 'var(--muted)', bg: 'var(--surface-4)', line: 'var(--line-soft)' };

export const TENANT_STATUS: Record<TenantStatus, Tone> = {
  draft: { label: 'Draft', ...IDLE },
  provisioning: { label: 'Provisioning', ...ACCENT },
  provisioned: { label: 'Provisioned', ...ACCENT },
  onboarding: { label: 'Onboarding', ...ACCENT },
  shadow: { label: 'Shadow', ...WARN },
  assisted: { label: 'Assisted', ...OK },
  live: { label: 'Live', ...OK },
  suspended: { label: 'Suspended', ...BAD },
  archived: { label: 'Archived', ...GONE },
};

export const STEP_STATE: Record<StepState, Tone & { icon: string }> = {
  pending: { label: 'Waiting', icon: '○', ...IDLE },
  running: { label: 'Running', icon: '◐', ...ACCENT },
  done: { label: 'Done', icon: '✓', ...OK },
  skipped: { label: 'Skipped', icon: '–', ...GONE },
  failed: { label: 'Failed', icon: '!', ...BAD },
};

export const INVITATION_STATE: Record<TenantInvitationDTO['state'], Tone> = {
  pending: { label: 'Pending', ...ACCENT },
  accepted: { label: 'Accepted', ...OK },
  expired: { label: 'Expired', ...WARN },
  revoked: { label: 'Revoked', ...GONE },
};

export const KEY_STATE: Record<TenantKeyDTO['state'], Tone> = {
  active: { label: 'Active', ...OK },
  retired: { label: 'Retired', ...IDLE },
  destroyed: { label: 'Destroyed', ...GONE },
};

export const OPERATOR_ROLE: Record<OperatorRole, string> = {
  platform_owner: 'Platform owner',
  operator: 'Operator',
  support: 'Support',
};

export const TEAM_ROLE = { staff: 'Staff', lead: 'Team lead', admin: 'Admin' } as const;

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
const dateOnly = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' });

export const when = (iso: string | null | undefined) => (iso ? dateTime.format(new Date(iso)) : '—');
export const day = (iso: string) => dateOnly.format(new Date(iso));
export const count = (n: number) => n.toLocaleString();
export const initials = (name: string) =>
  name
    .split(/[ .]+/)
    .filter(Boolean)
    .map((p) => p[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();
