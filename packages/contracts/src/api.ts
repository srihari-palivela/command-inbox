/**
 * Request schemas. Every mutating endpoint validates its body with one of these, and the web client
 * uses the inferred types, so the two cannot drift.
 */
import { z } from 'zod';
import {
  AgentTemplate,
  Capability,
  ClearanceLevel,
  CommentKind,
  DialLevel,
  EvalSplit,
  KnowledgeKind,
  KpiMetric,
  Lane,
  MailProvider,
  NotificationKind,
  PolicyEffect,
  Priority,
  RejectReason,
  RiskCell,
  Role,
  TicketStatus,
} from './enums.js';

const trimmed = (max: number) => z.string().trim().min(1).max(max);

// ── Auth & session ────────────────────────────────────────────────────────────
export const LoginBody = z.object({ email: z.string().trim().toLowerCase().email() });
export type LoginBody = z.infer<typeof LoginBody>;

export const SwitchOrgBody = z.object({ orgId: z.string().uuid() });
export type SwitchOrgBody = z.infer<typeof SwitchOrgBody>;

export const SettingsBody = z.object({
  prefs: z.record(z.string(), z.boolean()).optional(),
  signature: z.string().max(2000).optional(),
});
export type SettingsBody = z.infer<typeof SettingsBody>;

// ── Tickets ───────────────────────────────────────────────────────────────────
export const TicketFilters = z.object({
  board: z.string().optional(),
  status: z.enum(['triage', 'approval', 'executing', 'human', 'customer', 'resolved']).optional(),
  lane: Lane.optional(),
  team: z.string().optional(),
  owner: z.string().optional(), // 'mine' | 'ai' | 'unassigned' | <userId>
  due: z.enum(['risk', 'open', 'closed']).optional(),
  conf: z.enum(['low', 'high']).optional(),
  pri: Priority.optional(),
  bucket: z.string().optional(),
  q: z.string().max(200).optional(),
});
export type TicketFilters = z.infer<typeof TicketFilters>;
export type FilterKey = keyof TicketFilters;

export const InboxQuery = z.object({
  filter: z.enum(['all', 'auto', 'draft', 'manual', 'late']).default('all'),
});
export type InboxQuery = z.infer<typeof InboxQuery>;

export const NlFilterBody = z.object({ query: trimmed(300) });
export type NlFilterBody = z.infer<typeof NlFilterBody>;

export const TransitionBody = z.object({ to: TicketStatus });
export type TransitionBody = z.infer<typeof TransitionBody>;

export const AssignBody = z.object({ userId: z.string().uuid().nullable().optional() });
export type AssignBody = z.infer<typeof AssignBody>;

export const OverrideLaneBody = z.object({ lane: Lane });
export type OverrideLaneBody = z.infer<typeof OverrideLaneBody>;

export const CommentBody = z.object({ kind: CommentKind.exclude(['system', 'call']), body: trimmed(5000) });
export type CommentBody = z.infer<typeof CommentBody>;

export const SubtaskBody = z.object({ done: z.boolean() });
export type SubtaskBody = z.infer<typeof SubtaskBody>;

export const WatchBody = z.object({ watching: z.boolean() });
export type WatchBody = z.infer<typeof WatchBody>;

export const MergeBody = z.object({ intoNumber: z.string().regex(/^QRY-\d+$/) });
export type MergeBody = z.infer<typeof MergeBody>;

// ── Approval gateway ──────────────────────────────────────────────────────────
export const ApproveBody = z.object({
  /** Whether the approver opened the evidence (reasoning, fields or draft) before approving. */
  openedEvidence: z.boolean().default(false),
});
export type ApproveBody = z.infer<typeof ApproveBody>;

export const RejectBody = z.object({ reason: RejectReason });
export type RejectBody = z.infer<typeof RejectBody>;

export const BatchApproveBody = z.object({ ticketIds: z.array(z.string().uuid()).min(1).max(50) });
export type BatchApproveBody = z.infer<typeof BatchApproveBody>;

export const DraftBody = z.object({ body: z.string().max(20000) });
export type DraftBody = z.infer<typeof DraftBody>;

export const ReplyBody = z.object({ body: trimmed(20000) });
export type ReplyBody = z.infer<typeof ReplyBody>;

export const ActionFieldsBody = z.object({
  fields: z.array(z.object({ label: z.string(), value: z.string().max(500) })).min(1),
});
export type ActionFieldsBody = z.infer<typeof ActionFieldsBody>;

// ── Calls ─────────────────────────────────────────────────────────────────────
export const SaveCallBody = z.object({ discard: z.boolean().default(false) });
export type SaveCallBody = z.infer<typeof SaveCallBody>;

// ── People ────────────────────────────────────────────────────────────────────
export const ClearanceBody = z.object({
  userId: z.string().uuid(),
  departmentId: z.string().uuid(),
  level: ClearanceLevel,
});
export type ClearanceBody = z.infer<typeof ClearanceBody>;

