"""A tenant's own single sign-on connection (Entra ID or Google Workspace), set by its admin.

`orgs.sso_config` holds the provider, directory ID or domain, client ID, the client secret sealed with the
tenant's data key, and the connection state. Keycloak holds the live identity provider; this is the record
of what the admin asked for and whether it was applied.

Revision ID: 0007_workspace_sso
Revises: 0006_platform
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_workspace_sso"
down_revision = "0006_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orgs",
        sa.Column("sso_config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("orgs", "sso_config")
