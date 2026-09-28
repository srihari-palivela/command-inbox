"""Every router the API serves. Modules register here as they are ported."""

from __future__ import annotations

import importlib

from fastapi import APIRouter

# (module path, attribute). Missing modules are skipped so the API boots while the port is in progress.
ROUTERS: list[tuple[str, str]] = [
    ("command_inbox.routers.auth", "router"),
    ("command_inbox.modules.admin.router", "router"),
    ("command_inbox.modules.tickets.router", "router"),
    ("command_inbox.modules.gateway.router", "router"),
    ("command_inbox.modules.workspace.router", "router"),
    ("command_inbox.modules.setup.router", "router"),
    ("command_inbox.modules.deployments.router", "router"),
    ("command_inbox.modules.evals.router", "router"),
]


def routers() -> list[APIRouter]:
    out = []
    for module, attr in ROUTERS:
        try:
            mod = importlib.import_module(module)
        except ModuleNotFoundError as err:
            if err.name and module.startswith(err.name):
                continue
            raise
        out.append(getattr(mod, attr))
    return out
