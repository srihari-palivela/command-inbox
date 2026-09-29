# Command Inbox — Implementation Plan

**Status:** Approved for build · **Owner:** Platform architecture · **Last updated:** 2026-09-27
**Source of truth for UX:** `docs/design-source/Command Inbox.dc.html` (Claude Design prototype) and `docs/design-source/UX Review.dc.html`

---

## 1. What we are building

Command Inbox is a multi-tenant SaaS product for banks. It reads customer email that arrives in the
bank's service mailboxes, triages it with a pipeline of AI agents, and routes each query into one of
three **lanes**:

| Lane | Label in UI | What the AI does | Who is accountable |
|---|---|---|---|
| A | **Auto** — the AI does it | Fills an action template from the thread, validates it against core systems, and executes it — alone only in the one risk cell that policy allows, otherwise behind an approval gate | Named approver(s); sampled post-hoc review for the autonomous cell |
| B | **Draft** — you send it | Writes a reply grounded *only* in approved knowledge, with citations, and flags any gap instead of filling it | The person who sends it |
| C | **You** — the AI steps back | Writes nothing customer-facing. Assembles a brief (history, records, clocks, suggested moves) and hands over | The assignee |

The product principle, repeated on the sign-in screen, is the whole architecture in one line:
**"The AI addresses the query. You stay accountable."** Every design decision below is traceable to it:
approval is never implicit, irreversible actions are never automated, every decision is audited, and the
AI admits uncertainty rather than guessing.

### 1.1 Personas and surfaces

| Persona | Role key | Primary surfaces |
|---|---|---|
| Customer support staff | `staff` | Inbox (copilot workspace), Tickets, Boards, Learning, Settings |
| Team lead / checker | `lead` | + Performance, Results, Skills & clearance, auto-assign, second approvals |
| Risk & compliance admin | `admin` | + AI agents, What it can do (risk matrix), Rules & policies, What it knows, Who owns what, Where mail arrives |

### 1.2 Scope inventory (from the prototype)

15 screens, 11 overlays/wizards. Everything below is in scope for this build:

- **Inbox / copilot workspace** — personal queue with lane + deadline filters; ticket detail with
  Conversation / Ticket fields / AI trace tabs; streaming reasoning; evidence; filled action with field
  provenance; cited draft with diff-style edit; manual brief with suggested moves; **approval gateway**
  (reversibility, customer wait, duplicate check, maker–checker); override lane; reject with reason;
  reply composer; call customer (live transcript → recording, transcript, AI summary, ticket updates);
  AI activity rail (collapsible); shift stats; J/K/A keyboard.
- **Ticket system (Jira-like)** — 26+ fields, status transitions, watch, escalate, split, merge,
  reassign, sub-tasks, activity log (notes / public replies / calls / system history), people,
  attachments, labels, linked objects, customer history & recurrence, SLA clocks.
- **Tickets board** — kanban by status or query type, list view, board tabs (per mailbox), owner chips,
  8 faceted filters with live counts, free-text search, **natural-language query → filters**, ticket drawer.
- **Boards** — one board per mailbox source; create a board via **OAuth mailbox connection**
  (Microsoft 365 / Google Workspace / IMAP).
- **Performance** — KPI tiles with sparklines, speed by query type (before/now), agent-raised alerts,
  staff load & availability (calendar/check-ins), **auto-assign** at-risk work, custom KPIs.
- **Results** — baseline vs now, daily TAT chart, automation coverage, rollout phases, value pools.
- **Skills & clearance** — person × department clearance matrix (none/read/resolve/approve), load.
- **Learning & notification center** — messages and learning cards (swipe cards + quiz), agent-raised or
  manual, course completion tracking.
- **AI agents** — roster (template, model, boards, cost, eval score), prompt, evals, **new agent wizard**,
  feedback store (overrides / edits / rejections → prompt or context tuning).
- **What it can do** — reversibility × money-movement risk matrix, action library per cell, **autonomy
  dial** (locked for the irreversible+money cell), new action wizard.
- **Rules & policies** — bucketing rules, priority rules (hard vs weighted, toggles), permission matrix,
  approval cycles.
- **What it knows** — knowledge sources (connect/monitor/sync), readiness by department, gap tickets.
- **Who owns what** — taxonomy of query types by accountable department owner.
- **Where mail arrives** — mailboxes, system connectors, guardrails.
- **Chrome** — org switcher (multi-workspace), role, ⌘K palette (jump / ask the copilot), notifications,
  avatar menu, settings (profile, notifications, work prefs, signature, sessions, security), sign-in.

