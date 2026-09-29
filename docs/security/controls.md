# Security controls (for third-party risk questionnaires)

Answers reference where each control lives so reviewers can verify it.

| Area | Control | Evidence |
|---|---|---|
| Tenancy | Dedicated stack per bank; row-level security forced on every tenant table; composite foreign keys | `migrations/`, `tests/integration/test_deployments.py::test_every_tenant_table_has_rls_enabled_and_forced` |
| Identity (staff) | Bank SSO through Keycloak (Entra ID / Google); MFA and Conditional Access from the bank; SCIM 2.0 provisioning and deprovisioning | `auth/`, `modules/scim/` |
| Identity (vendor) | Separate operators realm; second factor required; platform RBAC; platform audit chain | `platform/` |
| Authorisation | One role per workspace; capability matrix with locked separation-of-duties; clearances per team | `rbac/`, Permissions screen |
| Encryption at rest | Database/volume encryption (cloud KMS); per-tenant data keys wrapping mailbox credentials, SIEM secrets and stored MIME; KEK in Vault Transit or AWS KMS (BYOK); rotation and re-wrap commands; crypto-shredding | `platform/keys.py`, Terraform `modules/keys` |
| Encryption in transit | TLS 1.2+ at ingress; `rds.force_ssl`; HSTS; private endpoints | Helm ingress, Terraform |
| Network | Default-deny NetworkPolicies with an egress allowlist; VPC endpoints for KMS/S3/Secrets | Helm `networkpolicy.yaml`, Terraform `modules/network` |
| Application | CSRF, strict cookies, CSP (web and API), per-tenant and per-user rate limits, upload limits, outbound URL guard, idempotency keys | `main.py`, `core/` |
| AI | PII masking before models; provider allow-list; budgets; no actions (D5); eval gates and four-eyes publishing; grounding checks | `agents/`, `evals/` |
| Content safety | ClamAV scan before parsing; sandboxed parsers | `knowledge/` |
| Logging and audit | Hash-chained tenant and platform audit; signed exports; SIEM streaming; structured logs; OpenTelemetry | `core/audit.py`, `modules/workspace/operations.py` |
| Monitoring | SLOs and paging alerts; tenant alerts; runbooks | `infra/prometheus/`, `docs/operations/runbooks.md` |
| Resilience | Multi-AZ Postgres, PITR 35 days, quarterly restore drill, mail catch-up after restore | `docs/operations/dr-drill.md` |
| Secure development | Code review, CI (lint, types, tests, contract and migration drift), dependency audit, Semgrep, image scanning, SBOM, signed images | `.github/workflows/` |
| Data lifecycle | Retention sweep; audit exports to WORM; tenant archive and key destruction | Organisation screen, console |
| Vulnerability management | Container and dependency scanning on every change; annual external pen test; findings tracked to closure | CI, pen-test report |
