/**
 * Response shapes. The server builds these; the web renders them. Presentation (colours, copy
 * variants) stays in the web app — these carry domain facts only.
 */
import type {
  ActionState,
  AgentState,
  AgentTemplate,
  ApprovalChain,
  ApprovalMode,
  Availability,
  BoardState,
  Capability,
  CommentKind,
  ConnectorState,
  DeploymentVersionState,
  DraftState,
  EvalRunState,
  EvalSplit,
  FeedbackKind,
  FixKind,
  GapSeverity,
  Health,
  InvitationState,
  KpiMetric,
  Lane,
  MailboxState,
  MailProvider,
  NotificationKind,
  OwnerKind,
  PolicyEffect,
  Priority,
  RiskCell,
  Role,
  SlaTone,
  SpanStatus,
  TicketStatus,
} from './enums.js';
import type { FilterKey, TicketFilters } from './api.js';

export interface UserRef {
  id: string;
  name: string;
  initials: string;
}

export interface OrgDTO {
  id: string;
  slug: string;
  name: string;
  short: string;
  tint: string;
  bg: string;
  plan: string;
  confidenceBar: number;
  /** BCP 47 locale, ISO 4217 currency and IANA time zone the tenant's people read figures in. */
  locale: string;
  currency: string;
  timeZone: string;
  status: WorkspaceStatus;
}

/** The tenant's lifecycle (the platform console moves it up to onboarding; the bank's go-live steps after). */
export type WorkspaceStatus =
  | 'draft'
  | 'provisioning'
  | 'provisioned'
  | 'onboarding'
  | 'shadow'
  | 'assisted'
  | 'live'
  | 'suspended'
  | 'archived';

export interface WorkspaceProfileDTO {
  name: string;
  legalName: string;
  supportEmail: string;
  locale: string;
  currency: string;
  timeZone: string;
  emailDomains: string[];
  region: string;
  dataResidency: string;
  status: WorkspaceStatus;
  sso: WorkspaceSsoDTO;
}

export interface WorkspaceSsoDTO {
  provider: 'entra' | 'google' | null;
  /** Entra: the directory (tenant) ID. Google: the Workspace primary domain. */
  directoryId: string;
  clientId: string;
  hasSecret: boolean;
  state: 'not_connected' | 'saved' | 'connected' | 'failed';
  detail: string;
  idpAlias: string | null;
  /** The redirect URI to register in the bank's Entra app or Google OAuth client. */
  redirectUri: string | null;
  /** People of this workspace who have signed in through single sign-on. */
  ssoMembers: number;
}

export interface OnboardingStepDTO {
  key: string;
  title: string;
  description: string;
  state: 'not_started' | 'in_progress' | 'done' | 'later';
  detail: string;
  /** Where in the app the admin does this step. */
  to: string | null;
}

export interface OnboardingDTO {
  status: WorkspaceStatus;
  steps: OnboardingStepDTO[];
  done: number;
  total: number;
}

export interface MembershipDTO {
  org: OrgDTO;
  role: Role;
  boards: number;
  people: number;
  unread: number;
}

export interface NavCounts {
  inbox: number;
  tickets: number;
  boards: number;
  alerts: number;
  learning: number;
  agents: number;
  gaps: number;
  /** Risk cells where the AI may act on its own (of 4). */
  autonomousCells: number;
}

export interface SettingsDTO {
  prefs: Record<string, boolean>;
  signature: string;
}

export interface MeDTO {
  user: UserRef & { email: string; title: string; pod: string; joinedAt: string };
  org: OrgDTO;
  role: Role;
  capabilities: Capability[];
  memberships: MembershipDTO[];
  csrfToken: string;
  demoMode: boolean;
  settings: SettingsDTO;
  nav: NavCounts;
  worker: { state: 'live' | 'degraded' | 'paused'; provider: 'claude' | 'openai' | 'heuristic' };
  /** Optional product areas switched on for this installation. */
  features: { telephony: boolean };
}

export interface DemoUserDTO {
  email: string;
  name: string;
  role: Role;
  title: string;
}

export interface OrgChoiceDTO extends OrgDTO {
  role: Role | null;
  boards: number;
  people: number;
}

// ── Tickets ───────────────────────────────────────────────────────────────────
export interface SlaDTO {
  tone: SlaTone;
  minutesLeft: number | null;
  budgetMinutes: number;
  dueAt: string | null;
}

export interface TicketSummaryDTO {
  id: string;
  number: string;
  subject: string;
  fromName: string;
  fromInitials: string;
  bucket: string;
  departmentId: string | null;
  department: string;
  lane: Lane;
  originalLane: Lane;
  laneNote: string;
  status: TicketStatus;
  priority: Priority;
  segment: string;
  confidence: number;
  receivedAt: string;
  sla: SlaDTO;
  assignee: UserRef | null;
  ownerKind: OwnerKind;
  nextMove: string;
  boardId: string | null;
  boardName: string;
  threadCount: number;
  pendingGate: 'maker' | 'checker' | 'send' | null;
}

export interface EvidenceDTO {
  tag: string;
  quote: string;
  why: string;
}

export interface TriageDTO {
  reasoning: string;
  evidence: EvidenceDTO[];
  confidence: number;
  latencyMs: number;
  provider: string;
  classifiedAt: string;
}

