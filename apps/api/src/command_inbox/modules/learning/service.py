"""Team updates (messages and learning cards), read state, and course completion."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from command_inbox.core.audit import audit
from command_inbox.core.clock import clock, iso_ms
from command_inbox.core.context import Ctx, actor_of
from command_inbox.core.errors import not_found, unprocessable
from command_inbox.core.outbox import publish
from command_inbox.db.models import Course, CourseCompletion, Notification, NotificationRead, Org
from command_inbox.modules.insights.jsfmt import js_round
from command_inbox.rbac.policy import require
from command_inbox.schemas import dto
from command_inbox.schemas.requests import NotificationBody


async def _headcount(tx: AsyncSession, org_id: str) -> int:
    return (await tx.execute(select(Org.headcount).where(Org.id == org_id))).scalar_one_or_none() or 0


async def learning(tx: AsyncSession, ctx: Ctx) -> dto.LearningDTO:
    team_size = max(await _headcount(tx, ctx.org_id), 1)
    notes = (
        (
            await tx.execute(
                select(Notification)
                .where(Notification.org_id == ctx.org_id)
                .order_by(Notification.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    read: set[str] = set()
    if notes:
        read = set(
            (
                await tx.execute(
                    select(NotificationRead.notification_id).where(
                        NotificationRead.org_id == ctx.org_id,
                        NotificationRead.user_id == ctx.user.id,
                        NotificationRead.notification_id.in_([n.id for n in notes]),
                    )
                )
            )
            .scalars()
            .all()
        )
    courses = (
        (
            await tx.execute(
                select(Course).where(Course.org_id == ctx.org_id).order_by(Course.sort, Course.created_at)
            )
        )
        .scalars()
        .all()
    )
    done = (
        (await tx.execute(select(CourseCompletion).where(CourseCompletion.org_id == ctx.org_id)))
        .scalars()
        .all()
    )
    notifications = [
        dto.NotificationDTO(
            id=n.id,
            kind=n.kind,
            source=n.source,
            title=n.title,
            body=n.body,
            course_id=n.course_id,
            urgent=n.urgent,
            read=n.id in read,
            at=iso_ms(n.created_at),
        )
        for n in notes
    ]
    out_courses = []
    for c in courses:
        completions = [d for d in done if d.course_id == c.id]
        # Baseline completions happened before this workspace tracked them; live completions add to it.
        pct = min(100, js_round(c.baseline_pct + len(completions) / team_size * 100))
        out_courses.append(
            dto.CourseDTO(
                id=c.id,
                key=c.key,
                title=c.title,
                minutes=c.minutes,
                cards=c.cards,
                quiz=c.quiz,
                team_completion_pct=pct,
                completed_by_me=any(d.user_id == ctx.user.id for d in completions),
            )
        )
    return dto.LearningDTO(
        notifications=notifications,
        unread=sum(1 for n in notifications if not n.read),
        team_size=team_size,
        courses=out_courses,
    )


async def mark_read(tx: AsyncSession, ctx: Ctx, ids: list[str] | None, all_: bool) -> None:
    query = select(Notification.id).where(Notification.org_id == ctx.org_id)
    if not all_:
        if not ids:
            return
        # Only this workspace's notifications: an unknown id is ignored rather than failing the batch.
        query = query.where(Notification.id.in_(ids))
    targets = (await tx.execute(query)).scalars().all()
    if not targets:
        return
    await tx.execute(
        insert(NotificationRead)
        .values([{"org_id": ctx.org_id, "notification_id": t, "user_id": ctx.user.id} for t in targets])
        .on_conflict_do_nothing()
    )


async def send_notification(tx: AsyncSession, ctx: Ctx, body: NotificationBody) -> int:
    """Manual updates to the whole support team: a message, or a learning card deck with a quiz."""
    require(ctx, "learning.send", "send a team update")
    course_id = body.course_id
    if course_id is not None:
        # A course from another workspace is not attachable (RLS hides it; say so instead of a bad FK).
        exists = (
            await tx.execute(select(Course.id).where(Course.org_id == ctx.org_id, Course.id == course_id))
        ).scalar_one_or_none()
        if exists is None:
            raise not_found("Course")
    if body.kind == "learning" and not course_id:
        course_id = (
            await tx.execute(
                select(Course.id).where(Course.org_id == ctx.org_id).order_by(Course.sort).limit(1)
            )
        ).scalar_one_or_none()
    if body.kind == "learning" and not course_id:
        raise unprocessable("no_course", "Attach a course to send a learning card.")
    tx.add(
        Notification(
            org_id=ctx.org_id,
            kind=body.kind,
            source=f"{ctx.user.name} · manual",
            title=body.title,
            body=body.body
            if body.body is not None
            else "Sent to all customer support people in this workspace.",
            course_id=course_id,
            urgent=False,
            created_by=ctx.user.id,
            created_at=clock.now(),
        )
    )
    await tx.flush()
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="notification.sent",
        entity="notification",
        summary=f"Team update sent: {body.title}",
    )
    await publish(tx, ctx.org_id, "notification.created", {})
    return await _headcount(tx, ctx.org_id)


async def complete_course(tx: AsyncSession, ctx: Ctx, course_id: str, answers: list[int]) -> tuple[int, int]:
    c = (
        await tx.execute(select(Course).where(Course.org_id == ctx.org_id, Course.id == course_id))
    ).scalar_one_or_none()
    if c is None:
        raise not_found("Course")
    quiz = c.quiz or []
    score = sum(1 for i, q in enumerate(quiz) if i < len(answers) and answers[i] == q.get("correct"))
    total = len(quiz)
    await tx.execute(
        insert(CourseCompletion)
        .values(org_id=ctx.org_id, course_id=course_id, user_id=ctx.user.id, score=score, total=total)
        .on_conflict_do_update(
            index_elements=[CourseCompletion.org_id, CourseCompletion.course_id, CourseCompletion.user_id],
            set_={"score": score, "total": total, "completed_at": clock.now()},
        )
    )
    related = (
        (
            await tx.execute(
                select(Notification.id).where(
                    Notification.org_id == ctx.org_id, Notification.course_id == course_id
                )
            )
        )
        .scalars()
        .all()
    )
    if related:
        await tx.execute(
            insert(NotificationRead)
            .values([{"org_id": ctx.org_id, "notification_id": r, "user_id": ctx.user.id} for r in related])
            .on_conflict_do_nothing()
        )
    await audit(
        tx,
        ctx.org_id,
        actor=actor_of(ctx),
        action="course.completed",
        entity="course",
        entity_id=course_id,
        summary=f'{ctx.user.name} completed "{c.title}" ({score}/{total})',
    )
    await publish(tx, ctx.org_id, "learning.updated", {"courseId": course_id})
    return score, total
