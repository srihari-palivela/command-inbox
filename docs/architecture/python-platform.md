# Command Inbox — Python platform plan (v2)

**Status:** Phases A–D delivered (the TypeScript API has been removed); phase E (hardening) open · **Supersedes:** the TypeScript API that lived in `apps/server`
**Scope of this revision:** Python backend; identity and access control; per-tenant mailbox deployments with
their own rules and agent flows; a System 1 / System 2 categorisation design built on an open-source
alternative to Jev; LangGraph flows; evals that gate publishing; Langfuse + OpenTelemetry; production gaps.

The product, lanes, risk matrix, approval gateway and UI are unchanged — see
[`implementation-plan.md`](implementation-plan.md). The React app keeps talking to the same `/v1` API, so
its 12 Playwright end-to-end tests are the parity check for the port.

---

## 1. Decisions

| # | Decision | Why | Rejected |
|---|---|---|---|
| P-01 | **FastAPI + Pydantic v2 + SQLAlchemy 2 (async, asyncpg) + Alembic**, Python 3.12, managed with `uv` | Typed, async end to end; Pydantic models are the API contract; Alembic owns the schema | Django (sync ORM, heavier), Flask |
| P-02 | **Keep Postgres as the only system of record**, with the same row-level security (`app.org_id` per transaction), non-owner runtime role, hash-chained append-only audit log, transactional outbox and `SKIP LOCKED` job queue | These are the correctness core; the port changes the language, not the guarantees | Celery + Redis (a second store to secure and back up); Kafka at pilot scale |
| P-03 | **Keycloak (OIDC, Authorization Code + PKCE) for identity**; the API is the OIDC client (backend-for-frontend) and keeps its own opaque server-side sessions (HttpOnly cookie + CSRF header) | Tokens never reach the browser; sessions are revocable ("sign out other devices"); Keycloak federates each bank's Entra ID / Google and runs in compose for development | JWTs in the SPA (XSS exposure, no revocation); a managed IdP only (no local dev) |
| P-04 | **Casbin RBAC-with-domains** for authorisation: subject = the user's role in the tenant, domain = tenant, object.action = capability; baseline policy as code, tenant overrides limited to delegable capabilities | One enforcement engine, auditable policy files, per-tenant customisation without letting a tenant break separation of duties | Hand-rolled ifs (the TS version); OPA (a separate service for a small policy) |
| P-05 | **Exactly one role per user per tenant** (staff, team lead, admin): `memberships` primary key (org, user) with one `role` column and a CHECK constraint | Matches how banks staff these desks; removes "which role am I acting as" ambiguity from audit | Multi-role users |
| P-06 | **Tenant admin owns configuration**: members and roles, delegable permissions, deployments (taxonomy, rules, flows, models, thresholds), knowledge sources, mailboxes | Configuration is a bank decision; the vendor ships safe defaults | Vendor-managed configuration |
| P-07 | **Deployments**: a tenant runs several mailbox categorisations side by side (e.g. Retail servicing, Trade ops, Disputes). Each has a versioned, immutable-once-published configuration and is bound to one or more mailboxes | Different desks need different taxonomies, rules and flows; versioning makes changes reviewable and reversible | One global configuration per tenant |
| P-08 | **LangGraph** for agent flows, compiled per deployment version from a declarative flow spec over a registry of vetted node types | Explicit state graph, conditional edges, retries, streaming, first-class tracing; tenants configure flows without writing code | Free-form agent loops (unbounded, unauditable); bespoke orchestration |
| P-09 | **System 1 / System 2 categorisation**: an open-weight decision engine (our open-source alternative to Jev) scores a fixed label set from model logits with calibrated confidence; low confidence or an ambiguous label set escalates to an LLM adjudicator; generation (extraction, drafts, briefs) is System 2 | Fast, cheap, deterministic, always a valid label with a real probability; escalation only where it pays | Asking a chat model for JSON labels (unparseable outputs, uncalibrated confidence) |
| P-10 | **Evals as a publish gate**: labelled datasets per deployment; a run scores a deployment version; publishing requires passing gates | Configuration changes are code changes for a model-driven system | Manual spot checks |
| P-11 | **OpenTelemetry everywhere, Langfuse for AI observability**: OTLP → OpenTelemetry Collector → Langfuse (AI spans, prompts, scores, datasets) and the tracing/logging backend; structlog JSON with trace ids | One trace from HTTP request to model call; AI-specific analytics without a second instrumentation path | Vendor-specific SDKs only |

