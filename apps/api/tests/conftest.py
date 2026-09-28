"""Test configuration.

Integration tests get their own database, cloned from a seeded template (`TEST_TEMPLATE_DB`, default
`ci_template`) once per session, so they never touch development data and start from identical state.
The environment is set before `command_inbox` is imported, because settings and engines are built at import.
"""

from __future__ import annotations

import os

ADMIN_BASE = os.environ.get("TEST_PG_ADMIN", "postgresql://postgres:postgres@localhost:5432")
TEST_DB = os.environ.get("TEST_DB", "command_inbox_test_py")
TEMPLATE_DB = os.environ.get("TEST_TEMPLATE_DB", "ci_template")
assert TEST_DB.endswith(("_test", "_test_py")) or "test" in TEST_DB, (
    "integration tests only run on a *test* database"
)

host = ADMIN_BASE.split("@", 1)[1]
os.environ.update(
    {
        "APP_ENV": "test",
        "DEMO_MODE": "true",
        "LLM_PROVIDER": "heuristic",
        "DECISION_ENGINE": "heuristic",
        "EMBEDDED_WORKER": "false",
        "LOG_LEVEL": "WARNING",
        "LOG_JSON": "false",
        "DATABASE_URL": f"postgresql+asyncpg://ci_app:ci_app@{host}/{TEST_DB}",
        "DATABASE_ADMIN_URL": f"postgresql+asyncpg://postgres:postgres@{host}/{TEST_DB}",
    }
)
