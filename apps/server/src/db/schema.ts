/**
 * Database schema. Every tenant table carries `org_id` and is protected by row-level security
 * (see migrations/0001_security.sql). Global tables: orgs, users, memberships, sessions.
 */
import { sql } from 'drizzle-orm';
import {
  bigserial,
  boolean,
  date,
  doublePrecision,
  index,
  integer,
  jsonb,
  pgTable,
  primaryKey,
  text,
  timestamp,
  uniqueIndex,
  uuid,
} from 'drizzle-orm/pg-core';

const id = () => uuid('id').primaryKey().default(sql`gen_random_uuid()`);
const orgId = () => uuid('org_id').notNull();
const ts = (name: string) => timestamp(name, { withTimezone: true, mode: 'date' });
const createdAt = () => ts('created_at').notNull().defaultNow();

// ── Global identity ───────────────────────────────────────────────────────────
export const orgs = pgTable('orgs', {
  id: id(),
  slug: text('slug').notNull().unique(),
  name: text('name').notNull(),
  short: text('short').notNull(),
  tint: text('tint').notNull(),
  bg: text('bg').notNull(),
  plan: text('plan').notNull(),
  confidenceBar: doublePrecision('confidence_bar').notNull().default(0.78),
  /** Support headcount, including people not yet onboarded as users (drives completion %). */
  headcount: integer('headcount').notNull().default(0),
  createdAt: createdAt(),
});

export const users = pgTable('users', {
  id: id(),
  email: text('email').notNull().unique(),
  name: text('name').notNull(),
  initials: text('initials').notNull(),
  createdAt: createdAt(),
});

export const memberships = pgTable(
  'memberships',
  {
    orgId: orgId().references(() => orgs.id),
    userId: uuid('user_id').notNull().references(() => users.id),
    role: text('role').notNull(), // staff | lead | admin
    title: text('title').notNull().default(''),
    pod: text('pod').notNull().default(''),
    capacity: integer('capacity').notNull().default(12),
    years: integer('years').notNull().default(1),
    /** Open work held outside this workspace's queue (other channels), counted in load. */
    baseLoad: integer('base_load').notNull().default(0),
    joinedAt: ts('joined_at').notNull().defaultNow(),
  },
  (t) => [primaryKey({ columns: [t.orgId, t.userId] })],
);

export const sessions = pgTable(
  'sessions',
  {
    id: id(),
    userId: uuid('user_id').notNull().references(() => users.id),
    orgId: uuid('org_id').notNull().references(() => orgs.id),
    tokenHash: text('token_hash').notNull().unique(),
    csrfToken: text('csrf_token').notNull(),
    userAgent: text('user_agent').notNull().default(''),
    device: text('device').notNull().default(''),
    location: text('location').notNull().default(''),
    ip: text('ip').notNull().default(''),
    createdAt: createdAt(),
    lastSeenAt: ts('last_seen_at').notNull().defaultNow(),
    expiresAt: ts('expires_at').notNull(),
    revokedAt: ts('revoked_at'),
  },
  (t) => [index('sessions_user_idx').on(t.userId)],
);

// ── Tenant: structure ─────────────────────────────────────────────────────────
export const userSettings = pgTable(
  'user_settings',
  {
    orgId: orgId(),
    userId: uuid('user_id').notNull(),
    prefs: jsonb('prefs').$type<Record<string, boolean>>().notNull().default({}),
    signature: text('signature').notNull().default(''),
  },
  (t) => [primaryKey({ columns: [t.orgId, t.userId] })],
);

export const departments = pgTable('departments', {
  id: id(),
  orgId: orgId(),
  name: text('name').notNull(),
  ownerId: uuid('owner_id'),
  sort: integer('sort').notNull().default(0),
  readinessPct: integer('readiness_pct').notNull().default(0),
  readinessNote: text('readiness_note').notNull().default(''),
  gapNote: text('gap_note').notNull().default(''),
  risk: boolean('risk').notNull().default(false),
  /** Shown as a column in the clearance matrix. */
  inMatrix: boolean('in_matrix').notNull().default(true),
});