// ── Insights ──────────────────────────────────────────────────────────────────
export const KpiBody = z.object({
  name: trimmed(80),
  metric: KpiMetric,
  viz: z.enum(['bars', 'number']),
  scope: z.enum(['team', 'me']),
  target: z.number().finite(),
});
export type KpiBody = z.infer<typeof KpiBody>;

export const AskBody = z.object({ question: trimmed(500) });
export type AskBody = z.infer<typeof AskBody>;

export const SearchQuery = z.object({ q: z.string().trim().max(200).default('') });
export type SearchQuery = z.infer<typeof SearchQuery>;

// ── Learning ──────────────────────────────────────────────────────────────────
export const NotificationBody = z.object({
  title: trimmed(160),
  kind: NotificationKind,
  body: z.string().max(2000).optional(),
  courseId: z.string().uuid().optional(),
});
export type NotificationBody = z.infer<typeof NotificationBody>;

export const MarkReadBody = z.object({
  ids: z.array(z.string().uuid()).optional(),
  all: z.boolean().optional(),
});
export type MarkReadBody = z.infer<typeof MarkReadBody>;

export const CourseCompleteBody = z.object({ answers: z.array(z.number().int().min(0)).max(50) });
export type CourseCompleteBody = z.infer<typeof CourseCompleteBody>;

// ── Setup: boards, agents, actions, rules, knowledge ─────────────────────────
export const BoardBody = z.object({
  name: trimmed(80),
  provider: MailProvider,
  mailbox: z.string().trim().toLowerCase().email(),
  departmentId: z.string().uuid().optional(),
});
export type BoardBody = z.infer<typeof BoardBody>;

export const AgentBody = z.object({
  name: trimmed(60),
  template: AgentTemplate,
  model: z.string().min(3).max(80),
  prompt: trimmed(8000),
  boardIds: z.array(z.string().uuid()).min(1),
});
export type AgentBody = z.infer<typeof AgentBody>;

export const AgentVersionBody = z.object({
  prompt: trimmed(8000),
  model: z.string().min(3).max(80).optional(),
});
export type AgentVersionBody = z.infer<typeof AgentVersionBody>;

export const AgentBoardsBody = z.object({ boardIds: z.array(z.string().uuid()) });
export type AgentBoardsBody = z.infer<typeof AgentBoardsBody>;

export const DialBody = z.object({ cell: RiskCell, level: DialLevel });
export type DialBody = z.infer<typeof DialBody>;

export const ActionTemplateBody = z.object({
  name: trimmed(120),
  system: trimmed(60),
  cell: RiskCell,
});
export type ActionTemplateBody = z.infer<typeof ActionTemplateBody>;

export const RuleToggleBody = z.object({ enabled: z.boolean() });
export type RuleToggleBody = z.infer<typeof RuleToggleBody>;

export const DecideBody = z.object({ approve: z.boolean() });
export type DecideBody = z.infer<typeof DecideBody>;

export const KnowledgeSourceBody = z.object({ kind: KnowledgeKind, name: trimmed(80).optional() });
export type KnowledgeSourceBody = z.infer<typeof KnowledgeSourceBody>;

export const OwnerBody = z.object({ userId: z.string().uuid() });
export type OwnerBody = z.infer<typeof OwnerBody>;

// ── Intake (dev / webhook) ────────────────────────────────────────────────────
export const IntakeMessageBody = z.object({
  mailbox: z.string().trim().toLowerCase().email(),
  fromName: trimmed(120),
  fromEmail: z.string().trim().toLowerCase().email(),
  subject: trimmed(300),
  body: trimmed(50000),
  messageId: z.string().max(300).optional(),
  inReplyTo: z.string().max(300).optional(),
});
export type IntakeMessageBody = z.infer<typeof IntakeMessageBody>;

// ── Deployments ───────────────────────────────────────────────────────────────
const key = z.string().regex(/^[a-z][a-z0-9_]{0,47}$/);

export const CreateDeploymentBody = z.object({
  key,
  name: trimmed(120),
  description: z.string().max(600).optional(),
  /** Copy the configuration of this deployment's active version (default: the tenant's default deployment). */
  copyFrom: z.string().uuid().optional(),
});
export type CreateDeploymentBody = z.infer<typeof CreateDeploymentBody>;

export const NewDraftBody = z.object({ notes: z.string().max(2000).optional() });
export type NewDraftBody = z.infer<typeof NewDraftBody>;

/** The config is validated server-side (DeploymentConfig); a 400 names the failing path in `detail`. */
export const DraftConfigBody = z.object({
  config: z.record(z.string(), z.unknown()),
  notes: z.string().max(2000).optional(),
});
export type DraftConfigBody = z.infer<typeof DraftConfigBody>;

export const BindMailboxesBody = z.object({ mailboxIds: z.array(z.string().uuid()).max(200) });
export type BindMailboxesBody = z.infer<typeof BindMailboxesBody>;

