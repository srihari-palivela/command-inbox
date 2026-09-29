# Command Inbox

An AI triage and resolution workspace for bank customer-service mail. Every incoming email is read,
classified and routed into one of three lanes:

| Lane | What the AI does | What a person does |
|---|---|---|
| **Auto** | Fills in the action (stop a cheque, re-issue a statement) and validates it | Approves it at the gateway — or it runs on its own, only where Risk has allowed that |
| **Draft** | Writes a reply grounded only in approved sources, with citations | Reads, edits if needed, and sends it (recallable for 60 s) |
| **You** | Stands down and hands over a brief: history, context, suggested next moves | Takes it on |

What the AI may do on its own is governed by a **risk matrix** (can it be undone × does money move) and
an autonomy dial per cell. Anything irreversible needs a maker and a *different* checker. Every
suggestion, approval, edit and execution is written to a hash-chained, append-only audit log.

Each tenant runs one or more **deployments** — a mailbox categorisation with its own taxonomy, rules,
agent flow, models and thresholds — versioned, evaluated against labelled datasets, and rolled out
through shadow and canary stages. Categorisation is **System 1** (a small open-weight model scoring a
fixed label set from its logits, calibrated, with conformal prediction sets); **System 2** (Claude)
adjudicates uncertain cases and writes extractions, drafts and briefs. The lane is always decided by
policy code, never by a model.

- **Production plan** (platform console, tenant onboarding, Microsoft 365 / Gmail connectors, knowledge, agents,
  monitoring, security, delivery phases): [`docs/architecture/production-plan.md`](docs/architecture/production-plan.md)
- Platform plan (Python API, identity, RBAC, deployments, System 1/2, evals, observability):
  [`docs/architecture/python-platform.md`](docs/architecture/python-platform.md)
- Original product plan: [`docs/architecture/implementation-plan.md`](docs/architecture/implementation-plan.md)
- Frontend review of the original design, and what was refined: [`docs/ux/frontend-review.md`](docs/ux/frontend-review.md)
- Conventions: [`docs/dev/python-conventions.md`](docs/dev/python-conventions.md),
  [`docs/dev/frontend-conventions.md`](docs/dev/frontend-conventions.md)

## Run it with Docker

```sh
docker compose up --build
```

Open <http://localhost:8080> and pick a demo user. The platform console is on <http://localhost:8082> and
transactional email lands in Mailpit on <http://localhost:8025>. Compose starts Postgres, runs migrations and the seed
(only into an empty database), then starts the API, a separate job worker and the web app behind nginx.

Optional profiles add the rest of the production shape (combine freely):

| Profile | Adds |
|---|---|
| `sso` | Keycloak on :8081 with a ready realm (users `p.sharma`, `r.menon`, `a.kapoor` / password `demo`). Set `OIDC_ISSUER=http://localhost:8081/realms/command-inbox` and `OIDC_CLIENT_SECRET=local-dev-client-secret` |
| `observability` | OpenTelemetry Collector + self-hosted Langfuse on :3000 (`admin@local.test` / `langfuse-local`). Set `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318` |
| `local-llm` | llama.cpp server for the System 1 decision engine on :8090 (put a GGUF model in `./models`). Set `DECISION_ENGINE=llamacpp`, `DECISION_ENGINE_URL=http://llamacpp:8090` |
| `av` | ClamAV for scanning knowledge uploads (needs ~1.5 GB of memory; first start downloads signatures). Set `CLAMAV_HOST=clamav` |

e.g. `docker compose --profile sso --profile observability up --build`.

## Run it for development

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, pnpm 10, Postgres 16 with the
[pgvector](https://github.com/pgvector/pgvector) extension running locally (user `postgres` / password `postgres`;
e.g. the `pgvector/pgvector:pg16` image, or `apt install postgresql-16-pgvector`).

```sh
pnpm install && (cd apps/api && uv sync)
cp apps/api/.env.example apps/api/.env      # development settings (demo sign-in, heuristic models)
ADMIN_URL=postgres://postgres:postgres@localhost:5432/postgres ./infra/scripts/setup-db.sh
pnpm db:reset          # migrate + seed three demo workspaces with a default deployment each
pnpm dev:api           # Python API on :4000 (worker embedded)
pnpm dev               # web on :5173
```

Without `APP_ENV=development` the API assumes production and refuses to start with development
defaults (demo mode, dev secrets, no SSO, keyword-only models).

A real deployment prepares its database with `command-inbox-migrate` (schema only). The demo seed needs
`--demo` and refuses to run in production. Scripted telephony is a prototype and stays off unless
`FEATURE_TELEPHONY=true`.

### Onboarding a bank (platform console)

The vendor's operators run a separate console (`apps/console`, `pnpm dev:console` on :5174) against
`/v1/platform/*`, with their own sign-in (the Keycloak realm `operators`), sessions and roles
(platform owner, operator, support). An operator creates a tenant; provisioning then creates its data key,
its Keycloak organization, a starter deployment and the first-admin invitation, and emails the invitation.
The admin opens the link (`/accept?token=…`), signs in and lands on **Getting started**, a checklist
computed from the workspace's real state (profile, single sign-on, people, mailbox, categories, knowledge,
rules, evals, shadow mode, go-live).