export const queryTypes = pgTable('query_types', {
  id: id(),
  orgId: orgId(),
  name: text('name').notNull(),
  /** Customer-facing label on the ownership map, when it differs from the internal name. */
  mapName: text('map_name'),
  departmentId: uuid('department_id'),
  defaultLane: text('default_lane').notNull(),
  monthlyVolume: integer('monthly_volume').notNull().default(0),
  live: boolean('live').notNull().default(true),
  baselineHours: doublePrecision('baseline_hours'),
  actualHours: doublePrecision('actual_hours'),
  lateCount: integer('late_count').notNull().default(0),
  ownerLabel: text('owner_label').notNull().default(''),
  showOnMap: boolean('show_on_map').notNull().default(true),
  showOnSpeed: boolean('show_on_speed').notNull().default(false),
  sort: integer('sort').notNull().default(0),
});

export const clearances = pgTable(
  'clearances',
  {
    orgId: orgId(),
    userId: uuid('user_id').notNull(),
    departmentId: uuid('department_id').notNull(),
    level: integer('level').notNull(),
  },
  (t) => [primaryKey({ columns: [t.orgId, t.userId, t.departmentId] })],
);

export const staffAvailability = pgTable(
  'staff_availability',
  {
    orgId: orgId(),
    userId: uuid('user_id').notNull(),
    status: text('status').notNull(), // available | busy | away
    checkin: text('checkin').notNull().default(''),
    calendar: text('calendar').notNull().default(''),
    updatedAt: ts('updated_at').notNull().defaultNow(),
  },
  (t) => [primaryKey({ columns: [t.orgId, t.userId] })],
);

// ── Tenant: intake ────────────────────────────────────────────────────────────
export const mailboxes = pgTable(
  'mailboxes',
  {
    id: id(),
    orgId: orgId(),
    address: text('address').notNull(),
    provider: text('provider').notNull(),
    departmentId: uuid('department_id'),
    teamLabel: text('team_label').notNull().default(''),
    permissions: jsonb('permissions').$type<string[]>().notNull().default([]),
    state: text('state').notNull(),
    volume24h: integer('volume_24h').notNull().default(0),
    /** AES-256-GCM encrypted OAuth token bundle (never returned by the API). */
    credentialsEnc: text('credentials_enc'),
    lastSyncAt: ts('last_sync_at'),
    sort: integer('sort').notNull().default(0),
    createdAt: createdAt(),
  },
  (t) => [uniqueIndex('mailboxes_org_address_uq').on(t.orgId, t.address)],
);

export const boards = pgTable('boards', {
  id: id(),
  orgId: orgId(),
  key: text('key').notNull(),
  name: text('name').notNull(),
  mailboxId: uuid('mailbox_id'),
  team: text('team').notNull().default(''),
  state: text('state').notNull(),
  autoRatePct: integer('auto_rate_pct').notNull().default(0),
  sort: integer('sort').notNull().default(0),
  createdAt: createdAt(),
});

export const inboundMessages = pgTable(
  'inbound_messages',
  {
    id: id(),
    orgId: orgId(),
    mailboxId: uuid('mailbox_id').notNull(),
    providerMessageId: text('provider_message_id').notNull(),
    ticketId: uuid('ticket_id'),
    receivedAt: ts('received_at').notNull().defaultNow(),
  },
  (t) => [uniqueIndex('inbound_dedup_uq').on(t.orgId, t.mailboxId, t.providerMessageId)],
);

// ── Tenant: customers ─────────────────────────────────────────────────────────
export const customers = pgTable(
  'customers',
  {
    id: id(),
    orgId: orgId(),
    cif: text('cif').notNull(),
    name: text('name').notNull(),
    email: text('email').notNull(),
    phone: text('phone').notNull().default(''),
    segment: text('segment').notNull(),
    sinceYear: integer('since_year').notNull(),
    account: text('account').notNull().default(''),
  },
  (t) => [uniqueIndex('customers_org_cif_uq').on(t.orgId, t.cif), index('customers_email_idx').on(t.orgId, t.email)],
);