export interface ActionFieldDTO {
  label: string;
  value: string;
  source: string;
  inferred: boolean;
}

export interface ActionDTO {
  id: string;
  templateCode: string;
  name: string;
  system: string;
  endpoint: string;
  reversible: boolean;
  moneyMoves: boolean;
  cell: RiskCell;
  chain: ApprovalChain;
  fields: ActionFieldDTO[];
  validation: string;
  state: ActionState;
  maker: UserRef | null;
  makerAt: string | null;
  checker: UserRef | null;
  checkerAt: string | null;
  executeAfter: string | null;
  executedAt: string | null;
  externalRef: string | null;
  duplicate: { clear: boolean; text: string };
}

export interface CitationDTO {
  n: number;
  docId: string;
  doc: string;
  section: string;
  verifiedAt: string;
  owner: string;
}

export interface DraftDTO {
  id: string;
  subject: string;
  to: string;
  originalBody: string;
  currentBody: string;
  citations: CitationDTO[];
  flagged: string[];
  state: DraftState;
  sendAfter: string | null;
  sentAt: string | null;
}

export interface BriefDTO {
  why: string;
  summary: string;
  context: { label: string; value: string }[];
  suggestions: { label: string; meta: string }[];
}

export type GateMode = 'action' | 'draft' | 'manual';
export type GateState =
  'open' | 'awaiting_checker' | 'scheduled' | 'executing' | 'done' | 'rejected' | 'taken';

export interface GateDTO {
  mode: GateMode;
  chain: ApprovalChain | null;
  state: GateState;
  maker: UserRef | null;
  checker: UserRef | null;
  /** Who the checker will be if the maker approves now (least-loaded eligible checker). */
  proposedChecker: UserRef | null;
  owner: UserRef | null;
  customerWaitingMinutes: number | null;
  reversible: boolean | null;
  moneyMoves: boolean | null;
  duplicateClear: boolean | null;
  canApprove: boolean;
  blockedReason: string | null;
  canUndo: boolean;
  undoUntil: string | null;
  note: string | null;
}

export interface MessageDTO {
  id: string;
  direction: 'inbound' | 'outbound' | 'note';
  fromName: string;
  fromAddr: string;
  toAddr: string;
  body: string;
  sentAt: string;
}

export interface SubtaskDTO {
  key: string;
  label: string;
  owner: string;
  done: boolean;
}

export interface LogEntryDTO {
  id: string;
  kind: CommentKind;
  authorName: string;
  authorInitials: string;
  body: string;
  at: string;
}

export interface SpanDTO {
  seq: number;
  offsetMs: number;
  agent: string;
  model: string;
  action: string;
  output: string;
  latencyMs: number;
  tokens: number | null;
  costMinor: number | null;
  status: SpanStatus;
}

export interface TraceDTO {
  traceId: string;
  totalMs: number;
  costMinor: number;
  spans: SpanDTO[];
}

export interface LinkDTO {
  kind: string;
  label: string;
  ref: string | null;
}

export interface AttachmentDTO {
  id: string;
  ext: string;
  name: string;
  size: string;
}

export interface PastTicketDTO {
  number: string;
  subject: string;
  at: string;
  outcome: string;
  tone: 'ok' | 'warn' | 'bad';
  sameTopic: boolean;
}

export interface CustomerDTO {
  id: string;
  /** null: an unmatched sender, not yet linked to a customer record. */
  cif: string | null;
  name: string;
  email: string;
  sinceYear: number | null;
  segment: string;
  account: string;
  history: PastTicketDTO[];
}

export interface TicketDetailDTO extends TicketSummaryDTO {
  version: number;
  fromEmail: string;
  mailbox: string;
  category: string;
  subcategory: string;
  product: string;
  regulatoryFlag: string | null;
  reopenCount: number;
  firstReplyAt: string | null;
  resolvedAt: string | null;
  splitProposed: boolean;
  messages: MessageDTO[];
  triage: TriageDTO | null;
  action: ActionDTO | null;
  draft: DraftDTO | null;
  brief: BriefDTO | null;
  gate: GateDTO;
  trace: TraceDTO | null;
  subtasks: SubtaskDTO[];
  log: LogEntryDTO[];
  watchers: UserRef[];
  watching: boolean;
  attachments: AttachmentDTO[];
  links: LinkDTO[];
  customer: CustomerDTO | null;
  loggedMinutes: number;
  allowedTransitions: TicketStatus[];
  permissions: { canWork: boolean; canAssign: boolean; canOverrideUp: boolean; canReply: boolean };
}

export interface FacetCounts {
  [key: string]: Record<string, number>;
}

export interface TicketListDTO {
  items: TicketSummaryDTO[];
  total: number;
  all: number;
  facets: Partial<Record<FilterKey, Record<string, number>>>;
  stats: { open: number; awaitingDecision: number; aiOwned: number; atRisk: number; closedToday: number };
  boards: { id: string; key: string; name: string; source: string; state: BoardState; count: number }[];
  buckets: string[];
  departments: { id: string; name: string }[];
}

export interface NlFilterDTO {
  filters: TicketFilters;
  chips: { key: FilterKey; value: string; text: string }[];
  understood: boolean;
  provider: string;
}

