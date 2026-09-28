"""A deployment config synthesised from a tenant's existing setup tables.

Used when a ticket has no deployment version (a tenant that predates deployments, or seeded demo data):
the taxonomy is the query types, hard stops are the platform baseline, bucket overrides carry the setup's
deterministic bucketing rules exactly, priority rules keep their keys and on/off state, and both confidence
bars are the tenant's single confidence bar. Triage of such a ticket behaves as the previous service did.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from command_inbox.agents.config import (
    BucketOverride,
    Category,
    DeploymentConfig,
    HardStop,
    PriorityRuleSpec,
    Rules,
    Taxonomy,
    Thresholds,
    standard_flow,
)
from command_inbox.agents.lexicon import BASELINE_HARD_STOPS
from command_inbox.agents.templates import legacy_template

log = structlog.get_logger(__name__)

FALLBACK_KEY = "other"
FALLBACK_NAME = "Unclassified"
UNOWNED = "Unassigned"
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


@dataclass(slots=True)
class Synthesis:
    config: DeploymentConfig
    query_type_by_key: dict[str, str] = field(default_factory=dict)  # category key -> query_types.id
    notes: list[str] = field(default_factory=list)


def _slug(text: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "category"
    if not base[0].isalpha():
        base = "c_" + base
    key, n = base, 2
    while key in taken:
        key = f"{base}_{n}"
        n += 1
    taken.add(key)
    return key


def _short(text: str, n: int = 120) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def baseline_hard_stops() -> list[HardStop]:
    return [
        HardStop(key=key, label=reason, keywords=list(words), question=question)
        for key, reason, words, question in BASELINE_HARD_STOPS
    ]


def synthesise_config(
    *,
    confidence_bar: float,
    departments: dict[str, str],
    query_types: Sequence[Any],
    bucket_rules: Sequence[Any],
    priority_rules: Sequence[Any],
) -> Synthesis:
    """`query_types` / `bucket_rules` / `priority_rules` are ORM rows (or anything with the same attributes)."""
    notes: list[str] = []
    taken = {FALLBACK_KEY}
    key_by_name: dict[str, str] = {}
    qt_by_key: dict[str, str] = {}
    categories: list[Category] = []
    for qt in sorted(query_types, key=lambda q: q.sort)[:63]:
        key = _slug(qt.name, taken)
        key_by_name.setdefault(qt.name, key)
        qt_by_key[key] = str(qt.id)
        lane = qt.default_lane if qt.default_lane in ("auto", "draft", "manual") else "draft"
        categories.append(
            Category(
                key=key,
                name=_short(qt.name),
                description=_short(qt.owner_label or qt.name, 600),
                department=_short(departments.get(str(qt.department_id or ""), UNOWNED)),
                default_lane=lane,
                action_template=legacy_template(qt.name, ""),
            )
        )
    if not categories:
        categories.append(
            Category(key="general", name="General enquiry", department=UNOWNED, default_lane="manual")
        )
    categories.append(
        Category(
            key=FALLBACK_KEY,
            name=FALLBACK_NAME,
            description="Mail that fits no other query type; a person looks at it.",
            department=UNOWNED,
            default_lane="manual",
        )
    )

    overrides: list[BucketOverride] = []
    for rule in sorted(bucket_rules, key=lambda r: r.sort):
        pattern = rule.pattern or {}
        target = key_by_name.get(str(pattern.get("queryType") or ""))
        if not target:
            continue
        words_all = [w for w in pattern.get("all") or [] if isinstance(w, str)][:10]
        words_any = [w for w in pattern.get("any") or [] if isinstance(w, str)]
        regex = pattern.get("regex")
        try:
            if regex and words_any:
                notes.append(f"bucket rule {rule.sort + 1} combines a pattern with alternatives; skipped")
            elif regex:
                overrides.append(
                    BucketOverride(match="regex", value=regex, category=target, requires=words_all)
                )
            elif words_any:
                overrides += [
                    BucketOverride(match="phrase", value=_short(w), category=target, requires=words_all)
                    for w in words_any
                ]
            elif words_all:
                overrides.append(
                    BucketOverride(
                        match="phrase", value=_short(words_all[0]), category=target, requires=words_all[1:]
                    )
                )
        except ValueError as err:
            notes.append(f"bucket rule {rule.sort + 1} not carried over: {err}")

    priority: list[PriorityRuleSpec] = []
    p_taken: set[str] = set()
    for rule in priority_rules:
        m = re.search(r"p\s*([1-4])", str(rule.target).lower())
        key = rule.key if _KEY_RE.match(rule.key) and rule.key not in p_taken else _slug(rule.key, p_taken)
        p_taken.add(key)
        priority.append(
            PriorityRuleSpec(
                key=key,
                description=_short(rule.description, 300),
                target=f"p{m.group(1) if m else 3}",  # type: ignore[arg-type]
                hard=bool(rule.hard),
                enabled=bool(rule.enabled),
            )
        )

    bar = min(1.0, max(0.5, float(confidence_bar or 0.78)))
    config = DeploymentConfig(
        taxonomy=Taxonomy(categories=categories, fallback=FALLBACK_KEY),
        rules=Rules(
            hard_stops=baseline_hard_stops(), bucket_overrides=overrides[:200], priority=priority[:40]
        ),
        flow=standard_flow(),
        thresholds=Thresholds(
            auto_min_confidence=bar, draft_min_confidence=bar, escalate_below=min(0.7, bar)
        ),
    )
    for n in notes:
        log.info("setup carried into a synthesised deployment with a gap", note=n)
    return Synthesis(config=config, query_type_by_key=qt_by_key, notes=notes)