---

## 2. Architecture overview

```
                ┌────────────────────────── Tenant: bank ──────────────────────────┐
 Mail providers │                                                                   │
 (Graph, Gmail, │  ┌──────────┐   ┌───────────────┐   ┌──────────────────────────┐   │
  IMAP, dev) ───┼─▶│  Intake  │──▶│ Triage        │──▶│ Ticket + lane + gate     │   │
                │  │ adapters │   │ pipeline      │   │ (Postgres, RLS)          │   │
                │  └──────────┘   │ (agents, LLM) │   └────────────┬─────────────┘   │
                │                 └──────┬────────┘                │                 │
                │                        │ spans/cost              │ approvals       │
                │                        ▼                         ▼                 │
                │                 ┌─────────────┐          ┌────────────────┐        │
                │                 │ Trace store │          │ Action executor │──▶ Core banking,
                │                 └─────────────┘          │ (idempotent)    │    cards, payments
                │                                          └────────────────┘        │
                │   every write ─▶ audit_events (hash-chained, append-only)          │
                │   every write ─▶ outbox ─▶ LISTEN/NOTIFY ─▶ SSE to browsers        │
                └───────────────────────────────────────────────────────────────────┘
```

### 2.1 Deployables — a modular monolith

One TypeScript codebase, three processes. We deliberately **do not** start with microservices: the domain
boundaries are still moving (the prototype changed IA four times), and the dominant risk is correctness of
the approval/audit path, not independent scaling. Modules have explicit interfaces and own their tables,
so any module can be extracted later without a rewrite.

| Process | Responsibility | Scale unit |
|---|---|---|
| `api` | HTTP JSON API (`/v1`), SSE stream, auth, OAuth callbacks | Stateless, horizontal behind LB |
| `worker` | Triage pipeline, action execution, delayed sends (recall window), outbox dispatch, schedulers (SLA sweep, 5-min priority re-rank, knowledge sync, checker escalation) | Horizontal; jobs claimed with `FOR UPDATE SKIP LOCKED` |
| `web` | Static SPA (React) served by CDN / nginx | CDN |

### 2.2 Technology choices (ADR summary)

| # | Decision | Why | Rejected alternative |
|---|---|---|---|
| ADR-01 | **TypeScript end to end** (Node 22, strict) with a shared `contracts` package (Zod) | One schema drives server validation, OpenAPI and client types; no drift between FE and BE | Separate OpenAPI codegen — more moving parts |
| ADR-02 | **Fastify 5** + `fastify-type-provider-zod` | Fast, schema-first, first-class hooks for auth/tenancy/audit | Express (no schema story), NestJS (heavier, decorator magic) |
| ADR-03 | **PostgreSQL 16** as the only system of record; **Drizzle ORM** + SQL migrations | Transactions across ticket/approval/audit are the core invariant; Drizzle stays close to SQL | Mongo (no multi-row txns we trust), Prisma (weaker SQL escape hatch for RLS/locks) |
| ADR-04 | **Row-level security** keyed on `app.org_id`, set per transaction, in addition to repository-level scoping | Defense in depth against cross-tenant leaks — a missed `WHERE` cannot leak another bank's mail | App-only scoping |
| ADR-05 | **Transactional outbox + Postgres job queue** (`SKIP LOCKED`), `LISTEN/NOTIFY` fan-out to API nodes | Exactly-once *effects* with at-least-once delivery, zero extra infra at pilot scale | Kafka/SQS on day one — revisit at > 200 msg/s sustained (§9) |
| ADR-06 | **Hash-chained, append-only audit log** (`UPDATE`/`DELETE` blocked by trigger and revoked grants) | Regulators ask "prove nothing was edited"; the chain makes tampering detectable | Plain audit table |
| ADR-07 | **LLM provider abstraction** with Claude (official `@anthropic-ai/sdk`, structured outputs) and a deterministic **heuristic provider** | Tests are deterministic; an LLM outage degrades to lane C instead of stopping intake | Hard dependency on one provider |
| ADR-08 | **Policy-as-code** for the permission matrix, risk cells and approval chains; rules (bucketing, priority) as data | Security-critical invariants are reviewed as code; tunable heuristics are editable by admins | Everything configurable (lets an admin disable maker–checker) |
| ADR-09 | **Opaque server sessions** (hashed token in DB, HttpOnly cookie) + CSRF header; OIDC SSO in production | "Sign out of other devices" and instant revocation are product features | Stateless JWT (cannot revoke) |
| ADR-10 | **React 19 + Vite + TanStack Query + React Router**, CSS variables design tokens, no UI kit | The design language is bespoke and dense; a kit would fight it | MUI/Chakra |

