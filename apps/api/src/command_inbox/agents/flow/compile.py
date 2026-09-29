"""Compile a deployment version's flow spec into a LangGraph `StateGraph`.

Nodes run in the configured order. After each node a router picks the next node that applies, which gives
the conditional edges: categorise → adjudicate only when System 1 escalated (confidence below
`escalateBelow` or a conformal set of more than one label); lane_policy → draft_reply only when a draft is
wanted; brief only for the manual lane. The safety nodes (mask_pii first, hard_stop_guard before
lane_policy, approval_gate last) are enforced by the config validator and re-checked here. Compiled graphs
are cached per deployment version.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from command_inbox.agents.config import REQUIRED_ORDER, DeploymentConfig, NodeType
from command_inbox.agents.flow.nodes import NODE_REGISTRY, NodeFn
from command_inbox.agents.flow.state import FlowState
from command_inbox.agents.tracing import node_span


class FlowCompileError(ValueError):
    pass


# Node → nodes that must run before it (when both are in the flow).
_AFTER: dict[str, tuple[str, ...]] = {
    "adjudicate": ("categorise",),
    "priority": ("hard_stop_guard", "categorise"),
    "multi_intent": ("categorise",),
    "extract_fields": ("categorise", "hard_stop_guard"),
    "lane_policy": ("categorise", "extract_fields", "multi_intent", "adjudicate", "sender_trust"),
    "draft_reply": ("lane_policy",),
    "brief": ("lane_policy", "draft_reply"),
    "route": ("categorise",),
}

# A node is skipped when its predicate is true for the state at that point.
_SKIP: dict[str, Callable[[FlowState], bool]] = {
    "adjudicate": lambda s: not s.get("escalate"),
    "draft_reply": lambda s: not s.get("want_draft"),
    "brief": lambda s: s.get("lane") != "manual",
}

_cache: OrderedDict[str, Any] = OrderedDict()
_CACHE_SIZE = 128


def validate_order(types: list[NodeType]) -> None:
    required = [t for t in types if t in REQUIRED_ORDER]
    if required != list(REQUIRED_ORDER) or types[0] != "mask_pii" or types[-1] != "approval_gate":
        raise FlowCompileError(
            "mask_pii must be first, approval_gate last, hard_stop_guard before lane_policy"
        )
    if "categorise" not in types:
        raise FlowCompileError("the categorise node is required to compile a triage flow")
    pos = {t: i for i, t in enumerate(types)}
    for node, befores in _AFTER.items():
        if node not in pos:
            continue
        for b in befores:
            if b in pos and pos[b] > pos[node]:
                raise FlowCompileError(f"{b} must run before {node}")


def _wrap(node: NodeType, fn: NodeFn, params: dict[str, Any]) -> Callable[[FlowState, RunnableConfig], Any]:
    async def run(state: FlowState, config: RunnableConfig) -> dict[str, Any]:
        with node_span(node):
            update = await fn(state, config, params)
        return {**update, "visited": [node]}

    run.__name__ = f"node_{node}"
    return run


def _router(later: list[NodeType]) -> Callable[[FlowState], str]:
    def route(state: FlowState) -> str:
        for n in later:
            skip = _SKIP.get(n)
            if skip is None or not skip(state):
                return n
        return END

    return route


def build_graph(config: DeploymentConfig) -> Any:
    types: list[NodeType] = [n.type for n in config.flow.nodes]
    validate_order(types)
    g = StateGraph(FlowState)
    for n in config.flow.nodes:
        action: Any = _wrap(n.type, NODE_REGISTRY[n.type], dict(n.params))
        g.add_node(n.type, action)
    g.add_edge(START, types[0])
    for i, t in enumerate(types):
        later = types[i + 1 :]
        if not later:
            g.add_edge(t, END)
            continue
        g.add_conditional_edges(t, _router(later), {**{n: n for n in later}, END: END})
    return g.compile()


def compile_flow(config: DeploymentConfig, version_key: str | None = None) -> Any:
    """Compiled graph for a deployment version (cached by version id, else by config hash)."""
    key = version_key or f"hash:{config.config_hash()}"
    graph = _cache.get(key)
    if graph is None:
        graph = build_graph(config)
        _cache[key] = graph
        if len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    else:
        _cache.move_to_end(key)
    return graph


def clear_cache() -> None:
    _cache.clear()