export interface InboxDTO {
  items: TicketSummaryDTO[];
  counts: { all: number; auto: number; draft: number; manual: number; late: number };
}

export interface ActivityDTO {
  id: string;
  text: string;
  meta: string;
  tone: 'ok' | 'stop' | 'flag' | 'info' | 'muted';
  at: string;
  ticketNumber: string | null;
}

export interface ShiftDTO {
  closedToday: number;
  sentAsDraftedPct: number;
  savedMinutes: number;
  missedDeadlines: number;
}

// ── Calls ─────────────────────────────────────────────────────────────────────
export interface CallDTO {
  id: string;
  ticketId: string;
  state: 'dialing' | 'live' | 'wrap' | 'saved' | 'discarded';
  startedAt: string;
  durationSec: number;
  number: string;
  customerName: string;
  customerInitials: string;
  transcript: { who: 'You' | 'Customer'; text: string }[];
  scriptLength: number;
  summary: string | null;
  updates: string[];
  recording: string | null;
}

// ── Setup ─────────────────────────────────────────────────────────────────────
export interface BoardDTO {
  id: string;
  key: string;
  name: string;
  source: string;
  provider: MailProvider;
  volume24h: number;
  open: number;
  autoRatePct: number;
  state: BoardState;
  team: string;
  agents: { id: string; name: string }[];
}

export interface AgentEvalDTO {
  label: string;
  value: string;
  tone: 'ok' | 'warn' | 'neutral';
}

export interface CalibrationBandDTO {
  band: string;
  predicted: number;
  observed: number | null;
  n: number;
}

export interface AgentDTO {
  id: string;
  name: string;
  abbr: string;
  template: AgentTemplate;
  model: string;
  state: AgentState;
  version: number;
  prompt: string;
  evalScore: number | null;
  costPer1kMinor: number;
  boards: { id: string; name: string }[];
  evals: AgentEvalDTO[];
  calibration: CalibrationBandDTO[];
  versions: { version: number; model: string; createdAt: string; createdBy: string; evalStatus: string }[];
}

export interface FeedbackDTO {
  id: string;
  agentName: string;
  ticketNumber: string | null;
  kind: FeedbackKind;
  text: string;
  fix: FixKind;
  status: 'open' | 'queued';
  at: string;
}

export interface AgentTemplateDTO {
  key: AgentTemplate;
  label: string;
  abbr: string;
  what: string;
  prompt: string;
  recommendedModel: string;
}

export interface AgentsOverviewDTO {
  agents: AgentDTO[];
  spendMonthMinor: number;
  feedback: FeedbackDTO[];
  templates: AgentTemplateDTO[];
  models: { id: string; note: string }[];
}

export interface StaffDTO {
  id: string;
  name: string;
  initials: string;
  role: Role;
  title: string;
  pod: string;
  years: number;
  open: number;
  capacity: number;
  availability: Availability;
  checkin: string;
  calendar: string;
  clearances: Record<string, number>;
  isMe: boolean;
}

export interface PeopleDTO {
  staff: StaffDTO[];
  departments: { id: string; name: string }[];
  editable: boolean;
}

export interface AutoAssignResultDTO {
  checked: number;
  moves: {
    ticketNumber: string;
    priority: Priority;
    minutesLeft: number | null;
    to: string | null;
    reason: string;
  }[];
}

export interface KpiTileDTO {
  key: string;
  label: string;
  value: string;
  unit: string;
  trendPct: string;
  trendGood: boolean;
  spark: number[];
  digest: string;
  tone: 'neutral' | 'bad';
}

export interface QueryTypeSpeedDTO {
  id: string;
  name: string;
  department: string;
  lane: Lane;
  volume: number;
  baselineHours: number;
  actualHours: number;
  late: number;
  owner: string;
}

export interface AlertDTO {
  id: string;
  sevLabel: string;
  sevKind: 'late' | 'pattern' | 'drift' | 'capacity' | 'health' | 'budget' | 'knowledge';
  bucket: string;
  text: string;
  actionLabel: string;
  owner: string;
  at: string;
  /** Where the records behind it are (system alerts). */
  ref?: string | null;
}

export interface CustomKpiDTO {
  id: string;
  name: string;
  metric: KpiMetric;
  metricLabel: string;
  viz: 'bars' | 'number';
  scope: 'team' | 'me';
  target: number;
  current: number;
  lowerIsBetter: boolean;
  onTrack: boolean;
  spark: number[];
}

export interface PerformanceDTO {
  tiles: KpiTileDTO[];
  queryTypes: QueryTypeSpeedDTO[];
  alerts: AlertDTO[];
  staff: StaffDTO[];
  customKpis: CustomKpiDTO[];
  metrics: { key: KpiMetric; label: string; current: number }[];
  approveWithoutOpenPct: number;
}

export interface ResultsDTO {
  baselineHours: number;
  nowHours: number;
  deltaPct: number;
  capacityMultiple: number;
  days: { label: string; baseline: number; actual: number }[];
  coverage: { lane: Lane; pct: number; volume: number }[];
  pools: { label: string; metric: string; note: string }[];
}

