# Threat model (STRIDE)

Scope: one bank's dedicated Command Inbox stack (decision D1) — web app, console, API, worker, Postgres,
the bank's mailbox provider (Microsoft 365 or Google Workspace), the model providers (System 2) and the
self-hosted classifier (System 1), Keycloak, KMS. Reviewed at every major release and before each pilot.

## Assets

| Asset | Why it matters |
|---|---|
| Customer mail and attachments | Personal and financial data; regulated. |
| Mailbox credentials (OAuth refresh tokens) | Read and send as the bank's shared mailbox. |
| Replies sent to customers | A wrong or forged reply is a conduct and fraud risk. |
| Approved knowledge | What the AI may state as fact. |
| Deployment configurations | Decide what the AI does with mail. |
| Audit log | Evidence for the bank, its auditors and regulators. |
| Tenant data keys | Decrypt everything sealed for the tenant. |

## Trust boundaries

1. Internet → ingress (browser, provider webhooks, SCIM from the bank's IdP).
2. API/worker → Postgres, KMS (private network / VPC endpoints).
3. Worker → Microsoft Graph / Gmail API, model providers, the bank's SIEM (egress allowlist).
4. Operators (vendor) → console; bank staff → web app.

## Threats and controls

| STRIDE | Threat | Controls (in the product) | Residual / owner |
|---|---|---|---|
| Spoofing | A forged provider webhook injects mail. | Graph `clientState` checked by hash; Gmail push checked as a Google-signed OIDC token for the configured service account and audience; notifications only trigger a fetch from the provider (content never taken from the webhook). | Low. |
| Spoofing | A spoofed sender (look-alike domain, forged From) asks for a payment change. | SPF/DKIM/DMARC verdicts recorded; own-domain spoofing flagged; unverified senders never reach an automatic lane; hard stops for payment-detail changes; a person approves every reply (D5). | Social engineering remains: staff training. |
| Spoofing | Stolen staff session. | Bank IdP with its MFA/Conditional Access; HttpOnly SameSite cookies; CSRF tokens; session revocation on member removal and SCIM deprovisioning; per-user rate limit. | Bank IdP policy. |
| Spoofing | Operator account takeover. | Separate operators realm; second factor required (acr/amr checked by the API); platform RBAC; every operator action in the platform audit chain. | Hardware keys recommended. |
| Tampering | Audit log edited to hide an action. | Per-tenant hash chain verified on demand and in restore drills; signed exports; SIEM streaming off-box; WORM bucket for exports. | DBA collusion detectable, not preventable: SIEM copy. |
| Tampering | Deployment config changed to auto-send. | No automatic lane in v1 (thresholds locked at 1.0 by the starter pack; D5); drafts need evals on the exact config hash and four-eyes publishing; model policy enforced at save and publish. | — |
| Tampering | Knowledge poisoned with false statements. | Upload → AV scan → sandboxed parse → pending until an approver with clearance approves; versions and expiry; grounding post-check flags unsupported sentences. | Approver diligence. |
| Repudiation | "I did not approve that reply." | The approver, time and draft diff are audited per ticket; the gateway records the evidence opened. | — |
| Information disclosure | Tenant A reads tenant B's data. | Postgres row-level security enabled and forced on every tenant table (tested); composite foreign keys; dedicated stack per bank (D1). | — |
| Information disclosure | Mail sent to a model provider is retained or used for training. | PII masked before any model call; allowed providers per workspace (policy); `store=false` on OpenAI; zero-retention agreements; System 1 self-hosted. | Contractual (ZDR). |
| Information disclosure | Prompt injection makes the model leak other data. | The model sees only this thread and retrieved approved passages; no tools or actions; structured outputs; citations checked against supplied sources. | Low. |
| Information disclosure | SSRF through a configured URL (SIEM). | HTTPS only; private, loopback, link-local and reserved addresses refused unless allowlisted; checked on save and at send. | — |
| Information disclosure | Secrets in logs or the repo. | Secrets from the secret manager only; sealed at rest with the tenant key; structured logs without bodies; Semgrep secrets rules in CI. | — |
| Denial of service | A runaway client or script exhausts the API or model budget. | Per-tenant and per-user rate limits; per-mail and monthly model budgets; upload size limits; parse sandbox with CPU/memory/time limits. | Volumetric DDoS: WAF/CDN. |
| Denial of service | Provider throttling stalls ingest. | `Retry-After` honoured; delta catch-up; polling fallback; alerts on lag. | — |
| Elevation of privilege | Staff grants themselves admin. | Roles changed only by admins (audited); separation-of-duties capabilities locked; SCIM roles only for provisioned members and never the last admin. | — |
| Elevation of privilege | Malicious document exploits the parser. | ClamAV before parsing; parsing in a subprocess with resource limits and no network; read-only root file system in Kubernetes. | Parser CVEs: image scanning. |

## Open items

- External penetration test before the pilot (plan §16 Phase 6 exit); findings tracked to closure.
- Egress by DNS name needs the cluster's FQDN policy (NetworkPolicy allows by CIDR only).
