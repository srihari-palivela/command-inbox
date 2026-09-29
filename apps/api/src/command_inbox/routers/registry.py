"""Every router the API serves. Modules register here as they are ported."""

from __future__ import annotations

import importlib

from fastapi import APIRouter

# (module path, attribute). Missing modules are skipped so the API boots while the port is in progress.
ROUTERS: list[tuple[str, str]] = [
    ("command_inbox.routers.auth", "router"),
    # tickets, search, copilot
    ("command_inbox.modules.tickets.router", "router"),
    ("command_inbox.modules.search.router", "router"),
    ("command_inbox.modules.copilot.router", "router"),
    # approval gateway, replies, calls, intake and mailbox connections
    ("command_inbox.modules.gateway.router", "router"),
    ("command_inbox.modules.calls.router", "router"),
    ("command_inbox.modules.intake.router", "router"),
    ("command_inbox.modules.mailboxes.router", "router"),
    ("command_inbox.knowledge.router", "router"),
    ("command_inbox.mail.hooks", "router"),
    # workspace: settings, people, insights, learning; AI setup and the admin overview
    ("command_inbox.modules.workspace.router", "router"),
    ("command_inbox.modules.people.router", "router"),
    ("command_inbox.modules.insights.router", "router"),
    ("command_inbox.modules.learning.router", "router"),
    ("command_inbox.modules.setup.router", "router"),
    ("command_inbox.modules.taxonomy.router", "router"),
    # tenant administration: members, invitations, permissions; deployments and evals
    ("command_inbox.modules.members.router", "router"),
    ("command_inbox.modules.deployments.router", "router"),
    ("command_inbox.modules.evals.router", "router"),
    ("command_inbox.modules.pilot.router", "router"),
    # provisioning from the bank's identity provider (bearer token, not a session)
    ("command_inbox.modules.scim.router", "router"),
    # the platform console (operators only)
    ("command_inbox.platform.router", "router"),
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