// ── Tenant: tickets ───────────────────────────────────────────────────────────
export const tickets = pgTable(
  'tickets',
  {
    id: id(),
    orgId: orgId(),
    number: integer('number').notNull(),
    boardId: uuid('board_id'),
    mailboxId: uuid('mailbox_id'),
    customerId: uuid('customer_id'),
    subject: text('subject').notNull(),
    fromName: text('from_name').notNull(),
    fromEmail: text('from_email').notNull(),
    receivedAt: ts('received_at').notNull(),
    lane: text('lane').notNull(),
    originalLane: text('original_lane').notNull(),
    laneNote: text('lane_note').notNull().default(''),
    status: text('status').notNull(),
    priority: text('priority').notNull(),
    segment: text('segment').notNull().default('Retail'),
    departmentId: uuid('department_id'),
    queryTypeId: uuid('query_type_id'),
    bucket: text('bucket').notNull().default(''),
    confidence: doublePrecision('confidence').notNull().default(0),
    assigneeId: uuid('assignee_id'),
    ownerKind: text('owner_kind').notNull(), // ai | user | unassigned
    slaMinutes: integer('sla_minutes').notNull(),
    dueAt: ts('due_at'),
    pausedAt: ts('paused_at'),
    firstReplyAt: ts('first_reply_at'),
    resolvedAt: ts('resolved_at'),
    closedAt: ts('closed_at'),
    reopenCount: integer('reopen_count').notNull().default(0),
    regulatoryFlag: text('regulatory_flag'),
    /** Outcome line shown in customer history, e.g. "Resolved in 2h 10m". */
    resolution: text('resolution'),
    sentiment: text('sentiment').notNull().default('neutral'),
    nextMove: text('next_move').notNull().default(''),
    category: text('category').notNull().default(''),
    subcategory: text('subcategory').notNull().default(''),
    product: text('product').notNull().default(''),
    parentId: uuid('parent_id'),
    mergedIntoId: uuid('merged_into_id'),
    splitProposed: boolean('split_proposed').notNull().default(false),
    /** When a person took ownership of a manual-lane ticket ("Take it on"). */
    acceptedAt: ts('accepted_at'),
    loggedMinutes: integer('logged_minutes').notNull().default(0),
    version: integer('version').notNull().default(1),
    createdAt: createdAt(),
    updatedAt: ts('updated_at').notNull().defaultNow(),
  },
  (t) => [
    uniqueIndex('tickets_org_number_uq').on(t.orgId, t.number),
    index('tickets_status_idx').on(t.orgId, t.status),
    index('tickets_assignee_idx').on(t.orgId, t.assigneeId),
    index('tickets_customer_idx').on(t.orgId, t.customerId),
    index('tickets_due_idx').on(t.orgId, t.dueAt),
  ],
);

export const messages = pgTable(
  'messages',
  {
    id: id(),
    orgId: orgId(),
    ticketId: uuid('ticket_id').notNull(),
    direction: text('direction').notNull(), // inbound | outbound | note
    fromName: text('from_name').notNull(),
    fromAddr: text('from_addr').notNull(),
    toAddr: text('to_addr').notNull(),
    body: text('body').notNull(),
    sentAt: ts('sent_at').notNull(),
    providerMessageId: text('provider_message_id'),
  },
  (t) => [index('messages_ticket_idx').on(t.orgId, t.ticketId)],
);

export const comments = pgTable(
  'comments',
  {
    id: id(),
    orgId: orgId(),
    ticketId: uuid('ticket_id').notNull(),
    kind: text('kind').notNull(), // note | public | call | system
    authorId: uuid('author_id'),
    authorName: text('author_name').notNull(),
    authorInitials: text('author_initials').notNull(),
    body: text('body').notNull(),
    createdAt: createdAt(),
  },
  (t) => [index('comments_ticket_idx').on(t.orgId, t.ticketId)],
);

export const subtasks = pgTable(
  'subtasks',
  {
    id: id(),
    orgId: orgId(),
    ticketId: uuid('ticket_id').notNull(),
    key: text('key').notNull(),
    label: text('label').notNull(),
    owner: text('owner').notNull(),
    sort: integer('sort').notNull().default(0),
    done: boolean('done').notNull().default(false),
    doneBy: uuid('done_by'),
    doneAt: ts('done_at'),
  },
  (t) => [uniqueIndex('subtasks_ticket_key_uq').on(t.orgId, t.ticketId, t.key)],
);

