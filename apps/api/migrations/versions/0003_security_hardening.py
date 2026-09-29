"""Security hardening from the pre-port audit.

- A mailbox address belongs to exactly one tenant, globally (intake must never route to the wrong bank).
- The address → tenant lookup refuses ambiguity and pins its search path (no pg_temp shadowing).
- Each tenant can pin the identity-provider alias and email domains it trusts for SSO linking.
- Invitations are unique only while pending, and emails are stored lowercase.

Revision ID: 0003_security_hardening
Revises: 0002_deployments
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_security_hardening"
down_revision = "0002_deployments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("mailboxes_address_global_uq", "mailboxes", [sa.text("lower(address)")], unique=True)
    op.execute("""
        CREATE OR REPLACE FUNCTION ci_mailbox_org(addr text) RETURNS uuid
          LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
        DECLARE found uuid; n int;
        BEGIN
          SELECT count(*), min(org_id::text)::uuid INTO n, found FROM public.mailboxes WHERE lower(address) = lower(addr);
          IF n > 1 THEN
            RAISE EXCEPTION 'mailbox % is claimed by more than one tenant', addr USING ERRCODE = 'unique_violation';
          END IF;
          RETURN found;
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION ci_mailbox_org(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION ci_mailbox_org(text) TO ci_app")

    op.add_column("orgs", sa.Column("sso_idp_alias", sa.Text(), nullable=True))
    op.add_column(
        "orgs",
        sa.Column(
            "sso_email_domains", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    )

    op.drop_index("invitations_org_email_uq", table_name="invitations")
    op.create_check_constraint("invitations_email_lower_ck", "invitations", "email = lower(email)")
    op.create_index(
        "invitations_pending_uq",
        "invitations",
        ["org_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at is null and revoked_at is null"),
    )


def downgrade() -> None:
    op.drop_index("invitations_pending_uq", table_name="invitations")
    op.drop_constraint("invitations_email_lower_ck", "invitations")
    op.create_index("invitations_org_email_uq", "invitations", ["org_id", "email"], unique=True)
    op.drop_column("orgs", "sso_email_domains")
    op.drop_column("orgs", "sso_idp_alias")
    op.drop_index("mailboxes_address_global_uq", table_name="mailboxes")
