# Command Inbox — Production plan (v3)

**Goal:** a product a bank can deploy and run on real customer mail. A platform operator creates the bank as a
tenant and invites its admin. The admin connects the bank's identity provider and **one shared mailbox (Microsoft
365 or Google Workspace)**, onboards users, loads knowledge, configures agents and workflows, and goes live in
stages. Everyone can see what is happening in the mailbox and in every agent run.

**Status:** plan for approval. It builds on [`python-platform.md`](python-platform.md) (v2), whose safety core stays:
row-level security, the hash-chained audit log, maker/checker, versioned deployments, eval gates, and the
System 1 / System 2 triage flow.

**Evidence base:**
- A code audit of the repository at `main` (c8f1b7f). Every gap below comes from that audit.
- A review of the Microsoft Graph, Exchange Online, Gmail API, Cloud Pub/Sub, Keycloak 26.x, Anthropic and
  Langfuse documentation. Sources are listed in [Appendix B](#appendix-b--sources).
- Items marked **[verify]** could not be confirmed against a primary source and must be checked before we rely
  on them.

---

## 0. Decisions recorded (2026-09-29)

| # | Decision | Consequence for this plan |
|---|---|---|
| D1 | **Bank #1 runs on a dedicated single-tenant stack** in the bank's region. | Same code and Helm chart as the SaaS; one tenant per stack. The platform console still exists (it provisions that one tenant and later ones). App registrations for mail and SSO can be **owned by the bank** (see D3). |
| D2 | **System 2: Anthropic API now, OpenAI supported as well. System 1: our open-source Jev-style decision engine** ("laya"), self-hosted. | Provider abstraction with two implementations (Anthropic, OpenAI) selectable per tenant and per node; Bedrock/Vertex deferred. Residency note: Anthropic's first-party API processes in US/global only; OpenAI offers regional data residency for eligible API projects **[verify for the bank's region]**. Both under zero-data-retention agreements. |
| D3 | **Mailbox v1: delegated OAuth for one mail account**, on Microsoft 365 or Google Workspace. The app-only models (Exchange RBAC for Applications, Gmail domain-wide delegation) move to v2. | See §7.0. Simplest consent (one account, one sign-in), smallest blast radius (the app can reach only that account's mail), no tenant-wide admin grants. Cost: the token belongs to one account, so the health model and a re-connect flow are essential. |
| D4 | **Langfuse open source.** | No Enterprise features. We mask before export (done), enforce retention with our own ClickHouse/S3 TTL jobs, and keep the audit trail in our hash-chained log, not in Langfuse. |
| D5 | **No actions against bank systems in v1.** | The AI reads, classifies, prioritises, extracts, drafts and briefs; **people send every reply and do every action in their own systems.** No connector registry, no core-banking or CRM calls, no auto lane in v1: the autonomy dial's ceiling is "draft for approval". §10 moves to v2. Customer matching uses the sender address and in-mail identifiers only. |

## 1. Summary

**What is real today:**
- tenant isolation (RLS) and the audit chain
- the approval gateway (maker ≠ checker, undo and recall windows)
- the job queue, deployments with versions, and eval gates
- the LangGraph triage flow, including a calibrated System 1 and Claude as System 2
- per-run traces and costs
- Keycloak BFF sign-in on the API side

**What is not real:**
- **Mail:** nothing reads mail from a mailbox and nothing sends mail. The OAuth "connect" stores a token that
  is never used, and replies are only written to our own database.
- **Actions** run against an in-memory stand-in, not a bank system.
- **Knowledge** is never ingested.
- **Sign-in:** the web sign-in page cannot use SSO.
- **Operator and tenants:** there is no platform operator, and tenants exist only in the seed.
- **Metrics:** most dashboard numbers come from seeded tables that nothing updates.
- **Empty tenants:** a fresh tenant has no way to create its departments, query types or templates.

**What this plan delivers, in order:**
1. Remove the prototype scaffolding (§4, Phase 0).
2. Build a **platform console** (super-admin app) and **tenant provisioning** with tokenised admin invites (§5).
3. Give the bank admin a **guided onboarding** that ends in a go-live checklist (§6).
4. Build **real mailbox connectors** for Microsoft Graph and Gmail: subscribe/watch, delta/history catch-up,
   in-thread send, attachments, and a health model (§7).
5. Build a **knowledge pipeline**: parse, chunk, embed with pgvector, approve and retrieve (§8).
6. Build an **agent and workflow studio** on top of deployments, with an **action connector registry** that
   calls the bank's systems through its integration layer (§9, §10).
7. Build **monitoring** for the mailbox, the pipeline, agents, SLAs and cost, computed from real events, with
   alerts (§11).
8. Complete **security, compliance, infrastructure and operations** to the level a bank's third-party-risk review
   expects (§12–§14).

The first bank runs through **shadow → assisted → partial autonomy** stages (§16).

**Recommended deployment for bank #1:** a **dedicated single-tenant stack** in the bank's cloud account or ours,
in the bank's region. It uses the same code as the multi-tenant SaaS, which stays the target for later banks.
Banks' third-party-risk processes clear a dedicated deployment much faster.

---

## 2. Current state: gap inventory

Severity: **B** = blocks bank go-live, **M** = major, **m** = minor.

| Area | Gap | Sev | Evidence (file) |
|---|---|---|---|
| Sign-in | The sign-in page's "Continue with Microsoft Entra ID" and "one-time passcode" buttons call the demo login. The web never uses `/v1/auth/oidc/login`. It also shows hard-coded marketing figures. | B | `apps/web/src/app/SignIn.tsx` |
| Demo leftovers | The role switcher is **removed (commit 41fdb83)**. Still present: the demo user picker, "Simulate an email", scripted telephony, and hard-coded insight claims. | m–M | `modules/intake/router.py`, `modules/calls/scripts.py`, `modules/insights/service.py` |
| Tenancy | There is no platform operator role, no tenant create/suspend/delete, and no plans or limits. Orgs exist only in the seed. | B | `core/context.py`, `seed/` |
| Invitations | An invitation creates a row only: no email, no token link. It completes only at SSO sign-in, and only if the org trusts an IdP. No API sets the org's IdP or domains. | B | `modules/members/service.py`, `auth/oidc.py` |
| Mailbox in | There is no Graph or Gmail client. Nothing does subscriptions, watch, delta or history, and there is no refresh-token use. `credentials_enc` is never read, and the "streaming" state is false. | B | `modules/intake/oauth.py` |
| Mailbox out | `send_reply` and `send_draft` only insert a local row. There is no in-thread headers or provider message IDs, and the OAuth scopes are read-only. | B | `modules/gateway/jobs.py` |
| Mail content | Plain text only: no HTML, attachments, cc or headers, and no AV scanning. | M | `schemas/requests.py` |
| Knowledge | The sync job invents a document count. There is no parsing, chunking, embeddings or vector store; retrieval takes "first 6 approved docs". There is no upload or approve API. | B | `modules/setup/jobs.py`, `agents/flow/nodes.py` |
| Agents | There are two agent concepts: the Setup catalog and deployment flows. Runtime agent selection has no ordering, and the agent `observe` state is ignored. Agent versions are display-only. | M | `agents/runner.py`, `setup/agents_boards.py` |
| Actions | Every template executes against a per-process `SandboxConnector`. The connector table is display-only, and field schemas are hard-coded per seeded template. | B | `gateway/connectors.py`, `agents/providers/types.py` |
| Customers | Unknown senders get a fabricated CIF. There is no customer lookup. | B | `modules/intake/service.py` |
| Master data | Departments, query types, alerts, courses, clearances, availability, connectors and knowledge docs are created only by the seed. There are no CRUD endpoints. | B | whole of `modules/setup` |
| Metrics | KPI tiles, Results, "saved minutes" and agent spend read `daily_metrics`, which nothing ever writes. Volumes and readiness are seed columns. | B | `modules/insights/service.py` |
| Monitoring | There is no mailbox health (lag, errors, subscription or token expiry) and no per-agent dashboard. SLA alerts never fire. | B | — |
| Jobs | `sla_sweep`, `retention_sweep` and `rerank` are no-ops. The scheduler enqueues only `rerank`. | M | `worker_registry.py`, `worker.py` |
| Config | All integration secrets are process env vars, there is no KMS, and there is one global app registration per provider. There are no per-tenant model, residency, retention, locale or currency settings, and no tenant settings UI. | M | `config.py`, `core/crypto.py` |
| Ops | There is no migrate-only command: a fresh deploy seeds three demo banks. There is no transactional email, no audit export, no backups/DR/IaC, and rate limits are per IP only. | B | `seed/cli.py`, `docker-compose.yml`, `infra/` |
| Frontend | There is no platform console, no onboarding wizard, no tenant settings, and no mailbox health or reconnect UI. Formatting is India-specific. | B | `apps/web` |
| Tests | Every integration test clones the seeded template, so nothing tests an empty tenant. | M | `tests/conftest.py` |

---

## 3. Target architecture

```
                         ┌──────────────── Platform console (console.<domain>) ─────────────────┐
 Operator staff ───SSO──▶│ tenants · plans · provisioning · invites · fleet health · support     │
 (our Keycloak realm,    │ break-glass (time-boxed, audited) · releases · usage & cost           │
  MFA required)          └─────────────────────────────┬─────────────────────────────────────────┘
                                                       │ /v1/platform/* (separate router, platform RBAC domain)
 Bank staff ──SSO──▶ Keycloak (realm "tenants", one Organization per bank, bank IdP brokered)
                     │
                     ▼
 Tenant app (app.<domain>) ── /v1/* ── API (FastAPI, stateless, N replicas) ── Postgres (RLS, pgvector)
                                          │                                      │
                                          ├─ outbox → SSE (LISTEN/NOTIFY)        ├─ object storage (raw MIME,
                                          │                                      │   attachments, knowledge, exports)
 Microsoft Graph ──webhook──▶ Ingress (/v1/hooks/graph, /v1/hooks/gmail) → enqueue → 202
 Gmail Pub/Sub  ──push──────▶         (verify, dedupe, persist, no processing inline)
                                          │
                               Workers (separate deployment, per-queue concurrency, per-mailbox semaphores)
                               ├─ mail-sync   (delta/history, fetch, parse, store, AV scan, thread)
                               ├─ mail-send   (createReply/send, Gmail messages.send, attachments)
                               ├─ triage      (LangGraph: System 1 local model · System 2 Claude)
                               ├─ knowledge   (parse → chunk → embed → index)
                               ├─ actions     (connector registry → bank integration layer)
                               ├─ schedulers  (subscription/watch renewal, token refresh, SLA sweep,
                               │               metrics rollup, retention, eval runs)
                               └─ notifications (transactional email, alert fan-out)
 Model plane: vLLM/llama.cpp (System 1, GPU/CPU pool) · Claude via Anthropic API | AWS Bedrock | Vertex (per tenant)
 Observability: OTel Collector → traces/logs backend + Langfuse (AI) · Prometheus → alerting
 Secrets: KMS/HSM envelope encryption (per-tenant data keys, BYOK option) · Vault or cloud secret manager
```

**Principles:**
1. **Webhooks never do work inline.** They verify, persist and enqueue, then return 202. Graph drops endpoints
   that answer slowly, and dropped notifications are not recoverable, so a periodic delta sweep backs up every
   subscription.
2. **The source mailbox is never destructive.** We use categories/labels and folder moves only, so the bank's
   retention, legal hold and journaling stay authoritative.
3. **Configuration is data.** Anything a bank chooses (identity provider, mailbox, taxonomy, rules, models,
   thresholds, connectors, retention) lives in versioned, audited tenant configuration, not in env vars.
4. **The same code runs every topology.** Multi-tenant SaaS and a dedicated per-bank stack differ only in
   Helm values and Terraform variables.
5. **Every model decision and every action is reproducible.** We record the deployment version, config hash,
   prompt version, model, inputs (masked) and outputs.

### 3.1 Roles

| Plane | Role | Can |
|---|---|---|
| Platform | `platform_owner` | Create, suspend or delete tenants; manage plans; manage operator staff. |
| Platform | `platform_operator` | Provision, invite tenant admins, see fleet health, run support tooling. **No tenant data access.** |
| Platform | `platform_support` (break-glass) | Time-boxed, read-only access to a tenant. It needs a ticket reference and the tenant's approval setting, is fully audited, and shows a banner inside the tenant. |
| Tenant | `admin`, `lead`, `staff` | Unchanged: exactly one role per tenant, delegable permissions only. |

Operator identities live in a separate Keycloak realm (`operators`) with mandatory MFA (WebAuthn). Platform APIs
live under `/v1/platform/*`, enforced by a separate Casbin domain (`platform`). **No platform role grants tenant
data access** outside break-glass.

---

## 4. Phase 0: Remove prototype scaffolding (1 week)

| Item | Action |
|---|---|
| Role switcher | ✅ Done (41fdb83). |
| Sign-in page | Replace with: an email field for **tenant discovery by domain**, which redirects to `/v1/auth/oidc/login?org=<alias>` and Keycloak routes to the bank's IdP. Remove the passcode button and the marketing figures. Keep the demo picker only in development builds (compile-time flag, not just `DEMO_MODE`). |
| Seed | Split the CLI: `command-inbox-migrate` (alembic only) and `command-inbox-seed --demo` (development and CI only). Production images and Helm run migrate only. Add a separate **starter pack** (see §6.5) for real tenants: neutral templates, not demo banks. |
| Simulated features | "Simulate an email" becomes a **test-mail tool**: it sends a real test message to the connected mailbox via the provider API and follows it through. Scripted telephony is hidden behind a feature flag (not in scope for v1). Hard-coded insight claims and phases are deleted. |
| Fabricated data | Stop inventing a CIF for unknown senders; show "Unmatched sender" and a link action. Stop the `12 + len(name)` doc count. |
| Locale | Add a tenant locale, currency and time zone; formatting reads them, replacing the INR/IN defaults. |
| Hygiene | Delete `apps/web/dist` from the tree and fix README references. |
| Tests | Add an **empty-tenant** integration fixture (migrated, no seed) and a smoke suite that runs every screen against it. |

---

## 5. Platform console and tenant lifecycle

A separate SPA: `apps/console`, which reuses the UI kit, served on its own origin with its own session
cookie and CSP. The API adds `/v1/platform/*` routers.

### 5.1 Tenant lifecycle

```
draft ──provision──▶ provisioned ──admin accepts──▶ onboarding ──checklist green──▶ shadow
  └─ delete            │                              │                              │
                       └─ invite expired → re-invite  └─ abandon → archived          ▼
live ◀── assisted ◀── shadow          live ──suspend──▶ suspended ──resume──▶ live
live/suspended ──offboard──▶ exporting ──▶ export delivered ──▶ purged (crypto-shred)
```

**Provisioning** is one idempotent, resumable job with steps. Each step is recorded and retried on failure, and
the console shows the progress.
1. Create the `orgs` row. Fields: slug, legal name, region, plan, locale, currency, time zone,
   `data_residency_profile`, `status=provisioned`.
2. Create a **per-tenant data key** in KMS; the bank may supply its own (BYOK).
3. Create the **Keycloak Organization** through the admin REST API (`POST /admin/realms/{realm}/organizations`,
   with alias = slug and the bank's verified email domains).
4. Create the default deployment from the **starter pack** (neutral taxonomy, required safety nodes,
   conservative thresholds, autonomy dial at "suggest only").
5. Create **plan limits and quotas**: mailboxes (1 at launch), seats, monthly mail volume, model spend cap,
   storage.
6. Send the **first-admin invitation** (§5.2).

### 5.2 First-admin invitation (tokenised)

- The operator enters the admin's name and email. The API creates an `invitations` row with a hashed
  single-use token (32 bytes, 72-hour expiry) and sends an email via transactional email (§13.3).
- The email link lands at `app.<domain>/accept?token=…`. It shows the tenant name and inviter, then routes to
  Keycloak:
  - **Before the bank IdP is connected:** the invite creates a *local* Keycloak user for the admin, with
    WebAuthn/TOTP and password required actions. That account is flagged `bootstrap=true` and is **disabled
    automatically once bank SSO is live** and another admin exists.
  - **After:** the admin signs in through the bank's IdP, and we bind by `issuer|sub`.
- On acceptance, the membership is created with `role=admin` and the tenant moves to `onboarding`. Everything is
  audited in the tenant chain and in the platform audit log.
- Keycloak 26.5+ also has org invitations (`POST /organizations/{id}/members/invite-user`). We still use our
  **own** tokens so the flow is independent of Keycloak version changes and works identically for
  non-Keycloak IdPs. Keycloak invites remain an option.

### 5.3 Console features (v1)

- **Tenants list:** status, region, plan, onboarding progress (checklist %), mailbox health, 24-hour volume,
  error rate, model spend against the cap, open incidents.
- **Tenant detail:**
  - lifecycle actions: suspend, resume, offboard, re-invite, reset bootstrap admin
  - plan and quotas, residency, KMS key status
  - provisioning log
  - support notes
  - a break-glass request button
- **Fleet health:** worker queues and lag, webhook error rates, subscription/watch expiries across tenants,
  model-provider errors, cost.
- **Releases:** the current version per stack, migrations applied, feature flags per tenant.
- **Platform audit log:** every operator action, exportable.

---

## 6. Tenant onboarding (bank admin)

A guided **Onboarding checklist** is the admin's landing page until go-live. Each step has a status (not
started, in progress, verified, blocked) and a **verification test** that must pass. Steps can be done in any
order except where noted.

| # | Step | What the admin does | Verification |
|---|---|---|---|
| 1 | **Organisation profile** | Legal name, logo, locale, currency, time zone, business hours and holidays (for SLAs), support email. | All fields set. |
| 2 | **Single sign-on** | Choose Entra ID or Google. For Entra: register our OIDC client in the bank tenant (we generate the exact values) or use our multi-tenant app. Enter the tenant ID. Verify email domains by **DNS TXT record**. | Test sign-in by a second bank user succeeds, and the domain is verified. |
| 3 | **People** | Invite users with one role each, or connect **SCIM** (phase 2). Assign departments and clearances. | At least 1 admin and 2 approvers (so maker ≠ checker is possible). |
| 4 | **Mailbox** (§7) | Connect one shared mailbox via Microsoft 365 or Google Workspace. | Initial sync completes; the **test mail round-trip** succeeds; subscription/watch is active; send permission is verified (dry-run draft). |
| 5 | **Departments and categories** | Start from the starter pack or import. Edit departments, owners, query types (categories with descriptions and examples), default lanes and sensitivity. | Every category has an owning department and ≥ 5 labelled examples. |
| 6 | **Knowledge** (§8) | Upload policies, product sheets and templates, or connect SharePoint/Drive. Approve documents. | ≥ 1 approved source per department; retrieval self-test passes. |
| 7 | **Rules and hard stops** | Review mandatory hard stops (regulator, fraud, legal threat, vulnerable customer, complaint escalation) and add the bank's own. | Hard-stop test set: 100% recall in evals. |
| 8 | **Actions and systems** (§10) | Optional in v1. Connect the integration layer (base URL, mTLS/OAuth) and enable read-only lookups (customer, account, case). | Connector health check and sandbox call. |
| 9 | **Agents and workflow** (§9) | Review the deployment: flow, models, thresholds, draft style, signature. | Eval run on the bank's labelled set passes the gates. |
| 10 | **Shadow mode** | Turn on. The AI triages every real mail but only records decisions; people work as today. | ≥ 2 weeks or N mails; agreement report reviewed. |
| 11 | **Go-live** | A second admin approves (four-eyes). The autonomy dial starts at *draft for approval* everywhere, and *auto* is not available until 30 days of assisted mode. | A signed go-live record in the audit chain. |

**Empty-state design:** every screen must be useful with zero data. It shows what the screen will contain and
the next action (for example, "Connect a mailbox to see volume").

### 6.5 Starter pack

This is versioned content, shipped in `apps/api/src/command_inbox/starter/` and not seeded:
- a neutral banking taxonomy: cards, payments, accounts, loans, complaints, fraud/security, statements,
  general
- the mandatory hard stops
- default SLA policies by priority
- a neutral reply style guide
- an evaluation starter set of ~150 synthetic, clearly marked labelled mails for smoke evals

The bank replaces or extends it. Starter content is never presented as live data.

---

## 7. Mailbox connectors

**Interface.** A `MailboxConnector` protocol with two implementations (`graph`, `gmail`) and one test double
(`fake`, used only in tests and development):

```
connect(tenant, mailbox_address, credentials_ref) -> ConnectResult
start_stream(mailbox) / renew_stream(mailbox) / stop_stream(mailbox)
catch_up(mailbox, cursor) -> list[ProviderMessageRef], new_cursor        # delta / history
fetch(mailbox, ref) -> RawMessage (MIME + headers + provider ids)
send_reply(mailbox, in_reply_to: ProviderMessageRef, body, attachments) -> SentRef
label(mailbox, ref, labels)          # categories / labels, never delete
health(mailbox) -> Health
```

### 7.0 v1 access model: delegated OAuth for one account (decision D3)

One mailbox per tenant, connected by **one interactive OAuth sign-in** as the mail account, with offline access.
We store the refresh token (KMS-wrapped, per-tenant key) and act only on that account's mailbox.

**Microsoft 365**
- **App registration owned by the bank** (single-tenant, in the bank's Entra; we supply a manifest and a
  checklist). The dedicated stack (D1) makes this natural, and there is no multi-tenant consent.
- **Delegated scopes:** `offline_access`, `User.Read`, `Mail.ReadWrite`, `Mail.Send`. The app can reach only
  mail the signed-in account can reach, so admin consent covers a narrow grant.
- **Which account signs in:**
  - The preferred case is a **licensed user mailbox** used as the service inbox (for example
    `customercare@bank.com`). It works with `/me/...` calls and change-notification subscriptions on its own
    Inbox.
  - Alternatively, if the bank's customer-care box is an Exchange **shared mailbox**, it has no sign-in of its
    own. A dedicated service user with FullAccess and SendAs on it signs in, and the app uses
    `Mail.ReadWrite.Shared` / `Mail.Send.Shared` on `/users/{shared}/...`. Whether change-notification
    subscriptions accept delegated `.Shared` permissions on a shared mailbox is **[verify]**. If they don't,
    this path uses the 60-second delta poll described below, with no subscription.
- **Refresh tokens:**
  - They are valid for 90 days of inactivity (continuously refreshed by our renewal job).
  - They are revoked by a password reset, account disablement, or a Conditional Access change.
  - The bank must **exclude the service account from interactive password expiry** or plan re-consent.
  - Health goes to `reauth_required` with an in-app and email alert and a one-click re-connect.
- **Conditional Access:** the bank may require the sign-in from a compliant device or network. The connect wizard
  runs in the admin's browser, so this is satisfied at consent time.
- **Receiving and sending:** as in §7.2 (subscription on `/me/mailFolders('inbox')/messages`, lifecycle, delta
  sweep, createReply/send, categories, immutable IDs, throttling). Only the token type differs: delegated
  instead of app-only.

**Google Workspace**
- **OAuth client owned by the bank:** created in a Google Cloud project that belongs to the bank's Workspace
  organisation, with **user type "Internal"**. Internal apps need no Google verification or CASA assessment, and
  their refresh tokens don't carry the 7-day testing-mode expiry.
- **Scopes:** `gmail.modify` and `gmail.send`. The signed-in account must be a **licensed user mailbox**, because
  Google Groups collaborative inboxes cannot be read through the API.
- **Pub/Sub:** the topic and push subscription live in the same bank project. Whether the topic must be in the
  OAuth client's project is **[verify]**; putting both in one project avoids the question. Grant
  `gmail-api-push@system.gserviceaccount.com` the Publisher role, and push to `/v1/hooks/gmail` with OIDC
  verification.
- **Refresh tokens:** revoked by a password change (for Gmail scopes), by the user or admin removing access, or
  after 6 months unused. They are also subject to the per-account refresh-token limit. Health and re-connect
  work as for Microsoft.
- **Receiving and sending:** as in §7.3 (watch renewed daily, `history.list` catch-up, 404 → full resync,
  `messages.send` with `threadId` and headers, labels).

**What changes in v2:**
- Exchange RBAC for Applications (app-only, scoped to the mailbox) and Gmail DWD. Both remove the dependency on
  one account's token and support many mailboxes.
- The connector interface already hides the credential type, so v2 adds a credential provider and does not touch
  the pipeline.

**Security notes for v1:**
- The signed-in account is a **service identity**: no personal mail, MFA registered, and only this app granted.
- Our audit log records every token refresh and every send.
- Revoking our access is a single action for the bank: remove the app's consent or disable the account.

### 7.1 New data model (migration 0005)

- **`mailboxes`:**
  - `provider` (graph|gmail)
  - `provider_mailbox_id` (Graph user id / Gmail address)
  - `connection_mode` (graph: app-only RBAC scoped | gmail: DWD | per-user OAuth)
  - `credentials_ref` (KMS-wrapped)
  - `state` (connecting|syncing|live|degraded|reauth_required|disconnected)
  - `stream_id`, `stream_expires_at`
  - `cursor` (deltaLink / historyId), `cursor_updated_at`
  - `last_message_at`, `last_error`, `error_count_1h`, `lag_seconds`
  - `send_enabled`, `observe_only`
- **`mail_messages`:**
  - `provider_message_id`, `internet_message_id`, `conversation_id` / `thread_id`, `in_reply_to`,
    `references`
  - `from`, `to`, `cc`, `subject`, `received_at`
  - `auth_results` (spf/dkim/dmarc/compauth/SCL)
  - `raw_object_key` (MIME in object storage, encrypted), `body_text`, `body_html_sanitised`
  - `direction`, `is_auto_reply`, `is_bounce`
  - unique `(org_id, mailbox_id, provider_message_id)`
- **`mail_attachments`:** `object_key`, filename, content type, size, sha256, `av_status`
  (pending|clean|infected|error), `parsed_text_key`.
- **`mail_sync_events`:** an append-only log of notifications, sweeps, fetches and errors, for health and support.

### 7.2 Microsoft 365 (Exchange Online via Microsoft Graph)

**Access model: application permissions scoped to the one mailbox.**
- One **multi-tenant Entra app registration** per cloud (commercial; GCC High and China need separate apps).
  **Credential:** certificate in KMS/HSM, or workload identity federation where we host on AKS/EKS/GKE.
  Microsoft states client secrets should not be used in production.
- **Permissions needed:** `Mail.ReadWrite` (fetch, `createReply` draft, categories, move) and `Mail.Send` (send),
  as application permissions.
- **Scoping to one mailbox with Exchange Online RBAC for Applications.** This replaces Application Access
  Policies, which Microsoft says not to create anymore. The bank's Exchange admin runs a script we generate:

  ```powershell
  New-ServicePrincipal -AppId <ourAppId> -ObjectId <enterpriseAppObjectId> -DisplayName "Command Inbox"
  New-ManagementScope -Name "CI shared mailbox" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'customercare@bank.com'"
  New-ManagementRoleAssignment -App <spObjectId> -Role "Application Mail.ReadWrite" -CustomResourceScope "CI shared mailbox"
  New-ManagementRoleAssignment -App <spObjectId> -Role "Application Mail.Send"      -CustomResourceScope "CI shared mailbox"
  Test-ServicePrincipalAuthorization -Identity <spObjectId> -Resource customercare@bank.com
  ```

  **Critical:**
  - RBAC-for-Apps grants **add to** Entra grants. If the tenant also admin-consents tenant-wide `Mail.*`
    application permissions in Entra, the app can read every mailbox.
  - Our app manifest therefore requests **no Mail.* application permissions** in Entra. Consent only creates
    the service principal (with `User.Read` delegated).
  - The onboarding verification calls `Test-ServicePrincipalAuthorization` output (pasted by the admin) **and**
    actively probes that a *different* mailbox returns 403.
  - Changes take 30 minutes to 2 hours to apply **[verify]**; the wizard shows a waiting state and polls.
- **Admin consent** uses `https://login.microsoftonline.com/{tenant}/v2.0/adminconsent?client_id=…&scope=https://graph.microsoft.com/.default&redirect_uri=…&state=…`.
  The state is bound to the admin's session; we store the tenant ID.

**Receiving:**
1. **Subscription:**
   - `POST /subscriptions`
   - resource: `users/{id}/mailFolders('inbox')/messages`
   - `changeType: created`
   - **basic notifications** (no resource data), which allow up to 10,080 minutes (~7 days) of lifetime; we
     request 6 days
   - a unique secret per subscription in `clientState`
   - `lifecycleNotificationUrl` set
   - `Prefer: IdType="ImmutableId"` on every call
2. **Validation:** echo `validationToken` as `text/plain`, 200, within 10 s, on both URLs.
3. **Notification handling:**
   - The ingress checks `clientState`, writes a `mail_sync_events` row, enqueues `mail-sync(mailbox)` (deduped
     per mailbox per 5 s), and returns **202 within 3 s**.
   - Graph retries for up to 4 hours. If more than 15% of responses in a 10-minute window take longer than
     10 s, Graph drops the endpoint's notifications for 10 minutes, and dropped ones cannot be recovered.
   - The ingress is a separate small deployment with its own autoscaling and SLO.
4. **Lifecycle:**
   - `reauthorizationRequired` → `POST /subscriptions/{id}/reauthorize`
   - `subscriptionRemoved` → recreate the subscription, then catch up
   - `missed` → catch up
5. **Catch-up:**
   - `GET /users/{id}/mailFolders('inbox')/messages/delta` with the stored `deltaLink`, run on every
     notification **and** by a **sweep every 10 minutes**.
   - `410 Gone` / `syncStateNotFound` → full resync bounded by `receivedDateTime ge <last_message_at − 1d>`,
     with dedupe by id.
6. **Renewal job:** every 12 hours it PATCHes `expirationDateTime` on any subscription expiring within 48 hours,
   and recreates it on 404.
7. **Throttling:**
   - The limit is 10,000 requests per 10 minutes and **4 concurrent requests per app+mailbox**.
   - The worker holds a **per-mailbox semaphore of 3**, honours `Retry-After`, and uses JSON batching of up to
     20 requests for fetches.
8. **Fetch:** `GET message?$select=…,internetMessageHeaders,conversationId,internetMessageId` plus `$value` for
   the MIME, stored encrypted in object storage. Attachments are fetched only when the scan policy allows.

**Sending in-thread (after human approval):**
- `POST /users/{mbx}/messages/{id}/createReply` → PATCH body and signature → upload attachments (< 3 MB
  directly; 3–150 MB via `createUploadSession`, chunks < 4 MB) → `POST /messages/{draftId}/send`.
- Exchange sets `In-Reply-To`/`References` and keeps `conversationId`. The copy lands in the shared mailbox's
  Sent Items.
- **Recall:** within the undo window we have not called `send`, so we delete our draft. After send, "recall" is a
  follow-up correction mail flow (explicit in the UI). We never use Outlook recall.
- **Idempotency:** the draft ID and our send-intent ID are stored before `send`. On retry, if the draft no longer
  exists and a Sent Items message has our `X-CI-Intent` header, we treat the send as done.

**Marking in the mailbox:** apply Outlook **categories** (`CI: triaged`, `CI: replied`, `CI: needs person`),
optionally move to folders the bank chooses. Never delete.

### 7.3 Google Workspace (Gmail API)

**Access model.** There are two options; recommend per bank:

**Option A: domain-wide delegation (DWD), the default.**
- A Workspace super admin adds our service account client ID with the scopes `gmail.modify` and `gmail.send`
  (Admin console → Security → API controls → Domain-wide delegation). It can take up to 24 hours.
- Use **keyless signing** (IAM Credentials `signJwt` / workload identity federation), never downloaded keys.
- **DWD cannot be restricted to one mailbox.**
  - We enforce `subject == configured mailbox` in code, as a hard allowlist checked at the token-minting layer.
  - We audit every impersonation.
  - We disclose this in the security pack.
- Banks uncomfortable with DWD use Option B.

**Option B: per-user OAuth for the shared account.**
- Someone signs in once as the shared mailbox account (a licensed user, e.g. `customercare@bank.com`); we store
  the refresh token KMS-wrapped. The blast radius is limited to that account.
- A public "External" OAuth app with `gmail.modify` needs Google verification and a **CASA security assessment**
  (annual).
- A private, domain-internal client avoids that for a dedicated deployment **[confirm with Google for
  multi-tenant]**.
- **Google Groups collaborative inboxes cannot be read by the Gmail API.** The shared mailbox must be a
  licensed user mailbox with delegation for humans.

**Receiving:**
1. A Cloud Pub/Sub topic in our GCP project, per region. Grant `gmail-api-push@system.gserviceaccount.com` the
   Publisher role.
2. `users.watch` with `topicName` and `labelIds:["INBOX"]`. Store `historyId` and `expiration`. **Renew daily**
   (the maximum is 7 days).
3. A **push subscription** to `/v1/hooks/gmail` with OIDC authentication. The ingress verifies:
   - the JWT signature
   - `aud` equals our configured audience
   - `email` equals our push service account
   - `email_verified`
4. The message carries only `emailAddress` and `historyId`. We enqueue `mail-sync(mailbox)` and return 202.
5. **Catch-up:** `users.history.list?startHistoryId=<stored>&historyTypes=messageAdded`, run on every push and
   in a **10-minute sweep**. **404 means full resync**: `messages.list` since the last-seen date, then set a new
   baseline `historyId`.
6. **Fetch:** `messages.get?format=raw` for the MIME, with `Authentication-Results` from its headers.
7. **Quotas:**
   - 15,000 quota units per user per minute; `messages.get` costs 5 and `messages.send` costs 100 **[verify
     table]**.
   - Workspace sending limit: 2,000 messages per user per day.
   - A per-mailbox token bucket enforces both; we alert at 70%.

**Sending in-thread:** build an RFC 2822 message with `In-Reply-To` and `References` set from the original's
`Message-ID`, the same `Subject`, and the `threadId` on the message resource. Send with
`users.messages.send` (`raw`, base64url). Attachments are inline in the MIME; the ~35 MB limit is **[verify]**.
Apply labels `CI/triaged`, `CI/replied`, `CI/needs-person`; never delete.

### 7.4 Common pipeline (both providers)

1. **Dedupe** by `(mailbox, provider_message_id)`, and secondarily by `internet_message_id`.
2. **Loop and noise guard.** Skip our own sent mail, `Auto-Submitted` / `X-Auto-Response-Suppress` /
   out-of-office messages, and bounces (DSN). Detect mail loops (`X-CI-Intent` on our sends; a rate limit per
   counterparty).
3. **Sender trust.** Parse `Authentication-Results` and, for Microsoft, `X-Forefront-Antispam-Report` (SCL).
   An unauthenticated or DMARC-failing sender never reaches auto; mail spoofing the bank's own domain is
   quarantined to a person.
4. **Content.**
   - Sanitise HTML (allowlist) and extract text; strip quoted history and signatures for the model while keeping
     the original.
   - Attachments: **AV scan** (ClamAV sidecar or cloud AV) → parse in a sandbox (Docling/Tika, no network,
     CPU/memory/time limits) → text for the model and redaction.
5. **Threading.** Use the provider thread/conversation ID first, then `References`/`In-Reply-To`, and only
   then the subject heuristic.
6. **Customer matching** (§10.3), then **triage** (existing flow), then the **approval gateway**, then **send**.
7. **Prompt-injection defence.** Email bodies are untrusted input:
   - Delimit them in prompts.
   - Never allow tools or actions to be chosen by the model; the lane and action come from policy code.
   - Strip hidden text (`display:none`, zero-width characters).
   - Run a System 1 boolean "contains instructions to the assistant?" and send positives to a person.

### 7.5 Mailbox health model (drives UI and alerts)

| Signal | Healthy | Degraded | Down |
|---|---|---|---|
| Subscription/watch expiry | > 48 h | < 48 h | expired |
| Notification-to-ingest lag (p95) | < 60 s | < 10 min | > 10 min |
| Last successful delta/history sweep | < 15 min | < 1 h | > 1 h |
| Token/credential | valid, > 7 days to expiry | < 7 days / 1 failure | reauth required |
| Send success (24 h) | ≥ 99% | ≥ 95% | < 95% |
| Provider throttling (429 rate) | < 1% | < 5% | ≥ 5% |

### 7.6 Test strategy for connectors

- **Contract tests** against recorded Graph and Gmail HTTP fixtures (respx).
- An **end-to-end suite** against a **Microsoft 365 developer tenant** and a **Google Workspace test domain** in
  CI nightly. It sends mail from an external account and asserts ingest, triage, draft, approval, an in-thread
  reply received, and the category/label applied.
- **Chaos tests:** a dropped webhook (sweep recovers), an expired subscription (recreate plus delta), a
  410/404 cursor (full resync with no duplicates), 429 storms, and a send retry after a crash (no double send).

---

## 8. Knowledge pipeline

| Stage | Design |
|---|---|
| Sources | v1: **upload** (PDF, DOCX, XLSX, HTML, MD, TXT) and **SharePoint/OneDrive via Graph** (`Sites.Selected` application permission, scoped to chosen sites). v1.1: Google Drive, Confluence, S3. |
| Storage | The original goes to object storage (encrypted with the tenant key). A `knowledge_documents` row holds version, checksum, owner department, status, effective and expiry dates. |
| Safety | AV scan, then sandboxed parsing (Docling for PDF layout and tables; Tika fallback). |
| Chunking | Structure-aware (headings, tables), ~500–800 tokens with overlap. Chunk metadata: document, section path, page, department, effective date. |
| Embeddings | A self-hosted embedding model (e.g. bge-m3 / e5-large via the same inference plane), so text stays in the bank's boundary, **or** a provider embedding per tenant policy. |
| Index | **pgvector** (HNSW) in Postgres with RLS, plus Postgres full-text. **Hybrid retrieval** (BM25 + vector, RRF), then a **reranker** (cross-encoder; this is what the `rerank` job becomes). |
| Governance | New or changed documents are `pending` until a department owner approves them. Only approved, in-effect chunks are retrievable. Expiry dates create **stale-knowledge alerts**. |
| Grounding contract | Drafts may cite only retrieved approved chunks. The draft node records chunk IDs, and the UI shows citations. A post-check rejects sentences without support (the draft-grounding gate that already exists in evals). |
| Gaps | "No good source found" events create **knowledge gap** tickets for the owning department (replacing the seeded gaps). |

---

## 9. Agents and workflow studio

**Unify the two agent concepts.** A deployment version is the single source of truth. An **agent** becomes a
named, versioned **node configuration** inside the flow (e.g. `draft_reply` with its prompt, style guide,
model, tools, budget). The Setup "Agents" catalog is removed as a separate runtime concept, and its screens
become views over deployment nodes.

**Studio (admin, deployment.edit):**
- **Flow view:** the compiled LangGraph, with required safety nodes locked (mask PII, hard-stop guard, lane
  policy, approval gate).
- **Node editor:** prompt (versioned, diffable), model/provider, temperature, max tokens, cost budget, retrieval
  settings, output schema, and style guide and signature.
- **Categories and rules:** taxonomy, examples, hard stops, priority rules, routing, lane defaults and
  thresholds.
- **Test bench:** paste or pick a real mail (masked). Run the draft version and see every node's input, output,
  latency and cost, compared side by side with the live version.
- **Evals:** dataset from real mails, labelled in-app (reviewer queue: label category, lane, hard stop,
  acceptable draft y/n). The calibration split fits temperature and q̂; test-split gates block publish (these
  exist).
- **Rollout:** draft → shadow → canary % → published, with four-eyes (exists). Rollback is one click.

**Model providers per tenant (decision D2):**
- **System 2 in v1:** an `LLMProvider` abstraction with two implementations:
  - **Anthropic** (Messages API, structured outputs, prompt caching). Zero data retention by agreement;
    residency is US or global today.
  - **OpenAI** (Responses API with structured outputs). Zero data retention by agreement; regional data
    residency for eligible projects **[verify for the bank's region]**.
- **Selection:** per tenant and per node in the deployment config (for example adjudication on one provider,
  drafting on another). Both providers go through the same masking, budgets, tracing and eval gates, and evals
  record the provider and model so switching is a measured change.
- **Later:** AWS Bedrock and Google Vertex adapters, for regional residency on Claude.
- **System 1:** the open-source Jev-style decision engine ("laya"): Choice, Score and boolean over lettered
  options from logprobs, with calibration and conformal sets. It is self-hosted on vLLM (GPU) or llama.cpp
  (CPU) in the bank's region. OpenAI chat models also return logprobs and could serve System 1, but we keep it
  on the self-hosted model so classification never leaves the bank boundary.
- **Budgets:** per-mail and monthly caps. When a cap is reached, drafting stops and mail routes to people. We
  never exceed a cap silently.

**Workflows (v1 scope).** The flow is linear with conditional branches (already compiled per version):
`ingest → mask → sender trust → hard stops → categorise (S1) → adjudicate (S2, if unsure) → priority → customer match (sender/identifiers only) → extract fields → lane policy → draft | brief → route → approval gate → person sends`.

**v1 has no actions (decision D5):**
- The "auto" lane is disabled; the lane ceiling is *draft for approval*.
- Extracted fields and suggested next steps are shown to the person, who acts in the bank's own systems.
- The approval gateway still records who approved and sent what.

Tenant-defined **post-approval workflows** over a connector registry (§10) arrive in v2.

---

## 10. Actions, connectors and bank systems

### 10.1 Connector registry (per tenant)

- `connectors`: type (rest | graph | servicenow | salesforce | temenos | finacle | custom-rest), base URL,
  auth (mTLS client certificate, OAuth2 client credentials, or API key, all stored as KMS-wrapped secret
  references), network path (egress via the bank's API gateway / private link), rate limit, timeout, and a
  health-check endpoint.
- **Operations:** a named operation with a request template, a JSON Schema for inputs and outputs, an
  idempotency-key header, and risk metadata (reversible? money moves?). This replaces hard-coded field schemas.
- **Action templates** reference an operation and a field schema. The **approval policy** comes from the risk
  matrix (exists).
- **Execution:**
  - A durable **idempotency ledger** table: `(org, template, idempotency_key) → status, response hash`, which
    replaces the in-process sandbox dict.
  - A **dry-run/sandbox mode** per connector.
  - A **circuit breaker**.
  - A **full request/response audit** (masked).
- **Decision D5: no actions in v1. This whole section is v2.** Planned first step for v2 was read-only lookups and case creation: customer/account lookup and case creation in the
  bank's CRM or ticketing system only. **No money-moving actions until the bank's Risk function signs off after
  pilot.** Those stay "prepare for a person to execute in their system".

### 10.2 Integration layer

Banks expose systems through an API gateway or ESB (MuleSoft, Apigee/Kong, IBM ACE). We integrate with **that**
layer, never directly with a core database. Examples by system:
- **Salesforce Service Cloud:** REST, Case/Contact.
- **ServiceNow CSM:** Table API.
- **Temenos Transact:** IRIS / Temenos APIs.
- **Infosys Finacle:** Finacle Connect / APIs.
- **Generic REST:** anything else, described by an OpenAPI import.

### 10.3 Customer matching

Match by sender address, then by identifiers in the mail (account or card last-4, masked). A **customer lookup
operation** on the connector returns a customer reference, segment and vulnerability flags. There is no
fabricated CIF. An unmatched sender is shown as unmatched, and a person can link them. Identifiers are verified
before any account-specific statement appears in a draft.

---

## 11. Monitoring

### 11.1 Tenant-facing (bank admin and leads)

**Mailbox health:** the §7.5 signals, the last 24 hours of volume (real counts from `mail_messages`), sync
events, and reconnect or re-consent actions.

**Pipeline funnel:** received → triaged → lane (auto/draft/person) → approved/edited/rejected → sent. Each
stage shows counts, drop-offs and time in stage.

**SLA:**
- first response and resolution times against the tenant's policy, using business hours
- breaches and at-risk tickets (the `sla_sweep` job computes these every minute and raises alerts)

**Agent quality:**
- per node and version: acceptance rate, edit distance, rejection reasons, escalation to System 2
- calibration drift (ECE over a rolling window on reviewed mails), hard-stop recall on reviewed items
- latency p50/p95, error rate, cost per mail, and spend against the cap

**Knowledge:** coverage, stale documents, gaps, and the most-cited documents.

**People:** load, queue ages, approvals pending, and maker/checker throughput.

**Audit:** chain verify, and **export** (CSV/JSON plus a signed manifest).

All numbers come from event tables through a **metrics rollup** job (hourly and daily materialisations), which
replaces the seeded `daily_metrics`. Each tile links to the underlying records.

### 11.2 Platform-facing (console)

- Fleet queue depth and lag, webhook latency and error rate, and subscription/watch expiries.
- Provider throttling, model-provider error and latency by region, spend by tenant against the plan, storage.
- **SLOs:**
  - ingest lag p95 < 60 s
  - API availability 99.9%
  - send success ≥ 99.5%
  - webhook 2xx within 3 s ≥ 99.9%
- **Alerting:** Prometheus Alertmanager to PagerDuty/Opsgenie. Tenant-facing alerts go in-app and by email
  (transactional email).

### 11.3 Tracing

We keep OTel end to end. Langfuse holds AI traces, with the session set to the ticket and the user set to the
tenant and pseudonymous user, and masked inputs. Langfuse self-host (v4) needs:
- Postgres, ClickHouse ≥ 25.12, Redis/Valkey (noeviction) and S3
- an **Enterprise licence** for audit logs, retention policies, server-side masking and RBAC

**Decision needed:** buy the Langfuse EE licence, or keep masking in our code (we already mask before
export) and use OSS.

---

## 12. Security and compliance

| Control | Plan |
|---|---|
| Identity | Operators: a separate Keycloak realm, WebAuthn MFA. Tenants: bank IdP via Keycloak Organizations (one org per bank, IdP linked to the org, domain routing). Keycloak 26.8 moves domain routing to a domain entity, so the provisioning code isolates this behind an adapter. Enforce the bank's MFA/Conditional Access; check `acr`/`amr` claims. |
| Provisioning | SCIM 2.0 endpoint in **our API** (`/scim/v2`, per-tenant bearer token or OAuth client credentials, as Entra supports). It maps to memberships and roles through IdP group mapping. Keycloak's SCIM API is preview in 26.7 and its per-org scoping is undocumented, so we don't depend on it. |
| Secrets and keys | KMS/HSM (AWS KMS, Azure Key Vault Managed HSM, GCP KMS) or Vault Transit. **Envelope encryption with a per-tenant data key**, and BYOK for banks. The Graph certificate and client assertions are signed in the HSM. Key rotation runbooks; crypto-shredding on tenant deletion. This replaces the env `ENCRYPTION_KEY`. |
| Data at rest | Postgres TDE/volume encryption, plus field-level encryption for credentials and (policy option) mail bodies. Object storage with SSE-KMS per tenant prefix. Object Lock (WORM) for audit exports. |
| Data in transit | TLS 1.2+ everywhere, mTLS to bank connectors, private endpoints for Postgres, KMS and object storage. Egress allowlist: Graph, Google APIs, the model provider and the bank gateway only. |
| Residency | A per-tenant profile: region for Postgres, object storage and workers; System 1 in-region; System 2 provider in-region (Bedrock/Vertex where needed); Langfuse in-region or off. |
| Retention | Per-tenant policy (e.g. mail content 7 years or the bank's schedule; model traces 90 days; attachments N days). `retention_sweep` enforces it, and legal hold overrides it. We never delete from the source mailbox. |
| Audit | Hash-chained tenant audit (exists) plus a platform audit. **SIEM export:** syslog-TLS CEF, Splunk HEC, Microsoft Sentinel (Logs Ingestion API), or an S3 drop, with a signed daily digest. |
| App security | CSP, a strict CORS allowlist, and rate limits **per tenant and per session** in the API (token bucket in Postgres or Redis) in addition to nginx. Upload size limits, SSRF-safe connector calls (allowlisted hosts), dependency and container scanning in CI (pip-audit exists; add Trivy, Semgrep), SBOM and signed images (cosign). |
| AI safety | Prompt-injection defences (§7.4). PII masking before the model (exists). No model-chosen actions. Budget caps. Human approval for all sends in v1. A model inventory and documentation pack for model risk management. |
| Assurance | Annual third-party pen test with the bank's right to test; threat model (STRIDE); DPIA template; SOC 2 Type II (start the observation window at pilot); ISO 27001 roadmap; ISO/IEC 42001 optional. |
| Regulatory pack | DPA / GDPR Art. 28 and sub-processor list (Anthropic/AWS/Google, and the hosting provider); EBA outsourcing guidelines and DORA ICT third-party register entries; PRA SS2/21 and SS1/23; RBI IT outsourcing directions for India banks; SR 11-7 model documentation. **[verify current versions per jurisdiction before submission]** |

---

## 13. Infrastructure, delivery and operations

### 13.1 Topology and IaC

- **Kubernetes** (EKS, AKS or GKE; on-prem OpenShift possible) with a **Helm chart**. Deployments:
  - `api` (stateless, HPA)
  - `ingress-hooks` (webhooks, HPA, separate SLO)
  - `worker-*` (per queue group, HPA on queue depth)
  - `scheduler` (singleton with leader election)
  - `console`, `web` (nginx static)
  - `keycloak` (HA, external DB)
  - `otel-collector`
  - `clamav`
  - `docparse` (sandboxed)
  - `vllm` (GPU node pool) or a managed endpoint
  - Langfuse (optional)
- **Terraform modules** per cloud:
  - VPC and private subnets
  - managed Postgres (multi-AZ, PITR 35 days, pgvector)
  - object storage (versioning, SSE-KMS, Object Lock bucket for audit)
  - KMS keys, secret manager, the Pub/Sub topic (GCP) or the push endpoint for Gmail
  - DNS and certificates, WAF
- **Environments:**
  - `dev`
  - `staging` (connected to the M365 developer tenant and the Workspace test domain)
  - `prod-saas`
  - `prod-<bank>` (dedicated)

### 13.2 Release and change management

- Trunk-based development. CI covers what exists today plus the empty-tenant suite, connector contract tests,
  the nightly real-provider end-to-end suite, image scanning, SBOM and signing.
- **Migrations:** backward-compatible expand/contract only; `command-inbox-migrate` runs as a Helm pre-upgrade
  hook.
- Feature flags per tenant for staged rollout.
- Deployment config changes are audited data changes, separate from code releases, gated by evals and
  four-eyes (exists).
- A change calendar and release notes for bank CAB processes.

### 13.3 Transactional email

- **SaaS:** Amazon SES or Postmark on a platform domain with SPF, DKIM and DMARC aligned.
- **Dedicated bank:** the bank's SMTP relay or Graph `sendMail` from a notifications mailbox, because many banks
  block third-party senders.
- **Templates:** invitation, re-invite, go-live approval request, alert digests, SLA at-risk, mailbox degraded,
  and export ready.

### 13.4 Backup, DR, runbooks

- **RPO ≤ 15 minutes** (PITR) and **RTO ≤ 4 hours**, with a quarterly restore drill.
- Cross-region backup copy where residency allows.
- **Mail recovery on DR:** after restore, run a full catch-up from the stored cursors. The provider mailbox is
  the source of truth, so no mail is lost.
- **Runbooks:**
  - mailbox reauth
  - subscription storm
  - provider outage
  - model provider outage (fail to person)
  - KMS outage
  - stuck queue
  - tenant offboarding
  - key rotation
  - incident comms

---

## 14. Integration call-outs

| Integration | Needed for | Who provides | v1? |
|---|---|---|---|
| Microsoft Graph + Exchange Online RBAC for Applications | M365 mailbox read/send | Bank: Global/Exchange admin consent and a PowerShell script (we generate it) | **Yes** |
| Gmail API + Cloud Pub/Sub (+ DWD or per-user OAuth) | Google mailbox | Us: GCP project, topic and push subscription. Bank: Workspace super admin for DWD | **Yes** |
| Keycloak 26.x Organizations + bank IdP (Entra/Google OIDC) | Staff SSO | Us: Keycloak. Bank: an app registration in their IdP, or use of our multi-tenant app | **Yes** |
| Transactional email (SES/Postmark or bank relay) | Invites, alerts | Us or the bank | **Yes** |
| KMS/HSM or Vault | Credential and data-key protection | Cloud or bank (BYOK) | **Yes** |
| Object storage (S3-compatible) | MIME, attachments, knowledge, exports | Cloud | **Yes** |
| AV scanning (ClamAV / cloud AV / bank ICAP) | Attachments, knowledge files | Us or the bank | **Yes** |
| Claude via Anthropic API / Bedrock / Vertex | System 2 | Us (and a ZDR agreement) or the bank's cloud account | **Yes** |
| vLLM/llama.cpp + GPU | System 1 in-region | Us | **Yes** (CPU ok for pilot volumes) |
| pgvector + embedding model | Knowledge retrieval | Us | **Yes** |
| SharePoint/OneDrive (Graph `Sites.Selected`) | Knowledge sync | Bank admin grant | v1.1 |
| SCIM 2.0 from Entra/Google | User lifecycle | Bank IdP admin | v1.1 |
| SIEM (Splunk HEC, Sentinel, syslog CEF) | Audit forwarding | Bank | v1.1 (export in v1) |
| CRM/ticketing (Salesforce, ServiceNow) via the bank gateway | Customer lookup, case creation | Bank integration team | v1 read-only (bank dependent) |
| Core banking (Temenos, Finacle) via the bank gateway | Account context, actions | Bank | Post-pilot |
| Langfuse (OSS or EE) | AI tracing | Us | Yes (EE decision open) |
| PagerDuty/Opsgenie | On-call | Us | Yes |

---

## 15. Framework and product gaps to manage

| Component | Gap | Mitigation |
|---|---|---|
| Exchange RBAC for Applications | Additive with Entra grants. Propagation takes 30 minutes to 2 hours **[verify]**. | Request no Mail.* Entra app permissions; run the negative probe on another mailbox; show a waiting state in the wizard. |
| Graph change notifications | Endpoints can be dropped (unrecoverable) if slow; subscription lifetime is 7 days maximum. | 202-fast ingress, delta sweeps, and a renewal job with alerts. |
| Gmail DWD | Cannot be scoped to one mailbox. | Code-level subject allowlist, audit, disclosure; per-user OAuth alternative. |
| Gmail per-user OAuth (public app) | Restricted scope needs CASA, renewed yearly. | Prefer DWD or an internal app for dedicated deployments; budget for CASA if multi-tenant OAuth is needed. |
| Google Groups collaborative inbox | No API access to messages. | Require a licensed user mailbox as the shared inbox. |
| Keycloak | SCIM is preview (26.7). Domain routing changes in 26.8. Org invite details vary by version. | Own SCIM endpoint; adapter around org/IdP/domain provisioning; pin the Keycloak version and run an upgrade test suite. |
| Anthropic first-party API | Data residency is US/global only; ZDR must be agreed; some models require 30-day retention. | Bedrock/Vertex for non-US; choose ZDR-eligible models; record the provider and region per tenant. |
| Langfuse OSS | No audit log, retention or server-side masking without EE. | Mask before export (done); EE licence or self-managed retention. |
| LangGraph | Checkpointing is not used; a long run is lost on a worker crash. | Idempotent node design (exists) plus Postgres checkpointer for flows with external calls. |
| Casbin | In-memory enforcer per process. | Cache invalidation via outbox (exists); keep policy small; load-test. |
| Postgres job queue | Throughput ceiling at very high volume. | Fine for 1–50k mails/day per tenant; plan a partitioned queue or SQS/Kafka adapter behind the same interface if exceeded. |

---

## 16. Delivery plan

The team assumed for these estimates is 2 backend, 1 frontend, 1 platform/SRE, 0.5 ML/evals, 0.5 security, 1
PM/implementation lead. Estimates are elapsed weeks with workstreams in parallel.

| Phase | Weeks | Scope | Exit criteria |
|---|---|---|---|
| **0. Clean-up** | 1 | §4. Migrate-only command, real SSO sign-in page, demo removal, empty-tenant tests, locale. | Fresh deploy has no demo data; a user signs in via Keycloak/Entra; every screen works on an empty tenant. |
| **1. Platform and identity** | 3 | §5 console, tenant lifecycle and provisioning job, tokenised invites and transactional email, Keycloak org/IdP adapter, operator realm, platform RBAC, break-glass, tenant settings UI, per-tenant config model, KMS envelope encryption. | Operator creates a tenant, the admin accepts, connects Entra SSO, and invites 2 users; all audited. The pen-test scope for identity is ready. |
| **2. Mailbox (M365 first, then Gmail)** | 5 | §7: connector interface, delegated-OAuth credential provider (§7.0: connect wizard, refresh, re-connect), Graph connector (subscription, lifecycle, delta, renewal, fetch, MIME store, send-in-thread, categories), Gmail connector (OAuth, watch/PubSub, history, send), common pipeline (sanitise, AV, parse, threading, loop guard, sender trust), health model and UI, test-mail tool, contract, e2e and chaos tests. | On the M365 dev tenant and the Workspace test domain: 1,000 test mails ingested with no loss or duplicates across forced failures; approved replies land in-thread; health shows real lag; nightly real-provider CI is green. |
| **3. Knowledge** | 3 (overlaps 2) | §8 upload, parsing, chunking, embeddings, pgvector hybrid + rerank, approval, expiry, citations, gaps; SharePoint sync (v1.1). | Retrieval eval: recall@5 ≥ 0.85 on a labelled Q/A set; drafts cite only approved chunks. |
| **4. Agents and studio** | 3 | §9 studio (unify agents into deployments, node editor, test bench, labelling queue), `LLMProvider` with Anthropic and OpenAI per tenant and node, budgets; CRUD for departments, query types, SLA policies; starter pack. No connectors (D5). | A bank admin configures a deployment from the starter pack, labels 300 real mails, and publishes via evals and four-eyes; the same eval set runs on both providers and the comparison is recorded. |
| **5. Monitoring and operations** | 3 (overlaps 4) | §11 metrics rollups replacing seeded metrics, SLA sweep, alerts, agent quality dashboards, mailbox health alerts, console fleet health; retention sweep, audit export and SIEM, per-tenant rate limits. | Every dashboard number traces to records; alert tests fire; retention verified on a test tenant. |
| **6. Hardening and assurance** | 3 | §12–§13: Helm + Terraform, staging and prod-bank stacks, backups and DR drill, load test (10× the pilot volume), external pen test and fixes, threat model, DPIA, security pack, runbooks, on-call. | Pen test has no open high/critical findings; DR drill meets RPO/RTO; load test meets the SLOs; the bank's TPRM questionnaire is answered. |
| **7. Pilot at bank #1** | 6–10 | Shadow (≥ 2 weeks) → assisted (draft-for-approval, 4+ weeks) → targeted partial autonomy on low-risk categories only after Risk sign-off. | Agreed KPIs: acceptance ≥ 70% of drafts unedited or lightly edited, zero hard-stop misses, SLA improvement vs baseline, and no P1 incidents. |

**Critical path:** Phase 1 identity → Phase 2 M365 connector → shadow start. The bank's own lead times often
dominate:
- IdP app registration
- Exchange admin script run
- network and egress approvals
- DPA and TPRM

Start those in week 1.

### 16.1 What we need from bank #1 (start in week 1)

1. Named contacts: IT/identity admin, Exchange or Workspace admin, security/TPRM, Risk/Compliance,
   operations lead, integration team.
2. **Identity:** Entra tenant ID or Google Workspace domain; consent to our app, or registration of an OIDC
   client; a test group of users.
3. **Mailbox:** the shared mailbox address (e.g. `customercare@bank.com`):
   - For M365: an Exchange admin to run the RBAC-for-Apps script.
   - For Google: a super admin to add the DWD client ID and scopes, or to complete the OAuth consent as the
     shared account.
4. **Knowledge:** policy and product documents or SharePoint sites, and department owners for approval.
5. **Historical mail** for labelling and evals (≥ 1,000 recent mails, under the DPA), or agreement to label
   during shadow mode.
6. **Integration:** API gateway access for customer lookup / case creation (sandbox first), with mTLS certificates.
7. **Security and legal:** DPA, sub-processor approval, data-residency decision, retention schedule, pen-test
   rights, incident contacts.
8. **Go-live governance:** who signs the four-eyes go-live and each autonomy increase.

---

## 17. Decisions still open

1. **Roles:** keep 3 tenant roles, or add a read-only auditor for regulators and internal audit (recommended for
   banks, cheap to add).
2. **Team leads' clearance edits:** limited to their own department?
3. **Staff navigation:** should staff see Deployments?
4. **Region and provider pairing for bank #1** (follows from D1 and D2): if the bank's region requires in-region
   processing, which of Anthropic or OpenAI can meet it for that region, or does System 2 wait for the
   Bedrock/Vertex adapter?

Resolved decisions are in §0.

---

## Appendix A — Detailed backlog by area (for ticketing)

**Platform (P):**
- **P1.** `platform_*` roles and Casbin domain, `/v1/platform` router, operator realm.
- **P2.** Tenant model: status, plan, quotas, residency, locale; lifecycle state machine; provisioning job
  (steps, retries, progress).
- **P3.** Keycloak admin client (org CRUD, IdP link, domain config), isolated behind an adapter with contract
  tests pinned to the Keycloak version.
- **P4.** Tokenised invitations (hash, expiry, single use), accept endpoint, bootstrap admin, auto-disable.
- **P5.** Transactional email service plus templates.
- **P6.** Console SPA: tenants list and detail, provisioning log, fleet health, platform audit, break-glass.
- **P7.** Break-glass: request, approve and expire flow, tenant banner, audit.
- **P8.** Offboarding: export bundle (mail metadata, tickets, audit, config) → crypto-shred → purge job.

**Identity (I):**
- **I1.** Sign-in: domain discovery and OIDC redirect; remove the demo login outside development builds.
- **I2.** Tenant SSO setup wizard (Entra/Google), DNS TXT domain verification.
- **I3.** SCIM 2.0 endpoint (users, groups, PATCH, filtering), group-to-role mapping (v1.1).
- **I4.** Back-channel logout / session revocation on IdP signals.

**Mailbox (M):**
- **M1.** Connector protocol, fake connector, migration 0005.
- **M2.** Graph:
  - consent flow and the RBAC script generator with verification
  - token acquisition (certificate/WIF)
  - subscriptions and lifecycle
  - delta and the sweep
  - renewal job
  - fetch MIME, headers and attachments
  - createReply/send and the upload session
  - categories
  - throttling semaphore
- **M3.** Gmail:
  - DWD (keyless) and OAuth modes
  - Pub/Sub topic and push, OIDC verification
  - watch and daily renewal
  - history, and full resync on 404
  - fetch raw, send in-thread, labels
  - quota bucket
- **M4.** Ingress service (validation handshakes, clientState/OIDC checks, enqueue, 202).
- **M5.** Common pipeline: dedupe, loop/noise guard, auth-results parsing, HTML sanitise and quote strip,
  threading.
- **M6.** Object storage client plus per-tenant encryption; AV service; sandboxed parser service.
- **M7.** Health model, events log, UI (health page, reconnect, test mail), alerts.
- **M8.** Contract tests (recorded fixtures), nightly real-provider e2e suite, chaos tests.

**Knowledge (K):**
- **K1.** Documents model (versions, status, owner, dates), upload API and UI, approval flow.
- **K2.** Parsing, chunking, embeddings (self-hosted), pgvector HNSW, full-text, hybrid retrieval with RRF,
  reranker job.
- **K3.** Citations in drafts and the grounding check; gap tickets; stale alerts.
- **K4.** SharePoint `Sites.Selected` sync (v1.1), then Drive and Confluence.

**Agents and workflows (A):**
- **A1.** Remove the separate agent catalog at runtime; node configs in the deployment version; migration of
  existing agents.
- **A2.** Studio UI: flow view, node editor with prompt versions and diff, test bench.
- **A3.** Labelling queue from real mails, dataset builder, and splits.
- **A4.** Provider abstraction: Anthropic / Bedrock / Vertex per tenant, budgets and caps.
- **A5.** Prompt-injection guard node; hidden-text stripping.
- **A6.** Post-approval workflow steps (declarative) executed via connectors.
- **A7.** CRUD: departments, query types, owners, SLA policies, hard stops, templates; starter pack.

**Connectors (C):**
- **C1.** Connector registry, secrets via KMS, mTLS, health checks, OpenAPI import.
- **C2.** Operations with JSON Schema; action templates bound to operations.
- **C3.** Idempotency ledger; dry-run; circuit breaker; masked request/response audit.
- **C4.** Customer lookup and matching; case creation (Salesforce/ServiceNow/generic REST).

**Monitoring (O):**
- **O1.** Metrics rollup job and tables; replace `daily_metrics` consumers; drill-through links.
- **O2.** SLA policies with business hours; `sla_sweep`; at-risk and breach alerts.
- **O3.** Agent quality dashboards (acceptance, edits, calibration drift, cost, latency).
- **O4.** Alert rules engine (tenant and platform); email and in-app delivery; Alertmanager rules.
- **O5.** Audit export and SIEM forwarders.

**Security and operations (S):**
- **S1.** KMS envelope encryption, BYOK, rotation, crypto-shred.
- **S2.** Per-tenant and per-session rate limits in the API.
- **S3.** Retention policies and `retention_sweep`; legal hold.
- **S4.** Helm chart, Terraform modules (AWS first, Azure second), environments.
- **S5.** CI additions: empty-tenant suite, Trivy, Semgrep, SBOM, cosign; nightly e2e.
- **S6.** Backups/PITR, DR drill, runbooks, on-call, status page.
- **S7.** Pen test, threat model, DPIA, security and regulatory pack, SOC 2 readiness.

---

## Appendix B — Sources

**Microsoft Graph and Entra:**
- Change notifications: learn.microsoft.com/graph/outlook-change-notifications-overview,
  change-notifications-delivery-webhooks, change-notifications-lifecycle-events,
  change-notifications-with-resource-data, api/resources/subscription
- Delta: learn.microsoft.com/graph/delta-query-messages, delta-query-overview
- Throttling and batching: learn.microsoft.com/graph/throttling-limits, json-batching
- Sending and attachments: learn.microsoft.com/graph/api/message-createreply, message-send, user-sendmail;
  outlook-send-mail-from-other-user; outlook-large-attachments; outlook-immutable-id
- Consent and credentials: learn.microsoft.com/entra/identity-platform/v2-admin-consent, how-to-add-credentials,
  access-tokens; learn.microsoft.com/graph/deployments (national clouds)

**Exchange Online:**
- learn.microsoft.com/exchange/permissions-exo/application-rbac
- learn.microsoft.com/powershell/module/exchangepowershell/new-managementroleassignment,
  new-applicationaccesspolicy
- learn.microsoft.com/defender-office-365/message-headers-eop-mdo

**Gmail and Google:**
- developers.google.com/workspace/gmail/api/guides/push, guides/sync, guides/threads, reference/quota,
  auth/scopes
- cloud.google.com/pubsub/docs/authenticate-push-subscriptions
- support.google.com/a/answer/162106 (domain-wide delegation), /14437356, /166852
- CASA and verification: support.google.com/cloud/answer/13465431, /13463073

**Keycloak:**
- github.com/keycloak/keycloak release notes 26.0–26.8
- server_admin organizations docs
- organization admin resources (tag 26.7.4)

**Anthropic:**
- platform.claude.com/docs/en/manage-claude/api-and-data-retention, manage-claude/data-residency
- build-with-claude/claude-on-amazon-bedrock, claude-on-vertex-ai

**Langfuse:**
- langfuse.com/self-hosting (infrastructure, licence key)

**Code audit:** repository `main` at c8f1b7f. File references are in §2.
