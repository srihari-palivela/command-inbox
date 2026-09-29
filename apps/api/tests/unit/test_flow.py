"""Flow compilation and the lane safety invariants, with fake System 1 / System 2 backends (no database)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from command_inbox.agents.config import (
    Category,
    DeploymentConfig,
    Flow,
    FlowNode,
    Rules,
    Taxonomy,
    Thresholds,
    standard_flow,
)
from command_inbox.agents.decision import (
    Calibration,
    ChoiceResult,
    DecisionEngineError,
    DecisionState,
    HeuristicEngine,
    Option,
    ResilientEngine,
    ScoreResult,
)
from command_inbox.agents.flow import (
    CategoryMeta,
    FlowCompileError,
    RunDeps,
    TemplateRow,
    build_graph,
    compile_flow,
    run_flow,
)
from command_inbox.agents.providers import Adjudication, ProviderRouter, Staged, Usage, set_provider_for_tests
from command_inbox.agents.providers.heuristic import HeuristicProvider
from command_inbox.agents.synthesis import baseline_hard_stops

CERT_MAIL = "Please send me the interest certificate for my savings account for FY 2025-26."


def make_config(**overrides: Any) -> DeploymentConfig:
    cats = [
        Category(
            key="certificate_requests",
            name="Certificate requests",
            department="Retail Service Desk",
            default_lane="auto",
            action_template="ACT-CRT-004",
        ),
        Category(key="balance", name="Balance & charge queries", department="Retail Service Desk"),
        Category(key="other", name="Unclassified", department="Unassigned", default_lane="manual"),
    ]
    base: dict[str, Any] = {
        "taxonomy": Taxonomy(categories=cats, fallback="other"),
        "rules": Rules(hard_stops=baseline_hard_stops()),
        "flow": standard_flow(),
        "thresholds": Thresholds(auto_min_confidence=0.78, draft_min_confidence=0.78, escalate_below=0.7),
    }
    base.update(overrides)
    return DeploymentConfig(**base)


def make_deps(
    config: DeploymentConfig,
    body: str = CERT_MAIL,
    *,
    engine: Any = None,
    provider: Any = None,
    sender_verified: bool = True,
    force_lane: str | None = None,
) -> RunDeps:
    now = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
    metas = {}
    for c in config.taxonomy.categories:
        fallback = c.key == config.taxonomy.fallback
        metas[c.key] = CategoryMeta(
            c,
            None if fallback else f"qt-{c.key}",
            None if fallback else "dept-retail",
            None if fallback else c.department,
            not fallback,
            "Unclassified" if fallback else c.name,
            is_fallback=fallback,
        )
    return RunDeps(
        config=config,
        deployment="test",
        engine=ResilientEngine(engine or HeuristicEngine()),
        providers=ProviderRouter(primary=provider or HeuristicProvider(), budget_minor=50),
        ticket_id="t1",
        org_id="o1",
        subject="Interest certificate for FY 2025-26",
        messages=[("Kavya Menon", body)],
        sender_email="kavya@example.com",
        customer_name="Kavya Menon",
        customer_facts="Kavya Menon · CIF-1",
        segment="Retail",
        ticket_priority="P3",
        received_at=now - timedelta(minutes=5),
        now=now,
        prior_contacts=0,
        prior_same_topic=0,
        categories=metas,
        templates={
            "ACT-CRT-004": TemplateRow("tpl", "ACT-CRT-004", "Interest certificate", True, False, "auto")
        },
        dial={"0-0": 2},
        docs=[],
        agents={},
        sender_verified=sender_verified,
        force_lane=force_lane,
    )


class FixedEngine:
    """A System 1 that always answers with the given distribution."""

    name = "fixed"

    def __init__(self, dist: dict[str, float], set_: list[str] | None = None) -> None:
        self.dist = dist
        self.set_ = set_

    async def choice(
        self,
        state: DecisionState,
        question: str,
        options: Sequence[Option],
        *,
        calibration: Calibration | None = None,
    ) -> ChoiceResult:
        label = max(self.dist, key=lambda k: self.dist[k])
        return ChoiceResult(label, dict(self.dist), self.dist[label], self.set_ or [label], engine=self.name)

    async def score(
        self, state: DecisionState, rubric_levels: Sequence[str], *, calibration: Any = None
    ) -> ScoreResult:
        return ScoreResult(3.0, {lvl: 1 / len(rubric_levels) for lvl in rubric_levels})

    async def boolean(self, state: DecisionState, question: str) -> float:
        return 0.0

    async def aclose(self) -> None:
        return None


class BrokenEngine(FixedEngine):
    async def choice(self, *a: Any, **k: Any) -> ChoiceResult:
        raise DecisionEngineError("model server down")


class UnsureAdjudicator(HeuristicProvider):
    name = "claude"  # type: ignore[assignment]

    async def adjudicate(self, text: str, candidate_labels: list[str], agent: Any) -> Staged[Adjudication]:
        return Staged(Adjudication(None, "cannot tell"), Usage("fake", 10, 1, 5))


@pytest.fixture(autouse=True)
def _reset_provider():
    set_provider_for_tests(None)
    yield
    set_provider_for_tests(None)


# ── compilation ──────────────────────────────────────────────────────────────────────────────────────────
def test_flow_without_a_safety_node_is_rejected():
    nodes = [FlowNode(type=t) for t in ("mask_pii", "hard_stop_guard", "categorise", "approval_gate")]
    with pytest.raises(ValidationError, match="lane_policy node is required"):
        Flow(nodes=nodes)
    reordered = [
        FlowNode(type=t)
        for t in ("hard_stop_guard", "mask_pii", "categorise", "lane_policy", "approval_gate")
    ]
    with pytest.raises(ValidationError):
        Flow(nodes=reordered)


def test_compiler_rejects_nodes_before_their_inputs():
    flow = Flow.model_construct(
        template="standard",
        nodes=[
            FlowNode(type=t)
            for t in (
                "mask_pii",
                "hard_stop_guard",
                "categorise",
                "draft_reply",
                "lane_policy",
                "approval_gate",
            )
        ],
    )
    cfg = make_config().model_copy(update={"flow": flow})
    with pytest.raises(FlowCompileError, match="lane_policy must run before draft_reply"):
        build_graph(cfg)


def test_compiled_graphs_are_cached_per_version():
    cfg = make_config()
    assert compile_flow(cfg, "v1") is compile_flow(cfg, "v1")
    assert compile_flow(cfg, "v2") is not compile_flow(cfg, "v1")


# ── routing ──────────────────────────────────────────────────────────────────────────────────────────────
async def test_confident_mail_skips_the_adjudicator_and_goes_auto():
    cfg = make_config()
    state = await run_flow(build_graph(cfg), make_deps(cfg))
    assert "adjudicate" not in state["visited"]
    assert state["category"] == "certificate_requests" and state["confidence"] == 0.88
    assert state["lane"] == "auto" and state["chain"] == "auto"
    assert state["visited"][0] == "mask_pii" and state["visited"][-1] == "approval_gate"
    assert "draft_reply" not in state["visited"] and "brief" not in state["visited"]
    for node in ("categorise", "lane_policy", "approval_gate"):
        assert node in state["outputs"]


async def test_low_confidence_routes_through_the_adjudicator():
    cfg = make_config()
    engine = FixedEngine({"certificate_requests": 0.5, "balance": 0.4, "other": 0.1})
    state = await run_flow(build_graph(cfg), make_deps(cfg, engine=engine))
    assert "adjudicate" in state["visited"]
    assert state["outputs"]["adjudicate"]["picked"] == "certificate_requests"
    assert state["lane"] != "auto"  # 0.5 is below the auto bar


async def test_a_conformal_set_of_two_routes_through_the_adjudicator_even_when_confident():
    cfg = make_config()
    engine = FixedEngine(
        {"certificate_requests": 0.8, "balance": 0.15, "other": 0.05}, ["certificate_requests", "balance"]
    )
    state = await run_flow(build_graph(cfg), make_deps(cfg, engine=engine))
    assert "adjudicate" in state["visited"]
    assert state["choice"]["escalation_reason"] == "2 labels in the conformal set"


async def test_adjudicator_that_stays_unsure_hands_the_mail_to_a_person():
    cfg = make_config()
    engine = FixedEngine({"certificate_requests": 0.5, "balance": 0.4, "other": 0.1})
    state = await run_flow(build_graph(cfg), make_deps(cfg, engine=engine, provider=UnsureAdjudicator()))
    assert state["escalated_unresolved"] is True
    assert state["lane"] == "manual" and "brief" in state["visited"]


# ── safety invariants ────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("force", [None, "auto", "draft"])
async def test_hard_stop_always_yields_manual_never_auto(force):
    cfg = make_config()
    body = CERT_MAIL + " If this is not sorted I will complain to the banking ombudsman."
    state = await run_flow(build_graph(cfg), make_deps(cfg, body, force_lane=force))
    assert state["guard"]["stop"] == "regulator named"
    assert state["lane"] == "manual" and state["chain"] is None and state.get("draft") is None
    assert "brief" in state["visited"] and state["priority"]["priority"] == "P1"


async def test_unverified_sender_never_reaches_auto():
    cfg = make_config()
    for force in (None, "auto"):
        state = await run_flow(build_graph(cfg), make_deps(cfg, sender_verified=False, force_lane=force))
        assert state["lane"] == "manual"
        assert state["lane_note"].startswith("Sender could not be verified") or force == "auto"
        assert state["chain"] is None


async def test_decision_engine_outage_degrades_to_a_person():
    cfg = make_config()
    state = await run_flow(build_graph(cfg), make_deps(cfg, engine=BrokenEngine({})))
    assert state["degraded"] is True
    assert state["lane"] == "manual" and state["lane_note"].startswith("AI partly unavailable")


async def test_pii_never_reaches_the_engine():
    cfg = make_config()
    seen: list[str] = []

    class Spy(FixedEngine):
        async def choice(self, state: DecisionState, *a: Any, **k: Any) -> ChoiceResult:
            seen.append(state.text)
            return await super().choice(state, *a, **k)

    body = CERT_MAIL + " Reach me on kavya.menon@example.com or 9876543210."
    await run_flow(
        build_graph(cfg), make_deps(cfg, body, engine=Spy({"certificate_requests": 0.95, "other": 0.05}))
    )
    assert seen and "kavya.menon@example.com" not in seen[0] and "9876543210" not in seen[0]
    assert "[EMAIL_1]" in seen[0] and "[PHONE_1]" in seen[0]