- The first platform owner of a new stack: `uv run command-inbox-operator add owner@vendor.example --name "…" --role platform_owner`.
- In development, the demo seed adds operators `owner@`, `ops@` and `support@platform.example`, the console
  offers a development sign-in, and emails without `SMTP_HOST` are kept in memory and listed at
  `GET /v1/dev/mailbox`. The compose stack sends them to Mailpit (<http://localhost:8025>).

### Connecting the bank's mailbox

Under **Where mail arrives**, an admin adds the shared mailbox and signs in as it once (Microsoft 365 or
Google Workspace). New mail then becomes tickets; approved replies go out in the customer's thread once a
test mail has made the round trip and an admin turns sending on. Configure the bank's app registration with
`MS_CLIENT_ID`, `MS_CLIENT_SECRET` and `MS_TENANT` (Microsoft) or `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_PUBSUB_TOPIC`, `GOOGLE_PUSH_AUDIENCE` and `GOOGLE_PUSH_SERVICE_ACCOUNT` (Google). With
`MAIL_WEBHOOK_BASE_URL` (public HTTPS) providers notify us within seconds; without it mailboxes are polled every
minute.

### Knowledge the AI may quote

Under **Setup → Knowledge**, upload policies, product sheets and procedures (PDF, DOCX, XLSX, HTML, Markdown,
text). Each file is virus-scanned (`CLAMAV_HOST`), read in a sandboxed process, split into passages and
indexed for hybrid (full-text + vector) search. Nothing is citable until someone with approve clearance for
its department approves it; expired documents stop being cited on their own. Drafts cite the passages they
used, and when nothing approved answers a mail the draft says so and raises a knowledge gap. The retrieval
playground on the same screen shows what a question would be grounded on.

- **Embeddings:** `EMBEDDING_PROVIDER=openai_compatible` with `EMBEDDING_URL` (serving `/v1/embeddings`, e.g.
  bge-m3 on Text Embeddings Inference or vLLM), `EMBEDDING_MODEL` and optionally `EMBEDDING_API_KEY`. The default
  `hash` embedder is lexical and for development only; production refuses it.
- **Reranking (optional):** `RERANK_URL` pointing at a TEI `/rerank` cross-encoder.
- **Retrieval eval:** `uv run command-inbox-evals retrieval --org SLUG --file cases.jsonl` with lines like
  `{"question": "…", "expected": ["Document title"]}` reports recall@k and MRR (exit 1 below `--min-recall`).

### Demo users

| Sign in as | Role | Try this |
|---|---|---|
| `p.sharma@bank.example` | Staff | Approve the stop-payment on QRY-48211 as maker; edit and send the NRE draft on QRY-48207, then recall it |
| `r.menon@bank.example` | Team lead (checker) | Counter-approve QRY-48211 and watch it execute; auto-assign at-risk work |
| `a.kapoor@bank.example` | Admin, Risk & Compliance | Turn the autonomy dial; approve a proposed hard-stop rule; verify the audit chain; under **Administration**, draft a deployment change, run its evals, and invite a member |

Every user holds exactly one role per workspace. The admin controls the workspace's configuration:
members and invitations, which delegable permissions staff and team leads get (separation-of-duties
rules stay locked), deployments and their rollout.

In demo mode (development only), as the admin **Where mail arrives → Simulate an email**
sends a sample customer email through the real intake and triage pipeline.

### Models

Without configuration the deterministic heuristic engine and provider run, so everything works offline.
- **System 1:** `DECISION_ENGINE=llamacpp|vllm` with `DECISION_ENGINE_URL` (and `DECISION_MODEL`).
- **System 2:** `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY` (optionally `OPENAI_BASE_URL` for a regional
  endpoint); `LLM_PROVIDER=anthropic|openai` picks the platform default. Each deployment's model nodes can name
  their own provider and model (**Deployments → a version → Agents**), within the workspace's model policy
  (**Organisation → AI model providers**: allowed providers and a monthly budget). `MODEL_PRICES` (JSON, minor
  units per 1,000 tokens) prices models for budgets. PII is masked before any model call.

Model calls go through circuit breakers: if a model is unavailable, the heuristic takes over and new mail
defaults to a person. The **test bench** on a deployment version runs a pasted mail through it and the live
version side by side without saving anything. Under **Evals**, a dataset can be filled by **labelling real mail**,
and a run can pin one provider to compare providers on the same cases.
Offline evals: `cd apps/api && uv run command-inbox-evals run --deployment KEY`.

Teams, query types and reply-time targets are edited under **Who owns what**.

## Tests

```sh
pnpm lint && pnpm format:check && pnpm typecheck
pnpm test:unit          # web unit tests
cd apps/api && uv run ruff check src tests && uv run pytest -q   # API unit + integration (real Postgres)
E2E_RESET=1 pnpm e2e    # Playwright; starts the Python API and the SPA, resets the dev database first
```

API integration tests clone a seeded template database (`ci_template` by default: create it with
`uv run command-inbox-seed --demo --database-url postgresql+asyncpg://postgres:postgres@localhost:5432/ci_template`).
CI (`.github/workflows/ci.yml`) also checks contract drift (`scripts/gen_dto.py --check`), model/migration
drift (`alembic check`), dependency advisories (`pip-audit`) and both container builds.

## Layout

```
apps/api        FastAPI + job worker, SQLAlchemy/Alembic/Postgres (RLS), Keycloak BFF, Casbin RBAC,
                System 1/2 agents on LangGraph, deployments, evals, OpenTelemetry + Langfuse
apps/web        React 19 SPA (Vite, TanStack Query, React Router), Playwright e2e
packages/contracts  the /v1 wire contract (TypeScript); the API's Pydantic DTOs are generated from it
infra           Dockerfiles, nginx, Keycloak realm, OTel Collector config, scripts
docs            plan, UX review, conventions, the original design source
```