### 2.3 Repository layout

```
apps/
  server/                 api + worker (one package, two entrypoints; tsup bundles each)
    src/
      config/             env parsing (zod) with production guards
      platform/           errors, logger, crypto, clock, request context, audit chain, outbox, jobs,
                          rbac, device/session, http helpers (tenant txn, idempotency), sse, metrics
      domain/             pure rules: risk matrix, lanes, priority, SLA, transitions, PII, NL filter
      modules/
        auth/             sign-in, sessions, org and demo-role switch, nav counts
        tickets/          queries, board facets, detail assembly, commands (status, assign, split…)
        gateway/          gate state, approvals (maker–checker), execution, undo/recall, duplicate check
        triage/           pipeline, trace spans, LLM providers (Claude, heuristic, circuit breaker)
        intake/           signed webhook, mail simulator, Microsoft/Google OAuth
        people/ calls/    staff, clearance, auto-assign; call sessions, transcripts, wrap-up
        setup/            agents & boards; actions, dial, policies, knowledge, ownership, mailboxes
        insights/ learning/ search/
      db/                 schema.ts, migrations/ (generated + security.sql), seed/, reset
      routes/             auth, tickets, workspace
      main.ts worker.ts
    test/integration/     Fastify + real Postgres: RLS, audit chain, gateway, idempotency, RBAC
  web/                    React SPA
    src/app ui lib features/<area>   shell & routes; primitives; api/query/SSE; one folder per screen
    e2e/                  Playwright against the running stack
packages/
  contracts/              Zod schemas + inferred types shared by server and web (side-effect free)
docs/                     architecture, ux, dev conventions, design source
infra/docker/             server & web Dockerfiles, nginx.conf, Postgres init
docker-compose.yml        local production-shaped stack
.github/workflows/ci.yml  lint, format, typecheck, unit, integration, e2e, image builds
```

---

## 3. Domain model

### 3.1 Aggregates and key tables

All tenant tables carry `org_id uuid not null` and an RLS policy `org_id = current_setting('app.org_id')::uuid`.

| Aggregate | Tables | Notes |
|---|---|---|
| Org & identity | `orgs`, `users`, `memberships(role)`, `sessions`, `user_settings` | Role is per membership — the same person can be Admin in one bank and Staff in another (the org switcher shows this) |
| Organisation structure | `departments`, `query_types` (taxonomy, owner, default lane), `clearances(user, dept, level 0–3)`, `staff_availability` | Nothing unowned can be automated (enforced in the lane decision) |
| Intake | `mailboxes` (provider, scopes, encrypted OAuth tokens), `boards`, `inbound_messages` (raw, dedup on provider message id) | Attachments stored by key; bodies PII-masked before any model call |
| Customers | `customers` (CIF, segment, since), `customer_accounts` | Customer history and recurrence are queries over tickets by customer |
| Ticket | `tickets` (number `QRY-nnnnn`, lane, status, priority, SLA, assignee, version), `messages`, `comments`, `subtasks`, `attachments`, `watchers`, `ticket_links` (split/merge/linked objects) | Optimistic concurrency via `version`; human key is per-org sequence |
| Triage | `triage_runs` (reasoning, evidence, confidence, lane decision), `trace_spans` (agent, model, latency, tokens, cost, status) | One run per (re)triage; spans are the "AI trace" tab |
| Actions & gateway | `action_templates` (code, system, reversible, money_moves, approval mode, owner), `autonomy_dial(cell, level, locked)`, `action_instances` (fields with provenance, idempotency key, maker/checker, execute_after, state) | See state machine §4.2 |
| Drafts | `drafts` (original, current, citations, flagged gaps, send_after, state) | Edit diff between `original` and `current` becomes eval data |
| Calls | `calls` (state, duration, transcript turns, summary, proposed ticket updates, recording key) | Telephony is an adapter; dev adapter simulates |
| Agents | `agents`, `agent_versions` (prompt, model, eval status), `agent_boards`, `agent_evals`, `feedback` | Every prompt change is a new version; new agents start in *observe* |
| Policies | `bucket_rules`, `priority_rules` (hard / weighted, enabled), permission matrix & approval cycles in code | Hard rules cannot be switched off — enforced server-side |
| Knowledge | `knowledge_sources` (kind, health, sync), `knowledge_docs` (status pending/approved/stale, verified_at, owner), `gap_tickets` | Only `approved` docs can be cited |
| Insights | `daily_metrics`, `alerts`, `kpis` | Live metrics are computed; history is materialised daily |
| Learning | `notifications`, `notification_reads`, `courses` (cards, quiz), `course_completions` | Team completion % is computed |
| Platform | `audit_events`, `outbox`, `jobs`, `idempotency_keys`, `ticket_sequences` | |

