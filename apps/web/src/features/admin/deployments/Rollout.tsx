/**
 * Rollout of one version: draft → shadow (runs beside the live version, never acted on) → canary (a share
 * of mail) → published. What blocks canary and publish comes from the server's publish check: a passing
 * eval run on this exact configuration, four-eyes (whoever edited it cannot put it in front of customers)
 * and, for a workspace with a single admin, an explicit and audited acknowledgement.
 */
import type { DeploymentDetailDTO, DeploymentVersionDTO } from '@ci/contracts';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../../lib/api';
import { keys } from '../../../lib/queries';
import { Button, Card, cx, Input } from '../../../ui';
import { Check, Confirm, Notice, ProblemAlert, useCaps, useInlineAction } from '../bits';
import s from '../admin.module.css';

type Target = 'shadow' | 'canary' | 'published';

export function Rollout({ d, v }: { d: DeploymentDetailDTO; v: DeploymentVersionDTO }) {
  const can = useCaps();
  const canPublish = can('deployment.publish');
  const canEdit = can('deployment.edit');
  const [ack, setAck] = useState(false);
  const [percent, setPercent] = useState(String(v.canaryPercent ?? 10));
  const [confirm, setConfirm] = useState<'discard' | 'withdraw' | 'restore' | null>(null);
  const invalidate = [keys.deployments, keys.evals];

  const promote = useInlineAction(
    (to: Target) =>
      api.post<DeploymentVersionDTO>(`/v1/deployments/${d.id}/versions/${v.id}/promote`, {
        to,
        canaryPercent: to === 'canary' ? Number(percent) : undefined,
        acknowledgeSingleAdmin: ack,
      }),
    {
      invalidate,
      success: (r, to) =>
        to === 'published'
          ? `Published v${r.version} of ${d.name}. The previous version is retired and can be restored.`
          : to === 'canary'
            ? `v${r.version} now takes ${r.canaryPercent}% of ${d.name}’s mail.`
            : `v${r.version} runs in shadow: decisions are recorded, never acted on.`,
    },
  );
  const withdraw = useInlineAction(
    () => api.post<DeploymentVersionDTO>(`/v1/deployments/${d.id}/versions/${v.id}/withdraw`),
    {
      invalidate,
      success: (r) => (v.state === 'draft' ? `Draft v${r.version} discarded.` : `v${r.version} withdrawn.`),
    },
  );
  const restore = useInlineAction(
    () => api.post<DeploymentDetailDTO>(`/v1/deployments/${d.id}/rollback`, { versionId: v.id }),
    { invalidate, success: `Rolled ${d.name} back to v${v.version}.` },
  );

  const check = v.publishCheck;
  const title = `Rollout — v${v.version}`;

  if (v.state === 'published')
    return (
      <Card className={s.card} title={title}>
        <Notice tone="ok">
          v{v.version} is live for {d.mailboxes.length} mailbox{d.mailboxes.length === 1 ? '' : 'es'}. To
          change it, start a new draft; to undo a publish, restore an earlier version from the history.
        </Notice>
      </Card>
    );

  if (v.state === 'retired') {
    const restorable = !!v.publishedAt;
    return (
      <Card className={s.card} title={title}>
        <div className={s.stack}>
          <p className={s.body}>
            {restorable
              ? `v${v.version} was live before and is retired now. Restoring it re-publishes this exact configuration and retires the current version.`
              : `v${v.version} was withdrawn before it was ever published, so it cannot be restored. Start a new draft instead.`}
          </p>
          {restorable &&
            (canPublish ? (
              <div className={s.actions}>
                <Button variant="dark" onClick={() => setConfirm('restore')}>
                  Restore v{v.version}
                </Button>
              </div>
            ) : (
              <p className={s.muted}>Only an admin can roll back a deployment.</p>
            ))}
          <Confirm
            open={confirm === 'restore'}
            title={`Restore v${v.version}?`}
            confirmLabel={`Restore v${v.version}`}
            onClose={() => setConfirm(null)}
            pending={restore.isPending}
            error={restore.error}
            onConfirm={() => restore.mutate(undefined, { onSuccess: () => setConfirm(null) })}
          >
            {d.activeVersion !== null ? `v${d.activeVersion} stops taking mail and is retired. ` : ''}
            New mail to {d.name} is triaged with v{v.version} again. This is audited.
          </Confirm>
        </div>
      </Card>
    );
  }

  const blockers = check?.blockers ?? [];
  const needsAck = !!check?.requiresSingleAdminAck;
  const gatedReady = !!check?.ready && (!needsAck || ack);
  const pct = Number(percent);
  const pctOk = Number.isInteger(pct) && pct >= 1 && pct <= 99;
  const shadowTaken = d.shadowVersion !== null && v.state !== 'shadow';
  const canaryTaken = !!d.canary && v.state !== 'canary';

  return (
    <Card
      className={s.card}
      title={title}
      meta={v.state === 'canary' ? `${v.canaryPercent}% of mail` : undefined}
    >
      <div className={s.stack}>
        <section aria-label="Publish check" className={s.stack} style={{ gap: 8 }}>
          <div className={s.fieldLabel}>Before v{v.version} can take real mail (canary or publish)</div>
          {blockers.length ? (
            <ul className={s.blockers}>
              {blockers.map((b) => (
                <li key={b} className={s.blocker}>
                  <span className={s.blockerMark} aria-hidden>
                    ✕
                  </span>
                  {b}
                </li>
              ))}
            </ul>
          ) : (
            <ul className={s.blockers}>
              <li className={s.blocker}>
                <span className={cx(s.blockerMark, s.okMark)} aria-hidden>
                  ✓
                </span>
                <span>
                  A passed eval run covers this exact configuration
                  {check?.evalRunId && (
                    <>
                      {' '}
                      — <Link to={`/admin/evals/runs/${check.evalRunId}`}>see the run</Link>
                    </>
                  )}
                  .
                </span>
              </li>
            </ul>
          )}
          {needsAck && (
            <div className={s.itemCard}>
              <p className={s.body}>
                You made the last edit to this version and you are the only admin, so nobody else can review
                it.
              </p>
              <Check checked={ack} onChange={setAck} disabled={!canPublish}>
                I am the only admin and put this version in front of customers without a second review. This
                acknowledgement is recorded in the audit log.
              </Check>
            </div>
          )}
        </section>

        {canPublish ? (
          <div className={s.actions}>
            {v.state === 'draft' && (
              <Button
                onClick={() => promote.mutate('shadow')}
                loading={promote.isPending && promote.variables === 'shadow'}
                disabled={shadowTaken}
                title={
                  shadowTaken
                    ? `v${d.shadowVersion} is already in shadow. Withdraw it first.`
                    : 'Run beside the live version; decisions are recorded, never acted on.'
                }
              >
                Put in shadow
              </Button>
            )}
            <label className={s.row} style={{ gap: 6 }}>
              <span className={s.fieldLabel}>Canary share (%)</span>
              <Input
                type="number"
                min={1}
                max={99}
                className={s.percent}
                value={percent}
                onChange={(e) => setPercent(e.target.value)}
                aria-invalid={!pctOk || undefined}
              />
            </label>
            <Button
              onClick={() => promote.mutate('canary')}
              loading={promote.isPending && promote.variables === 'canary'}
              disabled={!gatedReady || !pctOk || canaryTaken}
              title={
                canaryTaken ? `v${d.canary?.version} is already in canary. Withdraw it first.` : undefined
              }
            >
              {v.state === 'canary' ? 'Change canary share' : 'Start canary'}
            </Button>
            <Button
              variant="dark"
              onClick={() => promote.mutate('published')}
              loading={promote.isPending && promote.variables === 'published'}
              disabled={!gatedReady}
            >
              Publish v{v.version}
            </Button>
            {(v.state === 'shadow' || v.state === 'canary') && (
              <Button variant="ghost" onClick={() => setConfirm('withdraw')}>
                Withdraw
              </Button>
            )}
          </div>
        ) : (
          <p className={s.muted}>
            Only an admin can put a version in shadow, canary or publish it
            {canEdit ? '. You can still edit the draft and run the evals.' : '.'}
          </p>
        )}
        {canPublish && !gatedReady && check?.ready && needsAck && (
          <p className={s.muted}>Tick the acknowledgement to enable canary and publish.</p>
        )}
        <ProblemAlert error={promote.error} />

        {v.state === 'draft' && canEdit && (
          <div className={s.actions} style={{ borderTop: '1px solid var(--line-soft)', paddingTop: 10 }}>
            <Button size="sm" variant="ghost" onClick={() => setConfirm('discard')}>
              Discard draft
            </Button>
          </div>
        )}
        <Confirm
          open={confirm === 'discard' || confirm === 'withdraw'}
          title={confirm === 'discard' ? `Discard draft v${v.version}?` : `Withdraw v${v.version}?`}
          confirmLabel={confirm === 'discard' ? 'Discard draft' : 'Withdraw'}
          danger
          onClose={() => setConfirm(null)}
          pending={withdraw.isPending}
          error={withdraw.error}
          onConfirm={() => withdraw.mutate(undefined, { onSuccess: () => setConfirm(null) })}
        >
          {confirm === 'discard'
            ? 'The draft is retired and kept for the record. Its eval runs stay as evidence.'
            : `v${v.version} stops running in ${v.state} and is retired. The live version keeps taking all mail.`}
        </Confirm>
      </div>
    </Card>
  );
}
