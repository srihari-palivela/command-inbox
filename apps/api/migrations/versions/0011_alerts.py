"""Alerts raised by the system: a dedupe key, a kind, a link to the records, and when they last changed.

A condition (a mailbox down, mail past its deadline, the budget nearly spent) keeps one open alert per key;
the sweep updates its text while the condition holds and resolves it when it clears.

Revision ID: 0011_alerts
Revises: 0010_models_and_policies
"""

from __future__ import annotations

from alembic import op

revision = "0011_alerts"
down_revision = "0010_models_and_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in (
        "ALTER TABLE alerts ADD COLUMN key text, ADD COLUMN kind text NOT NULL DEFAULT 'manual', "
        "ADD COLUMN ref text, ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now()",
        "CREATE UNIQUE INDEX alerts_open_key_uq ON alerts (org_id, key) WHERE resolved_at IS NULL AND key IS NOT NULL",
    ):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX alerts_open_key_uq")
    op.execute("ALTER TABLE alerts DROP COLUMN key, DROP COLUMN kind, DROP COLUMN ref, DROP COLUMN updated_at")