### 3.2 The risk matrix is the core policy object

```
                    Can be undone                      Cannot be undone
No money moves   ┌───────────────────────────┐   ┌──────────────────────────────┐
                 │ cell 0-0                  │   │ cell 0-1                     │
                 │ dial: suggest|approve|AUTO│   │ dial: suggest|approve        │
                 │ auto only after evals +   │   │ maker + checker, always      │
                 │ Risk sign-off; 5% sampled │   │                              │
                 └───────────────────────────┘   └──────────────────────────────┘
Money moves      ┌───────────────────────────┐   ┌──────────────────────────────┐
                 │ cell 1-0                  │   │ cell 1-1   LOCKED            │
                 │ approver + 30 s undo      │   │ suggest only, maker+checker, │
                 │ window (delayed commit)   │   │ never automated (policy)     │
                 └───────────────────────────┘   └──────────────────────────────┘
```

The approval chain for an action is `max(template.approval_mode, cell_minimum)` — a template can raise
the bar (e.g. provisional credit is *Dual* inside cell 1-0) but never lower it below the cell.

---

## 4. Core flows and state machines

### 4.1 Intake → triage pipeline

1. **Intake adapter** receives a message (Graph change notification / Gmail watch / IMAP poll / signed dev
   webhook). Dedup on `(mailbox_id, provider_message_id)`; thread onto an open ticket by
   `In-Reply-To`/`References` or (customer, subject) within 14 days, else create a ticket in `triaging`.
2. A `triage` job is enqueued in the same transaction (outbox pattern).
3. The worker runs the pipeline; each stage emits a **trace span** (agent, model, action, output,
   latency, tokens, cost, status `ok|flag|stop`):

| # | Stage | Kind | Output |
|---|---|---|---|
| 1 | Mail intake | system | thread assembled, sender → CIF, **PII masked** |
| 2 | Guardrail Sentinel | model (fast) + keyword rules | hard stops: regulator/ombudsman, legal notice, fraud, vulnerable customer, 3rd contact → `stop` forces lane C, suppresses generation |
| 3 | Bucketer | deterministic bucket rules, then model | query type + confidence + 3 driving phrases; "ambiguous" allowed |
| 4 | Multi-intent check | model + rule | proposes split into child tickets (human decides) |
| 5a | Field Extractor (lane A candidates) | model | template fields with source (`email body`, `CIF`, `inferred · p`) — never guesses amounts/accounts/dates |
| 5b | Reply Drafter (lane B candidates) | model, **grounded** | draft citing only `approved` docs; unanswerable parts flagged and a **gap ticket** raised |
| 5c | Summariser (lane C) | model | brief: summary, context rows, suggested moves |
| 6 | Policy engine | rules | risk cell, approval chain, autonomy dial → lane |
| 7 | Priority Ranker | rules + model tie-break | P1–P4 with the rule that fired |
| 8 | Router | rules | department, clearance-checked assignee with a written reason |

**Lane decision (deterministic, unit-tested):**
- Any `stop` → **C**. Confidence `< 0.78` (org-configurable bar) → **C** (or **B** with flagged gaps when a
  partial grounded answer exists). Query type without an owner → **C**. Multi-intent → **C** with split proposal.
- Action template matched and all mandatory fields extracted → **A**; executes autonomously **only** if
  cell is 0-0, dial = auto, template approval = auto and the agent version has passed evals; otherwise waits
  at the gate.
- Informational with full grounded coverage → **B**.

**Degradation:** if the LLM provider errors or times out (circuit breaker, 3 retries with jittered
backoff), the ticket goes to **C** with an "AI unavailable — handed over with raw thread" note. Intake
never blocks on a model.