---

## 2. Identity and access control

**Sign-in.** `GET /v1/auth/oidc/login` redirects to Keycloak (PKCE, state, nonce). The callback validates the
ID token against Keycloak's JWKS, then links the identity to a user by `idp_subject` (falling back to the
verified email on first sign-in). A user with no membership is admitted only by a pending **invitation**
for that email, which grants exactly the invited role in that tenant. The API then creates its own session.
Logout revokes the session and returns Keycloak's end-session URL.

Demo sign-in (pick a seeded user) remains for local development and CI only; production refuses to start
with `DEMO_MODE=true` or without `OIDC_ISSUER`.

**Roles and capabilities.**

| Capability group | Staff | Team lead | Admin | Tenant can change? |
|---|---|---|---|---|
| Work tickets, reply, approve as maker, see deployments | ✓ | ✓ | ✓ | No |
| Approve as checker | — | ✓ | ✓ | No (never staff: separation of duties) |
| Reassign, override up, clearance, auto-assign, insights, KPIs, learning updates, see evals | — | ✓ | ✓ | Yes, for staff/lead |
| AI setup, rules, edit deployment drafts, run evals | — | — | ✓ | Some delegable to leads (view setup, rules, edit drafts, run evals) |
| Publish deployments, autonomy dial, audit verify, members & roles, permissions | — | — | ✓ | No (admin only) |

Enforcement is layered: route guard (capability) → domain service (capability + per-department
clearance + maker ≠ checker) → Postgres RLS (tenant). Every role, membership, invitation and permission
change is written to the audit chain.

---

## 3. Deployments

A **deployment** belongs to one tenant and is bound to one or more mailboxes. Its **version** is an
immutable JSON document once published:

| Section | Contents |
|---|---|
| `taxonomy` | Categories (query types) with description, examples, owning department, default lane, action template, sensitivity |
| `rules` | Hard stops (keyword and decision-engine booleans), bucketing overrides, priority rules (hard / weighted), routing preferences |
| `flow` | LangGraph spec: ordered nodes from the registry, their parameters, and conditional edges |
| `models` | Decision engine model + calibration temperature; System 2 models per node; budgets |
| `thresholds` | Confidence bar per lane, escalation threshold, conformal coverage target, abstain rules |
| `gates` | Eval gates this version must pass to publish |

Lifecycle: `draft` → (eval run passes gates) → `published` (becomes the deployment's active version; the
previous one is retired) → rollback is publishing an earlier version again. Every ticket records the
deployment version that triaged it, so behaviour is reproducible and explainable after the fact.

---

## 4. Agent flow and the System 1 / System 2 split

```
mail ─▶ mask PII ─▶ S1 guard (hard-stop booleans) ─stop─▶ You lane + brief (S2)
                         │ clear
                         ▼
                   S1 categorise (Choice over the taxonomy, calibrated)
                         │ confident & single label        │ low confidence / label set > 1
                         ▼                                 ▼
                   S1 priority Score, multi-intent bool   S2 adjudicator (LLM, constrained to the set)
                         ▼
                   policy (deterministic) ─▶ lane ─▶ S2 work: extract fields | grounded draft | brief
                         ▼
                   router (clearance, load) ─▶ gate
```

