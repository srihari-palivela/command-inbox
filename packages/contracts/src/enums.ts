import { z } from 'zod';

/** Membership role. Mirrors the "Who may do what" matrix columns (AI agents are not a login role). */
export const Role = z.enum(['staff', 'lead', 'admin']);
export type Role = z.infer<typeof Role>;

/** The three handling lanes. A = Auto, B = Draft, C = You (manual). */
export const Lane = z.enum(['auto', 'draft', 'manual']);
export type Lane = z.infer<typeof Lane>;

export const TicketStatus = z.enum([
  'triaging',
  'awaiting_approval',
  'executing',
  'with_human',
  'waiting_customer',
  'resolved',
  'closed',
]);
export type TicketStatus = z.infer<typeof TicketStatus>;

export const Priority = z.enum(['P1', 'P2', 'P3', 'P4']);
export type Priority = z.infer<typeof Priority>;

export const Segment = z.enum(['Retail', 'Corporate', 'SME', 'NRI']);
export type Segment = z.infer<typeof Segment>;

/** Deadline pressure, derived from due date and budget. */
export const SlaTone = z.enum(['on_track', 'due_soon', 'almost_late', 'late', 'paused', 'closed']);
export type SlaTone = z.infer<typeof SlaTone>;

export const OwnerKind = z.enum(['ai', 'user', 'unassigned']);
export type OwnerKind = z.infer<typeof OwnerKind>;

/** Approval chain required for an action instance. */
export const ApprovalChain = z.enum(['auto', 'single', 'single_undo', 'dual']);
export type ApprovalChain = z.infer<typeof ApprovalChain>;

export const ApprovalMode = z.enum(['auto', 'single', 'dual']);
export type ApprovalMode = z.infer<typeof ApprovalMode>;

export const ActionState = z.enum([
  'drafted',
  'awaiting_checker',
  'scheduled',
  'executing',
  'executed',
  'failed',
  'cancelled',
  'rejected',
]);
export type ActionState = z.infer<typeof ActionState>;

export const DraftState = z.enum(['draft', 'scheduled', 'sent', 'recalled', 'discarded']);
export type DraftState = z.infer<typeof DraftState>;

export const CommentKind = z.enum(['note', 'public', 'call', 'system']);
export type CommentKind = z.infer<typeof CommentKind>;

export const SpanStatus = z.enum(['ok', 'flag', 'stop']);
export type SpanStatus = z.infer<typeof SpanStatus>;

export const Availability = z.enum(['available', 'busy', 'away']);
export type Availability = z.infer<typeof Availability>;

/** 0 none · 1 can read · 2 can resolve · 3 can approve */
export const ClearanceLevel = z.number().int().min(0).max(3);
export type ClearanceLevel = 0 | 1 | 2 | 3;

export const AgentTemplate = z.enum(['bucketing', 'extraction', 'drafting', 'summarisation', 'policy_guard']);
export type AgentTemplate = z.infer<typeof AgentTemplate>;

export const AgentState = z.enum(['observe', 'live', 'paused']);
export type AgentState = z.infer<typeof AgentState>;

export const FeedbackKind = z.enum(['override', 'edit', 'rejection']);
export type FeedbackKind = z.infer<typeof FeedbackKind>;

export const FixKind = z.enum(['prompt', 'context']);
export type FixKind = z.infer<typeof FixKind>;

export const RejectReason = z.enum(['wrong_type', 'bad_field', 'tone', 'needs_human']);
export type RejectReason = z.infer<typeof RejectReason>;

export const BoardState = z.enum(['live', 'triage_only', 'observe', 'paused']);
export type BoardState = z.infer<typeof BoardState>;

export const MailProvider = z.enum(['microsoft', 'google', 'imap', 'dev']);
export type MailProvider = z.infer<typeof MailProvider>;

export const MailboxState = z.enum(['streaming', 'triage_only', 'observe', 'connecting', 'error']);
export type MailboxState = z.infer<typeof MailboxState>;

export const Health = z.enum(['ok', 'warn', 'bad']);
export type Health = z.infer<typeof Health>;

export const GapSeverity = z.enum(['blocking', 'stale', 'unowned', 'resolved']);
export type GapSeverity = z.infer<typeof GapSeverity>;

export const NotificationKind = z.enum(['message', 'learning', 'alert']);
export type NotificationKind = z.infer<typeof NotificationKind>;

export const KpiMetric = z.enum(['fr', 'tat', 'accept', 'reopen', 'auto', 'csat', 'cost', 'awo']);
export type KpiMetric = z.infer<typeof KpiMetric>;

export const ConnectorState = z.enum(['connected', 'read_only', 'suggest_only', 'error']);
export type ConnectorState = z.infer<typeof ConnectorState>;

export const KnowledgeKind = z.enum(['SharePoint', 'Confluence', 'Google Drive', 'S3', 'Upload']);
export type KnowledgeKind = z.infer<typeof KnowledgeKind>;

/** Risk cell key: `${moneyMoves ? 1 : 0}-${reversible ? 0 : 1}` — row = money, column = reversibility. */
export const RiskCell = z.enum(['0-0', '0-1', '1-0', '1-1']);
export type RiskCell = z.infer<typeof RiskCell>;

/** 0 suggest only · 1 suggest + approve · 2 auto-execute */
export const DialLevel = z.number().int().min(0).max(2);

/** Capabilities checked by the server; the UI reads them from /v1/me. */
export const Capability = z.enum([
  'ticket.work',
  'ticket.reply',
  'ticket.assign',
  'ticket.override_up',
  'action.approve_maker',
  'action.approve_checker',
  'people.edit_clearance',
  'people.auto_assign',
  'insights.view',
  'kpi.manage',
  'learning.send',
  'setup.view',
  'setup.edit',
  'autonomy.change',
  'rules.edit',
  'audit.verify',
  'deployment.view',
  'deployment.edit',
  'deployment.publish',
  'evals.view',
  'evals.run',
  'members.manage',
  'rbac.manage',
  'workspace.manage',
]);
export type Capability = z.infer<typeof Capability>;

// ── Tenant administration ─────────────────────────────────────────────────────
/** A deployment version's rollout state. Shadow runs beside the live version and is never acted on. */
export const DeploymentVersionState = z.enum(['draft', 'shadow', 'canary', 'published', 'retired']);
export type DeploymentVersionState = z.infer<typeof DeploymentVersionState>;

export const EvalRunState = z.enum(['queued', 'running', 'passed', 'failed', 'error']);
export type EvalRunState = z.infer<typeof EvalRunState>;

/** Calibration (temperature, conformal threshold) is fitted on `calibration`; gates are scored on `test`. */
export const EvalSplit = z.enum(['calibration', 'test']);
export type EvalSplit = z.infer<typeof EvalSplit>;

export const InvitationState = z.enum(['pending', 'accepted', 'revoked', 'expired']);
export type InvitationState = z.infer<typeof InvitationState>;

export const PolicyEffect = z.enum(['allow', 'deny']);
export type PolicyEffect = z.infer<typeof PolicyEffect>;
