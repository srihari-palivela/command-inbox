"""Seeds three tenants. Apex Bank carries the full designed scenario; Meridian and Northwind are small, so
switching workspace visibly changes the data (and demonstrates tenant isolation).

Port of apps/server/src/db/seed/index.ts. For the same `now` the rows match the TypeScript seed column for
column (ids aside); on top of that every tenant gets a default deployment with a published version bound to
its mailboxes and tickets, and a labelled eval dataset drawn from its seeded mail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from command_inbox.core import audit as audit_mod
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import AI_ACTOR, Actor
from command_inbox.core.crypto import canonical_json, sha256
from command_inbox.db import models as m
from command_inbox.db.engine import make_engine
from command_inbox.domain.risk import cell_of, chain_for
from command_inbox.seed import data as d
from command_inbox.seed._base import DAY, MIN, SeedClock, Writer, initials_of, js_round, to_fixed
from command_inbox.seed.deployments import seed_deployments
from command_inbox.seed.tickets import DETAILED, HISTORY, LIGHT

__all__ = ["SeedResult", "has_data", "seed", "seed_tx"]


@dataclass
class SeedResult:
    now: datetime
    orgs: dict[str, str] = field(default_factory=dict)  # slug -> id
    users: dict[str, str] = field(default_factory=dict)  # person name -> id


def asyncpg_url(url: str) -> str:
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


async def seed(database_url: str | None = None, now: datetime | None = None) -> SeedResult:
    """Seed an empty, migrated database in one transaction (as the schema owner)."""
    from command_inbox.config import settings

    engine = make_engine(asyncpg_url(database_url or settings.database_admin_url), pool_size=2)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as tx, tx.begin():
            return await seed_tx(tx, now)
    finally:
        await engine.dispose()


async def has_data(database_url: str | None = None) -> bool:
    from command_inbox.config import settings

    engine = make_engine(asyncpg_url(database_url or settings.database_admin_url), pool_size=1)
    try:
        async with engine.connect() as conn:
            return bool((await conn.execute(select(func.count()).select_from(m.Org))).scalar())
    finally:
        await engine.dispose()


async def seed_tx(tx: AsyncSession, now: datetime | None = None) -> SeedResult:
    sc = SeedClock(now or clock.now())
    w = Writer(tx, sc)
    try:
        result = SeedResult(now=sc.now)
        headcount = {"apex": 34, "meridian": 11}
        orgs = await w.insert(m.Org, [{**o, "headcount": headcount.get(o["slug"], 6)} for o in d.ORGS])
        org = {o["slug"]: o["id"] for o in orgs}
        # Demo platform operators for the console (fictional, like everything in this seed).
        await w.insert(m.PlatformOperator, d.OPERATORS)
        users = await w.insert(
            m.User,
            [{"email": p["email"], "name": p["name"], "initials": initials_of(p["name"])} for p in d.PEOPLE],
        )
        uid = {u["name"]: u["id"] for u in users}
        result.orgs, result.users = org, uid
        apex = org["apex"]

        await w.insert(
            m.Membership,
            [
                {
                    "org_id": apex,
                    "user_id": uid[p["name"]],
                    "role": p["role"],
                    "title": p["title"],
                    "pod": p["pod"],
                    "capacity": p["cap"],
                    "years": p["years"],
                    "base_load": d.BACKGROUND_LOAD[p["name"]],
                    "joined_at": sc.days_ago(p["years"] * 365 + i * 17),
                }
                for i, p in enumerate(d.PEOPLE)
            ],
        )
        # P. Sharma has a different role in each workspace — the org switcher shows it.
        await w.insert(
            m.Membership,
            [
                _member(org["meridian"], uid["P. Sharma"], "admin", "Workspace admin", "Pilot team", 10),
                _member(org["meridian"], uid["R. Menon"], "lead", "Pilot lead", "Pilot team", 8),
                _member(org["northwind"], uid["P. Sharma"], "staff", "Support staff", "Members desk", 12),
                _member(org["northwind"], uid["A. Kapoor"], "admin", "Admin", "Members desk", 6),
            ],
        )

        await _seed_apex(w, apex, uid)
        await _seed_small_org(w, org["meridian"], uid, "meridian")
        await _seed_small_org(w, org["northwind"], uid, "northwind")
        # Appended last so every row the TypeScript seed writes (including audit sequence numbers) matches.
        await seed_deployments(w, org, uid)
        await tx.flush()
        return result
    finally:
        w.detach()


def _member(org_id: str, user_id: str, role: str, title: str, pod: str, capacity: int) -> dict[str, Any]:
    return {
        "org_id": org_id,
        "user_id": user_id,
        "role": role,
        "title": title,
        "pod": pod,
        "capacity": capacity,
    }


def _person(uid: dict[str, str], name: str) -> Actor:
    return Actor("user", uid[name], name, initials_of(name))


def _band(confidence: float) -> str:
    return "high" if confidence >= 0.9 else "moderate" if confidence >= 0.78 else "low"


async def _seed_apex(w: Writer, org_id: str, uid: dict[str, str]) -> None:
    sc = w.clock
    await w.insert(
        m.UserSetting,
        [
            {
                "org_id": org_id,
                "user_id": user_id,
                "prefs": {},
                "signature": (
                    "Priyanka Sharma\nCustomer Service · Apex Bank\nThis mailbox is monitored 08:00–20:00 IST."
                    if name == "P. Sharma"
                    else ""
                ),
            }
            for name, user_id in uid.items()
        ],
    )

    # ── Departments, taxonomy, clearance, availability ──────────────────────
    depts = await w.insert(
        m.Department,
        [
            {
                "org_id": org_id,
                "name": x["name"],
                "owner_id": uid[x["owner"]],
                "sort": i,
                "readiness_pct": x["readiness"],
                "readiness_note": x["note"],
                "gap_note": x["gapNote"],
                "risk": x["risk"],
                "in_matrix": x["inMatrix"],
            }
            for i, x in enumerate(d.DEPARTMENTS)
        ],
    )
    dept = {x["name"]: x["id"] for x in depts}

    qts = await w.insert(
        m.QueryType,
        [
            {
                "org_id": org_id,
                "name": q["name"],
                "map_name": q.get("mapName"),
                "department_id": dept[q["dept"]] if q.get("dept") else None,
                "default_lane": q["lane"],
                "monthly_volume": q["vol"],
                "live": q["live"],
                "baseline_hours": q.get("base"),
                "actual_hours": q.get("act"),
                "late_count": q["late"],
                "owner_label": q["owner"],
                "show_on_map": q["map"],
                "show_on_speed": q["speed"],
                "sort": i,
            }
            for i, q in enumerate(d.QUERY_TYPES)
        ],
    )
    qt = {q["name"]: q["id"] for q in qts}

    await w.insert(
        m.Clearance,
        [
            {"org_id": org_id, "user_id": uid[p["name"]], "department_id": dept[dname], "level": level}
            for p in d.PEOPLE
            for dname, level in p["clear"].items()
        ],
    )
    await w.insert(
        m.StaffAvailability,
        [
            {
                "org_id": org_id,
                "user_id": uid[p["name"]],
                "status": p["avail"],
                "checkin": p["checkin"],
                "calendar": p["cal"],
            }
            for p in d.PEOPLE
        ],
    )

    # ── Mailboxes & boards ──────────────────────────────────────────────────
    mbs = await w.insert(
        m.Mailbox,
        [
            {
                "org_id": org_id,
                "address": x["address"],
                "provider": "microsoft",
                "department_id": dept[x["dept"]] if x.get("dept") else None,
                "team_label": x["team"],
                "permissions": list(x["perms"]),
                "state": x["state"],
                "volume_24h": x["vol"],
                "last_sync_at": sc.ago(1),
                "sort": i,
            }
            for i, x in enumerate(d.MAILBOXES)
        ],
    )
    mailbox = {x["address"]: x for x in mbs}
    boards = await w.insert(
        m.Board,
        [
            {
                "org_id": org_id,
                "key": b["key"],
                "name": b["name"],
                "mailbox_id": mailbox[b["mailbox"]]["id"],
                "team": b["team"],
                "state": b["state"],
                "auto_rate_pct": b["auto"],
                "sort": i,
            }
            for i, b in enumerate(d.BOARDS)
        ],
    )
    board = {b["key"]: b for b in boards}

    # ── Agents ──────────────────────────────────────────────────────────────
    agents = await w.insert(
        m.Agent,
        [
            {
                "org_id": org_id,
                "name": a["name"],
                "abbr": a["abbr"],
                "template": a["template"],
                "model": a["model"],
                "state": a["state"],
                "version": a["version"],
                "prompt": a["prompt"],
                "eval_score": a["score"],
                "cost_per_1k_minor": a["cost"],
                "role": a["role"],
                "sort": i,
                "created_at": sc.days_ago(120 - i * 9),
            }
            for i, a in enumerate(d.AGENTS)
        ],
    )
    agent_id = {a["name"]: a["id"] for a in agents}
    for a in d.AGENTS:
        aid = agent_id[a["name"]]
        kept = min(3, a["version"])
        await w.insert(
            m.AgentVersion,
            [
                {
                    "org_id": org_id,
                    "agent_id": aid,
                    "version": a["version"] - (kept - 1 - k),
                    "prompt": a["prompt"],
                    "model": a["model"],
                    "eval_status": "passed",
                    "created_by": "A. Kapoor",
                    "created_at": sc.days_ago((kept - k) * 11),
                }
                for k in range(kept)
            ],
        )
        await w.insert(
            m.AgentBoard,
            [{"org_id": org_id, "agent_id": aid, "board_id": board[b]["id"]} for b in a["boards"]],
        )
        await w.insert(
            m.AgentEval,
            [
                {"org_id": org_id, "agent_id": aid, "label": label, "value": value, "tone": tone, "sort": i}
                for i, (label, value, tone) in enumerate(a["evals"])
            ],
        )

    # Calibration history: observed accuracy by confidence band. Trade Bucketer is slightly overconfident.
    # A Park–Miller generator, seeded like the TS one, so both seeds write the same outcomes.
    state = 7

    def rand() -> float:
        nonlocal state
        state = (state * 16807) % 2147483647
        return state / 2147483647

    outcomes: list[dict[str, Any]] = []
    for a in d.AGENTS:
        if a["role"] not in ("bucketer", "extractor", "drafter"):
            continue
        skew = 0.08 if a["name"] == "Trade Bucketer" else 0.01
        for _ in range(240):
            conf = 0.4 + rand() * 0.6
            confidence = _js_round2(conf)
            correct = rand() < max(0, conf - skew)
            outcomes.append(
                {
                    "org_id": org_id,
                    "agent_name": a["name"],
                    "confidence": confidence,
                    "correct": correct,
                    "created_at": sc.days_ago(rand() * 30),
                }
            )
    await w.insert(m.PredictionOutcome, outcomes)

    await w.insert(
        m.Feedback,
        [
            {
                "org_id": org_id,
                "agent_id": agent_id[f["agent"]],
                "agent_name": f["agent"],
                "ticket_number": f["ticket"],
                "kind": f["kind"],
                "text": f["text"],
                "fix": f["fix"],
                "created_at": sc.days_ago(f["daysAgo"]),
            }
            for f in d.FEEDBACK
        ],
    )

    # ── Action library & autonomy dial ──────────────────────────────────────
    tpls = await w.insert(
        m.ActionTemplate,
        [
            {
                "org_id": org_id,
                "code": a["code"],
                "name": a["name"],
                "system": a["system"],
                "endpoint": a["endpoint"],
                "owner": a["owner"],
                "reversible": a["rev"],
                "money_moves": a["money"],
                "approval": a["approval"],
                "stp_pct": a["stp"],
                "monthly_volume": a["vol"],
                "state": "suggest_only" if not a["rev"] and a["money"] else "active",
                "sort": i,
            }
            for i, a in enumerate(d.ACTION_TEMPLATES)
        ],
    )
    tpl = {t["code"]: t for t in tpls}
    await w.insert(
        m.AutonomyDial,
        [
            {
                "org_id": org_id,
                "cell": cell,
                "level": level,
                "locked": cell == "1-1",
                "count_override": d.CELL_COUNTS[cell],
                "updated_by": uid["A. Kapoor"],
            }
            for cell, level in d.DIAL.items()
        ],
    )

    # ── Rules ───────────────────────────────────────────────────────────────
    await w.insert(
        m.BucketRule,
        [
            {
                "org_id": org_id,
                "sort": i,
                "description": r["description"],
                "target": r["target"],
                "kind": r["kind"],
                "hits": r["hits"],
                "pattern": dict(r["pattern"]) if r["pattern"] else None,
            }
            for i, r in enumerate(d.BUCKET_RULES)
        ],
    )
    await w.insert(m.PriorityRule, _priority_rules(org_id))

    # ── Knowledge ───────────────────────────────────────────────────────────
    srcs = await w.insert(
        m.KnowledgeSource,
        [
            {
                "org_id": org_id,
                "name": k["name"],
                "kind": k["kind"],
                "abbr": k["abbr"],
                "doc_count": k["docs"],
                "doc_unit": k["unit"],
                "approved_count": k["approved"],
                "health": k["health"],
                "sync_note": k["syncNote"],
                "note": k["note"],
                "last_sync_at": sc.ago(k["syncMinAgo"]),
                "sort": i,
            }
            for i, k in enumerate(d.KSOURCES)
        ],
    )
    src = {s["name"]: s["id"] for s in srcs}
    docs = await w.insert(
        m.KnowledgeDoc,
        [
            {
                "org_id": org_id,
                "source_id": src[x["source"]],
                "title": x["title"],
                "section": x["section"],
                "owner": x["owner"],
                "department_id": dept[x["dept"]],
                "status": x["status"],
                "verified_at": sc.days_ago(x["verifiedDaysAgo"]),
                "body": x["body"],
            }
            for x in d.KDOCS
        ],
    )
    doc = {x["key"]: docs[i] for i, x in enumerate(d.KDOCS)}

    await w.insert(
        m.GapTicket,
        [
            *(
                {
                    "org_id": org_id,
                    "number": g["number"],
                    "severity": g["severity"],
                    "question": g["question"],
                    "detail": g["detail"],
                    "hits": g["hits"],
                    "owner": g["owner"],
                    "state": g["state"],
                    "cta": g["cta"],
                    "opened_at": sc.days_ago(g["openDays"]),
                    "closed_at": sc.days_ago(g["closedDaysAgo"]) if "closedDaysAgo" in g else None,
                }
                for g in d.GAPS
            ),
            *(
                {
                    "org_id": org_id,
                    "number": 390 - i * 2,
                    "severity": "stale",
                    "question": q,
                    "detail": "Raised automatically when the agent could not ground an answer. Waiting on the content owner.",
                    "hits": 4 + i * 3,
                    "owner": owner,
                    "state": "Waiting on owner",
                    "cta": "Nudge owner",
                    "opened_at": sc.days_ago(10 + i * 2),
                    "closed_at": None,
                }
                for i, (q, owner) in enumerate(d.EXTRA_GAPS)
            ),
        ],
    )

    # ── Insights, admin, learning ───────────────────────────────────────────
    await w.insert(
        m.Alert,
        [
            {
                "org_id": org_id,
                "sev_label": a["sevLabel"],
                "sev_kind": a["sevKind"],
                "bucket": a["bucket"],
                "text": a["text"],
                "action_label": a["action"],
                "owner": a["owner"],
                "created_at": sc.ago(a["minAgo"]),
            }
            for a in d.ALERTS
        ],
    )
    await w.insert(m.Connector, [{"org_id": org_id, **c, "sort": i} for i, c in enumerate(d.CONNECTORS)])

    courses = await w.insert(
        m.Course,
        [
            {
                "org_id": org_id,
                "key": c["key"],
                "title": c["title"],
                "minutes": c["minutes"],
                "cards": [dict(x) for x in c["cards"]],
                "quiz": [{**x, "options": list(x["options"])} for x in c["quiz"]],
                "baseline_pct": c["baseline"],
                "sort": i,
            }
            for i, c in enumerate(d.COURSES)
        ],
    )
    course = {c["key"]: c["id"] for c in courses}
    await w.insert(
        m.Notification,
        [
            {
                "org_id": org_id,
                "kind": n["kind"],
                "source": n["source"],
                "title": n["title"],
                "body": n["body"],
                "course_id": course[n["course"]] if n.get("course") else None,
                "urgent": n["urgent"],
                "created_at": sc.ago(n["minAgo"]),
            }
            for n in d.NOTIFS
        ],
    )

    metrics: list[dict[str, Any]] = []
    for key in ("fr", "tat", "missed", "reopen", "accept", "auto", "csat", "cost", "awo"):
        for i, value in enumerate(d.SERIES[key]):
            day = sc.day(sc.days_ago((11 - i) * 7))
            metrics.append({"org_id": org_id, "metric": f"weekly.{key}", "day": day, "value": value})
    for metric, series in (
        ("exec.baseline", d.SERIES["execBaseline"]),
        ("exec.actual", d.SERIES["execActual"]),
    ):
        for i, value in enumerate(series):
            metrics.append(
                {"org_id": org_id, "metric": metric, "day": sc.day(sc.days_ago(13 - i)), "value": value}
            )
    today = sc.day(sc.now)
    metrics += [
        {"org_id": org_id, "metric": "spend.imported_minor", "day": today, "value": 214_000_00},
        {"org_id": org_id, "metric": "shift.closed_imported", "day": today, "value": 25},
        {"org_id": org_id, "metric": "shift.saved_imported", "day": today, "value": 124},
    ]
    await w.insert(m.DailyMetric, metrics)

    # ── Customers & tickets ─────────────────────────────────────────────────
    all_tickets = [*DETAILED, *LIGHT]
    cust_by_cif: dict[str, str] = {}
    for t in all_tickets:
        c = t["customer"]
        if c["cif"] in cust_by_cif:
            continue
        row = await w.one(
            m.Customer,
            {
                "org_id": org_id,
                "cif": c["cif"],
                "name": c["name"],
                "email": c["email"],
                "phone": c["phone"],
                "segment": c["segment"],
                "since_year": c["since"],
                "account": c["account"],
            },
        )
        cust_by_cif[c["cif"]] = row["id"]

    # Past (closed) tickets: customer history is a query over real tickets.
    for h in HISTORY:
        received = sc.days_ago(h["daysAgo"])
        q = next(x for x in d.QUERY_TYPES if x["name"] == h["queryType"])
        cust = next(x["customer"] for x in DETAILED if x["customer"]["cif"] == h["cif"])
        await w.one(
            m.Ticket,
            {
                "org_id": org_id,
                "number": h["number"],
                "customer_id": cust_by_cif[h["cif"]],
                "subject": h["subject"],
                "from_name": cust["name"],
                "from_email": cust["email"],
                "received_at": received,
                "lane": q["lane"],
                "original_lane": q["lane"],
                "status": "closed",
                "priority": "P3",
                "department_id": dept[q["dept"]] if q.get("dept") else None,
                "query_type_id": qt[h["queryType"]],
                "bucket": h["queryType"],
                "confidence": 0.8,
                "owner_kind": "user",
                "assignee_id": uid["A. Fernandes"],
                "sla_minutes": 1440,
                "due_at": _plus(received, DAY),
                "resolved_at": _plus(received, 3 * 60 * MIN),
                "closed_at": _plus(received, 4 * 60 * MIN),
                "resolution": h["outcome"],
                "next_move": h["outcome"],
                "created_at": received,
                "updated_at": received,
            },
        )

    feed_events: list[tuple[datetime, dict[str, Any]]] = []
    ticket_ids: dict[int, str] = {}
    mb_by_id = {x["id"]: x for x in mbs}

    for t in all_tickets:
        board_row = board[t["board"]]
        mb = mb_by_id[board_row["mailbox_id"]]
        received = sc.ago(t["receivedMinAgo"])
        owner = t["owner"]
        assignee_id = None if owner in ("AI", "Unassigned") else uid[owner]
        confidence = t["confidence"]
        ticket = await w.one(
            m.Ticket,
            {
                "org_id": org_id,
                "number": t["number"],
                "board_id": board_row["id"],
                "mailbox_id": mb["id"],
                "customer_id": cust_by_cif[t["customer"]["cif"]],
                "subject": t["subject"],
                "from_name": t["customer"]["name"],
                "from_email": t["customer"]["email"],
                "received_at": received,
                "lane": t["lane"],
                "original_lane": t["lane"],
                "lane_note": t["laneNote"],
                "status": t["status"],
                "priority": t["priority"],
                "segment": t["segment"],
                "department_id": dept[t["dept"]] if t.get("dept") else None,
                "query_type_id": qt.get(t["queryType"]),
                "bucket": t.get("bucketLabel") or t["queryType"],
                "confidence": confidence,
                "assignee_id": assignee_id,
                "owner_kind": "ai" if owner == "AI" else "unassigned" if owner == "Unassigned" else "user",
                "sla_minutes": t["slaMinutes"],
                "due_at": (
                    _plus(received, t["slaMinutes"] * MIN)
                    if t["dueInMin"] is None
                    else sc.in_min(t["dueInMin"])
                ),
                "paused_at": (
                    sc.ago(min(120, t["receivedMinAgo"] / 2)) if t["status"] == "waiting_customer" else None
                ),
                "first_reply_at": _plus(received, 60_000),
                "resolved_at": sc.ago(t["resolvedMinAgo"]) if t.get("resolvedMinAgo") is not None else None,
                "reopen_count": t.get("reopenCount") or 0,
                "regulatory_flag": t.get("regulatoryFlag"),
                "sentiment": t.get("sentiment") or "neutral",
                "next_move": t["nextMove"],
                "category": t["category"],
                "subcategory": t["subcategory"],
                "product": t["product"],
                "split_proposed": t.get("splitProposed") or False,
                "logged_minutes": t.get("loggedMinutes") or 0,
                "resolution": (
                    ("Resolved by the AI" if owner == "AI" else "Resolved by staff")
                    if t["status"] == "resolved"
                    else None
                ),
                "created_at": received,
                "updated_at": received,
            },
        )
        ticket_id = ticket["id"]
        ticket_ids[t["number"]] = ticket_id
        dept_name = t.get("dept") or t.get("deptLabel") or "Unowned"
        label = t.get("bucketLabel") or t["queryType"]

        # Thread
        for msg in t.get("thread") or []:
            internal = msg.get("internal") or False
            await w.one(
                m.Message,
                {
                    "org_id": org_id,
                    "ticket_id": ticket_id,
                    "direction": "note" if internal else "inbound",
                    "from_name": "Command Inbox" if internal else t["customer"]["name"],
                    "from_addr": "no-reply@commandinbox.bank" if internal else t["customer"]["email"],
                    "to_addr": "internal note — not sent" if internal else mb["address"],
                    "body": msg["body"],
                    "sent_at": sc.ago(msg["minAgo"]),
                },
            )

        # Triage run + trace
        trace_id = f"TRC-{t['number']}-01"
        reasoning = t.get("reasoning") or (
            f"Classified as {t['queryType'].lower()} with {_band(confidence)} confidence "
            f"({to_fixed(confidence)}). {t['laneNote']}."
        )
        spans = _build_spans(t, dept_name)
        run = await w.one(
            m.TriageRun,
            {
                "org_id": org_id,
                "ticket_id": ticket_id,
                "trace_id": trace_id,
                "reasoning": reasoning,
                "evidence": [{"tag": a, "quote": b, "why": c} for a, b, c in t.get("evidence") or []],
                "confidence": confidence,
                "lane": t["lane"],
                "latency_ms": t["latencyMs"],
                "cost_minor": sum(x["cost_minor"] or 0 for x in spans),
                "provider": "seed",
                "created_at": received,
            },
        )
        await w.insert(m.TraceSpan, [{"org_id": org_id, "run_id": run["id"], **x} for x in spans])

        # Action / draft / brief
        if t.get("action"):
            act = t["action"]
            template = tpl[act["code"]]
            cell = cell_of(template["reversible"], template["money_moves"])
            fields = [{**f, "inferred": f["source"].startswith("inferred")} for f in act["fields"]]
            executed = act["state"] == "executed"
            await w.one(
                m.ActionInstance,
                {
                    "org_id": org_id,
                    "ticket_id": ticket_id,
                    "template_id": template["id"],
                    "account_ref": act["account"],
                    "fields": fields,
                    "validation": act["validation"],
                    "state": act["state"],
                    "chain": chain_for(cell, template["approval"], d.DIAL[cell]),
                    "idempotency_key": sha256(
                        canonical_json(
                            {
                                "orgId": org_id,
                                "code": template["code"],
                                "fields": [[f["label"], f["value"]] for f in fields],
                            }
                        )
                    ),
                    "executed_at": received if executed else None,
                    "external_ref": f"STM-{t['number']}-OK" if executed else None,
                },
            )
        if t.get("draft"):
            dr = t["draft"]
            await w.one(
                m.Draft,
                {
                    "org_id": org_id,
                    "ticket_id": ticket_id,
                    "subject": dr["subject"],
                    "to_addr": t["customer"]["email"],
                    "original_body": dr["body"],
                    "current_body": dr["body"],
                    "citations": [
                        {
                            "n": i + 1,
                            "docId": doc[k]["id"],
                            "doc": doc[k]["title"],
                            "section": doc[k]["section"],
                            "verifiedAt": iso_ms(doc[k]["verified_at"]),
                            "owner": doc[k]["owner"],
                        }
                        for i, k in enumerate(dr["cites"])
                    ],
                    "flagged": dr["flagged"],
                    "state": "draft",
                },
            )
        if t.get("brief"):
            br = t["brief"]
            await w.one(
                m.Brief,
                {
                    "org_id": org_id,
                    "ticket_id": ticket_id,
                    "why": br["why"],
                    "summary": br["summary"],
                    "context": [{"label": a, "value": b} for a, b in br["context"]],
                    "suggestions": [{"label": a, "meta": b} for a, b in br["suggestions"]],
                },
            )

        # Sub-tasks (Jira-style), by lane
        await w.insert(
            m.Subtask,
            [
                {
                    "org_id": org_id,
                    "ticket_id": ticket_id,
                    "key": key,
                    "label": text,
                    "owner": who,
                    "sort": i,
                    "done": done,
                }
                for i, (key, text, who, done) in enumerate(_subtasks(t))
            ],
        )

        # History (system) + seeded human log
        assignee_name = "the AI" if owner == "AI" else "nobody yet" if owner == "Unassigned" else owner
        lane = t["lane"]
        system_log = [
            (
                t["receivedMinAgo"],
                f"Read the email and classified it as {label.lower()} with {_band(confidence)} confidence "
                f"({to_fixed(confidence)}).",
            ),
            (
                t["receivedMinAgo"] - 0.02,
                "Stood down — no customer-facing text generated. Assembled the context instead."
                if lane == "manual"
                else "Drafted a reply grounded in approved sources and attached the citations."
                if lane == "draft"
                else "Filled the action template and ran the validation checks.",
            ),
            (t["receivedMinAgo"] - 0.04, f"Routed to {dept_name} and placed in the queue by urgency."),
        ]
        if assignee_id:
            system_log.append(
                (
                    t["receivedMinAgo"] - 0.06,
                    f"Assigned to {assignee_name} — closest skill match for {label.lower()}, "
                    f"lightest live load in {dept_name}.",
                )
            )
        for minutes, body in system_log:
            await w.one(
                m.Comment,
                {
                    "org_id": org_id,
                    "ticket_id": ticket_id,
                    "kind": "system",
                    "author_id": None,
                    "author_name": "Command Inbox",
                    "author_initials": "AI",
                    "body": body,
                    "created_at": sc.ago(minutes),
                },
            )
        for entry in t.get("log") or []:
            await w.one(
                m.Comment,
                {
                    "org_id": org_id,
                    "ticket_id": ticket_id,
                    "kind": entry["kind"],
                    "author_id": uid[entry["who"]],
                    "author_name": entry["who"],
                    "author_initials": initials_of(entry["who"]),
                    "body": entry["text"],
                    "created_at": sc.ago(entry["minAgo"]),
                },
            )
        for ext, name, size in t.get("attachments") or []:
            await w.one(
                m.Attachment,
                {
                    "org_id": org_id,
                    "ticket_id": ticket_id,
                    "ext": ext,
                    "name": name,
                    "size": size,
                    "storage_key": f"seed/{t['number']}/{name}",
                },
            )

        # Linked objects
        links: list[dict[str, Any]] = []
        if t.get("action"):
            code = t["action"]["code"]
            links.append({"kind": "ACTION", "label": f"{code} · {tpl[code]['name']}", "ref": code})
        if t.get("draft"):
            for k in t["draft"]["cites"]:
                links.append(
                    {"kind": "SOURCE", "label": f"{doc[k]['title']} {doc[k]['section']}", "ref": doc[k]["id"]}
                )
        if t.get("brief"):
            links.append({"kind": "POLICY", "label": t["brief"]["why"], "ref": None})
        if t.get("gap"):
            gap = t["gap"]
            links.append(
                {"kind": "GAP", "label": f"GAP-0{gap} · receivables as margin", "ref": f"GAP-0{gap}"}
            )
        if t.get("dispute"):
            links.append({"kind": "DISPUTE", "label": t["dispute"], "ref": t["dispute"].split(" ")[0]})
        links.append({"kind": "AUDIT", "label": f"AUD-{t['number']}", "ref": None})
        await w.insert(
            m.TicketLink,
            [{"org_id": org_id, "ticket_id": ticket_id, **x, "sort": i} for i, x in enumerate(links)],
        )

        # Audit trail for the triage itself (not in the activity rail unless listed below)
        feed_events.append(
            (
                received,
                {
                    "actor": AI_ACTOR,
                    "action": "triage.completed",
                    "entity": "ticket",
                    "entity_id": ticket_id,
                    "ticket_id": ticket_id,
                    "summary": f"Classified QRY-{t['number']} as {t['queryType']} ({to_fixed(confidence)}) → {lane}",
                    "data": {"lane": lane, "confidence": confidence, "traceId": trace_id},
                },
            )
        )

    # The AI activity rail from the design, as real audit events.
    feed: list[tuple[int, int | None, str, audit_mod.FeedTone, str, Actor]] = [
        (1101, 48199, "Held QRY-48199 from all customer-facing output — ombudsman keyword", "stop", "policy 7.1", AI_ACTOR),
        (340, 48188, "Raised knowledge gap GAP-0412 · receivables as margin under DA terms", "flag", "QRY-48188", AI_ACTOR),
        (264, 48195, "Executed ACT-STM-002 · statement Apr–Jun emailed to registered address", "ok", "QRY-48195 · auto", AI_ACTOR),
        (182, None, "Re-prioritised 14 threads after an ageing sweep; 3 moved up the queue", "info", "queue", AI_ACTOR),
        (167, 48211, "Mandate check passed for M. Raghavan on CIF 8830412", "info", "QRY-48211", AI_ACTOR),
        (149, 48199, "Notified team lead R. Menon — QRY-48199 is 26 minutes from its deadline", "stop", "escalation", AI_ACTOR),
        (120, None, "Learned correction: reason code CONTRACT_TERMINATED preferred over DISPUTE", "muted", "from your edit", _person(uid, "P. Sharma")),
    ]  # fmt: skip
    for minutes, number, text, tone, meta, actor in feed:
        tid = ticket_ids[number] if number else None
        feed_events.append(
            (
                sc.ago(minutes),
                {
                    "actor": actor,
                    "action": "ai.activity",
                    "entity": "ticket" if number else "queue",
                    "entity_id": tid,
                    "ticket_id": tid,
                    "summary": text,
                    "feed": audit_mod.Feed(tone=tone, meta=meta),
                },
            )
        )
    feed_events.sort(key=lambda e: e[0])  # stable, like Array.prototype.sort
    for at, kwargs in feed_events:
        await audit_mod.audit(w.tx, org_id, at=at, **kwargs)

    await w.insert(
        m.Counter,
        [
            {"org_id": org_id, "name": "ticket", "value": 48216},
            {"org_id": org_id, "name": "gap", "value": 412},
        ],
    )


def _plus(dt: datetime, ms: int) -> datetime:
    return dt + timedelta(milliseconds=ms)


def _js_round2(x: float) -> float:
    return js_round(x * 100) / 100


def _priority_rules(org_id: str) -> list[dict[str, Any]]:
    return [{"org_id": org_id, "sort": i, **r, "enabled": True} for i, r in enumerate(d.PRIORITY_RULES)]


def _subtasks(t: dict[str, Any]) -> list[tuple[str, str, str, bool]]:
    resolved = t["status"] == "resolved"
    if t["lane"] == "auto":
        return [
            ("a1", "Verify the signatory against the mandate", "AI", True),
            ("a2", "Extract and validate the action fields", "AI", True),
            ("a3", "Approve as maker", "You", resolved),
            ("a4", "Counter-approve as checker", "Team lead", resolved),
        ]
    if t["lane"] == "draft":
        return [
            ("b1", "Find approved sources for the answer", "AI", True),
            ("b2", "Draft the reply with citations", "AI", True),
            ("b3", "Read the flagged paragraph", "You", False),
            ("b4", "Send and close", "You", False),
        ]
    return [
        ("c1", "Pull the history and records for the brief", "AI", True),
        (
            "c2",
            "Decide on provisional credit" if t["number"] == 48199 else "Decide the next step",
            "You",
            False,
        ),
        ("c3", "Give the customer a dated commitment", "You", False),
        (
            "c4",
            "Notify Compliance" if t.get("regulatoryFlag") else "Close the loop with the customer",
            "You",
            False,
        ),
    ]


_PRIORITY_NOTE = {
    "P1": "P1 · Critical · regulator, fraud, or money at risk today",
    "P2": "P2 · High · deadline inside 8 hours, or a repeat contact",
    "P3": "P3 · Normal · standard servicing, inside the day",
    "P4": "P4 · Low · informational, no deadline pressure",
}


def _build_spans(t: dict[str, Any], dept: str) -> list[dict[str, Any]]:
    """Port of the prototype's trace builder: the pipeline stages a ticket went through."""
    bucketer = "Trade Bucketer" if t.get("dept") == "Trade & Payments" else "Retail Bucketer"
    stop = t["number"] == 48199
    spans: list[dict[str, Any]] = []

    def push(
        offset_ms: int,
        agent: str,
        model: str,
        action: str,
        output: str,
        latency_ms: int,
        tokens: int | None,
        cost_minor: int | None,
        status: str,
    ) -> None:
        spans.append(
            {
                "seq": len(spans) + 1,
                "offset_ms": offset_ms,
                "agent": agent,
                "model": model,
                "action": action,
                "output": output,
                "latency_ms": latency_ms,
                "tokens": tokens,
                "cost_minor": cost_minor,
                "status": status,
            }
        )

    n = len(t.get("thread") or []) or 1
    vulnerable = t.get("sentiment") == "vulnerable"
    push(
        0,
        "Mail intake",
        "—",
        "Fetched the thread over Graph API, masked PII before any model call",
        f"{n} message{'s' if n > 1 else ''} · sender matched to {t['customer']['cif']}",
        128,
        None,
        None,
        "ok",
    )
    push(
        128,
        "Guardrail Sentinel",
        "claude-haiku-4-5",
        "Screened for hard stop rules before anything else ran",
        "STOP · ombudsman named + third contact — customer-facing generation suspended"
        if stop
        else "STOP · vulnerable-customer signal — drafting suspended"
        if vulnerable
        else "Clear · no hard stop fired",
        212,
        1100,
        4,
        "stop" if stop or vulnerable else "ok",
    )
    bar = t["confidence"] >= 0.78
    push(
        340,
        bucketer,
        "claude-sonnet-5",
        "Classified the query against the taxonomy",
        f"{t.get('bucketLabel') or t['queryType']} · confidence {to_fixed(t['confidence'])} · "
        f"{'above the bar' if bar else 'below the bar, human required'}",
        798,
        3400,
        19,
        "ok" if bar else "flag",
    )
    if t.get("brief") and t["number"] == 48199:
        push(
            1100,
            "Dispute Summariser",
            "claude-sonnet-5",
            "Assembled dispute history, merchant record and the chargeback clock for the human",
            "Brief attached · 5 context items · no customer-facing text generated",
            1900,
            6200,
            28,
            "ok",
        )
    elif t.get("action"):
        act = t["action"]
        inferred = sum(1 for f in act["fields"] if f["source"].startswith("inferred"))
        push(
            1100,
            "Field Extractor",
            "claude-sonnet-5",
            f"Filled {act['code']} from the thread and system records",
            f"{len(act['fields'])} fields · {inferred} inferred, rest verified against core",
            644,
            4100,
            31,
            "ok",
        )
        tpl = next(a for a in d.ACTION_TEMPLATES if a["code"] == act["code"])
        route = (
            "the AI may act alone" if tpl["rev"] and not tpl["money"] else "needs you and a second approver"
        )
        push(
            1800,
            "Policy engine",
            "rules",
            "Looked up the action’s risk cell and approval route",
            f"{'Can be undone' if tpl['rev'] else 'Cannot be undone'} · "
            f"{'Money moves' if tpl['money'] else 'No money moves'} → {route}",
            31,
            None,
            None,
            "ok" if tpl["rev"] else "flag",
        )
    elif t.get("draft"):
        cites = len(t["draft"]["cites"])
        gap = f" · 1 gap flagged, GAP-0{t['gap']} raised" if t.get("gap") else " · full coverage"
        push(
            1100,
            "Reply Drafter",
            "claude-sonnet-5",
            "Drafted the reply from approved sources only",
            f"{cites} citation{'s' if cites > 1 else ''}{gap}",
            1200,
            5800,
            42,
            "flag" if t.get("gap") else "ok",
        )
    elif t.get("splitProposed"):
        push(
            1100,
            "Split proposer",
            "claude-sonnet-5",
            "Detected two intents owned by two departments",
            "Proposed split into two child tickets · held for your decision",
            905,
            3900,
            22,
            "flag",
        )
    elif t["lane"] == "manual":
        push(
            1100,
            "Summariser",
            "claude-sonnet-5",
            "Assembled the history and records for the person taking over",
            "Brief attached · no customer-facing text generated",
            1100,
            3600,
            24,
            "ok",
        )
    elif t["lane"] == "draft":
        push(
            1100,
            "Reply Drafter",
            "claude-sonnet-5",
            "Drafting from approved sources only",
            "In progress",
            900,
            2100,
            18,
            "ok",
        )
    else:
        push(
            1100,
            "Field Extractor",
            "claude-sonnet-5",
            "Filling the action template from the thread",
            "In progress",
            600,
            2400,
            18,
            "ok",
        )
    push(
        2100,
        "Priority Ranker",
        "rules + claude-haiku-4-5",
        "Scored urgency from deadline, sentiment, amount and repeat contacts",
        _PRIORITY_NOTE[t["priority"]],
        96,
        800,
        2,
        "flag" if t["priority"] == "P1" else "ok",
    )
    push(
        2200,
        "Router",
        "rules",
        "Placed the ticket with the right person at the right position",
        f"Queued for {dept} · clearance-checked · position by {t['priority']}",
        18,
        None,
        None,
        "ok",
    )
    return spans


