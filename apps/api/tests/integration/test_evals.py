"""Evals: dataset and case management, runs end to end through the worker, metrics, gates and integrity."""

from __future__ import annotations

import pytest

from tests.integration.admin_support import (
    add_member,
    case,
    drain,
    make_dataset,
    make_deployment,
    passing_cases,
    run_eval,
)

pytestmark = pytest.mark.integration


async def test_eval_permissions(app, staff, lead):
    assert (await staff.get("/v1/evals/datasets")).status_code == 403
    assert (await lead.get("/v1/evals/datasets")).status_code == 200  # evals.view
    default = next(d for d in (await lead.get("/v1/deployments")).json() if d["key"] == "default")
    r = await lead.send("POST", "/v1/evals/datasets", {"deploymentId": default["id"], "name": "x"})
    assert r.status_code == 403 and r.json()["code"] == "capability_required"


async def test_dataset_and_case_management(app):
    a = await add_member(app, "meridian", "admin")
    dep_id, vid = await make_deployment(a, "eval_crud_desk")
    r = await a.send(
        "POST", "/v1/evals/datasets", {"deploymentId": dep_id, "name": "Golden", "description": "v1"}
    )
    assert r.status_code == 200
    ds = r.json()
    assert ds["cases"] == 0 and ds["deploymentId"] == dep_id
    r = await a.send("POST", "/v1/evals/datasets", {"deploymentId": dep_id, "name": "golden"})
    assert r.status_code == 409 and r.json()["code"] == "name_taken"

    cases = [case("cheque_stop", "calibration"), case("statement_copy", "test"), case("other", "test")]
    r = await a.send("POST", f"/v1/evals/datasets/{ds['id']}/cases", {"cases": cases})
    assert r.status_code == 200 and len(r.json()) == 3
    added = r.json()
    assert added[0]["expected"] == {"category": "cheque_stop", "hardStop": False}
    assert added[0]["input"]["fromEmail"] == "customer@mail.example"
    snap1 = (await a.get(f"/v1/evals/datasets/{ds['id']}")).json()
    assert snap1["splits"] == {"calibration": 1, "test": 2}

    # Bad labels are rejected by the schema; relabelling and archiving change the snapshot.
    bad = {"cases": [{**cases[0], "expected": {"category": "Not A Key"}}]}
    assert (await a.send("POST", f"/v1/evals/datasets/{ds['id']}/cases", bad)).status_code == 400
    relabel = case("other", "test")
    r = await a.send("PUT", f"/v1/evals/cases/{added[1]['id']}", relabel)
    assert r.status_code == 200 and r.json()["expected"]["category"] == "other"
    snap2 = (await a.get(f"/v1/evals/datasets/{ds['id']}")).json()
    assert snap2["snapshot"] != snap1["snapshot"]
    assert (await a.send("DELETE", f"/v1/evals/cases/{added[2]['id']}")).status_code == 200
    listed = (await a.get(f"/v1/evals/datasets/{ds['id']}/cases")).json()
    assert sorted(c["id"] for c in listed) == sorted([added[0]["id"], added[1]["id"]])
    assert (await a.send("DELETE", f"/v1/evals/cases/{added[2]['id']}")).status_code == 404

    r = await a.send("PATCH", f"/v1/evals/datasets/{ds['id']}", {"name": "Golden set"})
    assert r.status_code == 200 and r.json()["name"] == "Golden set"
    # A dataset that scored a run is kept as evidence; an unused one can be deleted.
    assert (await run_eval(a, vid, ds["id"]))["state"] in ("passed", "failed")
    r = await a.send("DELETE", f"/v1/evals/datasets/{ds['id']}")
    assert r.status_code == 409 and r.json()["code"] == "dataset_in_use"
    r = await a.send("POST", "/v1/evals/datasets", {"deploymentId": dep_id, "name": "scratch"})
    assert (await a.send("DELETE", f"/v1/evals/datasets/{r.json()['id']}")).status_code == 200


