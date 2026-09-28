"""Default deployment per tenant (published version 1, bound to every mailbox and ticket) and an eval set.

The configuration comes from `modules.deployments.defaults.build_default_config`, the same builder
migration 0004 uses to backfill tenants that predate deployments, so a seeded tenant looks exactly like a
migrated one. The eval dataset is labelled from the seeded tickets: category, lane, priority and the
hard stops the deployment's own keyword rules (plus the ticket's recorded signals) say should fire.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update

from command_inbox.agents.config import DeploymentConfig
from command_inbox.core.audit import audit
from command_inbox.core.context import Actor
from command_inbox.db import models as m
from command_inbox.modules.deployments.defaults import DEFAULT_KEY, Setup, build_default_config, config_json
from command_inbox.seed._base import Writer, initials_of

# The admin who publishes each seeded workspace's default version.
ADMIN = {"apex": "A. Kapoor", "meridian": "P. Sharma", "northwind": "A. Kapoor"}


async def seed_deployments(w: Writer, orgs: dict[str, str], uid: dict[str, str]) -> None:
    for slug, org_id in orgs.items():
        await _seed_one(w, slug, org_id, uid)


async def _rows(w: Writer, sql_table: Any, org_id: str, *order: Any) -> list[dict[str, Any]]:
    result = await w.tx.execute(select(sql_table).where(sql_table.c.org_id == org_id).order_by(*order))
    return [dict(r._mapping) for r in result]


async def _setup(w: Writer, org: Any) -> Setup:
    org_id = str(org["id"])
    d, q, b, p = (x.__table__ for x in (m.Department, m.QueryType, m.BucketRule, m.PriorityRule))
    return Setup(
        org_name=org["name"],
        confidence_bar=float(org["confidence_bar"]),
        departments={str(r["id"]): r["name"] for r in await _rows(w, d, org_id, d.c.sort)},
        query_types=await _rows(w, q, org_id, q.c.sort, q.c.name),
        bucket_rules=await _rows(w, b, org_id, b.c.sort),
        priority_rules=await _rows(w, p, org_id, p.c.sort),
    )


async def _seed_one(w: Writer, slug: str, org_id: str, uid: dict[str, str]) -> None:
    sc, tx = w.clock, w.tx
    orgs = m.Org.__table__
    org = (await tx.execute(select(orgs).where(orgs.c.id == org_id))).one()._mapping
    setup = await _setup(w, org)
    config, notes = build_default_config(setup)
    admin_name = ADMIN[slug]
    admin = Actor("user", uid[admin_name], admin_name, initials_of(admin_name))

    # The version points at the deployment and the deployment at its active version: insert the
    # deployment first, then the version, then activate it.
    deployment = await w.one(
        m.Deployment,
        {
            "org_id": org_id,
            "key": DEFAULT_KEY,
            "name": "Default",
            "description": f"Every mailbox of {org['name']}, categorised with the workspace's own taxonomy and rules.",
            "status": "active",
            "active_version_id": None,
            "created_by": admin.id,
        },
    )
    version = await w.one(
        m.DeploymentVersion,
        {
            "org_id": org_id,
            "deployment_id": deployment["id"],
            "version": 1,
            "state": "published",
            "config": config_json(config),
            "config_hash": config.config_hash(),
            "notes": " ".join(["Seeded from the workspace's setup.", *notes])[:2000],
            "created_by": None,
            "published_by": admin.id,
            "published_at": sc.now,
        },
    )
    await tx.execute(
        update(m.Deployment)
        .where(m.Deployment.id == deployment["id"])
        .values(active_version_id=version["id"])
    )
    await tx.execute(
        update(m.Mailbox).where(m.Mailbox.org_id == org_id).values(deployment_id=deployment["id"])
    )
    # The demo mail stands for mail that passed DKIM/SPF/DMARC at intake, so re-triaging a seeded
    # ticket reaches the same lane it was seeded in (an unverified sender is never auto).
    await tx.execute(
        update(m.Ticket)
        .where(m.Ticket.org_id == org_id)
        .values(deployment_id=deployment["id"], deployment_version_id=version["id"], sender_verified=True)
    )

    dataset = await w.one(
        m.EvalDataset,
        {
            "org_id": org_id,
            "deployment_id": deployment["id"],
            "name": "Seeded mail — regression set",
            "description": "Labelled from the seeded tickets: category, lane, priority and hard stops.",
        },
    )
    await w.insert(m.EvalCase, await _cases(w, org_id, dataset["id"], config))

    await audit(
        tx,
        org_id,
        actor=admin,
        action="deployment.published",
        entity="deployment",
        entity_id=deployment["id"],
        summary=f"Published Default v1 of {org['name']} (seeded)",
        data={"version": 1, "configHash": config.config_hash()},
        at=sc.now,
    )


def expected_hard_stops(
    config: DeploymentConfig, text: str, *, sentiment: str, regulatory: bool
) -> list[str]:
    """Keyword hard stops that fire on the text, plus the signals the ticket was triaged with."""
    lowered = text.lower()
    keys = [h.key for h in config.rules.hard_stops if any(k in lowered for k in h.keywords)]
    if sentiment == "vulnerable" and "vulnerable_customer" not in keys:
        keys.append("vulnerable_customer")
    if regulatory and "regulator_named" not in keys:
        keys.append("regulator_named")
    return keys


async def _cases(w: Writer, org_id: str, dataset_id: str, config: DeploymentConfig) -> list[dict[str, Any]]:
    """One case per open ticket (closed history tickets are left out), newest number first."""
    t, q, msg = m.Ticket.__table__, m.QueryType.__table__, m.Message.__table__
    result = (
        await w.tx.execute(
            select(t, q.c.name.label("query_type"))
            .join(q, q.c.id == t.c.query_type_id)
            .where(t.c.org_id == org_id, t.c.status != "closed")
            .order_by(t.c.number.desc())
        )
    ).mappings()
    tickets = [dict(r) for r in result]
    bodies: dict[str, list[str]] = {}
    for tid, body in await w.tx.execute(
        select(msg.c.ticket_id, msg.c.body)
        .where(msg.c.org_id == org_id, msg.c.direction == "inbound")
        .order_by(msg.c.sent_at, msg.c.id)
    ):
        bodies.setdefault(str(tid), []).append(body)
    key_of = {c.name: c.key for c in config.taxonomy.categories}

    cases = []
    for row in tickets:
        body = "\n\n".join(bodies.get(str(row["id"]), []))
        stops = expected_hard_stops(
            config,
            f"{row['subject']}\n{body}",
            sentiment=row["sentiment"],
            regulatory=bool(row["regulatory_flag"]),
        )
        category = key_of.get(row["query_type"], config.taxonomy.fallback)
        cases.append(
            {
                "org_id": org_id,
                "dataset_id": dataset_id,
                "input": {
                    "ticketNumber": row["number"],
                    "subject": row["subject"],
                    "from": row["from_email"],
                    "body": body,
                },
                "expected": {
                    "category": category,
                    "lane": row["lane"],
                    "priority": row["priority"].lower(),
                    "hardStop": bool(stops),
                    "hardStops": stops,
                },
                "tags": [row["lane"], category, *(["hard_stop"] if stops else [])],
                "source": "seed",
            }
        )
    return cases
