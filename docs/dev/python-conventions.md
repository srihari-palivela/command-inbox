# Python API conventions (`apps/api`)

Read this before adding or porting a module. The TypeScript server in `apps/server` is the behavioural
reference until it is deleted; the web app's `/v1` JSON contract (`packages/contracts`) must not change.

## Layout

```
src/command_inbox/
  core/       errors, clock, crypto, context (Ctx), audit, outbox, jobs, events, telemetry, http helpers
  db/         engine (tenant_tx, global_tx), models (SQLAlchemy 2, typed)
  rbac/       Casbin model + baseline policy, capability checks, clearance
  schemas/    CamelModel base; dto.py and enums.py are GENERATED (scripts/gen_dto.py); requests.py = bodies
  domain/     pure functions (risk, lane, priority, sla, transitions, pii, nl_filter); no I/O
  auth/       sessions, OIDC, sign-in service
  routers/    auth router + registry of module routers
  modules/<name>/  router.py (HTTP), service.py (use cases), queries.py, jobs.py (worker handlers)
  agents/     decision engine (System 1), providers (System 2), LangGraph flows, triage runner
  evals/      datasets, runner, metrics, gates
```

A module registers itself by existing: `routers/registry.py` and `worker_registry.py` already list the
expected module paths and skip ones that are missing.

## Rules

1. **Tenant data only inside `tenant_tx(ctx.org_id)`** (or `in_tenant(ctx, fn)`). It sets `app.org_id` for
   RLS. `global_tx()` is for sessions, users and cross-tenant lookups the platform owns — never tickets.
2. **Every state change writes audit in the same transaction**: `await audit(tx, org_id, actor=actor_of(ctx),
   action=..., entity=..., summary=..., ...)`. Keep the TS action names and summaries.
3. **Live updates** go through `outbox.publish(tx, org_id, topic, {...})` in the same transaction; only
   small scalar fields in the payload.
4. **Background work** is `jobs.enqueue(tx, ...)` in the same transaction as the state it depends on.
   Handlers are idempotent (use `dedupe_key`), receive the job row, and open their own `tenant_tx`.
5. **Authorisation**: `require(ctx, "cap.name", "do the thing")` at the start of every use case, plus
   `require_clearance` where the TS code checks departments, plus maker ≠ checker in the gateway.
6. **Errors**: raise `AppError` (or helpers in `core.errors`); the app renders RFC 9457 problem JSON.
   Keep TS error `code` strings — the web app switches on them.
7. **Responses**: return DTOs from `schemas.dto` (camelCase on the wire). Never hand-build dicts for
   responses. If a DTO is missing, add it to `packages/contracts` and run `uv run python scripts/gen_dto.py`.
8. **Mutating endpoints that the SPA retries** use `idempotent(request, response, ctx, body, fn)`.
9. **Time** comes from `core.clock.clock.now()` (tests move it). Timestamps on the wire via `iso_ms()`.
10. **PII**: mask with `domain.pii.mask_pii` before any text reaches a model or a log.
11. **No model identifiers or secrets in code or logs**; model names come from settings or deployment config.
12. Style: ruff (line length 110), type hints everywhere, `from __future__ import annotations`, small
    functions, comments only where the reason is not obvious.

## Tests

- Unit tests (`tests/unit`) for pure domain code. Integration tests (`tests/integration/test_<module>.py`)
  drive the ASGI app with the `staff` / `lead` / `admin` / `anon` fixtures (a signed-in `Client` with CSRF).
- Port the matching TS test in `apps/server/test/integration` alongside the module; same assertions.
- Each test session clones a fresh DB from the seeded template `ci_template`. When several people or agents
  run tests at once, give each its own DB: `TEST_DB=command_inbox_test_<name>`.
- Checks before you hand over: `uv run ruff check src tests && uv run ruff format --check src tests &&
  uv run pytest -q && uv run python scripts/gen_dto.py --check && uv run alembic check`.

## Migrations

`uv run alembic revision -m "..."`, hand-written SQL where RLS or constraints are involved. New tenant
tables: an `org_id` column, composite FK `(org_id, id)` where they point at other tenant rows, and
`select ci_enable_tenant_rls('<table>')`. `alembic check` must pass (models and migrations agree).
