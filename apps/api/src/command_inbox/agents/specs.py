"""Which agent runs each System 2 node: the deployment's node settings over the workspace catalogue.

A deployment version is the source of truth. For each model node (adjudicate, extract_fields, draft_reply,
brief) the node's own `agent` settings win; anything left empty is inherited from the deployment's model
defaults, then from the workspace's agent catalogue (older workspaces), then from the built-in defaults
below (what the starter pack writes into a new tenant's deployment).
"""

from __future__ import annotations

from command_inbox.agents.config import SYSTEM2_NODES, DeploymentConfig, NodeAgent
from command_inbox.agents.providers.types import AgentSpec
from command_inbox.db.models import Agent

DEFAULT_AGENTS: dict[str, tuple[str, str]] = {
    "adjudicator": (
        "Adjudicator",
        "You decide which query type a bank customer's email belongs to when a fast classifier could not. "
        "Read the whole thread. Choose only from the candidates you are given, and say you are unsure when "
        "the email does not clearly fit one of them: a person will then decide.",
    ),
    "extractor": (
        "Field Extractor",
        "You pull the fields an action template needs out of a bank customer's email thread. Every value "
        "must be quoted verbatim from the thread. Mark anything you inferred, and never guess amounts, "
        "account numbers or dates: leave the field empty instead.",
    ),
    "drafter": (
        "Reply Drafter",
        "You draft a reply to a bank customer using only the approved sources you are given, citing each "
        "statement as [n]. Where the sources do not cover part of the question, say so plainly in the draft "
        "and never fill the gap from general knowledge. Be courteous, brief and specific. Never promise an "
        "outcome, a refund or a timeline the sources do not state. A person reviews and sends every reply.",
    ),
    "summariser": (
        "Case Briefer",
        "You brief a bank employee who will handle this email personally. Summarise what the customer wants "
        "and why it needs a person, list the relevant facts from the records given, and suggest next steps. "
        "Never write to the customer and never state facts that are not in the thread or the records.",
    ),
}


def catalogue_agent(rows: list[Agent], on_board: set[str], role: str) -> AgentSpec | None:
    """The workspace catalogue's agent for a role: the one on the ticket's board, else the first active one."""
    candidates = [a for a in rows if a.role == role and a.state != "paused"]
    pick = next((a for a in candidates if str(a.id) in on_board), None) or (
        candidates[0] if candidates else None
    )
    if pick is None:
        return None
    return AgentSpec(pick.name, pick.model, pick.prompt, pick.cost_per_1k_minor)


def compose_prompt(prompt: str, agent: NodeAgent | None) -> str:
    if agent is None:
        return prompt
    parts = [prompt]
    if agent.style_guide.strip():
        parts.append("House style:\n" + agent.style_guide.strip())
    if agent.signature.strip():
        parts.append("End the reply with this sign-off, exactly:\n" + agent.signature.strip())
    return "\n\n".join(parts)


def agent_specs(config: DeploymentConfig, catalogue: dict[str, AgentSpec]) -> dict[str, AgentSpec]:
    """Role → the resolved agent for every model node in the flow, plus catalogue agents for other roles."""
    specs = dict(catalogue)
    for node in config.flow.nodes:
        role = SYSTEM2_NODES.get(node.type)
        if role is None:
            continue
        a = node.agent or NodeAgent()
        base = catalogue.get(role)
        default_name, default_prompt = DEFAULT_AGENTS[role]
        specs[role] = AgentSpec(
            name=a.name or (base.name if base else default_name),
            model=a.model or config.models.system2_model or (base.model if base else ""),
            prompt=compose_prompt(a.prompt or (base.prompt if base else default_prompt), a),
            cost_per_1k_minor=(
                a.cost_per_1k_minor
                if a.cost_per_1k_minor is not None
                else (base.cost_per_1k_minor if base else 0)
            ),
            effort=a.effort,
            provider=a.provider or config.models.system2_provider,
            max_tokens=a.max_tokens,
        )
    return specs


PROVIDER_NAME = {"anthropic": "Anthropic", "openai": "OpenAI"}


def providers_used(config: DeploymentConfig) -> dict[str, list[str]]:
    """Provider key → the model nodes that call it (empty settings resolved to the platform default)."""
    from command_inbox.config import settings

    used: dict[str, list[str]] = {}
    for node in config.flow.nodes:
        if node.type not in SYSTEM2_NODES:
            continue
        key = (node.agent.provider if node.agent else "") or config.models.system2_provider
        key = key or settings.default_provider
        if key != "heuristic":
            used.setdefault(key, []).append(node.type)
    return used


def policy_problems(config: DeploymentConfig, allowed: list[str], *, installation: bool = False) -> list[str]:
    """What the workspace's model policy (and, with `installation`, this installation) forbids."""
    from command_inbox.agents.providers import available

    problems = []
    for key, nodes in providers_used(config).items():
        name = PROVIDER_NAME.get(key, key)
        where = ", ".join(nodes)
        if key not in allowed:
            problems.append(f"{where} would call {name}, which the workspace's model policy does not allow.")
        elif installation and not available(key):
            problems.append(f"{where} would call {name}, which is not configured on this installation.")
    return problems
