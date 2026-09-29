# Backup and restore drill

Targets: **RPO ≤ 15 minutes** (continuous WAL / point-in-time recovery, 35 days) and **RTO ≤ 4 hours**.
Run quarterly, and after any change to the database platform. File the output with the change record.

1. Restore the production database to a new instance at a chosen point in time (managed: the provider's PITR,
   e.g. `aws rds restore-db-instance-to-point-in-time`), or run `infra/scripts/dr-drill.sh` against a replica.
2. Checks (the script runs them): counts of tenants, tickets and audit events; the newest write (the RPO
   actually achieved); `vector` extension present; every tenant's audit chain verifies
   (`python -m command_inbox.core.audit_verify_all`).
3. Point a staging API at the restored database and sign in; open a ticket and the audit log.
4. **Mail after the backup point:** run `command-inbox-operator mail catch-up`. Each connected mailbox catches
   up from its stored cursor; the provider mailbox is the source of truth and ingestion is idempotent, so no
   mail is lost or duplicated.
5. Record: restore start and end (RTO), newest restored write vs. incident time (RPO), issues found.

Keys: the tenant data keys are in the database (wrapped); the KEK must be reachable from the restore region
(multi-region KMS key or Vault replication). A restore without the KEK cannot read sealed secrets — test it.
