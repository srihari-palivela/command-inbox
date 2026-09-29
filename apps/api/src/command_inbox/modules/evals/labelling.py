"""The labelling queue: real mail the deployment has handled, turned into eval cases by a person.

Each candidate is shown and stored masked (names, numbers, addresses and so on are replaced), with the
sender reduced to its domain, which is all a bucket override can use. The AI's own answer is shown as a
starting point; the person's label is what is stored. A mail is labelled at most once per dataset.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.agents.config import DeploymentConfig
from command_inbox.core.audit import audit
from command_inbox.core.clock import iso_ms
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.errors import bad_request, conflict, not_found
from command_inbox.db.models import Deployment, DeploymentVersion, EvalCase, EvalDataset, Message, Ticket
from command_inbox.domain.pii import mask_pii
from command_inbox.modules.evals.schemas import LabelBody
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto

BODY_CHARS = 6000


async def _dataset_config(
    tx: AsyncSession, ctx: Ctx, dataset_id: str
) -> tuple[EvalDataset, DeploymentConfig]:
    d = (
        await tx.execute(
            select(EvalDataset).where(EvalDataset.org_id == ctx.org_id, EvalDataset.id == dataset_id)
        )
    ).scalar_one_or_none()
    if d is None or d.deployment_id is None:
        raise not_found("Eval dataset")
    dep = (await tx.execute(select(Deployment).where(Deployment.id == d.deployment_id))).scalar_one()
    versions = list(
        (
            await tx.execute(
                select(DeploymentVersion)
                .where(DeploymentVersion.deployment_id == dep.id)
                .order_by(DeploymentVersion.version.desc())
            )
        ).scalars()
    )
    # The categories to label against: the draft being worked on, else what is live.
    v = next((x for x in versions if x.state == "draft"), None) or next(
        (x for x in versions if x.id == dep.active_version_id), versions[0] if versions else None
    )
    if v is None:
        raise conflict("no_version", "This deployment has no version to label against.")
    return d, DeploymentConfig.model_validate(v.config)


def _masked(text: str) -> str:
    return mask_pii(text).text


async def _inbound_text(tx: AsyncSession, org_id: str, ticket_id: str) -> str:
    bodies = (
        await tx.execute(
            select(Message.body)
            .where(Message.org_id == org_id, Message.ticket_id == ticket_id, Message.direction == "inbound")
            .order_by(Message.sent_at)
        )
    ).scalars()
    return "\n\n".join(b for b in bodies if b)[:BODY_CHARS]


async def queue(tx: AsyncSession, ctx: Ctx, dataset_id: str, limit: int = 10) -> dto.LabellingQueueDTO:
    require(ctx, "evals.run", "label mail for evals")
    d, config = await _dataset_config(tx, ctx, dataset_id)
    labelled_ids = select(EvalCase.ticket_id).where(
        EvalCase.org_id == ctx.org_id, EvalCase.dataset_id == d.id, EvalCase.ticket_id.is_not(None)
    )
    tickets = list(
        (
            await tx.execute(
                select(Ticket)
                .where(
                    Ticket.org_id == ctx.org_id,
                    Ticket.status != "triaging",
                    Ticket.merged_into_id.is_(None),
                    Ticket.id.not_in(labelled_ids),
                )
                .order_by(Ticket.received_at.desc())
                .limit(limit)
            )
        ).scalars()
    )
    by_name = {c.name.lower(): c.key for c in config.taxonomy.categories}
    counts = dict(
        (
            await tx.execute(
                select(EvalCase.split, func.count())
                .where(
                    EvalCase.org_id == ctx.org_id, EvalCase.dataset_id == d.id, EvalCase.archived_at.is_(None)
                )
                .group_by(EvalCase.split)
            )
        )
        .tuples()
        .all()
    )
    labelled = int(
        (
            await tx.execute(
                select(func.count())
                .select_from(EvalCase)
                .where(
                    EvalCase.org_id == ctx.org_id,
                    EvalCase.dataset_id == d.id,
                    EvalCase.ticket_id.is_not(None),
                )
            )
        ).scalar_one()
    )
    candidates = []
    for t in tickets:
        hard_stop = bool(t.regulatory_flag) or (t.lane_note or "").lower().startswith("hard stop")
        candidates.append(
            dto.LabelCandidateDTO(
                ticket_id=t.id,
                number=t.number,
                subject=_masked(t.subject),
                body=_masked(await _inbound_text(tx, ctx.org_id, t.id)),
                received_at=iso_ms(t.received_at),
                suggested=dto.LabelCandidateDTOSuggested(
                    category=by_name.get((t.bucket or "").lower())
                    or by_name.get((t.subcategory or "").lower()),
                    hard_stop=hard_stop,
                    lane=t.lane,  # type: ignore[arg-type]
                ),
            )
        )
    return dto.LabellingQueueDTO(
        dataset_id=d.id,
        categories=[
            dto.LabellingQueueDTOCategories(key=c.key, name=c.name) for c in config.taxonomy.categories
        ],
        candidates=candidates,
        labelled=labelled,
        calibration=counts.get("calibration", 0),
        test=counts.get("test", 0),
    )


async def label(tx: AsyncSession, ctx: Ctx, dataset_id: str, body: LabelBody) -> dto.LabellingQueueDTO:
    require(ctx, "evals.run", "label mail for evals")
    d, config = await _dataset_config(tx, ctx, dataset_id)
    if body.category not in {c.key for c in config.taxonomy.categories}:
        raise bad_request("unknown_category", f"{body.category!r} is not a category of this deployment.")
    t = (
        await tx.execute(select(Ticket).where(Ticket.org_id == ctx.org_id, Ticket.id == body.ticket_id))
    ).scalar_one_or_none()
    if t is None:
        raise not_found("Ticket")
    domain = t.from_email.rsplit("@", 1)[-1] if "@" in (t.from_email or "") else "example.invalid"
    expected: dict[str, object] = {"category": body.category, "hardStop": body.hard_stop}
    if body.lane is not None:
        expected["lane"] = body.lane
    if body.draft_acceptable is not None:
        expected["draftAcceptable"] = body.draft_acceptable
    case = EvalCase(
        org_id=ctx.org_id,
        dataset_id=d.id,
        ticket_id=t.id,
        source="labelled",
        split=body.split,
        tags=["real-mail"],
        input={
            "subject": _masked(t.subject)[:300],
            "body": _masked(await _inbound_text(tx, ctx.org_id, t.id)),
            "fromEmail": f"customer@{domain}",
        },
        expected=expected,
    )
    try:
        async with tx.begin_nested():
            tx.add(case)
            await tx.flush()
    except IntegrityError:
        raise conflict("already_labelled", f"QRY-{t.number} is already in this dataset.") from None
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="eval.case_labelled",
        entity="eval_dataset",
        entity_id=d.id,
        summary=f"{ctx.user.name} labelled QRY-{t.number} as {body.category} for {d.name}",
        data={"ticketId": t.id, "caseId": case.id, **expected, "split": body.split},
    )
    return await queue(tx, ctx, dataset_id)