**System 1 — the decision engine (open-source alternative to Jev).** One interface, three primitives:
`choice(text, options) → label + distribution`, `score(text, rubric levels) → expectation`,
`boolean(text, question) → probability`. Implementation: options are presented as single-token letters;
we read the next-token log-probabilities for those tokens only (llama.cpp `n_probs` or vLLM `logprobs`),
so the output is always a valid option; probabilities come from a temperature-scaled softmax with the
temperature fitted per deployment on its labelled set. Backends: llama.cpp server (CPU/GPU, GGUF) and
vLLM (GPU, OpenAI-compatible); a deterministic keyword engine for tests and offline development.
Models: Qwen3 1.7B (fast) or 4B (accurate) class open weights, self-hosted inside the bank boundary.

**System 2 — deliberation.** Claude (structured outputs) for field extraction, grounded drafting, briefs,
the copilot and adjudication of uncertain categorisations. It is called only after System 1 decided that
generation is needed, and never decides the lane.

Calibration, conformal sets and the node-by-node split are specified in §8.

---

## 5. Evals

| Metric | Gate (default) | Why |
|---|---|---|
| Hard-stop recall | 100% | A missed regulator/fraud/vulnerable signal is the worst failure |
| Category accuracy / macro-F1 | ≥ 90% / ≥ 85% | Routing quality across rare classes |
| Expected calibration error | ≤ 0.05 | Confidence must mean what it says; it drives the lane |
| Selective accuracy at coverage | ≥ 98% at ≥ 70% | What System 1 accepts must be right; the rest escalates |
| Lane safety | 0 auto-lane decisions on hard-stop cases | The approval gateway's premise |
| Escalation rate to System 2 | Reported (budgeted) | Cost and latency |
| Field extraction exact match | ≥ 95% on mandatory fields | Money-moving actions |
| Draft grounding | 0 uncited or unknown-source sentences | No model recall in customer replies |
| p95 latency, cost per 1k mails | Reported | Capacity planning |

Datasets are seeded per deployment and grow from production feedback (overrides, rejections and draft edits
become labelled cases after review). Runs are stored in Postgres and mirrored to Langfuse datasets and
scores; CI runs the deterministic suite on every change.

---

## 6. Observability

- **Traces:** OpenTelemetry SDK (FastAPI, SQLAlchemy, httpx, job worker, every LangGraph node and model call
  with `gen_ai.*` attributes) → OTLP → OpenTelemetry Collector → Langfuse (AI traces, cost, prompts,
  scores) and the tracing backend.
- **Logs:** structlog JSON with `trace_id`/`span_id`, secrets redacted, exported over OTLP.
- **Metrics:** Prometheus `/metrics` — HTTP RED, job lag, lane mix per deployment, gate decisions with the
  approve-without-open canary, decision-engine latency, model cost.

---

## 7. Production gaps closed in this revision

| Area | What |
|---|---|
| Config safety | Production refuses to start with demo mode, dev secrets, insecure cookies or no OIDC issuer |
| Schema | Alembic migrations with `alembic check` in CI; baseline replays the audited SQL exactly |
| Access | Invitations, member and role management, tenant permission overrides with locked SoD rules, SSO subject linking |
| Contract | Pydantic response models generated from the web contracts; CI fails on drift |
| AI safety | Deterministic lane policy; calibrated System 1 with abstention; PII masked before any model; budgets per deployment |
| Operations | Health/readiness, graceful shutdown of worker and SSE, statement timeouts, pool pre-ping, retries with backoff |
| Still open | Secrets from a KMS/Vault, SCIM provisioning, data retention jobs per policy, per-tenant rate limits and model budgets enforced at the gateway, disaster-recovery drills |

## 8. System 1 / System 2 design (research brief)