export const attachments = pgTable('attachments', {
  id: id(),
  orgId: orgId(),
  ticketId: uuid('ticket_id').notNull(),
  ext: text('ext').notNull(),
  name: text('name').notNull(),
  size: text('size').notNull(),
  storageKey: text('storage_key'),
  createdAt: createdAt(),
});

export const watchers = pgTable(
  'watchers',
  {
    orgId: orgId(),
    ticketId: uuid('ticket_id').notNull(),
    userId: uuid('user_id').notNull(),
  },
  (t) => [primaryKey({ columns: [t.orgId, t.ticketId, t.userId] })],
);

export const ticketLinks = pgTable('ticket_links', {
  id: id(),
  orgId: orgId(),
  ticketId: uuid('ticket_id').notNull(),
  kind: text('kind').notNull(),
  label: text('label').notNull(),
  ref: text('ref'),
  sort: integer('sort').notNull().default(0),
});

// ── Tenant: triage ────────────────────────────────────────────────────────────
export const triageRuns = pgTable(
  'triage_runs',
  {
    id: id(),
    orgId: orgId(),
    ticketId: uuid('ticket_id').notNull(),
    traceId: text('trace_id').notNull(),
    reasoning: text('reasoning').notNull(),
    evidence: jsonb('evidence').$type<{ tag: string; quote: string; why: string }[]>().notNull().default([]),
    confidence: doublePrecision('confidence').notNull(),
    lane: text('lane').notNull(),
    latencyMs: integer('latency_ms').notNull(),
    costMinor: integer('cost_minor').notNull().default(0),
    provider: text('provider').notNull(),
    createdAt: createdAt(),
  },
  (t) => [index('triage_runs_ticket_idx').on(t.orgId, t.ticketId)],
);

export const traceSpans = pgTable(
  'trace_spans',
  {
    id: id(),
    orgId: orgId(),
    runId: uuid('run_id').notNull(),
    seq: integer('seq').notNull(),
    offsetMs: integer('offset_ms').notNull(),
    agent: text('agent').notNull(),
    model: text('model').notNull(),
    action: text('action').notNull(),
    output: text('output').notNull(),
    latencyMs: integer('latency_ms').notNull(),
    tokens: integer('tokens'),
    costMinor: integer('cost_minor'),
    status: text('status').notNull(),
  },
  (t) => [index('trace_spans_run_idx').on(t.orgId, t.runId)],
);

export const briefs = pgTable('briefs', {
  ticketId: uuid('ticket_id').primaryKey(),
  orgId: orgId(),
  why: text('why').notNull(),
  summary: text('summary').notNull(),
  context: jsonb('context').$type<{ label: string; value: string }[]>().notNull().default([]),
  suggestions: jsonb('suggestions').$type<{ label: string; meta: string }[]>().notNull().default([]),
});

// ── Tenant: actions & gateway ─────────────────────────────────────────────────
export const actionTemplates = pgTable(
  'action_templates',
  {
    id: id(),
    orgId: orgId(),
    code: text('code').notNull(),
    name: text('name').notNull(),
    system: text('system').notNull(),
    endpoint: text('endpoint').notNull().default(''),
    owner: text('owner').notNull().default(''),
    reversible: boolean('reversible').notNull(),
    moneyMoves: boolean('money_moves').notNull(),
    approval: text('approval').notNull(), // auto | single | dual
    stpPct: integer('stp_pct'),
    monthlyVolume: integer('monthly_volume').notNull().default(0),
    state: text('state').notNull().default('active'),
    sort: integer('sort').notNull().default(0),
    createdAt: createdAt(),
  },
  (t) => [uniqueIndex('action_templates_code_uq').on(t.orgId, t.code)],
);

