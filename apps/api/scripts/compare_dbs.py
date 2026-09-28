"""Compare a database seeded by the TypeScript seed with one seeded by `command-inbox-seed`.

    uv run python scripts/compare_dbs.py [--prepare] [--ts-db ci_seed_ts] [--py-db ci_seed_py]
                                         [--now 2026-09-28T12:02:00Z] [--json report.json]

`--prepare` (re)creates both databases and seeds them with the same pinned clock: the TS one with
`apps/server` (drizzle migrations + `seed(url, now)`), the Python one with `alembic upgrade head` + seed.

Ids are random in the TS seed, so rows cannot be matched by id. Every uuid primary key gets a canonical
label instead, computed from the row's content (iteratively, so a row's label also covers the rows it points
at), and every uuid anywhere (FK columns, text columns, JSON) is replaced by its label before comparison.
Tables are then compared as multisets of normalised rows over their common columns.

Differences are split into *explained* ones (listed in EXPECTED with the reason) and unexplained ones; the
exit status is 1 when anything is unexplained.
"""

# ruff: noqa: S603, S607, S608 - a developer tool that runs fixed commands against local databases
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

ADMIN = os.environ.get("TEST_PG_ADMIN", "postgresql://postgres:postgres@localhost:5432")
API_DIR = Path(__file__).resolve().parents[1]
SERVER_DIR = API_DIR.parent / "server"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
NOW = "<seed-now>"

# Differences that must exist, with the reason. Anything else is reported as unexplained.
EXPECTED_TABLES_ONLY_IN_PY = {
    "alembic_version": "Alembic's bookkeeping (the TS schema is managed by drizzle)",
    "deployments": "deployments exist only in the Python platform (migration 0002)",
    "deployment_versions": "deployments exist only in the Python platform (migration 0002)",
    "eval_datasets": "evals exist only in the Python platform (migration 0002)",
    "eval_cases": "evals exist only in the Python platform (migration 0002)",
    "eval_runs": "evals exist only in the Python platform (migration 0002)",
    "eval_results": "evals exist only in the Python platform (migration 0002)",
    "role_policies": "tenant permission overrides are new in the Python platform (migration 0002)",
    "invitations": "invitations are new in the Python platform (migration 0002)",
}
EXPECTED_COLUMNS_ONLY_IN_PY = {
    "mailboxes.deployment_id": "mailboxes are bound to the default deployment (Python only)",
    "tickets.deployment_id": "tickets are bound to the default deployment (Python only)",
    "tickets.deployment_version_id": "tickets record the deployment version that triaged them (Python only)",
    "users.idp_subject": "SSO subject link (migration 0002)",
    "users.last_login_at": "SSO bookkeeping (migration 0002)",
    "orgs.sso_idp_alias": "per-tenant SSO pinning (migration 0003)",
    "orgs.sso_email_domains": "per-tenant SSO pinning (migration 0003)",
    "inbound_messages.sender_auth": "DKIM/SPF/DMARC verdict recorded at intake (sender trust, migration 0004)",
    "tickets.sender_verified": "sender trust flag, column default (sender trust, migration 0004)",
}
# Columns compared in neither direction: they hash values that contain random (TS) or seeded (Python) ids.
IGNORED_COLUMNS = {
    "audit_events.hash": "hash chain covers the org uuid, which differs; each chain is verified on its own",
    "audit_events.prev_hash": "as audit_events.hash",
    "action_instances.idempotency_key": "sha256 over the org uuid, which differs",
}
# Rows the Python seed adds on purpose (predicate over the normalised row).
EXPECTED_EXTRA_PY_ROWS = {
    "audit_events": (
        lambda r: r.get("action") == "deployment.published",
        "the Python seed publishes a default deployment per tenant and audits it",
    ),
}


def _url(db: str) -> str:
    return f"{ADMIN}/{db}"


# ── Preparing the two databases ───────────────────────────────────────────────────────────────────────


def _recreate(db: str) -> None:
    with psycopg.connect(_url("postgres"), autocommit=True) as conn:
        conn.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s and pid <> pg_backend_pid()",
            (db,),
        )
        conn.execute(f'drop database if exists "{db}"')
        conn.execute(f'create database "{db}"')


def prepare(ts_db: str, py_db: str, now: str) -> None:
    for db in (ts_db, py_db):
        _recreate(db)
    env = {**os.environ, "DATABASE_ADMIN_URL": _url(ts_db), "NODE_ENV": "test"}
    tsx = str(SERVER_DIR / "node_modules" / ".bin" / "tsx")
    subprocess.run([tsx, "src/db/migrate.ts"], cwd=SERVER_DIR, env=env, check=True)
    script = (
        "const {seed} = await import('./src/db/seed/index.ts');"
        f"await seed(process.env.DATABASE_ADMIN_URL, new Date({json.dumps(now)}));"
    )
    subprocess.run(
        ["node", "--import", "tsx", "--input-type=module", "-e", script], cwd=SERVER_DIR, env=env, check=True
    )
    subprocess.run(
        [sys.executable, "-m", "command_inbox.seed.cli", "--database-url", _url(py_db), "--now", now],
        cwd=API_DIR,
        check=True,
    )


