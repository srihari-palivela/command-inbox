"""SCIM 2.0 provisioning from the bank's identity provider (Entra ID, Okta, Google via a connector).

- `scim_tokens`: the bearer token the IdP presents (stored as SHA-256), one active per workspace;
  `ci_scim_org` is the narrow lookup from a presented token to its workspace.
- `scim_groups`: groups the IdP pushes, with their members; `orgs.scim_group_roles` maps group names to
  workspace roles (the highest wins).
- `memberships.external_id` / `provisioned_by`: the IdP's id for the person, and how they joined
  ('invitation' or 'scim').

Revision ID: 0013_scim
Revises: 0012_operations
"""

from __future__ import annotations

from alembic import op

revision = "0013_scim"
down_revision = "0012_operations"
branch_labels = None
depends_on = None


def _run(sql: str) -> None:
    for statement in sql.split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(
        """
        CREATE TABLE scim_tokens (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          org_id uuid NOT NULL REFERENCES orgs(id),
          token_hash text NOT NULL,
          created_by uuid,
          created_at timestamptz NOT NULL DEFAULT now(),
          last_used_at timestamptz,
          revoked_at timestamptz,
          CONSTRAINT scim_tokens_pkey PRIMARY KEY (id)
        );
        CREATE UNIQUE INDEX scim_tokens_hash_uq ON scim_tokens (token_hash);
        CREATE UNIQUE INDEX scim_tokens_active_uq ON scim_tokens (org_id) WHERE revoked_at IS NULL;
        SELECT ci_enable_tenant_rls('scim_tokens');
        CREATE TABLE scim_groups (
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          org_id uuid NOT NULL REFERENCES orgs(id),
          display_name text NOT NULL,
          external_id text,
          members jsonb NOT NULL DEFAULT '[]'::jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT scim_groups_pkey PRIMARY KEY (id)
        );
        CREATE UNIQUE INDEX scim_groups_name_uq ON scim_groups (org_id, lower(display_name));
        SELECT ci_enable_tenant_rls('scim_groups');
        ALTER TABLE orgs ADD COLUMN scim_group_roles jsonb NOT NULL DEFAULT '{}'::jsonb;
        ALTER TABLE memberships ADD COLUMN external_id text,
          ADD COLUMN provisioned_by text NOT NULL DEFAULT 'invitation';
        CREATE OR REPLACE FUNCTION ci_scim_org(h text) RETURNS uuid
          LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT org_id FROM scim_tokens WHERE token_hash = h AND revoked_at IS NULL LIMIT 1
        $$;
        REVOKE ALL ON FUNCTION ci_scim_org(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION ci_scim_org(text) TO ci_app
        """
    )


def downgrade() -> None:
    _run(
        """
        DROP FUNCTION ci_scim_org(text);
        ALTER TABLE memberships DROP COLUMN external_id, DROP COLUMN provisioned_by;
        ALTER TABLE orgs DROP COLUMN scim_group_roles;
        DROP TABLE scim_groups;
        DROP TABLE scim_tokens
        """
    )
