"""Job kind → handler. Each module contributes its handlers; missing modules are skipped during the port."""

from __future__ import annotations

import importlib

from command_inbox.core.jobs import Worker

HANDLERS: list[tuple[str, str, str]] = [
    ("triage", "command_inbox.agents.runner", "run_triage_job"),
    ("execute_action", "command_inbox.modules.gateway.jobs", "run_execute_action"),
    ("send_draft", "command_inbox.modules.gateway.jobs", "run_send_draft"),
    ("send_reply", "command_inbox.modules.gateway.jobs", "run_send_reply"),
    ("escalate_checker", "command_inbox.modules.gateway.jobs", "run_escalate_checker"),
    ("knowledge_sync", "command_inbox.modules.setup.jobs", "run_knowledge_sync"),
    ("eval_run", "command_inbox.evals.runner", "run_eval_job"),
]


async def _noop(_job: object) -> None:
    return None


def register_all(worker: Worker) -> Worker:
    for kind, module, attr in HANDLERS:
        try:
            worker.register(kind, getattr(importlib.import_module(module), attr))
        except ModuleNotFoundError:
            continue
    worker.register("rerank", _noop).register("sla_sweep", _noop).register("retention_sweep", _noop)
    return worker
