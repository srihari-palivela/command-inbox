#!/usr/bin/env bash
# Restore drill (plan §13.4): restore the latest backup into a scratch database, check it, and time it.
# RPO is how old the newest restored write is; RTO is how long the whole restore took. Run quarterly and file
# the output with the change record.
#
#   SOURCE_URL=postgres://...  (read-only replica or the backup's host)  DRILL_URL=postgres://.../dr_drill
#   ./infra/scripts/dr-drill.sh
#
# With a managed database the restore step is the provider's point-in-time restore instead (for example
# `aws rds restore-db-instance-to-point-in-time`); the checks below are the same.
set -euo pipefail
: "${SOURCE_URL:?set SOURCE_URL}"
: "${DRILL_URL:?set DRILL_URL}"
start=$(date +%s)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "== dump"
pg_dump --format=custom --no-owner --file "$work/db.dump" "$SOURCE_URL"
echo "== restore"
pg_restore --clean --if-exists --no-owner --dbname "$DRILL_URL" "$work/db.dump"

echo "== checks"
psql "$DRILL_URL" -v ON_ERROR_STOP=1 -At <<'SQL'
select 'tenants', count(*) from orgs;
select 'tickets', count(*) from tickets;
select 'audit events', count(*) from audit_events;
select 'newest write (RPO reference)', greatest(max(at), (select max(created_at) from tickets)) from audit_events;
select 'extension vector', count(*) from pg_extension where extname = 'vector';
SQL
echo "== audit chains"
DRILL_DATABASE_URL="${DRILL_URL/postgres:/postgresql+asyncpg:}" python -m command_inbox.core.audit_verify_all || {
  echo "audit chain verification failed"; exit 1; }

end=$(date +%s)
echo "== restore took $((end - start)) s (RTO target 4 h). After a real restore, run:"
echo "   command-inbox-operator mail catch-up   # mail since the backup is fetched from the providers"
