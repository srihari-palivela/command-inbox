/**
 * The platform console's wire contract (responses). The API's Pydantic models are generated from this file
 * (apps/api/scripts/gen_dto.py → schemas/platform_dto.py). Request bodies are in platform-api.ts.
 */
import type { Role } from './enums.js';

export type TenantStatus =
  | 'draft'
  | 'provisioning'
  | 'provisioned'
  | 'onboarding'
  | 'shadow'
  | 'assisted'
  | 'live'
  | 'suspended'
  | 'archived';

export type OperatorRole = 'platform_owner' | 'operator' | 'support';

export type PlatformCapability =
  | 'tenants.view'
  | 'tenants.create'
  | 'tenants.lifecycle'
  | 'tenants.invite'
  | 'audit.view'
  | 'operators.manage';

export type TenantAction = 'provision' | 'suspend' | 'resume' | 'archive' | 'reinvite';

export type StepState = 'pending' | 'running' | 'done' | 'skipped' | 'failed';

export interface OperatorDTO {
  id: string;
  email: string;
  name: string;
  role: OperatorRole;
  capabilities: PlatformCapability[];
}

export interface PlatformMeDTO {
  operator: OperatorDTO;
  csrfToken: string;
}

export interface PlatformAuthConfigDTO {
  sso: boolean;
  /** Development only: operators who can sign in without SSO. Empty in production. */
  devOperators: { email: string; name: string; role: OperatorRole }[];
}

export interface TenantLimitsDTO {
  mailboxes: number;
  seats: number;
  monthlyMail: number;
  modelSpendCapMinor: number;
  storageGb: number;
}

export interface TenantSummaryDTO {
  id: string;
  slug: string;
  name: string;
  legalName: string;
  status: TenantStatus;
  region: string;
  plan: string;
  createdAt: string;
  statusChangedAt: string;
  members: number;
  admins: number;
  mailboxes: number;
  /** Provisioning progress while it runs or when it failed; null once done. */
  provisioning: StepState | null;
}

export interface ProvisioningStepDTO {
  step: string;
  label: string;
  state: StepState;
  attempts: number;
  detail: string;
  updatedAt: string;
}

export interface TenantInvitationDTO {
  id: string;
  email: string;
  name: string;
  role: Role;
  state: 'pending' | 'accepted' | 'expired' | 'revoked';
  createdAt: string;
  expiresAt: string;
  sentAt: string | null;
  sendCount: number;
  invitedBy: string;
}

export interface TenantKeyDTO {
  version: number;
  state: 'active' | 'retired' | 'destroyed';
  kekRef: string;
  createdAt: string;
}

export interface PlatformAuditEventDTO {
  seq: number;
  at: string;
  operatorEmail: string;
  action: string;
  tenantId: string | null;
  summary: string;
  data: Record<string, unknown>;
}

export interface TenantDetailDTO extends TenantSummaryDTO {
  locale: string;
  currency: string;
  timeZone: string;
  dataResidency: string;
  supportEmail: string;
  emailDomains: string[];
  ssoIdpAlias: string | null;
  limits: TenantLimitsDTO;
  keys: TenantKeyDTO[];
  steps: ProvisioningStepDTO[];
  invitations: TenantInvitationDTO[];
  audit: PlatformAuditEventDTO[];
  /** What this operator may do to the tenant in its current status. */
  actions: TenantAction[];
}

export interface PlatformAuditVerifyDTO {
  ok: boolean;
  events: number;
  brokenAt: number | null;
}

/** Public: what an invitee sees before signing in (no secrets, no other tenants). */
export interface InvitationPreviewDTO {
  tenantName: string;
  email: string;
  name: string;
  role: Role;
  invitedBy: string;
  expiresAt: string;
  state: 'pending' | 'accepted' | 'expired' | 'revoked';
  /** How the invitee signs in: through SSO, or (development) directly. */
  signIn: 'sso' | 'direct';
  /** Before the bank's SSO is connected, a first-time sign-in (password + authenticator) can be created. */
  canBootstrap: boolean;
}