async def test_run_end_to_end_produces_metrics_and_gate_verdicts(app):
    a = await add_member(app, "meridian", "admin")
    dep_id, vid = await make_deployment(a, "eval_e2e_desk")
    ds = await make_dataset(a, dep_id)
    run = await run_eval(a, vid, ds)
    assert run["state"] == "passed", run
    assert run["passed"] is True and run["engine"]
    assert run["split"] == {"calibration": 20, "test": 12}
    m = run["metrics"]
    # Gates are scored on the test split only; calibration is fitted on its own split.
    assert m["cases"] == 12 and m["calibrationCases"] == 20
    assert m["accuracy"] == 1.0 and m["macroF1"] == 1.0 and m["hardStopRecall"] == 1.0
    assert m["ece"] <= 0.05 and m["laneSafetyViolations"] == 0
    assert m["coverage"] >= 0.7 and m["selectiveAccuracy"] >= 0.98
    assert m["escalationRate"] == pytest.approx(1 - m["coverage"])
    assert m["p95LatencyMs"] is not None and m["costPerThousandMailsMinor"] is not None
    assert m["temperature"] > 0 and m["conformalQhat"] is not None
    assert {g["key"] for g in run["gates"]} == {
        "hard_stop_recall",
        "accuracy",
        "macro_f1",
        "ece",
        "selective_accuracy",
        "coverage",
        "lane_safety",
    }
    assert all(g["passed"] for g in run["gates"])

    results = (await a.get(f"/v1/evals/runs/{run['id']}/results")).json()
    assert len(results) == 32
    stops = [r for r in results if r["hardStopExpected"]]
    assert len(stops) == 3 and all(r["hardStopPredicted"] and r["lane"] == "manual" for r in stops)
    assert {r["split"] for r in results} == {"calibration", "test"}

    listed = (await a.get(f"/v1/evals/runs?versionId={vid}")).json()
    assert [r["id"] for r in listed] == [run["id"]]
    assert (await a.get(f"/v1/evals/runs?deploymentId={dep_id}")).json()[0]["id"] == run["id"]


async def test_missed_hard_stop_fails_recall_and_lane_safety(app):
    a = await add_member(app, "meridian", "admin")
    dep_id, vid = await make_deployment(a, "eval_fail_desk")
    cases = passing_cases()
    # Labelled as a hard stop, but nothing the guard screens for: an auto-lane decision on it is unsafe.
    cases.append(
        case(
            "cheque_stop",
            "test",
            hard_stop=True,
            body="Please stop the cheque, cancel cheque number 004513 and do not honour the cheque. "
            "My father has just been taken into intensive care.",
        )
    )
    ds = await make_dataset(a, dep_id, cases)
    run = await run_eval(a, vid, ds)
    assert run["state"] == "failed" and run["passed"] is False
    failed = {g["key"] for g in run["gates"] if not g["passed"]}
    assert {"hard_stop_recall", "lane_safety"} <= failed
    assert run["metrics"]["laneSafetyViolations"] == 1
    assert run["metrics"]["hardStopRecall"] == pytest.approx(0.75)
    # A failed run never unlocks publishing.
    other = await add_member(app, "meridian", "admin")
    r = await other.send("POST", f"/v1/deployments/{dep_id}/versions/{vid}/promote", {"to": "published"})
    assert r.status_code == 409 and r.json()["code"] == "eval_gate"


async def test_too_little_calibration_data_cannot_certify_coverage(app):
    a = await add_member(app, "meridian", "admin")
    dep_id, vid = await make_deployment(a, "eval_small_desk")
    cases = [c for c in passing_cases() if c["split"] == "test"] + [case("cheque_stop", "calibration")]
    run = await run_eval(a, vid, await make_dataset(a, dep_id, cases))
    assert run["state"] == "failed"
    assert run["metrics"]["coverage"] == 0.0
    assert {g["key"] for g in run["gates"] if not g["passed"]} >= {"coverage", "selective_accuracy"}


async def test_run_errors_if_the_dataset_changes_before_it_executes(app):
    a = await add_member(app, "meridian", "admin")
    dep_id, vid = await make_deployment(a, "eval_frozen_desk")
    ds = await make_dataset(a, dep_id)
    r = await a.send("POST", "/v1/evals/runs", {"deploymentVersionId": vid, "datasetId": ds})
    run_id = r.json()["id"]
    snapshot = r.json()["datasetSnapshot"]
    await a.send("POST", f"/v1/evals/datasets/{ds}/cases", {"cases": [case("other", "test")]})
    await drain()
    run = (await a.get(f"/v1/evals/runs/{run_id}")).json()
    assert run["state"] == "error" and "dataset changed" in run["error"]
    assert run["datasetSnapshot"] == snapshot
    assert (await a.get(f"/v1/evals/runs/{run_id}/results")).json() == []


async def test_offline_cli_scores_and_stores_a_run(app, capsys):
    from command_inbox.evals import cli

    a = await add_member(app, "meridian", "admin")
    dep_id, vid = await make_deployment(a, "eval_cli_desk")
    await make_dataset(a, dep_id)
    code = await cli.run("eval_cli_desk", None, "meridian", None)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "passed" in out and "[PASS] Hard-stop recall" in out
    runs = (await a.get(f"/v1/evals/runs?versionId={vid}")).json()
    assert len(runs) == 1 and runs[0]["state"] == "passed" and runs[0]["createdBy"] is None
    with pytest.raises(cli.CliError):
        await cli.run("default", None, None, None)  # the key exists in every workspace: --org is required