export interface ActionTemplateDTO {
  id: string;
  code: string;
  name: string;
  system: string;
  owner: string;
  approval: ApprovalMode;
  stpPct: number | null;
  volume: number;
  cell: RiskCell;
  state: 'active' | 'suggest_only' | 'pending_review';
}

export interface RiskCellDTO {
  cell: RiskCell;
  title: string;
  count: number;
  dial: number;
  locked: boolean;
  phase: string;
  audit: string;
  gateNote: string;
}

export interface ActionsDTO {
  cells: RiskCellDTO[];
  templates: ActionTemplateDTO[];
  autonomousCells: number;
}

export interface PoliciesDTO {
  bucketRules: { id: string; description: string; target: string; kind: string; hits: string }[];
  priorityRules: {
    id: string;
    key: string;
    description: string;
    target: string;
    hard: boolean;
    enabled: boolean;
    hits: string;
  }[];
  matrix: {
    cols: string[];
    rows: { capability: string; values: ('yes' | 'no' | 'appr' | 'cell' | 'auto')[] }[];
  };
  cycles: { cell: string; chain: string[]; note: string }[];
  proposedRules: {
    id: string;
    text: string;
    ticketNumber: string | null;
    proposedBy: string;
    at: string;
    status: string;
  }[];
}

export interface KnowledgeSourceDTO {
  id: string;
  name: string;
  kind: string;
  abbr: string;
  docs: string;
  approved: string;
  sync: string;
  health: Health;
  note: string;
}

export interface GapDTO {
  id: string;
  number: string;
  severity: GapSeverity;
  question: string;
  detail: string;
  hits: number;
  age: string;
  owner: string;
  state: string;
  cta: string;
}

export interface KnowledgeDTO {
  sources: KnowledgeSourceDTO[];
  readiness: { department: string; pct: number; note: string }[];
  honest: { quotablePct: number; ungroundedSentences: number; openGaps: number };
  gaps: GapDTO[];
}

export interface TaxonomyDTO {
  departments: {
    id: string;
    name: string;
    owner: UserRef | null;
    gapNote: string;
    tone: 'normal' | 'risk';
    queryTypes: { id: string; name: string; lane: Lane; volume: number; live: boolean }[];
  }[];
  owned: number;
  total: number;
  contract: { title: string; text: string }[];
}

export interface MailboxDTO {
  id: string;
  address: string;
  department: string;
  permissions: string[];
  volume24h: number;
  state: MailboxState;
  provider: MailProvider;
}

export interface ConnectorDTO {
  id: string;
  abbr: string;
  name: string;
  scope: string;
  state: ConnectorState;
}

export interface AdminDTO {
  mailboxes: MailboxDTO[];
  connectors: ConnectorDTO[];
  guardrails: string[];
}

// ── Learning ──────────────────────────────────────────────────────────────────
export interface NotificationDTO {
  id: string;
  kind: NotificationKind;
  source: string;
  title: string;
  body: string;
  courseId: string | null;
  urgent: boolean;
  read: boolean;
  at: string;
}

export interface CourseDTO {
  id: string;
  key: string;
  title: string;
  minutes: number;
  cards: { title: string; body: string }[];
  quiz: { q: string; options: string[]; correct: number }[];
  teamCompletionPct: number;
  completedByMe: boolean;
}

export interface LearningDTO {
  notifications: NotificationDTO[];
  unread: number;
  courses: CourseDTO[];
  teamSize: number;
}

// ── Copilot & search ──────────────────────────────────────────────────────────
export interface CopilotAnswerDTO {
  headline: string;
  lines: string[];
  actions: { label: string; filters?: TicketFilters; to?: string }[];
  provider: string;
}

export interface SearchResultDTO {
  tickets: { id: string; number: string; subject: string; lane: Lane }[];
  customers: {
    id: string;
    cif: string | null;
    name: string;
    tickets: number;
    latestTicketId: string | null;
  }[];
  knowledge: { id: string; title: string; section: string; status: string }[];
  policies: { id: string; text: string; kind: string }[];
}

export interface SessionDTO {
  id: string;
  device: string;
  location: string;
  lastSeenAt: string;
  current: boolean;
}

export interface AuditVerifyDTO {
  ok: boolean;
  events: number;
  brokenAt: number | null;
}

// ── Approval gateway, intake and ticket action results ────────────────────────
export type ApproveOutcome = 'awaiting_checker' | 'scheduled' | 'sending' | 'taken';

export interface ApproveResult {
  outcome: ApproveOutcome;
}

export interface BatchApproveItem {
  ticketId: string;
  ok: boolean;
  outcome?: ApproveOutcome | null;
  error?: string | null;
}

export interface BatchApproveResult {
  results: BatchApproveItem[];
}

export interface ReplyScheduled {
  replyId: string;
  sendAfter: string;
}

export interface IngestResult {
  ticketId: string;
  number: number;
  created: boolean;
  duplicate: boolean;
}

export interface AuthorizeUrlResult {
  authorizeUrl: string;
}

export interface AssignResult {
  assignee: string;
  reason: string;
}

export interface EscalateResult {
  to: string;
}

export interface SplitResult {
  children: string[];
}

