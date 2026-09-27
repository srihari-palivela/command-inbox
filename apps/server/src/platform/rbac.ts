import type { Capability, Role } from '@ci/contracts';
import { and, eq } from 'drizzle-orm';
import type { Tx } from '../db/client.js';
import { clearances } from '../db/schema.js';
import type { Ctx } from './context.js';
import { forbidden } from './errors.js';

/**
 * Policy-as-code. This table is the single source for both enforcement and the "Who may do what"
 * screen. It is deliberately not editable at runtime: an admin must not be able to switch off
 * maker–checker from a settings page.
 */
const STAFF: Capability[] = ['ticket.work', 'ticket.reply', 'action.approve_maker'];
const LEAD: Capability[] = [
  ...STAFF,
  'ticket.assign',
  'ticket.override_up',
  'action.approve_checker',
  'people.edit_clearance',
  'people.auto_assign',
  'insights.view',
  'kpi.manage',
  'learning.send',
];
const ADMIN: Capability[] = [
  ...LEAD,
  'setup.view',
  'setup.edit',
  'autonomy.change',
  'rules.edit',
  'audit.verify',
];

export const ROLE_CAPABILITIES: Record<Role, ReadonlySet<Capability>> = {
  staff: new Set(STAFF),
  lead: new Set(LEAD),
  admin: new Set(ADMIN),
};

export const ROLE_LABEL: Record<Role, string> = { staff: 'Staff', lead: 'Team lead', admin: 'Admin' };

export function can(ctx: Pick<Ctx, 'capabilities'>, cap: Capability): boolean {
  return ctx.capabilities.has(cap);
}

export function requireCap(ctx: Pick<Ctx, 'capabilities' | 'role'>, cap: Capability, doing: string): void {
  if (!ctx.capabilities.has(cap)) {
    throw forbidden(`${ROLE_LABEL[ctx.role]} cannot ${doing}.`, 'capability_required');
  }
}

export const CLEARANCE = { none: 0, read: 1, resolve: 2, approve: 3 } as const;
export const CLEARANCE_LABEL = ['None', 'Can read', 'Can resolve', 'Can approve'] as const;

export async function clearanceOf(
  tx: Tx,
  orgId: string,
  userId: string,
  departmentId: string | null,
): Promise<number> {
  if (!departmentId) return 0;
  const [row] = await tx
    .select({ level: clearances.level })
    .from(clearances)
    .where(
      and(
        eq(clearances.orgId, orgId),
        eq(clearances.userId, userId),
        eq(clearances.departmentId, departmentId),
      ),
    );
  return row?.level ?? 0;
}

export async function requireClearance(
  tx: Tx,
  ctx: Ctx,
  departmentId: string | null,
  min: number,
  doing: string,
): Promise<void> {
  const level = await clearanceOf(tx, ctx.orgId, ctx.user.id, departmentId);
  if (level < min) {
    throw forbidden(
      `You need "${CLEARANCE_LABEL[min]}" clearance for this team to ${doing}. You have "${CLEARANCE_LABEL[level]}".`,
      'clearance_required',
    );
  }
}

/** Display form of the capability matrix (Rules & policies → Who may do what). */
export const POLICY_MATRIX: {
  cols: string[];
  rows: { capability: string; values: ('yes' | 'no' | 'appr' | 'cell' | 'auto')[] }[];
} = {
  cols: ['AI agents', 'Staff', 'Team lead', 'Admin · Risk'],
  rows: [
    { capability: 'Suggest a draft or a filled action', values: ['yes', 'yes', 'yes', 'yes'] },
    { capability: 'Send a reply to a customer', values: ['no', 'yes', 'yes', 'yes'] },
    { capability: 'Execute · can be undone, no money', values: ['cell', 'yes', 'yes', 'yes'] },
    { capability: 'Execute · money moves', values: ['no', 'appr', 'appr', 'appr'] },
    { capability: 'Approve as checker (second pair of eyes)', values: ['no', 'no', 'yes', 'yes'] },
    { capability: 'Reassign tickets & clearance', values: ['auto', 'no', 'yes', 'yes'] },
    { capability: 'Change the autonomy dial', values: ['no', 'no', 'no', 'yes'] },
    { capability: 'Edit agent prompts & rules', values: ['no', 'no', 'no', 'yes'] },
  ],
};
