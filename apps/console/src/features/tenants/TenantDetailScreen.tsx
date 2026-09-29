/**
 * One tenant: its lifecycle actions, provisioning progress, first-admin invitations, profile, limits,
 * data keys and recent platform audit. The server says which actions this operator may take now.
 */
import type { TenantAction, TenantDetailDTO } from '@ci/contracts';
import {
  Button,
  Card,
  EmptyState,
  Input,
  Loadable,
  Modal,
  Page,
  PageHeader,
  Skeleton,
  TextArea,
} from '@web/ui';
import { useState, type ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { count, INVITATION_STATE, KEY_STATE, STEP_STATE, TEAM_ROLE, when } from '../../lib/presentation';
import { useLifecycle, useReinvite, useTenant, type LifecycleAction } from '../../lib/queries';
import { minorToMajor } from '../../lib/tenant-form';
import { css as s, ProblemAlert, ToneTag } from '../../ui/bits';
import { AuditTable } from '../audit/AuditTable';
import { StatusTag, StepIcon } from './bits';

export default function TenantDetailScreen() {
  const { id = '' } = useParams();
  const q = useTenant(id);
  return (
    <Page>
      <div className={s.crumbs}>
        <Link to="/tenants">Tenants</Link>
        <span aria-hidden>/</span>
        <span>{q.data?.name ?? '…'}</span>
      </div>
      <Loadable
        query={q}
        skeleton={
          <div style={{ display: 'grid', gap: 12 }}>
            <Skeleton h={30} w="35%" />
            <Skeleton h={180} />
            <Skeleton h={140} />
          </div>
        }
      >
        {(t) => <TenantDetail t={t} />}
      </Loadable>
    </Page>
  );
}

function TenantDetail({ t }: { t: TenantDetailDTO }) {
  const [dialog, setDialog] = useState<Exclude<TenantAction, 'provision'> | null>(null);
  const lifecycle = useLifecycle(t.id);
  const has = (a: TenantAction) => t.actions.includes(a);
  const failed = t.provisioning === 'failed';
  const open = (d: typeof dialog) => {
    lifecycle.reset();
    setDialog(d);
  };

  return (
    <>
      <div className="rise">
        <PageHeader
          title={
            <span className={s.row} style={{ gap: 10 }}>
              {t.name}
              <StatusTag status={t.status} />
            </span>
          }
          subtitle={
            <>
              {t.legalName} · <span className="mono">{t.region}</span> · {t.plan} plan ·{' '}
              <span className="mono">{t.slug}</span>
            </>
          }
          actions={
            t.actions.length ? (
              <>
                {has('provision') && (
                  <Button
                    variant="dark"
                    loading={lifecycle.isPending && lifecycle.variables?.action === 'provision'}
                    onClick={() => lifecycle.mutate({ action: 'provision' })}
                  >
                    {failed ? 'Retry provisioning' : 'Provision'}
                  </Button>
                )}
                {has('reinvite') && (
                  <Button onClick={() => open('reinvite')}>
                    {t.invitations.length ? 'Re-invite first admin' : 'Invite first admin'}
                  </Button>
                )}
                {has('resume') && (
                  <Button variant="dark" onClick={() => open('resume')}>
                    Resume
                  </Button>
                )}
                {has('suspend') && <Button onClick={() => open('suspend')}>Suspend</Button>}
                {has('archive') && (
                  <Button variant="danger" onClick={() => open('archive')}>
                    Archive
                  </Button>
                )}
              </>
            ) : undefined
          }
        />
      </div>

      {lifecycle.error && !dialog ? (
        <div style={{ marginBottom: 13 }}>
          <ProblemAlert error={lifecycle.error} />
        </div>
      ) : null}

      <Steps t={t} />
      <Invitations t={t} />

      <div className={s.two}>
        <Card title="Profile" className={s.card}>
          <dl className={s.meta}>
            <dt>Locale</dt>
            <dd className="mono">{t.locale}</dd>
            <dt>Currency</dt>
            <dd className="mono">{t.currency}</dd>
            <dt>Time zone</dt>
            <dd className="mono">{t.timeZone}</dd>
            <dt>Data residency</dt>
            <dd>{t.dataResidency || <span className={s.muted}>Not stated</span>}</dd>
            <dt>Support email</dt>
            <dd>{t.supportEmail || <span className={s.muted}>Not set</span>}</dd>
            <dt>Email domains</dt>
            <dd>
              <span className={s.chips}>
                {t.emailDomains.map((d) => (
                  <span key={d} className={s.chipMono}>
                    {d}
                  </span>
                ))}
              </span>
            </dd>
            <dt>Single sign-on</dt>
            <dd>
              {t.ssoIdpAlias ? (
                <span className="mono">{t.ssoIdpAlias}</span>
              ) : (
                <span className={s.muted}>Not connected</span>
              )}
            </dd>
            <dt>Created</dt>
            <dd>{when(t.createdAt)}</dd>
            <dt>Status since</dt>
            <dd>{when(t.statusChangedAt)}</dd>
          </dl>
        </Card>
        <Card
          title="Limits"
          meta={`${count(t.members)} members · ${count(t.mailboxes)} mailboxes today`}
          className={s.card}
        >
          <dl className={s.meta}>
            <dt>Mailboxes</dt>
            <dd>{count(t.limits.mailboxes)}</dd>
            <dt>Seats</dt>
            <dd>{count(t.limits.seats)}</dd>
            <dt>Monthly mail</dt>
            <dd>{count(t.limits.monthlyMail)}</dd>
            <dt>Model spend cap</dt>
            <dd>{money(t.limits.modelSpendCapMinor, t.currency, t.locale)} a month</dd>
            <dt>Storage</dt>
            <dd>{count(t.limits.storageGb)} GB</dd>
            <dt>API requests</dt>
            <dd>{count(t.limits.apiPerMinute)} a minute</dd>
          </dl>
        </Card>
      </div>

      <Keys t={t} />

      <Card
        title="Recent platform audit"
        actions={<Link to={`/audit?tenantId=${t.id}`}>Full audit log</Link>}
        flush
        className={s.card}
      >
        {t.audit.length ? (
          <AuditTable events={t.audit} showTenant={false} />
        ) : (
          <EmptyState title="Nothing recorded yet" />
        )}
      </Card>

      <ReasonDialog
        t={t}
        action={dialog === 'reinvite' ? null : dialog}
        onClose={() => {
          lifecycle.reset();
          setDialog(null);
        }}
        mutation={lifecycle}
      />
      {dialog === 'reinvite' && <ReinviteDialog t={t} onClose={() => setDialog(null)} />}
    </>
  );
}

function money(minor: number, currency: string, locale: string) {
  try {
    return new Intl.NumberFormat(locale, { style: 'currency', currency, maximumFractionDigits: 0 }).format(
      minorToMajor(minor),
    );
  } catch {
    return `${count(minorToMajor(minor))} ${currency}`;
  }
}

function Steps({ t }: { t: TenantDetailDTO }) {
  const running = t.status === 'provisioning' && t.provisioning !== 'failed';
  return (
    <Card
      title="Provisioning"
      meta={running ? 'Refreshing every 2 seconds' : undefined}
      flush
      className={s.card}
    >
      {t.steps.length ? (
        <div className={s.scroll}>
          <table className={s.table} aria-label="Provisioning steps">
            <thead>
              <tr>
                <th scope="col">Step</th>
                <th scope="col">State</th>
                <th scope="col" className={s.num}>
                  Attempts
                </th>
                <th scope="col">Detail</th>
                <th scope="col">Updated</th>
              </tr>
            </thead>
            <tbody>
              {t.steps.map((st) => (
                <tr key={st.step}>
                  <td>
                    <span className={s.row} style={{ flexWrap: 'nowrap' }}>
                      <StepIcon state={st.state} />
                      <span style={{ fontWeight: 500 }}>{st.label}</span>
                    </span>
                  </td>
                  <td>
                    <ToneTag tone={STEP_STATE[st.state]} />
                  </td>
                  <td className={s.num}>{st.attempts}</td>
                  <td style={{ color: st.state === 'failed' ? 'var(--bad-text)' : 'var(--text-2)' }}>
                    {st.detail || <span className={s.muted}>—</span>}
                  </td>
                  <td className={s.nowrap}>{when(st.updatedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState
          title="No provisioning record"
          text="This tenant was set up before the console tracked provisioning."
        />
      )}
    </Card>
  );
}

function Invitations({ t }: { t: TenantDetailDTO }) {
  return (
    <Card title="First-admin invitations" flush className={s.card}>
      {t.invitations.length ? (
        <div className={s.scroll}>
          <table className={s.table} aria-label="Invitations">
            <thead>
              <tr>
                <th scope="col">Invitee</th>
                <th scope="col">Role</th>
                <th scope="col">State</th>
                <th scope="col">Sent</th>
                <th scope="col">Expires</th>
                <th scope="col">Invited by</th>
              </tr>
            </thead>
            <tbody>
              {t.invitations.map((inv) => (
                <tr key={inv.id}>
                  <td>
                    <div style={{ fontWeight: 500 }}>{inv.name}</div>
                    <div className={s.sub}>{inv.email}</div>
                  </td>
                  <td>{TEAM_ROLE[inv.role]}</td>
                  <td>
                    <ToneTag tone={INVITATION_STATE[inv.state]} />
                  </td>
                  <td className={s.nowrap}>
                    {inv.sentAt ? when(inv.sentAt) : <span className={s.muted}>Not sent yet</span>}
                    {inv.sendCount > 1 && <div className={s.sub}>Sent {inv.sendCount} times</div>}
                  </td>
                  <td className={s.nowrap}>{when(inv.expiresAt)}</td>
                  <td>{inv.invitedBy}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState
          title="No invitation yet"
          text="The first admin is invited when provisioning reaches its last step."
        />
      )}
    </Card>
  );
}

function Keys({ t }: { t: TenantDetailDTO }) {
  return (
    <Card title="Data keys" meta="Envelope keys that encrypt this tenant's data" flush className={s.card}>
      {t.keys.length ? (
        <div className={s.scroll}>
          <table className={s.table} aria-label="Data keys">
            <thead>
              <tr>
                <th scope="col">Version</th>
                <th scope="col">State</th>
                <th scope="col">Wrapped by (KEK)</th>
                <th scope="col">Created</th>
              </tr>
            </thead>
            <tbody>
              {t.keys.map((k) => (
                <tr key={k.version}>
                  <td className={s.mono}>v{k.version}</td>
                  <td>
                    <ToneTag tone={KEY_STATE[k.state]} />
                  </td>
                  <td className={s.mono}>{k.kekRef}</td>
                  <td className={s.nowrap}>{when(k.createdAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="No data key yet" text="Provisioning creates the tenant's first data key." />
      )}
    </Card>
  );
}

const REASON_COPY: Record<
  Exclude<LifecycleAction, 'provision'>,
  { verb: string; text: ReactNode; danger?: boolean }
> = {
  suspend: {
    verb: 'Suspend',
    text: 'Everyone at the bank is signed out and the workspace stays closed until it is resumed.',
    danger: true,
  },
  resume: { verb: 'Resume', text: 'The workspace returns to the state it was in before it was suspended.' },
  archive: {
    verb: 'Archive',
    text: 'Everyone at the bank is signed out and the tenant is closed for good. It cannot be resumed.',
    danger: true,
  },
};

function ReasonDialog({
  t,
  action,
  onClose,
  mutation,
}: {
  t: TenantDetailDTO;
  action: Exclude<LifecycleAction, 'provision'> | null;
  onClose: () => void;
  mutation: ReturnType<typeof useLifecycle>;
}) {
  const [reason, setReason] = useState('');
  const copy = action ? REASON_COPY[action] : null;
  const close = () => {
    setReason('');
    onClose();
  };
  return (
    <Modal
      open={!!copy}
      onClose={close}
      title={copy ? `${copy.verb} ${t.name}?` : ''}
      width={480}
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button
            variant={copy?.danger ? 'danger' : 'dark'}
            disabled={!reason.trim()}
            loading={mutation.isPending}
            onClick={() => action && mutation.mutate({ action, reason: reason.trim() }, { onSuccess: close })}
          >
            {copy?.verb}
          </Button>
        </>
      }
    >
      <div className={s.stack}>
        <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-2)' }}>{copy?.text}</p>
        <label className={s.field}>
          <span className={s.fieldLabel}>Reason</span>
          <TextArea
            value={reason}
            data-autofocus
            maxLength={500}
            rows={3}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Recorded in the platform audit log and the bank's own audit log."
          />
        </label>
        <ProblemAlert error={mutation.error} />
      </div>
    </Modal>
  );
}

/** Mounted only while open, so it starts from the latest invitee each time. */
function ReinviteDialog({ t, onClose }: { t: TenantDetailDTO; onClose: () => void }) {
  const reinvite = useReinvite(t.id);
  const [name, setName] = useState(t.invitations[0]?.name ?? '');
  const [email, setEmail] = useState(t.invitations[0]?.email ?? '');
  const close = () => {
    reinvite.reset();
    onClose();
  };
  return (
    <Modal
      open
      onClose={close}
      title="Invite the first admin"
      subtitle={`A new invitation link goes to this address. Earlier links for ${t.name} stop working.`}
      width={480}
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button
            variant="dark"
            disabled={!name.trim() || !email.trim()}
            loading={reinvite.isPending}
            onClick={() => reinvite.mutate({ name: name.trim(), email: email.trim() }, { onSuccess: close })}
          >
            Send invitation
          </Button>
        </>
      }
    >
      <form
        className={s.stack}
        onSubmit={(e) => {
          e.preventDefault();
        }}
      >
        <label className={s.field}>
          <span className={s.fieldLabel}>Name</span>
          <Input value={name} data-autofocus maxLength={120} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className={s.field}>
          <span className={s.fieldLabel}>Work email</span>
          <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <ProblemAlert error={reinvite.error} />
      </form>
    </Modal>
  );
}