export const autonomyDial = pgTable(
  'autonomy_dial',
  {
    orgId: orgId(),
    cell: text('cell').notNull(),
    level: integer('level').notNull(),
    locked: boolean('locked').notNull().default(false),
    countOverride: integer('count_override'),
    updatedBy: uuid('updated_by'),
    updatedAt: ts('updated_at').notNull().defaultNow(),
  },
  (t) => [primaryKey({ columns: [t.orgId, t.cell] })],
);

export const actionInstances = pgTable(
  'action_instances',
  {
    id: id(),
    orgId: orgId(),
    ticketId: uuid('ticket_id').notNull(),
    templateId: uuid('template_id').notNull(),
    accountRef: text('account_ref').notNull().default(''),
    fields: jsonb('fields')
      .$type<{ label: string; value: string; source: string; inferred: boolean }[]>()
      .notNull()
      .default([]),
    validation: text('validation').notNull().default(''),
    state: text('state').notNull(),
    chain: text('chain').notNull(),
    idempotencyKey: text('idempotency_key').notNull(),
    makerId: uuid('maker_id'),
    makerAt: ts('maker_at'),
    checkerId: uuid('checker_id'),
    checkerAt: ts('checker_at'),
    executeAfter: ts('execute_after'),
    executedAt: ts('executed_at'),
    externalRef: text('external_ref'),
    failure: text('failure'),
    rejectedNote: text('rejected_note'),
    createdAt: createdAt(),
  },
  (t) => [
    uniqueIndex('action_instances_idem_uq').on(t.orgId, t.idempotencyKey),
    index('action_instances_ticket_idx').on(t.orgId, t.ticketId),
  ],
);

/** One row per human approval; drives the approve-without-open canary metric. */
export const approvals = pgTable('approvals', {
  id: id(),
  orgId: orgId(),
  ticketId: uuid('ticket_id').notNull(),
  subjectKind: text('subject_kind').notNull(), // action | draft | reply
  subjectId: uuid('subject_id').notNull(),
  userId: uuid('user_id').notNull(),
  step: text('step').notNull(), // maker | checker | sender
  openedEvidence: boolean('opened_evidence').notNull(),
  createdAt: createdAt(),
});

export const drafts = pgTable('drafts', {
  id: id(),
  orgId: orgId(),
  ticketId: uuid('ticket_id').notNull().unique(),
  subject: text('subject').notNull(),
  toAddr: text('to_addr').notNull(),
  originalBody: text('original_body').notNull(),
  currentBody: text('current_body').notNull(),
  citations: jsonb('citations')
    .$type<{ n: number; docId: string; doc: string; section: string; verifiedAt: string; owner: string }[]>()
    .notNull()
    .default([]),
  flagged: jsonb('flagged').$type<string[]>().notNull().default([]),
  state: text('state').notNull(),
  sendAfter: ts('send_after'),
  sentAt: ts('sent_at'),
  sentBy: uuid('sent_by'),
  createdAt: createdAt(),
  updatedAt: ts('updated_at').notNull().defaultNow(),
});

/** Free-text replies composed by staff (outside the AI draft), with the same recall window. */
export const replies = pgTable('replies', {
  id: id(),
  orgId: orgId(),
  ticketId: uuid('ticket_id').notNull(),
  body: text('body').notNull(),
  state: text('state').notNull(), // scheduled | sent | recalled
  sendAfter: ts('send_after').notNull(),
  sentAt: ts('sent_at'),
  authorId: uuid('author_id').notNull(),
  createdAt: createdAt(),
});

export const calls = pgTable('calls', {
  id: id(),
  orgId: orgId(),
  ticketId: uuid('ticket_id').notNull(),
  startedBy: uuid('started_by').notNull(),
  state: text('state').notNull(),
  startedAt: ts('started_at').notNull().defaultNow(),
  liveAt: ts('live_at'),
  endedAt: ts('ended_at'),
  durationSec: integer('duration_sec').notNull().default(0),
  script: jsonb('script').$type<{ who: 'You' | 'Customer'; text: string }[]>().notNull().default([]),
  summary: text('summary'),
  updates: jsonb('updates').$type<string[]>().notNull().default([]),
  recordingKey: text('recording_key'),
});

