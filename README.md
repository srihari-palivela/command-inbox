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

- Architecture and delivery plan: [`docs/architecture/implementation-plan.md`](docs/architecture/implementation-plan.md)
- Frontend review of the original design, and what was refined: [`docs/ux/frontend-review.md`](docs/ux/frontend-review.md)
- Frontend conventions: [`docs/dev/frontend-conventions.md`](docs/dev/frontend-conventions.md)

## Run it with Docker

```sh
docker compose up --build
```

Open <http://localhost:8080> and pick a demo user. Compose starts Postgres, runs migrations and the seed
(only into an empty database), then starts the API, a separate job worker and the web app behind nginx.

## Run it for development

Requirements: Node 22, pnpm 10, Postgres 16 running locally (user `postgres` / password `postgres`).

```sh
pnpm install
ADMIN_URL=postgres://postgres:postgres@localhost:5432/postgres ./apps/server/scripts/setup-db.sh
pnpm db:reset          # migrate + seed three demo workspaces
pnpm dev               # API on :4000 (worker embedded), web on :5173
```

Copy `apps/server/.env.example` to `apps/server/.env` to change settings.

### Demo users

| Sign in as | Role | Try this |
|---|---|---|
| `p.sharma@bank.example` | Staff | Approve the stop-payment on QRY-48211 as maker; edit and send the NRE draft on QRY-48207, then recall it |
| `r.menon@bank.example` | Team lead (checker) | Counter-approve QRY-48211 and watch it execute; auto-assign at-risk work |
| `a.kapoor@bank.example` | Admin, Risk & Compliance | Turn the autonomy dial; approve a proposed hard-stop rule; verify the audit chain |

In demo mode the header has a role switch, and as the admin **Where mail arrives → Simulate an email**
sends a sample customer email through the real intake and triage pipeline.

### Using Claude

Without an API key the deterministic heuristic provider runs, so everything works offline. To use Claude
for triage, drafting and the copilot, set `ANTHROPIC_API_KEY` (and optionally `LLM_PROVIDER=claude`).
Model calls go through a circuit breaker: if Claude is unavailable, the heuristic provider takes over
and new mail defaults to a person.

## Tests

```sh
pnpm lint && pnpm format:check && pnpm typecheck
pnpm test:unit          # domain rules + web unit tests
pnpm test:integration   # Fastify + real Postgres (creates and resets command_inbox_test)
E2E_RESET=1 pnpm e2e    # Playwright against the running stack (resets the dev database first)
```

CI (`.github/workflows/ci.yml`) runs all of these, plus both container builds, on every pull request.

## Layout

```
apps/server     Fastify API + job worker, Drizzle/Postgres, domain rules, LLM providers
apps/web        React 19 SPA (Vite, TanStack Query, React Router), Playwright e2e
packages/contracts  Zod request schemas and response types shared by both
infra/docker    Dockerfiles, nginx config, Postgres init
docs            plan, UX review, conventions, the original design source
```
