"""Tenant locale, currency and time zone; unmatched senders carry no invented customer number.

- Each tenant records the locale, ISO 4217 currency and IANA time zone its people read numbers, money and
  times in. Existing tenants are backfilled with the values the product used to assume; new tenants must
  state theirs (no server default).
- A customer created for an unknown sender has no CIF and no "customer since" year until someone links it
  to a real customer record.

Revision ID: 0005_tenant_locale_unmatched
Revises: 0004_rollout_and_integrity
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_tenant_locale_unmatched"
down_revision = "0004_rollout_and_integrity"
branch_labels = None
depends_on = None

_LOCALE = (("locale", "en-IN"), ("currency", "INR"), ("time_zone", "Asia/Kolkata"))


def upgrade() -> None:
    for column, backfill in _LOCALE:
        op.add_column("orgs", sa.Column(column, sa.Text(), nullable=False, server_default=backfill))
        op.alter_column("orgs", column, server_default=None)
    op.create_check_constraint("orgs_currency_iso4217", "orgs", "currency ~ '^[A-Z]{3}$'")
    op.alter_column("customers", "cif", nullable=True)
    op.alter_column("customers", "since_year", nullable=True)
    # Provisional records the intake used to invent ("CIF P-1234567") become unmatched.
    op.execute("update customers set cif = null, since_year = null where cif like 'CIF P-%'")


def downgrade() -> None:
    op.execute(
        "update customers set cif = 'CIF P-' || substr(replace(id::text, '-', ''), 1, 7), "
        "since_year = extract(year from now())::int where cif is null"
    )
    op.alter_column("customers", "since_year", nullable=False)
    op.alter_column("customers", "cif", nullable=False)
    op.drop_constraint("orgs_currency_iso4217", "orgs", type_="check")
    for column, _ in reversed(_LOCALE):
        op.drop_column("orgs", column)