// ── Tenant: agents ────────────────────────────────────────────────────────────
export const agents = pgTable('agents', {
  id: id(),
  orgId: orgId(),
  name: text('name').notNull(),
  abbr: text('abbr').notNull(),
  template: text('template').notNull(),
  model: text('model').notNull(),
  state: text('state').notNull(),
  version: integer('version').notNull().default(1),
  prompt: text('prompt').notNull(),
  evalScore: integer('eval_score'),
  costPer1kMinor: integer('cost_per_1k_minor').notNull().default(0),
  role: text('role').notNull().default(''), // pipeline role: guard | bucketer | extractor | drafter | summariser | ranker
  sort: integer('sort').notNull().default(0),
  createdAt: createdAt(),
});

export const agentVersions = pgTable('agent_versions', {
  id: id(),
  orgId: orgId(),
  agentId: uuid('agent_id').notNull(),
  version: integer('version').notNull(),
  prompt: text('prompt').notNull(),
  model: text('model').notNull(),
  evalStatus: text('eval_status').notNull(),
  createdBy: text('created_by').notNull(),
  createdAt: createdAt(),
});

export const agentBoards = pgTable(
  'agent_boards',
  {
    orgId: orgId(),
    agentId: uuid('agent_id').notNull(),
    boardId: uuid('board_id').notNull(),
  },
  (t) => [primaryKey({ columns: [t.orgId, t.agentId, t.boardId] })],
);

export const agentEvals = pgTable('agent_evals', {
  id: id(),
  orgId: orgId(),
  agentId: uuid('agent_id').notNull(),
  label: text('label').notNull(),
  value: text('value').notNull(),
  tone: text('tone').notNull(),
  sort: integer('sort').notNull().default(0),
});

export const feedback = pgTable('feedback', {
  id: id(),
  orgId: orgId(),
  agentId: uuid('agent_id'),
  agentName: text('agent_name').notNull(),
  ticketId: uuid('ticket_id'),
  ticketNumber: text('ticket_number'),
  kind: text('kind').notNull(),
  text: text('text').notNull(),
  fix: text('fix').notNull(),
  status: text('status').notNull().default('open'),
  diff: jsonb('diff').$type<{ original: string; current: string } | null>(),
  createdBy: uuid('created_by'),
  createdAt: createdAt(),
});

/** Observed outcomes per triage decision, for confidence calibration. */
export const predictionOutcomes = pgTable('prediction_outcomes', {
  id: id(),
  orgId: orgId(),
  agentName: text('agent_name').notNull(),
  ticketId: uuid('ticket_id'),
  confidence: doublePrecision('confidence').notNull(),
  correct: boolean('correct'),
  createdAt: createdAt(),
});

// ── Tenant: rules & policies ──────────────────────────────────────────────────
export const bucketRules = pgTable('bucket_rules', {
  id: id(),
  orgId: orgId(),
  sort: integer('sort').notNull(),
  description: text('description').notNull(),
  target: text('target').notNull(),
  kind: text('kind').notNull(),
  hits: text('hits').notNull().default(''),
  pattern: jsonb('pattern').$type<{ any?: string[]; all?: string[]; regex?: string; queryType?: string } | null>(),
});

export const priorityRules = pgTable('priority_rules', {
  id: id(),
  orgId: orgId(),
  key: text('key').notNull(),
  sort: integer('sort').notNull(),
  description: text('description').notNull(),
  target: text('target').notNull(),
  hard: boolean('hard').notNull(),
  enabled: boolean('enabled').notNull().default(true),
  hits: text('hits').notNull().default(''),
});

export const proposedRules = pgTable('proposed_rules', {
  id: id(),
  orgId: orgId(),
  text: text('text').notNull(),
  ticketId: uuid('ticket_id'),
  ticketNumber: text('ticket_number'),
  proposedBy: uuid('proposed_by').notNull(),
  proposedByName: text('proposed_by_name').notNull(),
  status: text('status').notNull().default('pending'),
  decidedBy: uuid('decided_by'),
  createdAt: createdAt(),
});