export const PromoteBody = z.object({
  to: z.enum(['shadow', 'canary', 'published']),
  canaryPercent: z.number().int().min(1).max(99).optional(),
  /** Required (and audited) when the tenant's only admin publishes their own edit. */
  acknowledgeSingleAdmin: z.boolean().default(false),
});
export type PromoteBody = z.infer<typeof PromoteBody>;

export const RollbackBody = z.object({ versionId: z.string().uuid().optional() });
export type RollbackBody = z.infer<typeof RollbackBody>;

// ── Evals ─────────────────────────────────────────────────────────────────────
export const EvalDatasetBody = z.object({
  deploymentId: z.string().uuid(),
  name: trimmed(120),
  description: z.string().max(600).optional(),
});
export type EvalDatasetBody = z.infer<typeof EvalDatasetBody>;

export const EvalDatasetPatchBody = z.object({
  name: trimmed(120).optional(),
  description: z.string().max(600).optional(),
});
export type EvalDatasetPatchBody = z.infer<typeof EvalDatasetPatchBody>;

export const EvalCaseBody = z.object({
  input: z.object({
    subject: z.string().max(300),
    body: z.string().max(20000),
    fromEmail: z.string().trim().toLowerCase().email().optional(),
  }),
  expected: z.object({ category: key, hardStop: z.boolean().default(false) }),
  split: EvalSplit.default('test'),
  tags: z.array(z.string().max(40)).max(20).default([]),
});
export type EvalCaseBody = z.infer<typeof EvalCaseBody>;

export const EvalCasesBody = z.object({ cases: z.array(EvalCaseBody).min(1).max(500) });
export type EvalCasesBody = z.infer<typeof EvalCasesBody>;

export const StartEvalRunBody = z.object({
  deploymentVersionId: z.string().uuid(),
  datasetId: z.string().uuid(),
  /** Pin System 2 to one provider to compare providers; such a run does not count for publishing. */
  provider: z.enum(['anthropic', 'openai']).nullable().optional(),
});
export type StartEvalRunBody = z.infer<typeof StartEvalRunBody>;

// ── Members, invitations, permissions ─────────────────────────────────────────
export const MemberRoleBody = z.object({ role: Role });
export type MemberRoleBody = z.infer<typeof MemberRoleBody>;

export const InvitationBody = z.object({
  email: z.string().trim().toLowerCase().email(),
  role: Role,
  expiresInDays: z.number().int().min(1).max(30).default(7),
});
export type InvitationBody = z.infer<typeof InvitationBody>;

/** `effect: null` removes the override (back to the product baseline). */
export const PermissionOverridesBody = z.object({
  overrides: z
    .array(z.object({ role: Role, capability: Capability, effect: PolicyEffect.nullable() }))
    .min(1)
    .max(100),
});
export type PermissionOverridesBody = z.infer<typeof PermissionOverridesBody>;

// ── Workspace (organisation profile and single sign-on) ──────────────────────
export const WorkspaceProfileBody = z.object({
  legalName: trimmed(200),
  supportEmail: z.string().trim().toLowerCase().email().or(z.literal('')),
  locale: z
    .string()
    .trim()
    .regex(/^[a-z]{2,3}(-[A-Z]{2})?$/),
  currency: z
    .string()
    .trim()
    .regex(/^[A-Z]{3}$/),
  timeZone: trimmed(64),
});
export type WorkspaceProfileBody = z.infer<typeof WorkspaceProfileBody>;

export const WorkspaceSsoBody = z.object({
  provider: z.enum(['entra', 'google']),
  directoryId: trimmed(120),
  clientId: trimmed(200),
  /** Omit to keep the stored secret. */
  clientSecret: z.string().min(8).max(500).optional(),
});
export type WorkspaceSsoBody = z.infer<typeof WorkspaceSsoBody>;

// ── Mailbox connections ─────────────────────────────────────────────────────────
export const CreateMailboxBody = z.object({
  address: z.string().trim().toLowerCase().email(),
  provider: z.enum(['microsoft', 'google']),
  teamLabel: z.string().trim().max(80).default(''),
});
export type CreateMailboxBody = z.infer<typeof CreateMailboxBody>;

export const MailboxSendingBody = z.object({ enabled: z.boolean() });
export type MailboxSendingBody = z.infer<typeof MailboxSendingBody>;

// ── Knowledge documents ─────────────────────────────────────────────────────────
export const KnowledgeReviewBody = z.object({ reason: z.string().trim().max(500).default('') });
export type KnowledgeReviewBody = z.infer<typeof KnowledgeReviewBody>;

export const KnowledgeSearchBody = z.object({
  query: z.string().trim().min(2).max(2000),
  departmentId: z.string().uuid().nullable().optional(),
});
export type KnowledgeSearchBody = z.infer<typeof KnowledgeSearchBody>;

// ── Model policy ────────────────────────────────────────────────────────────────
export const ModelPolicyBody = z.object({
  allowedProviders: z.array(z.enum(['anthropic', 'openai'])).max(2),
  monthlyBudgetMinor: z.number().int().min(0).max(1_000_000_000_000).nullable(),
});
export type ModelPolicyBody = z.infer<typeof ModelPolicyBody>;
