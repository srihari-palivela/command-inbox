"""System 2 in an eval run: the adjudicator on the cases System 1 escalated, and grounded drafts on a sample.

This is what makes a provider switch a measured change. Run the same dataset with each provider (the run's
`provider`, else the deployment's own node settings) and compare:

- adjudication accuracy on escalated test cases, and end-to-end accuracy (System 1 where it was sure, the
  adjudicator where it was not; "unsure" counts as handed to a person, not as right);
- the grounded-draft rate: drafts whose every sentence is supported by the passages they cite, over drafts
  written from retrieved, approved knowledge (a capped sample, since each draft costs money);
- cost, latency and how often the provider fell back to the deterministic one (a fallback means the
  numbers above are not the provider's).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from command_inbox.agents.config import DeploymentConfig
from command_inbox.agents.providers import AgentSpec, GroundingDoc, ProviderRouter, ThreadInput, ThreadMessage
from command_inbox.agents.specs import agent_specs
from command_inbox.evals import metrics as m
from command_inbox.knowledge.grounding import unsupported_sentences

Retriever = Callable[[str], Awaitable[list[tuple[str, str, str]]]]  # query → [(title, section, text)]

DRAFT_SAMPLE = 20


@dataclass(slots=True)
class System2Case:
    case_id: str
    adjudicated: str | None = None  # the picked category key; None: unsure (a person decides)
    adjudication_correct: bool | None = None
    draft_grounded: bool | None = None
    draft_sources: int = 0
    unsupported: list[str] = field(default_factory=list)


@dataclass(slots=True)
class System2Outcome:
    metrics: dict[str, Any]
    cases: dict[str, System2Case]


def _pin(spec: AgentSpec, provider: str | None) -> AgentSpec:
    return dataclasses.replace(spec, provider=provider) if provider else spec


async def score_system2(
    config: DeploymentConfig,
    outcomes: list[Any],  # runner.CaseOutcome
    *,
    catalogue: dict[str, AgentSpec],
    provider: str | None,
    allowed: list[str] | None,
    retrieve: Retriever | None,
    draft_sample: int = DRAFT_SAMPLE,
) -> System2Outcome:
    agents = agent_specs(config, catalogue)
    router = ProviderRouter(allowed=allowed)
    names = {c.key: c.name for c in config.taxonomy.categories}
    by_name = {c.name: c.key for c in config.taxonomy.categories}
    results: dict[str, System2Case] = {}
    latencies: list[float] = []
    models: set[str] = set()
    test = [o for o in outcomes if o.case.split == "test"]

    adjudicator = _pin(agents.get("adjudicator") or AgentSpec("Adjudicator", "", ""), provider)
    adjudicated = right = unsure = 0
    end_to_end: list[bool] = []
    for o in test:
        r = results.setdefault(o.case.id, System2Case(o.case.id))
        if not o.escalated or o.hard_stops:
            end_to_end.append(not o.escalated and o.correct)
            continue
        ranked = sorted(o.distribution, key=o.distribution.__getitem__, reverse=True)[:3]
        candidates = [names[k] for k in ranked if k in names]
        text = f"{o.case.input.get('subject', '')}\n{o.case.input.get('body', '')}"
        staged = await router.call(
            "adjudicate", adjudicator, lambda p, t=text, c=candidates: p.adjudicate(t, c, adjudicator)
        )
        latencies.append(staged.usage.latency_ms)
        models.add(staged.usage.model)
        picked = by_name.get(staged.result.label or "")
        adjudicated += 1
        r.adjudicated = picked
        if picked is None:
            unsure += 1
            end_to_end.append(False)
        else:
            r.adjudication_correct = picked == o.case.category
            right += r.adjudication_correct
            end_to_end.append(r.adjudication_correct)

    drafter = _pin(agents.get("drafter") or AgentSpec("Reply Drafter", "", ""), provider)
    drafts = grounded = no_source = 0
    if retrieve is not None:
        for o in [o for o in test if not o.case.hard_stop][:draft_sample]:
            subject = str(o.case.input.get("subject", ""))
            body = str(o.case.input.get("body", ""))
            docs = await retrieve(f"{subject}\n{body}")
            grounding = [GroundingDoc(i + 1, t, s, x) for i, (t, s, x) in enumerate(docs)]
            thread = ThreadInput(subject, (ThreadMessage("Customer", body),))
            staged = await router.call(
                "draft_reply",
                drafter,
                lambda p, th=thread, g=grounding: p.draft_reply(th, g, drafter, "the customer"),
            )
            latencies.append(staged.usage.latency_ms)
            models.add(staged.usage.model)
            draft = staged.result
            r = results.setdefault(o.case.id, System2Case(o.case.id))
            cited = [g.body for g in grounding if g.n in draft.citations]
            r.draft_sources = len(cited)
            drafts += 1
            if not cited:
                no_source += 1
                r.draft_grounded = False
                continue
            r.unsupported = unsupported_sentences(draft.body, cited)
            r.draft_grounded = not r.unsupported
            grounded += r.draft_grounded

    key = provider or ",".join(sorted({a.provider or "default" for a in (adjudicator, drafter)}))
    return System2Outcome(
        metrics={
            "system2Provider": key,
            "system2Models": sorted(models - {"rules"}),
            "adjudicatedCases": adjudicated,
            "adjudicationAccuracy": _r(right / adjudicated) if adjudicated else None,
            "adjudicationUnsureRate": _r(unsure / adjudicated) if adjudicated else None,
            "endToEndAccuracy": _r(sum(end_to_end) / len(end_to_end)) if end_to_end else None,
            "draftsScored": drafts,
            "groundedDraftRate": _r(grounded / drafts) if drafts else None,
            "noSourceDraftRate": _r(no_source / drafts) if drafts else None,
            "system2CostMinor": router.spent_minor,
            "system2P95LatencyMs": _r(m.percentile(latencies, 95)) if latencies else None,
            "system2Fallbacks": len(router.reasons),
        },
        cases=results,
    )


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 4)
