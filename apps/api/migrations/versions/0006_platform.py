"""Platform console: operators, their sessions and audit chain; tenant lifecycle, provisioning, data keys,
transactional email, and tokenised invitations.

- Operators (the vendor's platform staff) are not tenant users: their own table, sessions and roles
  (platform_owner, operator, support). Platform tables name the tenant `tenant_id`, not `org_id`: they are
  read across tenants by the console and are deliberately outside tenant row-level security.
- Every operator action is written to `platform_audit_events`, a single hash chain, append-only for the app.
- Tenants gain a lifecycle status (existing tenants are live), a legal name, region, data-residency profile,
  plan limits and contact details.
- Provisioning is a resumable job whose steps are recorded in `tenant_provisioning`.
- Each tenant has data-encryption keys wrapped by a key-encryption key (`tenant_keys`, envelope encryption).
- Transactional email goes through `email_messages`; the body is sealed at rest and dropped once sent.
- Invitations carry the SHA-256 of a single-use token and may be sent by an operator (first admin).
  `ci_invitation_by_token` is the narrow cross-tenant lookup an invitee needs before they are anyone, and
  `ci_tenant_counts` gives the console per-tenant counts (numbers only, never content).

Revision ID: 0006_platform
Revises: 0005_tenant_locale_unmatched
"""

from __future__ import annotations

from alembic import op

revision = "0006_platform"
down_revision = "0005_tenant_locale_unmatched"
branch_labels = None
depends_on = None


