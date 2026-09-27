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
  DraftState,
  FeedbackKind,
  FixKind,
  GapSeverity,
  Health,
  KpiMetric,
  Lane,
  MailboxState,
  MailProvider,
  NotificationKind,
  OwnerKind,
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
  worker: { state: 'live' | 'degraded' | 'paused'; provider: 'claude' | 'heuristic' };
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
export type GateState = 'open' | 'awaiting_checker' | 'scheduled' | 'executing' | 'done' | 'rejected' | 'taken';

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
  cif: string;
  name: string;
  email: string;
  sinceYear: number;
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
  moves: { ticketNumber: string; priority: Priority; minutesLeft: number | null; to: string | null; reason: string }[];
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
  sevKind: 'late' | 'pattern' | 'drift' | 'capacity';
  bucket: string;
  text: string;
  actionLabel: string;
  owner: string;
  at: string;
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
  phases: { n: number; label: string; scope: string; state: string; current: boolean }[];
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
  priorityRules: { id: string; key: string; description: string; target: string; hard: boolean; enabled: boolean; hits: string }[];
  matrix: { cols: string[]; rows: { capability: string; values: ('yes' | 'no' | 'appr' | 'cell' | 'auto')[] }[] };
  cycles: { cell: string; chain: string[]; note: string }[];
  proposedRules: { id: string; text: string; ticketNumber: string | null; proposedBy: string; at: string; status: string }[];
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
  customers: { id: string; cif: string; name: string; tickets: number; latestTicketId: string | null }[];
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

export interface ProblemDTO {
  type: string;
  title: string;
  status: number;
  detail?: string;
  code: string;
  requestId?: string;
}
