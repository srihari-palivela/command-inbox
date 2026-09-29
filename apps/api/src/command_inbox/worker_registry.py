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
    ("send_email", "command_inbox.core.email", "run_send_email"),
    ("provision_tenant", "command_inbox.platform.provisioning", "run_provision_tenant"),
    ("mail_connect", "command_inbox.mail.sync", "run_mail_connect"),
    ("mail_sync", "command_inbox.mail.sync", "run_mail_sync"),
    ("mail_renew", "command_inbox.mail.sync", "run_mail_renew"),
    ("mail_send", "command_inbox.mail.sync", "run_mail_send"),
    ("mail_test", "command_inbox.mail.sync", "run_mail_test"),
    ("knowledge_ingest", "command_inbox.knowledge.service", "run_knowledge_ingest"),
    ("metrics_rollup", "command_inbox.modules.insights.rollup", "run_rollup_job"),
    ("sla_sweep", "command_inbox.modules.insights.alerts", "run_sla_sweep"),
    ("retention_sweep", "command_inbox.modules.workspace.operations", "run_retention_sweep"),
    ("siem_push", "command_inbox.modules.workspace.operations", "run_siem_push"),
]


async def _noop(_job: object) -> None:
    return None


def register_all(worker: Worker) -> Worker:
    for kind, module, attr in HANDLERS:
        try:
            worker.register(kind, getattr(importlib.import_module(module), attr))
        except ModuleNotFoundError:
            continue
    worker.register("rerank", _noop)
    return worker


# Modules that react when a job exhausts its retries (e.g. hand the ticket back to a person). Each
# `install(worker)` chains the handler installed before it.
FAILURE_HANDLERS: list[str] = ["command_inbox.modules.gateway.jobs", "command_inbox.agents.runner"]


def install_failure_handlers(worker: Worker) -> Worker:
    for module in FAILURE_HANDLERS:
        try:
            importlib.import_module(module).install(worker)
        except ModuleNotFoundError:
            continue
    return worker