// ── Deployments ───────────────────────────────────────────────────────────────
/** Why a version can or cannot be promoted by the current user right now. */
export interface PublishCheckDTO {
  ready: boolean;
  blockers: string[];
  /** The only admin also made the last edit: promoting needs an explicit, audited acknowledgement. */
  requiresSingleAdminAck: boolean;
  /** The passing eval run on this exact config hash, if any. */
  evalRunId: string | null;
}

export interface DeploymentVersionDTO {
  id: string;
  deploymentId: string;
  version: number;
  state: DeploymentVersionState;
  canaryPercent: number | null;
  configHash: string;
  notes: string;
  createdBy: UserRef | null;
  createdAt: string;
  editedBy: UserRef | null;
  editedAt: string | null;
  publishedBy: UserRef | null;
  publishedAt: string | null;
  retiredAt: string | null;
  evalRunId: string | null;
  /** The DeploymentConfig document (taxonomy, rules, flow, models, thresholds, gates). */
  config: Record<string, unknown>;
  publishCheck: PublishCheckDTO | null;
}

export interface DeploymentDTO {
  id: string;
  key: string;
  name: string;
  description: string;
  status: 'active' | 'archived';
  activeVersionId: string | null;
  activeVersion: number | null;
  shadowVersion: number | null;
  canary: { version: number; percent: number } | null;
  draftVersionId: string | null;
  mailboxes: { id: string; address: string }[];
  createdAt: string;
}

export interface DeploymentDetailDTO extends DeploymentDTO {
  versions: DeploymentVersionDTO[];
}

// ── Evals ─────────────────────────────────────────────────────────────────────
export interface EvalDatasetDTO {
  id: string;
  deploymentId: string | null;
  name: string;
  description: string;
  cases: number;
  splits: { calibration: number; test: number };
  /** Hash of the current (non-archived) cases; a run records the snapshot it scored. */
  snapshot: string;
  createdAt: string;
}

export interface EvalCaseDTO {
  id: string;
  datasetId: string;
  input: { subject: string; body: string; fromEmail: string | null };
  expected: {
    category: string;
    hardStop: boolean;
    /** What a person says should happen; absent on cases labelled before it was asked. */
    lane?: 'draft' | 'manual' | null;
    draftAcceptable?: boolean | null;
  };
  split: EvalSplit;
  tags: string[];
  source: string;
  /** The real mail a labelled case came from. */
  ticketId: string | null;
  createdAt: string;
}

export interface EvalMetricsDTO {
  cases: number;
  calibrationCases: number;
  accuracy: number | null;
  macroF1: number | null;
  hardStopRecall: number | null;
  ece: number | null;
  eceUncalibrated: number | null;
  selectiveAccuracy: number | null;
  coverage: number | null;
  conformalCoverage: number | null;
  laneSafetyViolations: number;
  escalationRate: number | null;
  p95LatencyMs: number | null;
  costPerThousandMailsMinor: number | null;
  temperature: number;
  conformalQhat: number | null;
  // System 2 (absent on runs from before it was scored). See evals/system2.py.
  /** The provider pinned for this run, else the providers the configuration names. */
  system2Provider?: string;
  system2Models?: string[];
  adjudicatedCases?: number;
  adjudicationAccuracy?: number | null;
  adjudicationUnsureRate?: number | null;
  /** System 1 where it was sure, the adjudicator where it was not; unsure counts as not right. */
  endToEndAccuracy?: number | null;
  draftsScored?: number;
  /** Drafts whose every sentence is supported by the passages they cite. */
  groundedDraftRate?: number | null;
  noSourceDraftRate?: number | null;
  system2CostMinor?: number;
  system2P95LatencyMs?: number | null;
  /** Calls answered by the deterministic fallback instead of the provider. */
  system2Fallbacks?: number;
}

export interface EvalGateDTO {
  key: string;
  label: string;
  metric: string;
  op: 'gte' | 'lte';
  threshold: number;
  value: number | null;
  passed: boolean;
}

export interface EvalRunDTO {
  id: string;
  datasetId: string;
  datasetName: string;
  deploymentId: string;
  deploymentVersionId: string;
  version: number;
  state: EvalRunState;
  engine: string;
  configHash: string;
  datasetSnapshot: string;
  /** False once the version's config changed after this run: it no longer counts for publishing. */
  current: boolean;
  /** System 2 pinned to one provider (a comparison run; never counts for publishing). */
  provider: ModelProviderKey | null;
  split: { calibration: number; test: number };
  metrics: EvalMetricsDTO | null;
  gates: EvalGateDTO[];
  passed: boolean | null;
  createdBy: UserRef | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  error: string | null;
}

export interface EvalResultDTO {
  id: string;
  caseId: string;
  split: EvalSplit;
  expected: string;
  predicted: string;
  confidence: number;
  correct: boolean;
  hardStopExpected: boolean;
  hardStopPredicted: boolean;
  lane: Lane;
  escalated: boolean;
  latencyMs: number;
}

// ── Members, invitations, permissions ─────────────────────────────────────────
export interface MemberDTO {
  user: UserRef & { email: string };
  role: Role;
  title: string;
  joinedAt: string;
  lastLoginAt: string | null;
  isMe: boolean;
}

export interface InvitationDTO {
  id: string;
  email: string;
  role: Role;
  state: InvitationState;
  invitedBy: UserRef | null;
  createdAt: string;
  expiresAt: string;
  acceptedAt: string | null;
  revokedAt: string | null;
}