# ── Loading and normalising ───────────────────────────────────────────────────────────────────────────


class Snapshot:
    def __init__(self, db: str, now: datetime) -> None:
        self.db = db
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self.columns: dict[str, list[str]] = {}
        now_default: list[tuple[str, str]] = []
        with psycopg.connect(_url(db)) as conn:
            cols = conn.execute(
                """select table_name, column_name, coalesce(column_default, '') from information_schema.columns
                    where table_schema = 'public' order by table_name, ordinal_position"""
            ).fetchall()
            tables = {
                r[0]
                for r in conn.execute(
                    "select table_name from information_schema.tables "
                    "where table_schema = 'public' and table_type = 'BASE TABLE'"
                )
            }
            for table, column, default in cols:
                if table in tables:
                    self.columns.setdefault(table, []).append(column)
                    if default == "now()":
                        now_default.append((table, column))
            for table in sorted(tables):
                cur = conn.execute(f'select * from "{table}"')
                names = [d.name for d in cur.description or []]
                self.tables[table] = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
        # The TS seed leaves `now()` defaults to the database (the wall clock of its transaction); the
        # Python seed fills them from the pinned seed clock. Both map to one token, as does the seed clock.
        seen: Counter[datetime] = Counter(
            r[c] for t, c in now_default for r in self.tables.get(t, []) if isinstance(r.get(c), datetime)
        )
        self.tx_time = seen.most_common(1)[0][0] if seen else None
        self.now_values = {now, *([self.tx_time] if self.tx_time else [])}

    def scalar(self, v: Any, labels: dict[str, str]) -> Any:
        if isinstance(v, UUID):
            v = str(v)
        if isinstance(v, datetime):
            v = v.astimezone(UTC)
            return NOW if v in self.now_values else v.isoformat()
        if isinstance(v, date):
            return v.isoformat()
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, str):
            return UUID_RE.sub(lambda m_: labels.get(m_.group(0), "<uuid?>"), v)
        if isinstance(v, list):
            return [self.scalar(x, labels) for x in v]
        if isinstance(v, dict):
            return {k: self.scalar(x, labels) for k, x in v.items()}
        return v


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def label_ids(snap: Snapshot, compared: dict[str, list[str]], rounds: int = 4) -> dict[str, str]:
    """uuid -> canonical label: a hash of the row's compared content, refined over several rounds."""
    labels: dict[str, str] = {}
    for _ in range(rounds):
        nxt: dict[str, str] = {}
        for table, cols in compared.items():
            if "id" not in snap.columns.get(table, []):
                continue
            for row in snap.tables[table]:
                if not isinstance(row["id"], UUID):
                    continue
                content = {c: snap.scalar(row[c], labels) for c in cols if c != "id"}
                nxt[str(row["id"])] = f"{table}#{_digest(content)}"
        labels = nxt
    return labels


# ── Comparing ─────────────────────────────────────────────────────────────────────────────────────────


