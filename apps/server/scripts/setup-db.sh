#!/usr/bin/env bash
# Local development: create the database and give the runtime role a login.
# Usage: ADMIN_URL=postgres://postgres:postgres@localhost:5432/postgres ./scripts/setup-db.sh [dbname]
set -euo pipefail
DB="${1:-command_inbox}"
ADMIN_URL="${ADMIN_URL:-postgres://postgres:postgres@localhost:5432/postgres}"
psql "$ADMIN_URL" -tc "SELECT 1 FROM pg_database WHERE datname = '$DB'" | grep -q 1 || psql "$ADMIN_URL" -c "CREATE DATABASE \"$DB\""
psql "$ADMIN_URL" -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ci_app') THEN CREATE ROLE ci_app LOGIN PASSWORD 'ci_app'; ELSE ALTER ROLE ci_app LOGIN PASSWORD 'ci_app'; END IF; END \$\$;"
echo "database $DB ready; runtime role ci_app can log in"