export interface RolePolicyDTO {
  role: Role;
  capability: Capability;
  effect: PolicyEffect;
  changedBy: UserRef | null;
  changedAt: string;
}

export interface PermissionMatrixDTO {
  roles: Role[];
  rows: {
    capability: Capability;
    label: string;
    /** Staff and team-lead cells can be changed by the tenant; everything else is fixed by policy. */
    delegable: boolean;
    adminOnly: boolean;
    cells: {
      role: Role;
      allowed: boolean;
      baseline: boolean;
      override: PolicyEffect | null;
      locked: boolean;
    }[];
  }[];
  overrides: RolePolicyDTO[];
}

export interface ProblemDTO {
  type: string;
  title: string;
  status: number;
  detail?: string;
  code: string;
  requestId?: string;
}

// ── Mailbox connections (Microsoft 365 / Google Workspace) ─────────────────────
export type MailConnection =
  'not_connected' | 'connecting' | 'syncing' | 'live' | 'degraded' | 'reauth_required' | 'disconnected';

export type HealthLevel = 'healthy' | 'degraded' | 'down' | 'unknown';

export interface MailboxHealthSignalDTO {
  key: 'stream' | 'lag' | 'sweep' | 'credential' | 'send' | 'throttling';
  label: string;
  level: HealthLevel;
  value: string;
}

export interface MailSyncEventDTO {
  at: string;
  kind: string;
  ok: boolean;
  summary: string;
}

export interface MailboxConnectionDTO {
  id: string;
  address: string;
  provider: MailProvider;
  connection: MailConnection;
  account: string | null;
  /** How new mail is noticed: provider notifications (with a sweep) or polling. */
  mode: 'notifications' | 'polling' | null;
  sendEnabled: boolean;
  level: HealthLevel;
  signals: MailboxHealthSignalDTO[];
  lastError: string;
  lastErrorAt: string | null;
  lastMessageAt: string | null;
  lastTestAt: string | null;
  lastTestOkAt: string | null;
  messages24h: number;
  events: MailSyncEventDTO[];
}

export interface MailConnectorsDTO {
  /** Which providers this stack has an app registration for. */
  providers: { microsoft: boolean; google: boolean };
  /** Whether providers can notify us (a public HTTPS webhook URL is configured); otherwise we poll. */
  webhooks: boolean;
  mailboxLimit: number;
  mailboxes: MailboxConnectionDTO[];
}

// ── Knowledge documents (upload, approval, retrieval) ─────────────────────────
export type KnowledgeDocStatus = 'pending' | 'approved' | 'rejected' | 'retired' | 'stale';
export type KnowledgeParseStatus =
  'none' | 'queued' | 'scanning' | 'parsing' | 'ready' | 'failed' | 'infected';

export interface KnowledgeDocumentDTO {
  id: string;
  title: string;
  filename: string;
  version: number;
  replacesId: string | null;
  status: KnowledgeDocStatus;
  parseStatus: KnowledgeParseStatus;
  parseError: string;
  avStatus: 'not_scanned' | 'clean' | 'infected' | 'error';
  departmentId: string | null;
  department: string | null;
  size: number;
  chunkCount: number;
  effectiveFrom: string | null;
  expiresAt: string | null;
  uploadedBy: string | null;
  approvedBy: string | null;
  approvedAt: string | null;
  createdAt: string;
  /** Whether the signed-in person may approve it (approve clearance for its department). */
  canApprove: boolean;
}

export interface KnowledgeChunkDTO {
  id: string;
  ordinal: number;
  section: string;
  page: number | null;
  text: string;
  tokens: number;
}

export interface KnowledgeDocumentDetailDTO extends KnowledgeDocumentDTO {
  chunks: KnowledgeChunkDTO[];
}

export interface KnowledgeHitDTO {
  chunkId: string;
  docId: string;
  title: string;
  section: string;
  page: number | null;
  text: string;
  score: number;
  similarity: number;
  textMatch: boolean;
}

export interface KnowledgeSearchDTO {
  query: string;
  /** Empty: no approved source answers this; a draft would say so and raise a gap. */
  hits: KnowledgeHitDTO[];
}

// ── Model providers (System 2) ────────────────────────────────────────────────
export type ModelProviderKey = 'anthropic' | 'openai';

export interface ModelProviderDTO {
  key: ModelProviderKey;
  name: string;
  /** This installation has credentials for it. */
  configured: boolean;
  /** The workspace's policy allows it. */
  allowed: boolean;
  /** Nodes that name no provider use this one. */
  isDefault: boolean;
  defaultModel: string;
  spentMinor: number;
  calls: number;
}

export interface ModelPolicyDTO {
  providers: ModelProviderDTO[];
  /** Null: no monthly cap. Minor units of the workspace currency. */
  monthlyBudgetMinor: number | null;
  spentMinor: number;
  /** First day of the current month (UTC), YYYY-MM-DD. */
  month: string;
  budgetReached: boolean;
  canEdit: boolean;
}

