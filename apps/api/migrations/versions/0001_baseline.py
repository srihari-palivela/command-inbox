"""Baseline schema: every table, row-level security, grants and the append-only audit log.

Replays the SQL the service has always been created from (migrations/sql), statement by statement.

Revision ID: 0001_baseline
Revises:
"""

from pathlib import Path

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

_SQL = Path(__file__).resolve().parent.parent / "sql"


def _run_file(name: str) -> None:
    for statement in (_SQL / name).read_text().split("--> statement-breakpoint"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    _run_file("0000_init.sql")
    _run_file("0001_security.sql")


def downgrade() -> None:
    raise NotImplementedError("the baseline cannot be downgraded; restore from backup")