// ── Tenant: knowledge ─────────────────────────────────────────────────────────
export const knowledgeSources = pgTable('knowledge_sources', {
  id: id(),
  orgId: orgId(),
  name: text('name').notNull(),
  kind: text('kind').notNull(),
  abbr: text('abbr').notNull(),
  docCount: integer('doc_count').notNull().default(0),
  docUnit: text('doc_unit').notNull().default('documents'),
  approvedCount: integer('approved_count').notNull().default(0),
  health: text('health').notNull(),
  syncNote: text('sync_note').notNull().default(''),
  note: text('note').notNull().default(''),
  lastSyncAt: ts('last_sync_at'),
  sort: integer('sort').notNull().default(0),
  createdAt: createdAt(),
});

export const knowledgeDocs = pgTable('knowledge_docs', {
  id: id(),
  orgId: orgId(),
  sourceId: uuid('source_id'),
  title: text('title').notNull(),
  section: text('section').notNull().default(''),
  owner: text('owner').notNull().default(''),
  departmentId: uuid('department_id'),
  status: text('status').notNull(), // approved | pending | stale
  verifiedAt: ts('verified_at'),
  body: text('body').notNull().default(''),
});

export const gapTickets = pgTable('gap_tickets', {
  id: id(),
  orgId: orgId(),
  number: integer('number').notNull(),
  severity: text('severity').notNull(),
  question: text('question').notNull(),
  detail: text('detail').notNull(),
  hits: integer('hits').notNull().default(0),
  owner: text('owner').notNull(),
  state: text('state').notNull(),
  cta: text('cta').notNull(),
  openedAt: ts('opened_at').notNull().defaultNow(),
  closedAt: ts('closed_at'),
});

// ── Tenant: insights ──────────────────────────────────────────────────────────
export const dailyMetrics = pgTable(
  'daily_metrics',
  {
    orgId: orgId(),
    metric: text('metric').notNull(),
    day: date('day', { mode: 'string' }).notNull(),
    value: doublePrecision('value').notNull(),
  },
  (t) => [primaryKey({ columns: [t.orgId, t.metric, t.day] })],
);

export const alerts = pgTable('alerts', {
  id: id(),
  orgId: orgId(),
  sevLabel: text('sev_label').notNull(),
  sevKind: text('sev_kind').notNull(),
  bucket: text('bucket').notNull(),
  text: text('text').notNull(),
  actionLabel: text('action_label').notNull(),
  owner: text('owner').notNull(),
  createdAt: createdAt(),
  resolvedAt: ts('resolved_at'),
  resolvedBy: uuid('resolved_by'),
});

export const kpis = pgTable('kpis', {
  id: id(),
  orgId: orgId(),
  ownerId: uuid('owner_id').notNull(),
  name: text('name').notNull(),
  metric: text('metric').notNull(),
  viz: text('viz').notNull(),
  scope: text('scope').notNull(),
  target: doublePrecision('target').notNull(),
  createdAt: createdAt(),
});

export const connectors = pgTable('connectors', {
  id: id(),
  orgId: orgId(),
  abbr: text('abbr').notNull(),
  name: text('name').notNull(),
  scope: text('scope').notNull(),
  state: text('state').notNull(),
  sort: integer('sort').notNull().default(0),
});

// ── Tenant: learning ──────────────────────────────────────────────────────────
export const courses = pgTable('courses', {
  id: id(),
  orgId: orgId(),
  key: text('key').notNull(),
  title: text('title').notNull(),
  minutes: integer('minutes').notNull(),
  cards: jsonb('cards').$type<{ title: string; body: string }[]>().notNull(),
  quiz: jsonb('quiz').$type<{ q: string; options: string[]; correct: number }[]>().notNull(),
  baselinePct: integer('baseline_pct').notNull().default(0),
  sort: integer('sort').notNull().default(0),
  createdAt: createdAt(),
});

export const courseCompletions = pgTable(
  'course_completions',
  {
    orgId: orgId(),
    courseId: uuid('course_id').notNull(),
    userId: uuid('user_id').notNull(),
    score: integer('score').notNull(),
    total: integer('total').notNull(),
    completedAt: ts('completed_at').notNull().defaultNow(),
  },
  (t) => [primaryKey({ columns: [t.orgId, t.courseId, t.userId] })],
);

