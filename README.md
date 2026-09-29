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

Open <http://localhost:8080> and pick a demo user. Compose starts Postgres, runs migrations and the seed
(only into an empty database), then starts the API, a separate job worker and the web app behind nginx.

Optional profiles add the rest of the production shape (combine freely):

| Profile | Adds |
|---|---|
| `sso` | Keycloak on :8081 with a ready realm (users `p.sharma`, `r.menon`, `a.kapoor` / password `demo`). Set `OIDC_ISSUER=http://localhost:8081/realms/command-inbox` and `OIDC_CLIENT_SECRET=local-dev-client-secret` |
| `observability` | OpenTelemetry Collector + self-hosted Langfuse on :3000 (`admin@local.test` / `langfuse-local`). Set `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318` |
| `local-llm` | llama.cpp server for the System 1 decision engine on :8090 (put a GGUF model in `./models`). Set `DECISION_ENGINE=llamacpp`, `DECISION_ENGINE_URL=http://llamacpp:8090` |

e.g. `docker compose --profile sso --profile observability up --build`.

## Run it for development

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, pnpm 10, Postgres 16 running
locally (user `postgres` / password `postgres`).

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
- **System 2:** `ANTHROPIC_API_KEY` and `LLM_PROVIDER=claude` for adjudication, extraction, drafts, briefs
  and the copilot. PII is masked before any model call.

Model calls go through circuit breakers: if a model is unavailable, the heuristic takes over and new mail
defaults to a person. Offline evals: `cd apps/api && uv run command-inbox-evals run --deployment KEY`.

## Tests

```sh
pnpm lint && pnpm format:check && pnpm typecheck
pnpm test:unit          # web unit tests
cd apps/api && uv run ruff check src tests && uv run pytest -q   # API unit + integration (real Postgres)
E2E_RESET=1 pnpm e2e    # Playwright; starts the Python API and the SPA, resets the dev database first
```

API integration tests clone a seeded template database (`ci_template` by default: create it with
`uv run command-inbox-seed --database-url postgresql+asyncpg://postgres:postgres@localhost:5432/ci_template`).
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
