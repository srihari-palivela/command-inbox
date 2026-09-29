# Runbooks

Each Prometheus alert (`infra/prometheus/rules.yml`) links here. Every runbook follows the same shape:
what it means for the bank, how to confirm, what to do, and when to escalate. Tenant-facing alerts
(deadlines, mailbox health, budget, knowledge, SIEM) are raised in the product itself and emailed to the
bank's admins; the runbooks below are for the operator on call.

## Ingest lag

**Means:** customer mail takes more than a minute (p95) to become a ticket. Deadlines start at receipt, so
the bank loses time.

**Confirm:** console → Fleet health (last mail per tenant, unhealthy mailboxes); `mail_ingest_lag_seconds`
by provider; the `mail_sync` rows in the job queue.

**Act:**
1. Job queue backed up (`JobBacklog` also firing)? See [Job backlog](#job-backlog).
2. One provider only: check Microsoft 365 / Google Workspace status pages and provider throttling
   (`Retry-After` in `mail_sync_events`). Throttling clears on its own; polling continues.
3. Webhooks failing ([Webhooks](#webhooks)): mail still arrives by polling every minute; fix the ingress.

**Escalate:** lag above 15 minutes for 30 minutes — tell the bank's admins (status page / email) that
deadlines may be affected.

## API errors

**Means:** more than 1.44% of API requests fail (a 99.9% monthly error budget burning 14× too fast).

**Confirm:** `ci:api_errors:ratio_5m` by route; API logs by `request_id`; `/readyz` (database).

**Act:** roll back the last deploy if it correlates (`helm rollback`); check Postgres (connections,
locks, disk) and the KMS; scale the API if CPU-bound.

**Escalate:** above 5% for 10 minutes — incident, status page.

## Send failures

**Means:** approved replies are not reaching customers.

**Confirm:** `mail_sends_total{outcome="failed"}`; the ticket notes ("send failed"); the mailbox's health on
the bank's Where mail arrives screen (often `reauth_required`).

**Act:** a revoked consent needs the bank's admin to reconnect — contact them. Provider outage: sends are
retried with the intent recorded, so nothing is sent twice.

## Webhooks

**Means:** provider notifications are slow or failing. Mail falls back to polling (≤ 1 minute), so this is a
ticket, not a page.

**Act:** check the ingress for `/v1/hooks/*` (TLS, WAF rules), Graph subscription and Gmail watch expiries
(Fleet health → renewing), and clock skew (signature checks).

## Job backlog

**Means:** the oldest due job has waited more than 5 minutes: triage, sends and sweeps are delayed.

**Confirm:** Fleet health → Job queue (which kind); worker logs; `jobs_processed_total{outcome="error"}`.

**Act:** restart stuck workers (leases are reclaimed automatically after expiry); scale workers; a poison
job fails after its attempts and is logged — fix and requeue.

## Model provider

**Means:** model stages keep falling back to the deterministic provider: drafts stop and mail goes to
people (safe, but slower).

**Confirm:** `system2_fallbacks_total` by stage; the reasons on triage traces ("circuit open", "not allowed",
"budget"); provider status pages.

**Act:** provider outage — wait, the circuit breaker retries every minute. Budget — the bank's admin
decides (Organisation → AI model providers). Policy — a deployment names a provider the bank removed.

## Service down

**Means:** the API or worker stopped answering `/metrics`.

**Act:** check the pod/container, recent deploys, and Postgres reachability; roll back if a deploy is the
cause.