**What Jev is.** A hosted "System One" decision model from TypeSafe AI (early access, Sept 2026): send a state
and typed questions, get back typed answers with probabilities from a single forward pass — **Choice**
(one option + full distribution), **Score** (expectation over ordered rubric levels), **Noul** (P(yes)),
short **Text** — at a claimed ~200 ms median. LLMs stay "System Two" for generation and reasoning. Its own
guidance matches ours: schema-valid output is not a correct decision; validate on your data, calibrate
thresholds, keep high-impact actions behind deterministic checks and human fallback.

**Our open-source alternative.** OpenSourceJev (MIT, research experiment) shows the mechanism: softmax over
the candidate options only, from llama.cpp logits, then temperature scaling. We build the same primitives
as a production service rather than depend on it, and — unlike its BoolQ-fitted temperature — fit
calibration **per deployment on that deployment's labelled mail**.

**Patterns adopted** (dual-process agent literature): confidence-gated escalation from a fast model to a
slow one (SwiftSage), a tunable System-1/System-2 mix per task (System-1.x — our per-deployment threshold),
cascades with an acceptance scorer (FrugalGPT, RouteLLM), temperature scaling measured by ECE (Guo et al.),
and split conformal prediction over the option softmax for selective classification (Kumar et al.; MAPIE).

**Scoring.** Categories are presented as lettered options so each label is one token; one forward pass reads
`top_logprobs` for those tokens only (llama.cpp `n_probs` / `logit_bias`, vLLM `logprobs`). This avoids the
long-label bias of summing multi-token likelihoods. Hard-stop and multi-intent questions are Noul; priority
is a Score over P1–P4 levels.

**Calibration per deployment.** Label ≥ 50–100 mails per category → split fit / calibration / test → fit one
temperature T by minimising NLL → compute the conformal threshold q̂ at α = 0.05–0.10 (LAC/APS) → at run
time the prediction set is every label passing q̂. **Accept** when the set has one label and its probability
≥ the deployment's bar; otherwise **escalate**. Refit when the taxonomy changes or drift alarms fire.

**The flow (LangGraph nodes).**

| Node | System | Rule |
|---|---|---|
| `pii_mask` | deterministic | regex (IBAN, PAN with Luhn, Aadhaar, phones, emails) + GLiNER PII model; always first; nothing is traced before it |
| `hard_stop_guard` | System 1 (Noul per stop) + rules | keywords OR model at a recall-tuned threshold; **third contact is counted from thread history, never a model**; any hit → You lane, priority floor |
| `multi_intent` | System 1 (Noul) | above threshold → split proposal, You lane |
| `categorise` | System 1 (Choice + conformal set) | accept or escalate |
| `adjudicate` | System 2 (Claude) | only when escalated; must choose **from the conformal set** or "unsure → a person" |
| `priority` | System 1 (Score) + hard rules | hard rules override |
| `lane_policy` | **pure code** | category policy × risk cell × hard stops × confidence; money moves always maker–checker |
| `extract` / `draft` / `brief` | System 2 | extraction never guesses amounts/accounts/dates; drafts cite approved sources only |
| `route` | deterministic | clearance, availability, load |

**Traced per mail** (Langfuse scores + OTel attributes): model and profile, T / α / bar / taxonomy version,
the full label distribution, top probability, conformal set and size, escalation reason, latency; human
overrides become feedback scores and the next calibration set. Only masked text is ever traced.

**Eval gates added** (to §5): ECE ≤ 0.05 after calibration; selective accuracy ≥ 98% at ≥ 70% coverage;
conformal empirical coverage ≥ 1 − α; end-to-end accuracy (System 1 + adjudicator) ≥ System 1 alone;
escalation rate within the deployment's budget.

