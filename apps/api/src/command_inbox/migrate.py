"""`command-inbox-migrate`: bring a database schema to the latest revision, and nothing else.

    command-inbox-migrate [--database-url URL]

This is the only schema step a production deployment runs (Helm hook, compose one-shot). It never
creates tenants or sample data: real workspaces are created from the platform console.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _alembic_ini() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "alembic.ini"
        if candidate.is_file():
            return candidate
    raise SystemExit("alembic.ini not found next to the package; run from the apps/api checkout")


def migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_alembic_ini()))
    cfg.attributes["url"] = url
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


def main(argv: list[str] | None = None) -> int:
    from command_inbox.config import settings
    from command_inbox.seed import asyncpg_url

    p = argparse.ArgumentParser(prog="command-inbox-migrate", description=__doc__.splitlines()[0])
    p.add_argument("--database-url", default=None, help="schema-owner URL (default: DATABASE_ADMIN_URL)")
    args = p.parse_args(argv)
    migrate(asyncpg_url(args.database_url or settings.database_admin_url))
    print("database is at the latest revision")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