// ── Teams, query types and reply-time targets (admin) ─────────────────────────
export interface DepartmentAdminDTO {
  id: string;
  name: string;
  risk: boolean;
  owner: UserRef | null;
  queryTypes: number;
  openTickets: number;
  /** A team with tickets, query types, mailboxes or documents cannot be deleted (rename it instead). */
  deletable: boolean;
}

export interface QueryTypeAdminDTO {
  id: string;
  name: string;
  departmentId: string | null;
  defaultLane: Lane;
  live: boolean;
  tickets: number;
  deletable: boolean;
}

export interface TaxonomyAdminDTO {
  departments: DepartmentAdminDTO[];
  queryTypes: QueryTypeAdminDTO[];
  canEdit: boolean;
}

export interface SlaPolicyDTO {
  id: string;
  name: string;
  /** Null matches any priority. */
  priority: Priority | null;
  /** Null matches any customer segment. */
  segment: string | null;
  /** Applies to escalations (a regulator named, a repeat contact) before any other rule. */
  escalation: boolean;
  minutes: number;
}

export interface SlaPoliciesDTO {
  policies: SlaPolicyDTO[];
  /** No policies of its own: the built-in defaults apply (shown as the policies). */
  usingDefaults: boolean;
  /** Segments seen on this workspace's tickets, for the picker. */
  segments: string[];
  canEdit: boolean;
}

// ── Test bench ─────────────────────────────────────────────────────────────────
export interface BenchSpanDTO {
  agent: string;
  model: string;
  action: string;
  output: string;
  latencyMs: number;
  tokens: number | null;
  costMinor: number | null;
  status: SpanStatus;
}

export interface BenchRunDTO {
  versionId: string;
  version: number;
  state: DeploymentVersionState;
  lane: Lane;
  laneNote: string;
  /** The category's display name; null when nothing was chosen. */
  category: string | null;
  confidence: number;
  hardStop: string | null;
  /** Text as the models saw and wrote it: personal data stays masked ([PHONE_1] and so on). */
  draft: {
    body: string;
    coverage: 'full' | 'partial' | 'none';
    citations: { n: number; title: string; section: string }[];
    flagged: string[];
  } | null;
  brief: { summary: string } | null;
  fields: { label: string; value: string; inferred: boolean }[];
  spans: BenchSpanDTO[];
  costMinor: number;
  latencyMs: number;
  /** Why a model stage fell back to the deterministic provider (policy, budget, outage). */
  degraded: string[];
}

export interface BenchResultDTO {
  runs: BenchRunDTO[];
}

// ── Labelling queue (real mail → eval cases) ─────────────────────────────────
export interface LabelCandidateDTO {
  ticketId: string;
  number: number;
  /** Masked: personal data is replaced before a case is stored or shown here. */
  subject: string;
  body: string;
  receivedAt: string;
  /** What the AI did with it, as a starting point (never a default answer). */
  suggested: { category: string | null; hardStop: boolean; lane: Lane };
}

export interface LabellingQueueDTO {
  datasetId: string;
  categories: { key: string; name: string }[];
  candidates: LabelCandidateDTO[];
  labelled: number;
  calibration: number;
  test: number;
}

// ── Monitoring: every number from records, each linked to them ───────────────
export interface MonitoringCountDTO {
  key: string;
  label: string;
  count: number;
  /** A screen that lists the records behind the number. */
  href: string | null;
}

export interface NodeQualityDTO {
  agent: string;
  model: string;
  calls: number;
  p50Ms: number | null;
  p95Ms: number | null;
  costMinor: number;
  flagged: number;
}

export interface VersionQualityDTO {
  deployment: string;
  version: number | null;
  mails: number;
  /** Share of runs that fell back to the deterministic model (outage, policy, budget). */
  degradedRate: number | null;
  /** Share of runs where the classifier was unsure and asked System 2. */
  escalationRate: number | null;
  costPerMailMinor: number | null;
}

export interface MonitoringDTO {
  days: number;
  since: string;
  funnel: MonitoringCountDTO[];
  sla: {
    firstReplyMedianMin: number | null;
    resolveMedianHours: number | null;
    breached: MonitoringCountDTO;
    atRisk: MonitoringCountDTO;
    byPriority: { priority: Priority; total: number; breached: number }[];
  };
  drafts: {
    sent: number;
    unedited: number;
    edited: number;
    discarded: number;
    /** Normalised character edit distance of sent drafts, 0 (untouched) to 1 (rewritten). */
    meanEditDistance: number | null;
    rejectReasons: { reason: string; count: number }[];
  };
  versions: VersionQualityDTO[];
  nodes: NodeQualityDTO[];
  spend: { monthMinor: number; capMinor: number | null };
  knowledge: {
    approved: number;
    pending: number;
    stale: number;
    expiringSoon: number;
    openGaps: number;
    mostCited: { docId: string; title: string; citations: number }[];
  };
  mailboxes: { id: string; address: string; level: string; lagSeconds: number | null; messages24h: number }[];
  openAlerts: number;
}

// ── Operations: retention, audit export, SIEM ─────────────────────────────────
export interface OperationsDTO {
  /** Customer mail text of tickets closed longer ago than this is removed; null keeps it. */
  retentionMailDays: number | null;
  /** Model traces older than this are deleted; null keeps them. */
  retentionTraceDays: number | null;
  siem: {
    url: string | null;
    hasSecret: boolean;
    /** Audit events delivered so far (sequence number) and how many are waiting. */
    deliveredSeq: number;
    pending: number;
    lastOkAt: string | null;
    lastError: string | null;
  };
  auditEvents: number;
  canEdit: boolean;
}