**Sources:** [OpenSourceJev](https://github.com/sabeel111/OpenSourceJev) ·
[awesome-jev](https://github.com/cobanov/awesome-jev) ·
[vLLM structured outputs](https://github.com/vllm-project/vllm/blob/main/docs/features/structured_outputs.md) ·
[llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) ·
[SGLang choices bias](https://github.com/sgl-project/sglang/issues/523) ·
[FrugalGPT](https://github.com/stanford-futuredata/FrugalGPT) · [SwiftSage](https://github.com/SwiftSage/SwiftSage) ·
[RouteLLM](https://github.com/lm-sys/RouteLLM) · [MAPIE](https://github.com/scikit-learn-contrib/MAPIE) ·
[GLiNER](https://github.com/urchade/GLiNER) · [Temperature scaling](https://github.com/gpleiss/temperature_scaling) ·
Talker–Reasoner ([arXiv 2410.08328](https://arxiv.org/abs/2410.08328)) ·
System-1.x ([arXiv 2407.14414](https://arxiv.org/abs/2407.14414)) ·
Conformal LLM classification ([arXiv 2305.18404](https://arxiv.org/abs/2305.18404)) ·
Calibration ([arXiv 1706.04599](https://arxiv.org/abs/1706.04599)).
Jev's own pages were not reachable from the build environment; Jev facts come from the two GitHub sources.

---

## 9. Delivery plan (strangler, in phases)

The TypeScript API stays deployable until the Python API passes the same checks. Each phase ends with a check
that can fail.

| Phase | Scope | Exit criterion |
|---|---|---|
| A — Foundation | FastAPI app, sessions, Keycloak BFF, Casbin, RLS, audit, outbox, jobs, telemetry, migrations 0001–0003 | Platform tests green: RLS isolation, CSRF, audit chain (including chains the TS server wrote), locked overrides |
| B — Module parity | Tickets, search, copilot, gateway, calls, intake, workspace, people, insights, learning, setup, seed + reset CLI | Every TS integration test ported and green; the response-diff harness shows no unexplained difference on the recorded `/v1` corpus |
| C — Deployments and AI | Deployments with versions and four-eyes publish, System 1 engine, LangGraph flows, triage runner, evals, Langfuse | Eval gates enforced on publish; triage of the seeded mail matches the TS lanes on the heuristic engine |
| D — Cut-over | Playwright suite against the Python API, compose with Keycloak / Langfuse / OTel collector / llama.cpp, CI jobs | 12/12 end-to-end tests green on Python; `apps/server` deleted |
| E — Hardening | Shadow and canary rollout of deployment versions, rate limits, retention jobs, residency profile | Canary promotion and rollback exercised in staging |

### Review findings accepted into the plan

| Area | Change |
|---|---|
| Deployments | Migration backfills a **default deployment** per tenant (from its current setup) and binds existing mailboxes and tickets, so no ticket is left without a version |
| Publishing | **Four-eyes publish**: the admin who edited a draft cannot publish it; publish requires a passing eval run on the exact config hash and a frozen dataset snapshot |
| Rollout | Versions go `draft → shadow` (runs beside the live version, decisions recorded, never acted on) `→ canary` (a share of mail) `→ published`; rollback is one click |
| Flows | Flows compile only from vetted templates; the PII mask, hard-stop guard, lane policy and approval gate nodes are **required** and cannot be removed or reordered |
| Calibration | Temperature is fitted on a held-out split, never on the gate's test split; ECE is reported on the test split |
| Sender trust | DKIM/SPF/DMARC verdict is recorded at intake; an unauthenticated sender never reaches the Auto lane |
| Intake | Webhooks signed over the raw body with a timestamp (replay window 5 min); mailbox OAuth `state` bound to the session with PKCE; the connected account must match the mailbox address |
| Edge | nginx sets security headers per location, HSTS, `X-Forwarded-For` from `$remote_addr`; the app trusts forwarded headers only from the proxy |
| Abuse | Rate limits per session and per tenant on sign-in, copilot, search and intake |
| Schema | Composite foreign keys `(org_id, id)` on tenant tables so a row can never point into another tenant; a test asserts RLS is enabled and forced on every tenant table |
| Roles | Three roles stay. A read-only **auditor** is a candidate fourth role for regulators and internal audit; not built until asked |
