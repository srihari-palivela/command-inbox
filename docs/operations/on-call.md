# On-call

- **Pages** (severity `page`): ingest lag, API error budget burn, send failures, job backlog, service down.
  Acknowledge within 15 minutes, 24×7, during the pilot and after go-live.
- **Tickets** (severity `ticket`): webhooks slow, model fallbacks. Next business day.
- Tenant-facing alerts (deadlines, mailbox health, budget, knowledge, SIEM) go to the bank's admins in the
  product and by email; the vendor sees them in Fleet health.

Each alert links to its runbook in `runbooks.md`. For an incident: open a channel, name an incident lead,
update the bank's contact within 30 minutes, and write a post-incident review within 5 working days.

Routine work:
- Weekly: Fleet health review (renewals, failed jobs, fallback rates, spend against plan).
- Monthly: dependency and image updates; restore of one tenant's audit export to verify its manifest.
- Quarterly: restore drill (`dr-drill.md`); key rotation (`command-inbox-operator keys rotate`); access review
  of operators.