export interface AuditManifestDTO {
  tenant: string;
  generatedAt: string;
  since: string | null;
  until: string | null;
  events: number;
  firstSeq: number | null;
  lastSeq: number | null;
  /** The hash-chain value of the last event exported: ties the export to the live chain. */
  lastHash: string | null;
  files: { name: string; sha256: string; bytes: number }[];
  /** HMAC-SHA256 over the canonical manifest without this field, with the workspace's export key. */
  signature: string;
}

// ── SCIM provisioning ───────────────────────────────────────────────────────────
export interface ScimSettingsDTO {
  /** The SCIM base URL to give the identity provider. */
  baseUrl: string;
  enabled: boolean;
  tokenCreatedAt: string | null;
  lastUsedAt: string | null;
  /** Identity-provider group name → role; empty keeps roles managed in the app. */
  groupRoles: Record<string, Role>;
  groups: { name: string; members: number }[];
  provisionedMembers: number;
  canEdit: boolean;
}

export interface ScimTokenDTO {
  /** Shown once; only its hash is stored. */
  token: string;
  settings: ScimSettingsDTO;
}

// ── Pilot at a bank ─────────────────────────────────────────────────────────────
/** onboarding → shadow → assisted → live. Replies go out only from assisted on, always after a person approves. */
export type PilotStage = 'onboarding' | 'shadow' | 'assisted' | 'live';
/** pending: not enough records yet to judge. */
export type PilotGateState = 'pass' | 'fail' | 'pending';
export type PilotRequestState = 'pending' | 'approved' | 'rejected' | 'withdrawn';
export type IncidentSeverity = 'P1' | 'P2' | 'P3' | 'P4';
export type IncidentKind = 'hard_stop_miss' | 'wrong_reply' | 'data_exposure' | 'outage' | 'other';

export interface PilotGateDTO {
  key: string;
  label: string;
  state: PilotGateState;
  value: string;
  target: string;
}

export interface PilotTargetsDTO {
  /** Share of decided drafts sent unedited or lightly edited. */
  acceptance: number;
  /** Largest edit distance (0–1) that still counts as a light edit. */
  lightEditMax: number;
  /** AI vs people agreement on category and lane in shadow. */
  agreement: number;
  shadowDays: number;
  assistedDays: number;
  minLabelled: number;
  minDrafts: number;
}

export interface PilotBaselineDTO {
  onTimeRate: number | null;
  firstReplyMinutes: number | null;
  capturedAt: string | null;
  source: 'records' | 'manual' | null;
  days: number | null;
}

export interface PilotKpisDTO {
  windowStart: string;
  days: number;
  draftsDecided: number;
  draftsAccepted: number;
  acceptanceRate: number | null;
  labelled: number;
  categoryAgreement: number | null;
  laneCompared: number;
  laneAgreement: number | null;
  hardStopMisses: number;
  onTimeRate: number | null;
  medianFirstReplyMinutes: number | null;
  p1Incidents: number;
  openIncidents: number;
}

export interface PilotRequestDTO {
  id: string;
  fromStage: string;
  toStage: string;
  reason: string;
  state: PilotRequestState;
  requestedBy: UserRef | null;
  requestedAt: string;
  decidedBy: UserRef | null;
  decidedAt: string | null;
  decisionNote: string;
  /** The gates as they stood when the request was made. */
  evidence: PilotGateDTO[];
  canDecide: boolean;
  canWithdraw: boolean;
}

export interface PilotDTO {
  stage: WorkspaceStatus;
  stageSince: string;
  daysInStage: number;
  next: PilotStage | null;
  /** The gates for moving to `next`. */
  gates: PilotGateDTO[];
  ready: boolean;
  pending: PilotRequestDTO | null;
  history: PilotRequestDTO[];
  kpis: PilotKpisDTO;
  targets: PilotTargetsDTO;
  baseline: PilotBaselineDTO;
  /** People who may sign off a move forward; empty means any other admin. */
  riskApprovers: UserRef[];
  sendsAllowed: boolean;
  canRequest: boolean;
  canStepBack: boolean;
  canEditSettings: boolean;
}

export interface ShadowReportDTO {
  days: number;
  compared: number;
  labelled: number;
  lanes: { ai: string; final: string; count: number }[];
  categories: { key: string; labelled: number; agreed: number }[];
  disagreements: {
    ticketId: string;
    number: number;
    subject: string;
    kind: 'category' | 'lane' | 'hard_stop_miss';
    aiCategory: string | null;
    humanCategory: string | null;
    aiLane: string;
    finalLane: string;
    aiHardStop: string | null;
  }[];
}

export interface PilotIncidentDTO {
  id: string;
  severity: IncidentSeverity;
  kind: IncidentKind;
  title: string;
  detail: string;
  ticketId: string | null;
  ticketNumber: number | null;
  openedBy: UserRef | null;
  openedAt: string;
  resolvedBy: UserRef | null;
  resolvedAt: string | null;
  resolution: string;
}
