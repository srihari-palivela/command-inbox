"""The default deployment a tenant gets from its existing setup (query types, departments and rules).

Used by migration 0004 to backfill every tenant that predates deployments, and by `ensure_default_deployment`
for tenants created afterwards (seed, onboarding). The builder is pure so the migration and the tests can
call it without a database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

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

DEFAULT_KEY = "default"
FALLBACK_KEY = "other"

# The platform's baseline hard stops (the same signals the heuristic guard has always screened for).
BASELINE_HARD_STOPS: tuple[HardStop, ...] = (
    HardStop(
        key="regulator_named",
        label="Regulator or ombudsman named",
        keywords=["ombudsman", "rbi complaint", "regulator", "consumer forum"],
        question="Does the customer name a regulator, ombudsman or consumer forum?",
    ),
    HardStop(
        key="legal_notice",
        label="Legal notice",
        keywords=["legal notice", "my lawyer", "advocate", "court"],
        question="Does the mail threaten or announce legal action?",
    ),
    HardStop(
        key="suspected_fraud",
        label="Suspected fraud",
        keywords=["fraud", "scam", "phishing", "hacked"],
        question="Does the customer report fraud, a scam or a compromised account?",
    ),
    HardStop(
        key="vulnerable_customer",
        label="Vulnerable-customer signal",
        keywords=[
            "passed away",
            "deceased",
            "bereave",
            "died",
            "terminal",
            "serious illness",
            "hospitalised",
            "hospitalized",
            "can't afford",
            "cannot afford",
            "financial distress",
        ],
        question="Does the mail show the customer may be vulnerable (bereavement, illness, hardship)?",
        threshold=0.3,
    ),
)


@dataclass(slots=True)
class Setup:
    """A tenant's current (pre-deployment) setup, as plain rows."""

    org_name: str
    confidence_bar: float = 0.78
    departments: dict[str, str] = field(default_factory=dict)  # id -> name
    query_types: list[dict[str, Any]] = field(default_factory=list)  # name, default_lane, department_id
    bucket_rules: list[dict[str, Any]] = field(default_factory=list)  # description, kind, pattern
    priority_rules: list[dict[str, Any]] = field(default_factory=list)  # key, description, target, hard


def slug(text: str, taken: set[str]) -> str:
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


def _regex_override(regex: str, required: list[str], category: str) -> BucketOverride | None:
    """A regex bucketing rule, when the config schema supports them (older schemas only know phrases)."""
    try:
        return BucketOverride.model_validate(
            {
                "match": "regex",
                "value": regex,
                "category": category,
                "requires": [w[:80] for w in required[:10]],
            }
        )
    except ValueError:
        return None


def build_default_config(setup: Setup) -> tuple[DeploymentConfig, list[str]]:
    """Returns the validated config and notes on anything that could not be carried over."""
    notes: list[str] = []
    taken: set[str] = {FALLBACK_KEY}
    by_name: dict[str, str] = {}
    categories: list[Category] = []
    first_department = next(iter(setup.departments.values()), "Unassigned")
    for qt in setup.query_types:
        name = _short(qt["name"])
        key = slug(name, taken)
        by_name[qt["name"]] = key
        lane = qt.get("default_lane") if qt.get("default_lane") in ("auto", "draft", "manual") else "draft"
        department = setup.departments.get(str(qt.get("department_id") or ""), "Unassigned")
        categories.append(
            Category(
                key=key,
                name=name,
                description=_short(f"{name}. {qt.get('owner_label') or ''}".strip(" ."), 600),
                department=_short(department),
                default_lane=lane,
            )
        )
    categories.append(
        Category(
            key=FALLBACK_KEY,
            name="Other / unclear",
            description="Mail that fits no other category; a person looks at it.",
            department=_short(first_department),
            default_lane="manual",
        )
    )

    hard_stops = list(BASELINE_HARD_STOPS)
    overrides: list[BucketOverride] = []
    hs_taken = {h.key for h in hard_stops}
    for rule in setup.bucket_rules:
        kind = str(rule.get("kind") or "").lower()
        pattern = rule.get("pattern") or {}
        if "hard stop" in kind:
            hard_stops.append(
                HardStop(
                    key=slug(rule["description"], hs_taken),
                    label=_short(rule["description"]),
                    question=_short(rule["description"], 300),
                )
            )
            continue
        target = by_name.get(str(pattern.get("queryType") or ""))
        if not target:
            continue
        phrases = [p for p in pattern.get("any", []) if isinstance(p, str)]
        required = [w for w in pattern.get("all", []) if isinstance(w, str)]
        if pattern.get("regex"):
            override = _regex_override(str(pattern["regex"]), required, target)
            if override is None:
                notes.append(f"Bucket rule not carried over (pattern): {_short(rule['description'], 80)}")
            else:
                overrides.append(override)
        elif required and not phrases:
            notes.append(
                f"Bucket rule not carried over (all-words pattern): {_short(rule['description'], 80)}"
            )
        overrides += [BucketOverride(match="phrase", value=_short(p), category=target) for p in phrases]

    priority: list[PriorityRuleSpec] = []
    p_taken: set[str] = set()
    can_disable = "enabled" in PriorityRuleSpec.model_fields
    for rule in setup.priority_rules:
        enabled = rule.get("enabled") is not False
        if not enabled and (rule.get("hard") or not can_disable):
            continue  # a disabled hard rule has no representation (hard rules always fire)
        m = re.search(r"p\s*([1-4])", str(rule.get("target", "")).lower())
        if not m:
            notes.append(f"Priority rule not carried over (no P1-P4 target): {rule.get('key')}")
            continue
        priority.append(
            PriorityRuleSpec(
                key=slug(str(rule.get("key") or rule["description"]), p_taken),
                description=_short(rule["description"], 300),
                target=f"p{m.group(1)}",  # type: ignore[arg-type]
                hard=bool(rule.get("hard")),
                **({} if enabled else {"enabled": False}),
            )
        )

    bar = min(1.0, max(0.5, float(setup.confidence_bar or 0.78)))
    draft = min(0.7, bar)
    config = DeploymentConfig(
        taxonomy=Taxonomy(categories=categories[:64], fallback=FALLBACK_KEY),
        rules=Rules(hard_stops=hard_stops[:32], bucket_overrides=overrides[:200], priority=priority[:40]),
        flow=standard_flow(),
        thresholds=Thresholds(auto_min_confidence=bar, draft_min_confidence=draft, escalate_below=draft),
    )
    # Round-trip through JSON so the stored document is exactly what validation accepts.
    config = DeploymentConfig.model_validate(config.model_dump(mode="json", by_alias=True))
    return config, notes


def config_json(config: DeploymentConfig) -> dict[str, Any]:
    return config.model_dump(mode="json", by_alias=True)
