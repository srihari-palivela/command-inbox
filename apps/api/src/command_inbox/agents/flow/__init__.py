"""LangGraph flows compiled from a deployment version's flow spec over the vetted node registry."""

from __future__ import annotations

from command_inbox.agents.flow.compile import FlowCompileError, build_graph, clear_cache, compile_flow
from command_inbox.agents.flow.nodes import NODE_REGISTRY
from command_inbox.agents.flow.state import CategoryMeta, DocRow, FlowState, RunDeps, TemplateRow


async def run_flow(graph: object, deps: RunDeps) -> FlowState:
    result: FlowState = await graph.ainvoke(  # type: ignore[attr-defined]
        {"spans": [], "outputs": {}, "visited": [], "degraded": False},
        config={"configurable": {"deps": deps}, "recursion_limit": 64},
    )
    return result


__all__ = [
    "NODE_REGISTRY",
    "CategoryMeta",
    "DocRow",
    "FlowCompileError",
    "FlowState",
    "RunDeps",
    "TemplateRow",
    "build_graph",
    "clear_cache",
    "compile_flow",
    "run_flow",
]
