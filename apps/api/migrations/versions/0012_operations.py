"""Operations: retention periods, SIEM streaming of the audit log, and redaction markers.

- `orgs.retention_mail_days`: customer mail text (and the original MIME) of tickets closed longer ago than
  this is removed (null: kept). `orgs.retention_trace_days`: model traces older than this are deleted.
  The audit log is never removed by retention.
- `orgs.siem_*`: an HTTPS endpoint the audit log is streamed to (signed batches), the sealed signing
  secret, how far it has been delivered, and its last outcome.
- `messages.redacted_at`, `mail_messages.redacted_at`: when retention removed a message's text.

Revision ID: 0012_operations
Revises: 0011_alerts
"""

from __future__ import annotations

from alembic import op

revision = "0012_operations"
down_revision = "0011_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in (
        "ALTER TABLE orgs ADD COLUMN retention_mail_days integer DEFAULT 730, "
        "ADD COLUMN retention_trace_days integer DEFAULT 180, "
        "ADD COLUMN siem_url text, ADD COLUMN siem_secret_sealed text, "
        "ADD COLUMN siem_cursor bigint NOT NULL DEFAULT 0, ADD COLUMN siem_last_ok_at timestamptz, "
        "ADD COLUMN siem_last_error text, "
        "ADD CONSTRAINT orgs_retention_ck CHECK ((retention_mail_days IS NULL OR retention_mail_days >= 30) "
        "AND (retention_trace_days IS NULL OR retention_trace_days >= 7))",
        "ALTER TABLE messages ADD COLUMN redacted_at timestamptz",
        "ALTER TABLE mail_messages ADD COLUMN redacted_at timestamptz",
    ):
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE mail_messages DROP COLUMN redacted_at")
    op.execute("ALTER TABLE messages DROP COLUMN redacted_at")
    op.execute(
        "ALTER TABLE orgs DROP CONSTRAINT orgs_retention_ck, DROP COLUMN retention_mail_days, "
        "DROP COLUMN retention_trace_days, DROP COLUMN siem_url, DROP COLUMN siem_secret_sealed, "
        "DROP COLUMN siem_cursor, DROP COLUMN siem_last_ok_at, DROP COLUMN siem_last_error"
    )