async def _seed_small_org(w: Writer, org_id: str, uid: dict[str, str], kind: str) -> None:
    """Small tenants: enough to show a different workspace, and to prove isolation."""
    sc = w.clock
    meridian = kind == "meridian"
    members = ("P. Sharma", "R. Menon") if meridian else ("P. Sharma", "A. Kapoor")
    await w.insert(
        m.UserSetting, [{"org_id": org_id, "user_id": uid[x], "prefs": {}, "signature": ""} for x in members]
    )
    dname = "Wealth Desk" if meridian else "Member Services"
    dep = await w.one(
        m.Department,
        {
            "org_id": org_id,
            "name": dname,
            "owner_id": uid[members[0]],
            "readiness_pct": 40,
            "readiness_note": "pilot content only",
        },
    )
    await w.insert(
        m.Clearance,
        [{"org_id": org_id, "user_id": uid[x], "department_id": dep["id"], "level": 3} for x in members],
    )
    await w.insert(
        m.StaffAvailability,
        [
            {
                "org_id": org_id,
                "user_id": uid[x],
                "status": "available",
                "checkin": "Checked in 09:00",
                "calendar": "Free",
            }
            for x in members
        ],
    )
    q = await w.one(
        m.QueryType,
        {
            "org_id": org_id,
            "name": "General enquiry",
            "department_id": dep["id"],
            "default_lane": "draft",
            "monthly_volume": 40,
            "owner_label": dname,
        },
    )
    address = "wealth@meridian.example" if meridian else "members@northwind.example"
    mb = await w.one(
        m.Mailbox,
        {
            "org_id": org_id,
            "address": address,
            "provider": "google",
            "department_id": dep["id"],
            "team_label": dname,
            "permissions": ["read", "label", "draft"],
            "state": "streaming",
            "volume_24h": 64 if meridian else 21,
        },
    )
    b = await w.one(
        m.Board,
        {
            "org_id": org_id,
            "key": "main",
            "name": "Wealth mail" if meridian else "Member mail",
            "mailbox_id": mb["id"],
            "team": dname,
            "state": "observe",
            "auto_rate_pct": 0,
        },
    )
    await w.insert(m.PriorityRule, _priority_rules(org_id))
    for cell, level in d.DIAL.items():
        await w.one(
            m.AutonomyDial, {"org_id": org_id, "cell": cell, "level": min(level, 1), "locked": cell == "1-1"}
        )
    cust = await w.one(
        m.Customer,
        {
            "org_id": org_id,
            "cif": "CIF 100001",
            "name": "Harini Balaji" if meridian else "Tom Okafor",
            "email": "harini.b@gmail.com" if meridian else "tom.okafor@mail.example",
            "segment": "Retail",
            "since_year": 2021,
            "account": "A/C ••0001",
        },
    )
    received = sc.ago(95)
    number = 1201 if meridian else 3301
    t = await w.one(
        m.Ticket,
        {
            "org_id": org_id,
            "number": number,
            "board_id": b["id"],
            "mailbox_id": mb["id"],
            "customer_id": cust["id"],
            "subject": (
                "Portfolio statement for Q2 not received"
                if meridian
                else "How do I update my address on the membership?"
            ),
            "from_name": cust["name"],
            "from_email": cust["email"],
            "received_at": received,
            "lane": "draft",
            "original_lane": "draft",
            "lane_note": "Observe mode — AI drafts are shadowed only",
            "status": "with_human",
            "priority": "P3",
            "segment": "Retail",
            "department_id": dep["id"],
            "query_type_id": q["id"],
            "bucket": "General enquiry",
            "confidence": 0.7,
            "assignee_id": uid["P. Sharma"],
            "owner_kind": "user",
            "sla_minutes": 1440,
            "due_at": _plus(received, 1440 * MIN),
            "next_move": "Reply to the customer",
            "category": "Servicing",
            "subcategory": "Information request",
            "product": "Savings account",
        },
    )
    await w.one(
        m.Message,
        {
            "org_id": org_id,
            "ticket_id": t["id"],
            "direction": "inbound",
            "from_name": cust["name"],
            "from_addr": cust["email"],
            "to_addr": address,
            "body": (
                "I have not received my portfolio statement for the quarter ending June. Could you resend it?"
                if meridian
                else "I moved house last month. What do I need to send you to update my address?"
            ),
            "sent_at": received,
        },
    )
    await w.insert(
        m.Counter,
        [
            {"org_id": org_id, "name": "ticket", "value": number},
            {"org_id": org_id, "name": "gap", "value": 1},
        ],
    )
    await audit_mod.audit(
        w.tx,
        org_id,
        actor=AI_ACTOR,
        action="workspace.created",
        entity="org",
        entity_id=org_id,
        summary=f"Workspace {kind} seeded",
        at=received,
    )
