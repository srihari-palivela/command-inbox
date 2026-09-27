import type { CourseDTO, LearningDTO, NotificationBody, NotificationDTO, SettingsBody } from '@ci/contracts';
import { and, asc, desc, eq, inArray, sql } from 'drizzle-orm';
import type { Tx } from '../../db/client.js';
import * as s from '../../db/schema.js';
import { audit } from '../../platform/audit.js';
import { clock } from '../../platform/clock.js';
import { actorOf, type Ctx } from '../../platform/context.js';
import { notFound, unprocessable } from '../../platform/errors.js';
import { publish } from '../../platform/outbox.js';
import { requireCap } from '../../platform/rbac.js';

export async function learning(tx: Tx, ctx: Ctx): Promise<LearningDTO> {
  const [org] = await tx.select().from(s.orgs).where(eq(s.orgs.id, ctx.orgId));
  const teamSize = Math.max(org?.headcount ?? 1, 1);
  const notes = await tx.select().from(s.notifications).where(eq(s.notifications.orgId, ctx.orgId)).orderBy(desc(s.notifications.createdAt)).limit(50);
  const reads = notes.length
    ? await tx
        .select({ id: s.notificationReads.notificationId })
        .from(s.notificationReads)
        .where(and(eq(s.notificationReads.orgId, ctx.orgId), eq(s.notificationReads.userId, ctx.user.id), inArray(s.notificationReads.notificationId, notes.map((n) => n.id))))
    : [];
  const read = new Set(reads.map((r) => r.id));
  const courses = await tx.select().from(s.courses).where(eq(s.courses.orgId, ctx.orgId)).orderBy(asc(s.courses.sort), asc(s.courses.createdAt));
  const done = await tx.select().from(s.courseCompletions).where(eq(s.courseCompletions.orgId, ctx.orgId));
  const notifications: NotificationDTO[] = notes.map((n) => ({
    id: n.id,
    kind: n.kind as NotificationDTO['kind'],
    source: n.source,
    title: n.title,
    body: n.body,
    courseId: n.courseId,
    urgent: n.urgent,
    read: read.has(n.id),
    at: n.createdAt.toISOString(),
  }));
  return {
    notifications,
    unread: notifications.filter((n) => !n.read).length,
    teamSize,
    courses: courses.map((c): CourseDTO => {
      const completions = done.filter((d) => d.courseId === c.id);
      // Baseline completions happened before this workspace tracked them; live completions add to it.
      const pct = Math.min(100, Math.round(c.baselinePct + (completions.length / teamSize) * 100));
      return {
        id: c.id,
        key: c.key,
        title: c.title,
        minutes: c.minutes,
        cards: c.cards,
        quiz: c.quiz,
        teamCompletionPct: pct,
        completedByMe: completions.some((d) => d.userId === ctx.user.id),
      };
    }),
  };
}

export async function markRead(tx: Tx, ctx: Ctx, ids: string[] | undefined, all: boolean): Promise<void> {
  const targets = all
    ? (await tx.select({ id: s.notifications.id }).from(s.notifications).where(eq(s.notifications.orgId, ctx.orgId))).map((r) => r.id)
    : ids ?? [];
  if (!targets.length) return;
  await tx
    .insert(s.notificationReads)
    .values(targets.map((notificationId) => ({ orgId: ctx.orgId, notificationId, userId: ctx.user.id })))
    .onConflictDoNothing();
}

/** Manual updates to the whole support team: a message, or a learning card deck with a quiz. */
export async function sendNotification(tx: Tx, ctx: Ctx, body: NotificationBody): Promise<{ recipients: number }> {
  requireCap(ctx, 'learning.send', 'send a team update');
  let courseId = body.courseId ?? null;
  if (body.kind === 'learning' && !courseId) {
    const [first] = await tx.select().from(s.courses).where(eq(s.courses.orgId, ctx.orgId)).orderBy(asc(s.courses.sort)).limit(1);
    courseId = first?.id ?? null;
  }
  if (body.kind === 'learning' && !courseId) throw unprocessable('no_course', 'Attach a course to send a learning card.');
  await tx.insert(s.notifications).values({
    orgId: ctx.orgId,
    kind: body.kind,
    source: `${ctx.user.name} · manual`,
    title: body.title,
    body: body.body ?? 'Sent to all customer support people in this workspace.',
    courseId,
    urgent: false,
    createdBy: ctx.user.id,
  });
  const [org] = await tx.select().from(s.orgs).where(eq(s.orgs.id, ctx.orgId));
  await audit(tx, ctx.orgId, { actor: actorOf(ctx), action: 'notification.sent', entity: 'notification', summary: `Team update sent: ${body.title}` });
  await publish(tx, ctx.orgId, 'notification.created', {});
  return { recipients: org?.headcount ?? 0 };
}

export async function completeCourse(tx: Tx, ctx: Ctx, courseId: string, answers: number[]): Promise<{ score: number; total: number }> {
  const [c] = await tx.select().from(s.courses).where(and(eq(s.courses.orgId, ctx.orgId), eq(s.courses.id, courseId)));
  if (!c) throw notFound('Course');
  const score = c.quiz.filter((q, i) => answers[i] === q.correct).length;
  await tx
    .insert(s.courseCompletions)
    .values({ orgId: ctx.orgId, courseId, userId: ctx.user.id, score, total: c.quiz.length })
    .onConflictDoUpdate({
      target: [s.courseCompletions.orgId, s.courseCompletions.courseId, s.courseCompletions.userId],
      set: { score, total: c.quiz.length, completedAt: clock.now() },
    });
  const related = await tx.select({ id: s.notifications.id }).from(s.notifications).where(and(eq(s.notifications.orgId, ctx.orgId), eq(s.notifications.courseId, courseId)));
  if (related.length) {
    await tx
      .insert(s.notificationReads)
      .values(related.map((r) => ({ orgId: ctx.orgId, notificationId: r.id, userId: ctx.user.id })))
      .onConflictDoNothing();
  }
  await audit(tx, ctx.orgId, { actor: actorOf(ctx), action: 'course.completed', entity: 'course', entityId: courseId, summary: `${ctx.user.name} completed "${c.title}" (${score}/${c.quiz.length})` });
  return { score, total: c.quiz.length };
}

export async function updateSettings(tx: Tx, ctx: Ctx, body: SettingsBody): Promise<void> {
  const [cur] = await tx
    .select()
    .from(s.userSettings)
    .where(and(eq(s.userSettings.orgId, ctx.orgId), eq(s.userSettings.userId, ctx.user.id)));
  const prefs = { ...(cur?.prefs ?? {}), ...(body.prefs ?? {}) };
  await tx
    .insert(s.userSettings)
    .values({ orgId: ctx.orgId, userId: ctx.user.id, prefs, signature: body.signature ?? cur?.signature ?? '' })
    .onConflictDoUpdate({
      target: [s.userSettings.orgId, s.userSettings.userId],
      set: { prefs, signature: body.signature ?? cur?.signature ?? sql`${s.userSettings.signature}` },
    });
}