export const notifications = pgTable('notifications', {
  id: id(),
  orgId: orgId(),
  kind: text('kind').notNull(),
  source: text('source').notNull(),
  title: text('title').notNull(),
  body: text('body').notNull().default(''),
  courseId: uuid('course_id'),
  urgent: boolean('urgent').notNull().default(false),
  createdBy: uuid('created_by'),
  createdAt: createdAt(),
});

export const notificationReads = pgTable(
  'notification_reads',
  {
    orgId: orgId(),
    notificationId: uuid('notification_id').notNull(),
    userId: uuid('user_id').notNull(),
    readAt: ts('read_at').notNull().defaultNow(),
  },
  (t) => [primaryKey({ columns: [t.orgId, t.notificationId, t.userId] })],
);

// ── Platform ──────────────────────────────────────────────────────────────────
export const auditEvents = pgTable(
  'audit_events',
  {
    seq: bigserial('seq', { mode: 'number' }).primaryKey(),
    id: uuid('id').notNull().default(sql`gen_random_uuid()`),
    orgId: orgId(),
    at: ts('at').notNull().defaultNow(),
    actorKind: text('actor_kind').notNull(), // user | ai | system
    actorId: uuid('actor_id'),
    actorName: text('actor_name').notNull(),
    action: text('action').notNull(),
    entity: text('entity').notNull(),
    entityId: text('entity_id'),
    ticketId: uuid('ticket_id'),
    summary: text('summary').notNull(),
    /** Shown in the AI activity rail when set. */
    feedTone: text('feed_tone'),
    feedMeta: text('feed_meta'),
    data: jsonb('data').$type<Record<string, unknown>>().notNull().default({}),
    prevHash: text('prev_hash').notNull(),
    hash: text('hash').notNull(),
  },
  (t) => [index('audit_org_seq_idx').on(t.orgId, t.seq), index('audit_ticket_idx').on(t.orgId, t.ticketId)],
);

export const outbox = pgTable(
  'outbox',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    orgId: orgId(),
    topic: text('topic').notNull(),
    payload: jsonb('payload').$type<Record<string, unknown>>().notNull(),
    createdAt: createdAt(),
    dispatchedAt: ts('dispatched_at'),
  },
  (t) => [index('outbox_pending_idx').on(t.dispatchedAt, t.id)],
);

export const jobs = pgTable(
  'jobs',
  {
    id: bigserial('id', { mode: 'number' }).primaryKey(),
    orgId: orgId(),
    kind: text('kind').notNull(),
    payload: jsonb('payload').$type<Record<string, unknown>>().notNull().default({}),
    runAt: ts('run_at').notNull().defaultNow(),
    state: text('state').notNull().default('queued'), // queued | running | done | failed | cancelled
    attempts: integer('attempts').notNull().default(0),
    maxAttempts: integer('max_attempts').notNull().default(5),
    lockedBy: text('locked_by'),
    lockedAt: ts('locked_at'),
    lastError: text('last_error'),
    dedupeKey: text('dedupe_key'),
    createdAt: createdAt(),
  },
  (t) => [
    index('jobs_ready_idx').on(t.state, t.runAt),
    uniqueIndex('jobs_dedupe_uq').on(t.orgId, t.dedupeKey),
  ],
);

export const idempotencyKeys = pgTable(
  'idempotency_keys',
  {
    orgId: orgId(),
    userId: uuid('user_id').notNull(),
    key: text('key').notNull(),
    route: text('route').notNull(),
    requestHash: text('request_hash').notNull(),
    statusCode: integer('status_code').notNull(),
    response: jsonb('response').$type<unknown>(),
    createdAt: createdAt(),
  },
  (t) => [primaryKey({ columns: [t.orgId, t.userId, t.key] })],
);

export const counters = pgTable(
  'counters',
  {
    orgId: orgId(),
    name: text('name').notNull(),
    value: integer('value').notNull(),
  },
  (t) => [primaryKey({ columns: [t.orgId, t.name] })],
);

/** Tables without org scoping; everything else gets RLS. */
export const GLOBAL_TABLES = ['orgs', 'users', 'memberships', 'sessions'] as const;