def _run(sql: str) -> None:
    """asyncpg runs one statement per call. No statement here has `;` followed by a newline inside it."""
    for statement in sql.split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(
        """
        CREATE TABLE platform_operators (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          email text NOT NULL,
          name text NOT NULL,
          role text NOT NULL,
          idp_subject text,
          created_at timestamptz NOT NULL DEFAULT now(),
          last_login_at timestamptz,
          disabled_at timestamptz,
          CONSTRAINT platform_operators_email_uq UNIQUE (email),
          CONSTRAINT platform_operators_subject_uq UNIQUE (idp_subject),
          CONSTRAINT platform_operators_email_lower_ck CHECK (email = lower(email)),
          CONSTRAINT platform_operators_role_ck CHECK (role in ('platform_owner', 'operator', 'support'))
        );
        CREATE TABLE platform_sessions (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          operator_id uuid NOT NULL REFERENCES platform_operators(id) ON DELETE CASCADE,
          token_hash text NOT NULL,
          csrf_token text NOT NULL,
          ip text NOT NULL DEFAULT '',
          user_agent text NOT NULL DEFAULT '',
          created_at timestamptz NOT NULL DEFAULT now(),
          last_seen_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL,
          revoked_at timestamptz,
          CONSTRAINT platform_sessions_token_uq UNIQUE (token_hash)
        );
        CREATE TABLE platform_audit_events (
          seq bigserial PRIMARY KEY,
          at timestamptz NOT NULL,
          operator_id uuid,
          operator_email text NOT NULL,
          action text NOT NULL,
          tenant_id uuid,
          summary text NOT NULL,
          data jsonb NOT NULL DEFAULT '{}'::jsonb,
          prev_hash text NOT NULL,
          hash text NOT NULL
        );
        CREATE INDEX platform_audit_tenant_idx ON platform_audit_events (tenant_id, seq);
        REVOKE UPDATE, DELETE, TRUNCATE ON platform_audit_events FROM ci_app;
        CREATE TRIGGER platform_audit_no_update BEFORE UPDATE OR DELETE ON platform_audit_events
          FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();

        ALTER TABLE orgs
          ADD COLUMN status text NOT NULL DEFAULT 'live',
          ADD COLUMN status_before_suspend text,
          ADD COLUMN status_changed_at timestamptz NOT NULL DEFAULT now(),
          ADD COLUMN legal_name text NOT NULL DEFAULT '',
          ADD COLUMN region text NOT NULL DEFAULT '',
          ADD COLUMN data_residency text NOT NULL DEFAULT '',
          ADD COLUMN support_email text NOT NULL DEFAULT '',
          ADD COLUMN limits jsonb NOT NULL DEFAULT '{}'::jsonb,
          ADD COLUMN created_by_operator uuid REFERENCES platform_operators(id),
          ADD CONSTRAINT orgs_status_ck CHECK (status in ('draft', 'provisioning', 'provisioned', 'onboarding', 'shadow',
            'assisted', 'live', 'suspended', 'archived'));
        ALTER TABLE orgs ALTER COLUMN status DROP DEFAULT;
        UPDATE orgs SET legal_name = name WHERE legal_name = '';

        CREATE TABLE tenant_provisioning (
          tenant_id uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
          step text NOT NULL,
          position int NOT NULL,
          state text NOT NULL DEFAULT 'pending',
          attempts int NOT NULL DEFAULT 0,
          detail text NOT NULL DEFAULT '',
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, step),
          CONSTRAINT tenant_provisioning_state_ck
            CHECK (state in ('pending', 'running', 'done', 'skipped', 'failed'))
        );

        CREATE TABLE tenant_keys (
          tenant_id uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
          version int NOT NULL,
          kek_ref text NOT NULL,
          wrapped_key text,
          state text NOT NULL DEFAULT 'active',
          created_at timestamptz NOT NULL DEFAULT now(),
          destroyed_at timestamptz,
          PRIMARY KEY (tenant_id, version),
          CONSTRAINT tenant_keys_state_ck CHECK (state in ('active', 'retired', 'destroyed')),
          CONSTRAINT tenant_keys_destroyed_ck CHECK ((state = 'destroyed') = (wrapped_key is null))
        );
        CREATE UNIQUE INDEX tenant_keys_one_active_uq ON tenant_keys (tenant_id) WHERE state = 'active';

        CREATE TABLE email_messages (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid REFERENCES orgs(id) ON DELETE SET NULL,
          template text NOT NULL,
          to_addr text NOT NULL,
          subject text NOT NULL,
          body_sealed text,
          state text NOT NULL DEFAULT 'queued',
          attempts int NOT NULL DEFAULT 0,
          last_error text NOT NULL DEFAULT '',
          provider_message_id text,
          created_at timestamptz NOT NULL DEFAULT now(),
          sent_at timestamptz,
          CONSTRAINT email_messages_state_ck CHECK (state in ('queued', 'sent', 'logged', 'failed'))
        );
        CREATE INDEX email_messages_tenant_idx ON email_messages (tenant_id, created_at);

        ALTER TABLE invitations
          ADD COLUMN token_hash text,
          ADD COLUMN name text NOT NULL DEFAULT '',
          ADD COLUMN invited_by_operator uuid REFERENCES platform_operators(id),
          ADD COLUMN sent_at timestamptz,
          ADD COLUMN send_count int NOT NULL DEFAULT 0,
          ALTER COLUMN invited_by DROP NOT NULL,
          ADD CONSTRAINT invitations_inviter_ck CHECK (invited_by is not null or invited_by_operator is not null);
        CREATE UNIQUE INDEX invitations_token_uq ON invitations (token_hash) WHERE token_hash is not null;

        CREATE OR REPLACE FUNCTION ci_invitation_by_token(h text) RETURNS TABLE (invitation_id uuid, org_id uuid)
          LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT id, org_id FROM invitations WHERE token_hash = h LIMIT 1
        $$;
        REVOKE ALL ON FUNCTION ci_invitation_by_token(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION ci_invitation_by_token(text) TO ci_app;

        CREATE OR REPLACE FUNCTION ci_tenant_counts() RETURNS TABLE (org_id uuid, mailboxes bigint)
          LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT o.id, (SELECT count(*) FROM mailboxes m WHERE m.org_id = o.id) FROM orgs o
        $$;
        REVOKE ALL ON FUNCTION ci_tenant_counts() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION ci_tenant_counts() TO ci_app;
        """
    )


def downgrade() -> None:
    _run(
        """
        DROP FUNCTION IF EXISTS ci_tenant_counts();
        DROP FUNCTION IF EXISTS ci_invitation_by_token(text);
        DROP INDEX IF EXISTS invitations_token_uq;
        DELETE FROM invitations WHERE invited_by IS NULL;
        ALTER TABLE invitations
          DROP CONSTRAINT invitations_inviter_ck,
          ALTER COLUMN invited_by SET NOT NULL,
          DROP COLUMN send_count,
          DROP COLUMN sent_at,
          DROP COLUMN invited_by_operator,
          DROP COLUMN name,
          DROP COLUMN token_hash;
        DROP TABLE email_messages;
        DROP TABLE tenant_keys;
        DROP TABLE tenant_provisioning;
        ALTER TABLE orgs
          DROP CONSTRAINT orgs_status_ck,
          DROP COLUMN created_by_operator,
          DROP COLUMN limits,
          DROP COLUMN support_email,
          DROP COLUMN data_residency,
          DROP COLUMN region,
          DROP COLUMN legal_name,
          DROP COLUMN status_changed_at,
          DROP COLUMN status_before_suspend,
          DROP COLUMN status;
        DROP TABLE platform_audit_events;
        DROP TABLE platform_sessions;
        DROP TABLE platform_operators;
        """
    )