### 4.2 Action instance state machine (the approval gateway)

```
 drafted ──(maker approves)──▶ awaiting_checker ──(checker approves)──▶ scheduled ──▶ executing ──▶ executed
    │                                 │                                   │  (undo window,           │
    │                                 │(checker silent 2h → escalate)     │   money+reversible)      │
    ├──(reject w/ reason)──▶ rejected ◀┘                                  └──(undo)──▶ cancelled      └─▶ failed (retry/compensate)
    └──(single-approval chain)──────────────────────────────────────────▶ scheduled
```

Invariants (enforced in a single DB transaction with `SELECT … FOR UPDATE` on the instance):
- **Maker ≠ checker.** Checker must hold role `lead|admin` **and** clearance *approve* (3) in the ticket's department.
- Maker must hold clearance ≥ *resolve* (2) in the department.
- **Idempotency:** `idempotency_key = sha256(org, template, canonical(fields))`, unique. A duplicate
  approval or a retried job cannot execute twice; the executor also asks the connector whether the effect
  already exists before writing.
- **Duplicate check at the gate:** "No similar action on this account in 90 days" is computed from
  executed instances for the same account + template — shown *before* commitment.
- **Undo is only offered where the action is reversible** (fixes a prototype defect that showed "Undo 29s"
  on a "Cannot be undone" action). Reversible + money actions use a **delayed commit** (`execute_after =
  now + 30s`); undo cancels before anything reaches the core system.
- Cell 1-1 can never reach `scheduled` without two distinct human approvals, regardless of dial state.

### 4.3 Draft send

`approve & send` → draft `scheduled` with `send_after = now + 60s` ("Recallable for 60s after send") →
worker sends via the mailbox adapter under the approver's name → `sent`. Recall inside the window cancels.
If `current ≠ original`, the diff is stored as a **draft-edit feedback** item against the Reply Drafter
(UX watchlist: "draft edits as eval data").

### 4.4 Ticket status machine

`triaging → awaiting_approval | executing | with_human`, `awaiting_approval → executing → resolved`,
`with_human ⇄ waiting_customer` (**SLA clock paused**), `* → resolved → closed`, `resolved → with_human`
(reopen, `reopen_count++`). Transitions are validated server-side; the UI only offers legal moves.

### 4.5 SLA and priority

- Budget by priority/segment policy (e.g. corporate urgent 4h, escalation 8h, standard 24h). `due_at` is
  recomputed when the clock pauses/resumes.
- Tone: **late** if past due; **almost late** if ≤ 60 min or ≤ 10% of budget left; **due soon** if ≤ 50% left;
  else **on track**.
- Priority rules (seeded from the design): regulator named → P1 (hard); deadline < 2h → P1 (hard);
  vulnerable customer → P1 + hard stop (hard); amount ≥ ₹10L → ≥P2; 3rd contact → ≥P2; deadline < 8h → ≥P2;
  informational → P4. Re-ranked every 5 minutes and on every ticket event.

### 4.6 Routing and auto-assign

