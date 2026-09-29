"""Model providers per workspace: the allow-list, the monthly budget and spend, and provider-pinned evals."""

from __future__ import annotations

from typing import Any

import pytest

from command_inbox.agents.providers import HeuristicProvider, ProviderRouter, set_provider_for_tests
from command_inbox.config import settings
from tests.integration.admin_support import (
    add_member,
    audit_actions,
    desk_config,
    last_seq,
    make_dataset,
    make_deployment,
    org_id,
    run_eval,
)

pytestmark = pytest.mark.integration


def with_openai_drafter(config: dict[str, Any]) -> dict[str, Any]:
    for node in config["flow"]["nodes"]:
        if node["type"] == "draft_reply":
            node["agent"] = {"provider": "openai", "model": "gpt-5-mini", "signature": "Customer Care"}
    return config


@pytest.fixture
def openai_configured(monkeypatch):
    """OpenAI has a key on this installation; every call is answered by the deterministic provider."""
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    set_provider_for_tests(HeuristicProvider())
    yield
    set_provider_for_tests(None)


async def test_the_allow_list_governs_drafts_and_live_versions(app, openai_configured):
    a = await add_member(app, "meridian", "admin")
    oid = await org_id("meridian")
    r = await a.get("/v1/workspace/model-policy")
    assert r.status_code == 200, r.text
    policy = r.json()
    assert [p["key"] for p in policy["providers"]] == ["anthropic", "openai"]
    assert all(p["allowed"] for p in policy["providers"]) and policy["monthlyBudgetMinor"] is None
    assert next(p for p in policy["providers"] if p["key"] == "openai")["configured"] is True

    seq = await last_seq(oid)
    r = await a.send(
        "PUT", "/v1/workspace/model-policy", {"allowedProviders": ["anthropic"], "monthlyBudgetMinor": 5000}
    )
    assert r.status_code == 200, r.text
    assert [p["allowed"] for p in r.json()["providers"]] == [True, False]
    assert r.json()["monthlyBudgetMinor"] == 5000
    assert "workspace.model_policy_changed" in [x["action"] for x in await audit_actions(oid, seq)]

    # A draft may not name a provider the policy leaves out.
    dep, vid = await make_deployment(a, "policy_desk")
    r = await a.send(
        "PUT", f"/v1/deployments/{dep}/versions/{vid}/config", {"config": with_openai_drafter(desk_config())}
    )
    assert r.status_code == 400 and r.json()["code"] == "provider_not_allowed", r.text
    assert "draft_reply would call OpenAI" in r.json()["detail"]

    # Allowed again: the draft saves and goes to shadow; now the provider cannot be withdrawn under it.
    r = await a.send(
        "PUT",
        "/v1/workspace/model-policy",
        {"allowedProviders": ["anthropic", "openai"], "monthlyBudgetMinor": None},
    )
    assert r.status_code == 200, r.text
    r = await a.send(
        "PUT", f"/v1/deployments/{dep}/versions/{vid}/config", {"config": with_openai_drafter(desk_config())}
    )
    assert r.status_code == 200, r.text
    r = await a.send("POST", f"/v1/deployments/{dep}/versions/{vid}/promote", {"to": "shadow"})
    assert r.status_code == 200, r.text
    r = await a.send(
        "PUT", "/v1/workspace/model-policy", {"allowedProviders": ["anthropic"], "monthlyBudgetMinor": None}
    )
    assert r.status_code == 409 and r.json()["code"] == "provider_in_use", r.text
    assert "Policy Desk v1" in r.json()["detail"]

    staff = await add_member(app, "meridian", "staff")
    r = await staff.send(
        "PUT", "/v1/workspace/model-policy", {"allowedProviders": [], "monthlyBudgetMinor": None}
    )
    assert r.status_code == 403


async def test_publishing_needs_the_provider_on_this_installation(app, monkeypatch):
    a = await add_member(app, "meridian", "admin")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    dep, vid = await make_deployment(a, "installation_desk", with_openai_drafter(desk_config()))
    monkeypatch.setattr(settings, "openai_api_key", None)
    r = await a.get(f"/v1/deployments/{dep}/versions/{vid}")
    assert r.status_code == 200, r.text
    blockers = r.json()["publishCheck"]["blockers"]
    assert any("not configured on this installation" in b for b in blockers), blockers


async def test_spend_is_recorded_per_month_and_crossing_the_cap_is_audited(app):
    from command_inbox.agents.budget import record_spend
    from command_inbox.db.engine import tenant_tx
    from command_inbox.db.models import Org

    a = await add_member(app, "meridian", "admin")
    oid = await org_id("meridian")
    r = await a.send(
        "PUT",
        "/v1/workspace/model-policy",
        {"allowedProviders": ["anthropic", "openai"], "monthlyBudgetMinor": 100},
    )
    assert r.status_code == 200
    before = r.json()["spentMinor"]
    seq = await last_seq(oid)

    router = ProviderRouter(primary=HeuristicProvider())
    router.spent_by_provider["openai"] = 60
    router.calls_by_provider["openai"] = 2
    await record_spend(oid, router)
    router.spent_by_provider["openai"] = 60  # a second run crosses 100
    await record_spend(oid, router)

    policy = (await a.get("/v1/workspace/model-policy")).json()
    openai = next(p for p in policy["providers"] if p["key"] == "openai")
    assert policy["spentMinor"] == before + 120 and openai["calls"] >= 4
    assert policy["budgetReached"] is True
    assert [x["action"] for x in await audit_actions(oid, seq)].count("model.budget_reached") == 1

    from command_inbox.agents.budget import monthly_remaining

    async with tenant_tx(oid) as tx:
        org = await tx.get(Org, oid)
        assert await monthly_remaining(tx, org) == 0
    r = await a.send(
        "PUT",
        "/v1/workspace/model-policy",
        {"allowedProviders": ["anthropic", "openai"], "monthlyBudgetMinor": None},
    )
    assert r.status_code == 200


async def test_an_eval_scores_system2_and_a_provider_pinned_run_never_counts_for_publishing(app):
    a = await add_member(app, "meridian", "admin")
    dep, vid = await make_deployment(a, "compare_desk")
    ds = await make_dataset(a, dep)
    own = await run_eval(a, vid, ds)
    assert own["state"] == "passed" and own["provider"] is None
    metrics = own["metrics"]
    for key in (
        "endToEndAccuracy",
        "adjudicatedCases",
        "draftsScored",
        "system2Fallbacks",
        "system2Provider",
    ):
        assert key in metrics, key
    assert metrics["system2Fallbacks"] == 0

    r = await a.send(
        "POST", "/v1/evals/runs", {"deploymentVersionId": vid, "datasetId": ds, "provider": "anthropic"}
    )
    assert r.status_code == 200, r.text
    from tests.integration.admin_support import drain

    await drain()
    pinned = (await a.get(f"/v1/evals/runs/{r.json()['id']}")).json()
    assert pinned["provider"] == "anthropic" and pinned["metrics"]["system2Provider"] == "anthropic"
    # No Anthropic key in tests: the provider could not answer, and the run says so rather than scoring rules.
    assert pinned["metrics"]["system2Fallbacks"] > 0

    from command_inbox.db.engine import tenant_tx
    from command_inbox.db.models import DeploymentVersion
    from command_inbox.modules.deployments.service import passing_run

    oid = await org_id("meridian")
    async with tenant_tx(oid) as tx:
        v = await tx.get(DeploymentVersion, vid)
        run = await passing_run(tx, v)
    assert run is not None and run.id == own["id"]
