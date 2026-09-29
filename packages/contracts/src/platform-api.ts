/** Request schemas for the platform console (`/v1/platform/*`) and invitation acceptance. */
import { z } from 'zod';

const trimmed = (max: number) => z.string().trim().min(1).max(max);
const email = z.string().trim().toLowerCase().email();

export const PlatformDevLoginBody = z.object({ email });
export type PlatformDevLoginBody = z.infer<typeof PlatformDevLoginBody>;

export const TenantLimitsBody = z.object({
  mailboxes: z.number().int().min(1).max(50).default(1),
  seats: z.number().int().min(1).max(5000).default(25),
  monthlyMail: z.number().int().min(100).max(10_000_000).default(20_000),
  modelSpendCapMinor: z.number().int().min(0).max(1_000_000_000).default(5_000_000),
  storageGb: z.number().int().min(1).max(10_000).default(50),
  /** API requests per minute per API process; protects the stack from a runaway client. */
  apiPerMinute: z.number().int().min(60).max(100_000).default(3000),
});
export type TenantLimitsBody = z.infer<typeof TenantLimitsBody>;

export const CreateTenantBody = z.object({
  slug: z
    .string()
    .trim()
    .regex(/^[a-z][a-z0-9-]{1,38}[a-z0-9]$/),
  name: trimmed(120),
  legalName: trimmed(200),
  region: trimmed(40),
  dataResidency: z.string().trim().max(200).default(''),
  plan: z.enum(['Pilot', 'Standard', 'Enterprise']),
  locale: z
    .string()
    .trim()
    .regex(/^[a-z]{2,3}(-[A-Z]{2})?$/),
  currency: z
    .string()
    .trim()
    .regex(/^[A-Z]{3}$/),
  timeZone: trimmed(64),
  supportEmail: email.optional(),
  emailDomains: z
    .array(
      z
        .string()
        .trim()
        .toLowerCase()
        .regex(/^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$/),
    )
    .min(1)
    .max(20),
  limits: TenantLimitsBody.prefault({}),
  admin: z.object({ name: trimmed(120), email }),
  /** Start provisioning straight away (default), or leave the tenant as a draft. */
  provision: z.boolean().default(true),
});
export type CreateTenantBody = z.infer<typeof CreateTenantBody>;

export const TenantReasonBody = z.object({ reason: trimmed(500) });
export type TenantReasonBody = z.infer<typeof TenantReasonBody>;

export const ReinviteBody = z.object({ name: trimmed(120), email });
export type ReinviteBody = z.infer<typeof ReinviteBody>;

export const AcceptInvitationBody = z.object({ token: z.string().min(20).max(200) });
export type AcceptInvitationBody = z.infer<typeof AcceptInvitationBody>;