Candidate = member with clearance ≥ *resolve* in the department, availability `available`, sorted by live
load (`open / capacity`). The system writes the reason into the ticket history ("closest skill match for
disputed transactions, lightest live load in Cards" — UX finding 04). **Auto-assign** (lead+) takes open
tickets that are P1/P2 or not on track and owned by the AI or nobody, and assigns them the same way; if
nobody qualifies it escalates to the team lead and says so.

---

## 5. API design

- REST/JSON under `/v1`, one resource per aggregate; OpenAPI 3.1 generated from the Zod contracts at `/v1/openapi.json`.
- **Errors:** RFC 9457 problem details (`type`, `title`, `status`, `detail`, `code`, `requestId`).
- **Auth:** session cookie `ci_session` (HttpOnly, Secure, SameSite=Lax) + `x-csrf-token` on mutations.
- **Tenancy:** active org lives on the session; `POST /v1/session/org` switches it. Every DB call runs in
  `withTenant(orgId)` which sets `app.org_id` for RLS.
- **Idempotency:** `Idempotency-Key` header honoured on approvals, sends, executions, call saves.
- **Concurrency:** ticket mutations accept `If-Match: <version>`; stale writes get `409 conflict`.
- **Pagination:** cursor-based (`?cursor=&limit=`) on list endpoints.
- **Realtime:** `GET /v1/stream` (SSE) pushes `ticket.updated`, `activity.created`, `notification.created`,
  `gate.updated`; the SPA invalidates the matching queries.

Key endpoints (full list in `apps/server/src/modules/*/routes.ts`):

| Area | Endpoints |
|---|---|
| Session | `POST /v1/auth/login`, `POST /v1/auth/logout`, `GET /v1/me`, `POST /v1/session/org`, `GET/DELETE /v1/sessions/:id` |
| Inbox & tickets | `GET /v1/inbox`, `GET /v1/tickets?filters`, `POST /v1/tickets/nl-filter`, `GET /v1/tickets/:id`, `PATCH /v1/tickets/:id`, `POST /v1/tickets/:id/{transition,assign,watch,escalate,split,merge,override-lane,comments,subtasks/:key}` |
| Gateway | `POST /v1/tickets/:id/gate/{approve,reject,undo,take}`, `POST /v1/actions/:id/{approve,reject}` |
| Drafts & replies | `PUT /v1/tickets/:id/draft`, `POST /v1/tickets/:id/replies`, `POST /v1/replies/:id/recall` |
| Calls | `POST /v1/tickets/:id/calls`, `POST /v1/calls/:id/{end,save}` |
| Intake | `POST /v1/intake/messages` (HMAC-signed), `GET /v1/oauth/:provider/start`, `GET /v1/oauth/:provider/callback` |
| Setup | `/v1/boards`, `/v1/agents` (+`/versions`, `/evals`, `/feedback`), `/v1/actions/templates`, `/v1/policies/*`, `/v1/knowledge/*`, `/v1/taxonomy`, `/v1/mailboxes`, `/v1/connectors` |
| People | `/v1/staff`, `PUT /v1/clearances`, `POST /v1/assignments/auto` |
| Insights | `/v1/insights/{performance,results,shift}`, `/v1/kpis`, `/v1/alerts/:id/{act,notify}`, `POST /v1/copilot/ask`, `GET /v1/search?q=` |
| Learning | `/v1/notifications`, `/v1/courses/:id/complete` |

---

## 6. Security, privacy, compliance

| Control | Implementation |
|---|---|
| Tenant isolation | RLS on every tenant table (`FORCE ROW LEVEL SECURITY`), app connects as a non-owner role; integration test proves a cross-tenant read returns zero rows |
| AuthN | OIDC (Entra ID / Google) in production; passwordless one-time code in dev/demo; sessions hashed (SHA-256) at rest, rotated on privilege change, idle timeout 12h |
| AuthZ | Capability matrix in code (mirrors "Who may do what"), checked in route guards **and** in domain services; clearance (ABAC) per department for resolve/approve; maker ≠ checker |
| PII | Account numbers, PAN, Aadhaar, card numbers, phones and emails masked to typed tokens before any model call; unmasked values rebound only at execution inside the executor |
| Secrets | OAuth refresh tokens encrypted with AES-256-GCM (key from KMS in prod, env in dev); never logged (pino redaction) |
| Outbound email | The AI holds no send scope; sends happen only under a named approver's identity |
| Audit | Append-only, hash-chained `audit_events` with actor, timestamp, source and confidence; `GET /v1/audit/verify` recomputes the chain |
| Web | Helmet headers, strict CSP for the SPA, SameSite cookies + CSRF header, rate limiting per session and per IP |
| Data residency | Single-region deployment per bank cluster; model calls routed with `inference_geo` where required |
| Retention | Raw mail and recordings retained per bank policy (default 7 years for complaints); deletion jobs are audited |

---

## 7. AI layer

- **Provider interface** `LlmProvider.classify / extract / draft / summarise / answer` with typed Zod
  outputs. Implementations: `ClaudeProvider` (official `@anthropic-ai/sdk`, `messages.parse` with
  `zodOutputFormat` structured outputs, prompt caching of the stable system prompt + taxonomy, adaptive
  thinking, refusal handling via `stop_reason` with server-side `fallbacks: "default"`) and
  `HeuristicProvider` (deterministic keyword/rule implementation used in tests, offline dev and degradation).
- **Models** are per agent and versioned. Seeds follow the design: judgement agents on `claude-sonnet-5`,
  guards on `claude-haiku-4-5`, the copilot on `claude-opus-5`. Admins can change the model per agent
  version; the change ships as a new version in *observe* until evals pass.
- **Grounding:** the drafter receives only `approved` knowledge documents for the query type's
  department and must cite `[n]`; the server rejects a draft that cites unknown sources.
- **Evals:** golden-set runs per agent version; live metrics (routing accuracy = not overridden,
  sent-as-drafted rate, hallucinated-value rate = 0 invariant). **Calibration** (UX watchlist) is computed
  per agent as observed accuracy by confidence bin and surfaced on the agent drawer.
- **Feedback loop:** overrides, rejections and draft edits are stored against the responsible agent,
  classified as *prompt tuning* or *context tuning*, and added to the golden set when a new version ships.
- **Cost:** tokens × model price per span; per-agent cost per 1k mails and monthly spend on the agents screen;
  per-org monthly budget with alerting.

---

## 8. Frontend architecture

- React 19 + TypeScript + Vite; React Router with **deep links** (`/inbox/QRY-48211`, `/tickets?status=approval`)
  — the prototype had no URLs, so nothing could be shared or bookmarked.
- TanStack Query for server state (per-resource keys, SSE-driven invalidation, optimistic updates on
  subtasks/watch/clearance); local UI state in components.
- Design tokens (`tokens.css`) extracted from the prototype palette: warm neutrals (`#F6F5F2` canvas,
  `#191A1D` ink), indigo accent `#4B4EC8`, semantic green/amber/red, Instrument Sans + IBM Plex Mono.
- Primitives: `Button`, `Pill`, `Card`, `Tabs`, `Segmented`, `Toggle`, `Avatar`, `Meter`, `Spark`,
  `Drawer`, `Modal`, `Menu`, `Toast`, `Kbd`, `EmptyState`, `Skeleton`.
- Accessibility: every clickable is a `button`/`a`; dialogs trap focus and restore it; tabs follow the
  WAI-ARIA pattern; visible focus rings; `prefers-reduced-motion` disables the rise/stream animations;
  text colours keep ≥ 4.5:1 on tinted panels (the design already corrected this).
- The detailed UI review and the refinements applied are in `docs/ux/frontend-review.md`.

---

## 9. Non-functional targets

| Dimension | Pilot target | GA target | How |
|---|---|---|---|
| Volume | 3k mails/day/bank, 5 banks | 50 banks × 20k/day (~12 msg/s avg, 100/s peak) | Horizontal workers; move job queue to SQS/Kafka above ~200/s sustained |
| Triage latency | p95 < 8 s to lane decision | p95 < 5 s | Parallel stages where independent, prompt caching, fast model for guards |
| API latency | p95 < 250 ms reads | p95 < 150 ms | Indexed queries, per-request tenant txn, no N+1 (tested) |
| Availability | 99.9% | 99.95% | Stateless API, managed Postgres with HA, workers idempotent |
| Durability | RPO 5 min, RTO 1 h | RPO 1 min, RTO 30 min | PITR/WAL archiving, tested restores |
| LLM outage | Intake continues; all new mail → lane C | same | Circuit breaker + heuristic provider |

Observability: pino JSON logs (request id, org id, user id, trace id), Prometheus `/metrics`
(HTTP RED, job lag, pipeline stage latency, lane mix, gate latency, approve-without-open rate, LLM
cost), OpenTelemetry traces across HTTP → DB → job → LLM. SLO alerts on job lag, triage latency and
executor failures.

---

## 10. Delivery plan

| Phase | Scope | Exit criteria |
|---|---|---|
| **0 · Foundations** (this build) | Monorepo, contracts, schema + RLS + audit chain, auth/sessions, RBAC, jobs/outbox, SSE, CI | Tenancy + audit tests green; CI < 10 min |
| **1 · Drafts only** | Intake (dev + Graph), triage pipeline, Inbox, ticket system, board, knowledge grounding, gap tickets | 2-week observe run; ≥ 75% drafts sent unedited; 0 ungrounded sentences |
| **2 · Reversible actions** | Action library, cell 0-0 auto with sampled review, auto-assign, performance | Reversal rate < 0.5% over 30 days |
| **3 · Money moves with approval** | Cells 1-0 / 0-1 / 1-1 with maker–checker, undo windows, duplicate checks | Risk sign-off; zero duplicate executions in chaos tests |
| **4 · Wider autonomy** | One cell at a time, gated on Risk | Per-cell evidence pack |

Team shape for GA: 2 × product squads (Workspace, Setup & Risk), 1 × AI platform squad (pipeline, evals,
cost), 1 × infra/SRE, embedded Risk & Compliance partner. Each phase ends with a security review and a
5–8 participant task study on the approve/reject flow (per the UX review's method note).

### 10.1 Top risks

| Risk | Mitigation |
|---|---|
| Reviewers rubber-stamp a noisy gate | Track **approve-without-open** rate (canary), batch related approvals, cap gate volume per person |
| Model confidence drifts from accuracy | Calibration per agent on the agents screen; auto-demote an agent version to observe on drift |
| Knowledge staleness produces wrong answers | Source health monitoring, confidence down-weighting on stale docs, gap tickets with owners |
| Duplicate money movement | Idempotency keys + executor pre-check + 90-day duplicate check at the gate |
| Cross-tenant leak | RLS + non-owner DB role + isolation tests in CI |

---

## 11. What is real vs simulated in this repository

| Capability | Status |
|---|---|
| Tickets, lanes, gateway, maker–checker, idempotency, undo/recall windows, audit chain, RLS, RBAC, clearance, auto-assign, rules, knowledge, learning, KPIs, NL filters, search, SSE | **Real**, backed by Postgres and tested |
| Triage pipeline | **Real** orchestration; Claude provider when `ANTHROPIC_API_KEY` is set, deterministic heuristic provider otherwise |
| Mail intake | Signed dev webhook + simulator are real; Microsoft Graph / Gmail OAuth flows are implemented behind configuration and need tenant credentials |
| Core banking / cards / payments connectors | Sandbox connector with idempotency semantics; real connectors are per-bank integration work |
| Telephony | Simulated adapter (transcript playback); a CPaaS adapter (e.g. Twilio/Exotel) is the production path |
| Historical metrics | Seeded 12-week history so dashboards render on day one; live metrics are computed from tickets |

---

## 12. Verification

| Suite | What it proves | Where |
|---|---|---|
| Unit (25) | Risk matrix and chains (irreversible is always dual; a template can raise the bar, never lower it; only 0-0 auto-executes), lane order (safety → ownership → confidence), hard priority rules can't be disabled, SLA grading/pausing, transitions, PII masking, NL filter | `apps/server/src/domain/domain.test.ts` |
| Integration (17) | 401/CSRF/validation problem documents; RLS returns nothing without a tenant and one tenant with it; cross-workspace reads are 404; the app role cannot update or delete audit rows and the hash chain verifies; idempotent maker approval; maker ≠ checker; checker approval executes via the worker with no undo; recall inside the window, send after it; staff can't widen autonomy; the locked cell can't be dialled up; clearance writes are org-scoped; batch approvals count as approve-without-open | `apps/server/test/integration` (own `*_test` database) |
| Web unit (4) | Edit diff and flagged-phrase highlighting | `apps/web/src/features/inbox/diff.test.ts` |
| End-to-end (12) | Demo sign-in, queue order and deep links, J/K, full maker–checker execution, edit → send → recall, send-back with reason, take-over, fields/trace/composer, every lead and admin screen, staff denied setup, NL filter, command palette | `apps/web/e2e` — passes against the dev stack and against the Docker images behind nginx |

Bugs these found and fixed during the build: the session cache was orphaned on sign-in (new session
never rendered); card numbers were masked as Aadhaar numbers, leaking the last four digits to the model;
the Inbox auto-advanced past an open recall window; nav and queue counts disagreed.

### 12.1 Known gaps (tracked, not blocking the pilot)

| Gap | Why it matters | Next step |
|---|---|---|
| Calibration is keyed by agent name | A renamed agent would lose its history | Store `agent_id` on `prediction_outcomes` |
| A broken knowledge source can only request re-consent | Nothing completes the re-consent in this build | Wire the source OAuth callback, as for mailboxes |
| No endpoint lists who may own a query type | Ownership UI can only pick "next eligible" | `GET /v1/taxonomy/departments/:id/eligible` |
| Action template wizard's guardrails are display-only | `ActionTemplateBody` has no guardrails field | Add guardrail rows to templates and enforce them at the gate |
| Session list labels every device generically | Harder for a user to spot a stray session | Parse the user agent at sign-in |
| Seeded "light" tickets in Auto/Draft lanes carry no prepared work | The gate says "Nothing prepared yet" for them | Seed an action/draft for each, or run them through triage at seed time |

