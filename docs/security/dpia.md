# Data protection impact assessment — template, prefilled

The bank is the controller; the vendor is its processor (DPA / GDPR Art. 28). This template is prefilled with
how the product processes data; the bank completes the sections marked **[bank]**.

## 1. Processing

| Item | Description |
|---|---|
| Purpose | Sorting, prioritising and drafting replies to customer email for the bank's service teams; every reply approved and sent by a person. |
| Data subjects | Bank customers and prospects who write to the connected mailbox; bank staff using the product. |
| Personal data | Email addresses, names, message content (may include account numbers, amounts, contact details, and special-category data customers choose to write); staff names, emails, actions. |
| Sources | The bank's shared mailbox (Microsoft 365 / Google Workspace); the bank's identity provider (staff). |
| Recipients | Bank staff with access to the workspace; sub-processors (see `subprocessors.md`); the bank's SIEM if configured. |
| Retention | Mail text of closed tickets: `retention_mail_days` (default 730, **[bank]** sets it to its schedule); model traces: `retention_trace_days` (default 180); audit log: kept for the life of the tenant, exported to WORM storage. The source mailbox is never modified beyond labels. |
| Location | The bank's chosen region for the database, storage and workers **[bank]**; System 2 provider region per contract **[bank]**. |

## 2. Necessity and proportionality

- Only the connected mailbox is read; no other mailbox or file store.
- Models see masked text (names, account and card numbers, phone numbers, emails replaced) and only the
  current thread plus retrieved approved knowledge.
- No automated decisions with legal or similarly significant effect: a person approves every reply (D5).
- Staff data: minimum for access control and accountability (who approved what).

## 3. Risks and measures

| Risk | Likelihood / severity | Measures |
|---|---|---|
| Disclosure to the wrong customer | Low / high | Replies in-thread only; recipient shown at approval; sender verification; four-eyes where configured. |
| Inaccurate information sent | Medium / medium | Drafts only from approved knowledge with citations; unsupported sentences flagged; a person approves. |
| Model provider retention | Low / high | Masking; zero-retention agreements; provider allow-list per workspace; self-hosted System 1. |
| Unauthorised staff access | Low / high | Bank SSO with MFA; roles and clearances; SCIM deprovisioning; audit. |
| Excessive retention | Medium / medium | Retention sweep; audit of every sweep. |

## 4. Data subject rights

Access and erasure requests are handled by the bank from its own systems; the vendor assists: tickets and mail
for an email address can be exported, and mail text removed ahead of the retention period **[process to agree]**.

## 5. Sign-off **[bank]**

DPO, information security, and the business owner.
