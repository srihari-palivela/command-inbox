"""Insights: performance, results, KPIs, alerts, the activity rail and the shift summary."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_staff_cannot_see_performance_or_results(staff):
    for url in ("/v1/insights/performance", "/v1/insights/results"):
        r = await staff.get(url)
        assert r.status_code == 403 and r.json()["code"] == "capability_required"


async def test_performance_and_results_for_a_lead(lead):
    perf = (await lead.get("/v1/insights/performance")).json()
    assert [t["key"] for t in perf["tiles"]] == ["fr", "tat", "missed", "reopen"]
    assert all(isinstance(t["value"], str) for t in perf["tiles"])
    assert len(perf["metrics"]) == 8 and perf["staff"]
    res = (await lead.get("/v1/insights/results")).json()
    assert [c["lane"] for c in res["coverage"]] == ["auto", "draft", "manual"]
    assert "phases" not in res and [p["label"] for p in res["pools"]] == ["Capacity released"]


async def test_kpi_create_and_delete(lead, staff):
    body = {"name": "  Reopens under 3%  ", "metric": "reopen", "viz": "number", "scope": "team", "target": 3}
    assert (await staff.send("POST", "/v1/kpis", body)).status_code == 403
    r = await lead.send("POST", "/v1/kpis", body)
    assert r.status_code == 200 and r.json() == {"ok": True}
    kpis = (await lead.get("/v1/insights/performance")).json()["customKpis"]
    kpi = next(k for k in kpis if k["name"] == "Reopens under 3%")
    assert kpi["lowerIsBetter"] is True and kpi["metricLabel"] == "Reopen rate (%)"
    assert (await staff.send("DELETE", f"/v1/kpis/{kpi['id']}")).status_code == 403
    r = await lead.send("DELETE", f"/v1/kpis/{kpi['id']}")
    assert r.status_code == 200
    kpis = (await lead.get("/v1/insights/performance")).json()["customKpis"]
    assert all(k["id"] != kpi["id"] for k in kpis)
    assert (await lead.send("DELETE", f"/v1/kpis/{kpi['id']}")).status_code == 404

    from command_inbox.db.engine import rows, tenant_tx

    async with tenant_tx(lead.me["org"]["id"]) as tx:
        events = await rows(
            tx,
            "select action, summary from audit_events where action in ('kpi.created', 'kpi.deleted')"
            " order by seq desc limit 2",
        )
    assert [e["action"] for e in events] == ["kpi.deleted", "kpi.created"]
    assert events[1]["summary"] == 'KPI "Reopens under 3%" created on the team dashboard'


async def test_kpi_body_is_validated(lead):
    r = await lead.send(
        "POST", "/v1/kpis", {"name": "x", "metric": "nope", "viz": "number", "scope": "team", "target": 1}
    )
    assert r.status_code in (400, 422)


async def test_acting_on_an_alert_clears_it_and_notifies(lead):
    alerts = (await lead.get("/v1/insights/performance")).json()["alerts"]
    assert alerts
    a = alerts[0]
    r = await lead.send("POST", f"/v1/alerts/{a['id']}/notify")
    assert r.status_code == 200 and r.json()["message"].startswith(f"{a['owner']} notified")
    r = await lead.send("POST", f"/v1/alerts/{a['id']}/act")
    assert r.status_code == 200 and "alert cleared" in r.json()["message"]
    after = (await lead.get("/v1/insights/performance")).json()["alerts"]
    assert all(x["id"] != a["id"] for x in after)
    assert (await lead.send("POST", f"/v1/alerts/{a['id']}/sideways")).status_code in (400, 404, 422)


async def test_activity_and_shift(staff):
    r = await staff.get("/v1/activity")
    assert r.status_code == 200 and len(r.json()) <= 20
    s = (await staff.get("/v1/shift")).json()
    assert set(s) == {"closedToday", "sentAsDraftedPct", "savedMinutes", "missedDeadlines"}