def compare(ts: Snapshot, py: Snapshot, examples: int) -> dict[str, Any]:
    explained: list[str] = []
    unexplained: list[str] = []
    tables: dict[str, Any] = {}

    for t in sorted(set(py.tables) - set(ts.tables)):
        msg = f"table {t} only in Python ({len(py.tables[t])} rows)"
        (explained if t in EXPECTED_TABLES_ONLY_IN_PY else unexplained).append(
            msg + (f": {EXPECTED_TABLES_ONLY_IN_PY[t]}" if t in EXPECTED_TABLES_ONLY_IN_PY else "")
        )
    for t in sorted(set(ts.tables) - set(py.tables)):
        if t == "__drizzle_migrations":
            continue
        unexplained.append(f"table {t} only in TS ({len(ts.tables[t])} rows)")

    common = sorted(set(ts.tables) & set(py.tables))
    compared: dict[str, list[str]] = {}
    for t in common:
        ts_cols, py_cols = set(ts.columns[t]), set(py.columns[t])
        for c in sorted(py_cols - ts_cols):
            key = f"{t}.{c}"
            filled = sum(1 for r in py.tables[t] if r[c] is not None)
            if key in EXPECTED_COLUMNS_ONLY_IN_PY:
                explained.append(
                    f"column {key} only in Python ({filled} non-null): {EXPECTED_COLUMNS_ONLY_IN_PY[key]}"
                )
            else:
                unexplained.append(f"column {key} only in Python ({filled} non-null)")
        for c in sorted(ts_cols - py_cols):
            unexplained.append(f"column {t}.{c} only in TS")
        compared[t] = [c for c in ts.columns[t] if c in py_cols and f"{t}.{c}" not in IGNORED_COLUMNS]
    for key, why in IGNORED_COLUMNS.items():
        explained.append(f"column {key} not compared: {why}")

    ts_labels, py_labels = label_ids(ts, compared), label_ids(py, compared)

    for t in common:
        cols = [c for c in compared[t] if c != "id" or "id" not in ts.columns[t]]
        # Primary-key ids are represented by the row content itself; drop them from the row.
        cols = [c for c in cols if c != "id"]

        def norm(snap: Snapshot, labels: dict[str, str], cols: list[str] = cols, t: str = t) -> list[dict]:
            return [{c: snap.scalar(r[c], labels) for c in cols} for r in snap.tables[t]]

        ts_rows, py_rows = norm(ts, ts_labels), norm(py, py_labels)
        ts_c = Counter(json.dumps(r, sort_keys=True, default=str) for r in ts_rows)
        py_c = Counter(json.dumps(r, sort_keys=True, default=str) for r in py_rows)
        only_ts, only_py = ts_c - py_c, py_c - ts_c
        extra_ok: list[str] = []
        if t in EXPECTED_EXTRA_PY_ROWS and only_py:
            pred, why = EXPECTED_EXTRA_PY_ROWS[t]
            for row in list(only_py):
                if pred(json.loads(row)):
                    extra_ok.extend([row] * only_py.pop(row))
            if extra_ok:
                explained.append(f"{t}: {len(extra_ok)} extra Python rows: {why}")
        entry = {
            "rows": {"ts": len(ts_rows), "py": len(py_rows)},
            "columns": len(cols),
            "onlyTs": sum(only_ts.values()),
            "onlyPy": sum(only_py.values()),
        }
        if only_ts or only_py:
            entry["examples"] = {
                "ts": [json.loads(r) for r in list(only_ts)[:examples]],
                "py": [json.loads(r) for r in list(only_py)[:examples]],
            }
            unexplained.append(
                f"{t}: {entry['onlyTs']} rows only in TS, {entry['onlyPy']} only in Python "
                f"(of {len(ts_rows)} / {len(py_rows)})"
            )
        tables[t] = entry

    return {
        "tsDb": ts.db,
        "pyDb": py.db,
        "tsTransactionTime": ts.tx_time.isoformat() if ts.tx_time else None,
        "tablesCompared": len(common),
        "rowsCompared": sum(v["rows"]["ts"] for v in tables.values()),
        "identicalTables": sum(1 for v in tables.values() if not v["onlyTs"] and not v["onlyPy"]),
        "explained": explained,
        "unexplained": unexplained,
        "tables": tables,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    p.add_argument("--ts-db", default="ci_seed_ts")
    p.add_argument("--py-db", default="ci_seed_py")
    p.add_argument("--now", default="2026-09-28T12:02:00Z", help="the seed clock both databases used")
    p.add_argument("--prepare", action="store_true", help="recreate and seed both databases first")
    p.add_argument("--json", type=Path, help="also write the full report here")
    p.add_argument("--examples", type=int, default=3)
    args = p.parse_args()

    if args.prepare:
        prepare(args.ts_db, args.py_db, args.now)
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")).astimezone(UTC)
    report = compare(Snapshot(args.ts_db, now), Snapshot(args.py_db, now), args.examples)

    print(
        f"compared {report['tablesCompared']} tables, {report['rowsCompared']} TS rows: "
        f"{report['identicalTables']} identical"
    )
    for t, v in report["tables"].items():
        mark = "ok " if not v["onlyTs"] and not v["onlyPy"] else "DIFF"
        print(f"  {mark} {t:<22} ts={v['rows']['ts']:<5} py={v['rows']['py']:<5} cols={v['columns']}")
    print(f"\nexplained differences ({len(report['explained'])}):")
    for line in report["explained"]:
        print(f"  - {line}")
    print(f"\nunexplained differences ({len(report['unexplained'])}):")
    for line in report["unexplained"]:
        print(f"  - {line}")
    for t, v in report["tables"].items():
        if "examples" in v:
            print(f"\n{t} examples:\n  ts: {json.dumps(v['examples']['ts'], ensure_ascii=False)[:1500]}")
            print(f"  py: {json.dumps(v['examples']['py'], ensure_ascii=False)[:1500]}")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 1 if report["unexplained"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
