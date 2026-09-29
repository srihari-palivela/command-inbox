"""The monitoring dashboard reads records: funnel, reply times, drafts, quality, knowledge, mailboxes."""

from __future__ import annotations

import pytest

from command_inbox.modules.insights.monitoring import edit_distance
from tests.integration.admin_support import add_member

pytestmark = pytest.mark.integration


def test_edit_distance():
    assert edit_distance("Dear Kavya, thanks.", "Dear Kavya, thanks.") == 0
    assert 0 < edit_distance("Dear Kavya, thanks.", "Dear Kavya, many thanks.") < 0.3
    assert edit_distance("abc", "xyz") == 1


async def test_monitoring_numbers_agree_with_the_records(admin):
    r = await admin.get("/v1/insights/monitoring?days=90")
    assert r.status_code == 200, r.text
    m = r.json()
    funnel = {x["key"]: x["count"] for x in m["funnel"]}
    assert funnel["received"] >= funnel["triaged"] >= funnel["draft"]
    assert funnel["triaged"] >= funnel["draft"] + funnel["manual"]
    assert all(x["href"] is None or x["href"].startswith("/tickets") for x in m["funnel"])
    assert m["drafts"]["sent"] == m["drafts"]["unedited"] + m["drafts"]["edited"]
    assert sum(p["total"] for p in m["sla"]["byPriority"]) == funnel["received"]
    assert m["nodes"] and all(n["calls"] > 0 for n in m["nodes"])
    assert m["knowledge"]["approved"] >= 0 and isinstance(m["openAlerts"], int)


async def test_staff_cannot_see_monitoring(app):
    staff = await add_member(app, "apex", "staff")
    assert (await staff.get("/v1/insights/monitoring")).status_code == 403
