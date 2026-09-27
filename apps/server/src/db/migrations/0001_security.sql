-- Security baseline: runtime role, grants, row-level security, append-only audit log.
-- The runtime role is created NOLOGIN here; infrastructure grants LOGIN and a password
-- (see scripts/setup-db.sh for local development).

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ci_app') THEN
    CREATE ROLE ci_app NOLOGIN;
  END IF;
END
$$;
--> statement-breakpoint

GRANT USAGE ON SCHEMA public TO ci_app;
--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ci_app;
--> statement-breakpoint
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ci_app;
--> statement-breakpoint
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ci_app;
--> statement-breakpoint
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO ci_app;
--> statement-breakpoint

-- The audit log is append-only for the application.
REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM ci_app;
--> statement-breakpoint

CREATE OR REPLACE FUNCTION audit_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_events is append-only (% blocked)', TG_OP USING ERRCODE = 'insufficient_privilege';
END
$$;
--> statement-breakpoint

DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events;
--> statement-breakpoint
CREATE TRIGGER audit_events_no_update BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();
--> statement-breakpoint

-- Row-level security on every tenant table: every table with an org_id column, except the global
-- identity tables and the internal job/outbox queues (claimed across tenants by the worker, never
-- exposed through the API).
CREATE OR REPLACE FUNCTION ci_enable_tenant_rls(tbl text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
  EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', tbl);
  EXECUTE format(
    'CREATE POLICY tenant_isolation ON %I USING (org_id = nullif(current_setting(''app.org_id'', true), '''')::uuid) '
    'WITH CHECK (org_id = nullif(current_setting(''app.org_id'', true), '''')::uuid)', tbl);
END
$$;
--> statement-breakpoint

DO $$
DECLARE t record;
BEGIN
  FOR t IN
    SELECT c.table_name
      FROM information_schema.columns c
      JOIN information_schema.tables tb ON tb.table_name = c.table_name AND tb.table_schema = c.table_schema
     WHERE c.table_schema = 'public'
       AND c.column_name = 'org_id'
       AND tb.table_type = 'BASE TABLE'
       AND c.table_name NOT IN ('memberships', 'sessions', 'jobs', 'outbox')
  LOOP
    PERFORM ci_enable_tenant_rls(t.table_name);
  END LOOP;
END
$$;
--> statement-breakpoint

-- Intake webhooks arrive before any tenant is known. This narrowly scoped lookup (address → org) is the
-- only cross-tenant read the application role can make.
CREATE OR REPLACE FUNCTION ci_mailbox_org(addr text) RETURNS uuid
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT org_id FROM mailboxes WHERE address = lower(addr) LIMIT 1
$$;
--> statement-breakpoint
REVOKE ALL ON FUNCTION ci_mailbox_org(text) FROM PUBLIC;
--> statement-breakpoint
GRANT EXECUTE ON FUNCTION ci_mailbox_org(text) TO ci_app;
