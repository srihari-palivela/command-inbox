/**
 * Members and invitations. Everyone holds exactly one role in a workspace; nobody changes their own role
 * or removes themselves, and a workspace always keeps at least one admin (the server enforces all three).
 * Removing a member signs them out of this workspace at once. Needs `members.manage`.
 */
import type { InvitationDTO, MemberDTO, Role } from '@ci/contracts';
import { useState } from 'react';
import { api, ApiError, newIdempotencyKey } from '../../lib/api';
import { ago, shortDate } from '../../lib/format';
import { TEAM_ROLE_LABEL } from '../../lib/presentation';
import { keys, useInvitations, useMembers } from '../../lib/queries';
import { Avatar, Button, Card, EmptyState, Input, Loadable, Page, PageHeader, Skeleton } from '../../ui';
import { Confirm, isForbidden, NoAccess, ProblemAlert, Select, Tone, useInlineAction } from './bits';
import { INVITATION_STATE, ROLES } from './model';
import { ScimCard } from './ScimCard';
import s from './admin.module.css';

const TITLE = 'Members';
const SUB =
  'Who belongs to this workspace and in which role. Each person has exactly one role: Staff, Team lead or Admin.';

const ROLE_HINT: Record<Role, string> = {
  staff: 'works tickets, approves as maker',
  lead: 'also checks, reassigns, sees insights',
  admin: 'also configures, publishes, manages people',
};

/** The server's own words, with the fix spelled out for the one error people can do something about. */
function explain(err: unknown): string | null {
  if (err instanceof ApiError && err.code === 'last_admin')
    return 'Make someone else an Admin first, then change or remove this person.';
  return null;
}

export default function MembersScreen() {
  const members = useMembers();
  return (
    <Page>
      <div className="rise">
        <PageHeader title={TITLE} subtitle={SUB} />
      </div>
      {isForbidden(members.error) ? (
        <NoAccess error={members.error} what="members" who="Members and roles are managed by admins." />
      ) : (
        <>
          <Loadable query={members} skeleton={<Skeleton h={260} />}>
            {(list) => <MemberTable list={list} />}
          </Loadable>
          <Invitations />
          <ScimCard />
        </>
      )}
    </Page>
  );
}

function MemberTable({ list }: { list: MemberDTO[] }) {
  const [change, setChange] = useState<{ m: MemberDTO; role: Role } | null>(null);
  const [removing, setRemoving] = useState<MemberDTO | null>(null);
  const admins = list.filter((m) => m.role === 'admin').length;
  const setRole = useInlineAction(
    (v: { m: MemberDTO; role: Role }) =>
      api.put<MemberDTO>(`/v1/members/${v.m.user.id}/role`, { role: v.role }),
    {
      invalidate: [keys.members, keys.people],
      success: (r) => `${r.user.name} is now ${TEAM_ROLE_LABEL[r.role]}.`,
    },
  );
  const remove = useInlineAction(
    (m: MemberDTO) => api.del<{ ok: boolean; revokedSessions: number }>(`/v1/members/${m.user.id}`),
    {
      invalidate: [keys.members, keys.people],
      success: (r, m) =>
        `${m.user.name} was removed${r.revokedSessions ? ` and signed out of ${r.revokedSessions} session${r.revokedSessions === 1 ? '' : 's'}` : ''}.`,
    },
  );

  return (
    <Card
      className={s.card}
      title="People"
      meta={`${list.length} members · ${admins} admin${admins === 1 ? '' : 's'}`}
      flush
    >
      <div className={s.scroll}>
        <table className={s.table} aria-label="Members">
          <thead>
            <tr>
              <th scope="col">Person</th>
              <th scope="col">Title</th>
              <th scope="col">Role</th>
              <th scope="col">Last sign-in</th>
              <th scope="col">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {list.map((m) => (
              <tr key={m.user.id}>
                <td>
                  <span className={s.row}>
                    <Avatar initials={m.user.initials} size={28} />
                    <span>
                      <span className={s.name}>
                        {m.user.name}
                        {m.isMe && <span className={s.muted}> (you)</span>}
                      </span>
                      <div className={s.sub}>{m.user.email}</div>
                    </span>
                  </span>
                </td>
                <td className={s.muted}>{m.title || '—'}</td>
                <td>
                  <Select
                    aria-label={`Role for ${m.user.name}`}
                    value={m.role}
                    disabled={m.isMe}
                    title={m.isMe ? 'You cannot change your own role. Ask another admin.' : undefined}
                    onChange={(e) => {
                      setRole.reset();
                      setChange({ m, role: e.target.value as Role });
                    }}
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {TEAM_ROLE_LABEL[r]}
                      </option>
                    ))}
                  </Select>
                  {m.isMe && <div className={s.sub}>Another admin can change your role.</div>}
                </td>
                <td className={s.muted}>{m.lastLoginAt ? ago(m.lastLoginAt) : 'never'}</td>
                <td>
                  {!m.isMe && (
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Remove ${m.user.name}`}
                      onClick={() => {
                        remove.reset();
                        setRemoving(m);
                      }}
                    >
                      Remove
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Confirm
        open={!!change}
        title={change ? `Make ${change.m.user.name} ${TEAM_ROLE_LABEL[change.role]}?` : ''}
        confirmLabel="Change role"
        onClose={() => setChange(null)}
        pending={setRole.isPending}
        error={setRole.error}
        onConfirm={() => change && setRole.mutate(change, { onSuccess: () => setChange(null) })}
      >
        {change && (
          <>
            {TEAM_ROLE_LABEL[change.m.role]} → <b>{TEAM_ROLE_LABEL[change.role]}</b> ({ROLE_HINT[change.role]}
            ). The change applies to their next request and is recorded in the audit log.
            {explain(setRole.error) && <p style={{ marginTop: 8 }}>{explain(setRole.error)}</p>}
          </>
        )}
      </Confirm>
      <Confirm
        open={!!removing}
        title={removing ? `Remove ${removing.user.name}?` : ''}
        confirmLabel="Remove from workspace"
        danger
        onClose={() => setRemoving(null)}
        pending={remove.isPending}
        error={remove.error}
        onConfirm={() => removing && remove.mutate(removing, { onSuccess: () => setRemoving(null) })}
      >
        They lose access to this workspace immediately and are signed out of it. Their past work and audit
        history stay. To bring them back, invite them again.
        {explain(remove.error) && <p style={{ marginTop: 8 }}>{explain(remove.error)}</p>}
      </Confirm>
    </Card>
  );
}

function Invitations() {
  const q = useInvitations();
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<Role>('staff');
  const [days, setDays] = useState('7');
  const [revoking, setRevoking] = useState<InvitationDTO | null>(null);
  const invite = useInlineAction(
    () =>
      api.post<InvitationDTO>(
        '/v1/invitations',
        { email, role, expiresInDays: Number(days) },
        { idempotencyKey: newIdempotencyKey() },
      ),
    {
      invalidate: [keys.invitations],
      success: (i) => `Invited ${i.email} as ${TEAM_ROLE_LABEL[i.role]}. They join on their first sign-in.`,
    },
  );
  const revoke = useInlineAction((i: InvitationDTO) => api.del<InvitationDTO>(`/v1/invitations/${i.id}`), {
    invalidate: [keys.invitations],
    success: (i) => `Invitation for ${i.email} revoked.`,
  });
  const daysOk = Number.isInteger(Number(days)) && Number(days) >= 1 && Number(days) <= 30;

  return (
    <Card className={s.card} title="Invitations" meta="signing in with the invited email grants the role">
      <div className={s.stack}>
        <form
          className={s.actions}
          aria-label="Invite someone"
          onSubmit={(e) => {
            e.preventDefault();
            invite.mutate(undefined, { onSuccess: () => setEmail('') });
          }}
        >
          <label className={s.field} style={{ flex: '1 1 260px' }}>
            <span className={s.fieldLabel}>Email</span>
            <Input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@bank.example"
            />
          </label>
          <label className={s.field}>
            <span className={s.fieldLabel}>Role</span>
            <Select value={role} onChange={(e) => setRole(e.target.value as Role)} aria-label="Invited role">
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {TEAM_ROLE_LABEL[r]}
                </option>
              ))}
            </Select>
          </label>
          <label className={s.field}>
            <span className={s.fieldLabel}>Expires after (days)</span>
            <Input
              type="number"
              min={1}
              max={30}
              value={days}
              style={{ width: 110 }}
              onChange={(e) => setDays(e.target.value)}
            />
          </label>
          <Button
            type="submit"
            variant="dark"
            loading={invite.isPending}
            disabled={!email.trim() || !daysOk}
            style={{ alignSelf: 'end' }}
          >
            Send invitation
          </Button>
        </form>
        <ProblemAlert error={invite.error} />
        <Loadable query={q} skeleton={<Skeleton h={80} />}>
          {(list) =>
            list.length ? (
              <div className={s.scroll}>
                <table className={s.table} aria-label="Invitations">
                  <thead>
                    <tr>
                      <th scope="col">Email</th>
                      <th scope="col">Role</th>
                      <th scope="col">State</th>
                      <th scope="col">Invited</th>
                      <th scope="col">Expires</th>
                      <th scope="col">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...list]
                      .sort((a, b) => Number(b.state === 'pending') - Number(a.state === 'pending'))
                      .map((i) => (
                        <tr key={i.id}>
                          <td className={s.name}>{i.email}</td>
                          <td>{TEAM_ROLE_LABEL[i.role]}</td>
                          <td>
                            <Tone tone={INVITATION_STATE[i.state]} />
                          </td>
                          <td className={s.muted}>
                            {ago(i.createdAt)}
                            {i.invitedBy ? ` by ${i.invitedBy.name}` : ''}
                          </td>
                          <td className={s.muted}>
                            {i.state === 'pending'
                              ? `${shortDate(i.expiresAt)} (${untilLabel(i.expiresAt)})`
                              : '—'}
                          </td>
                          <td>
                            {i.state === 'pending' && (
                              <Button
                                size="sm"
                                variant="ghost"
                                aria-label={`Revoke the invitation for ${i.email}`}
                                onClick={() => {
                                  revoke.reset();
                                  setRevoking(i);
                                }}
                              >
                                Revoke
                              </Button>
                            )}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState
                title="No invitations"
                text="Invite someone by email; they join on their first sign-in."
              />
            )
          }
        </Loadable>
      </div>
      <Confirm
        open={!!revoking}
        title={revoking ? `Revoke the invitation for ${revoking.email}?` : ''}
        confirmLabel="Revoke invitation"
        danger
        onClose={() => setRevoking(null)}
        pending={revoke.isPending}
        error={revoke.error}
        onConfirm={() => revoking && revoke.mutate(revoking, { onSuccess: () => setRevoking(null) })}
      >
        Signing in with this email will no longer grant access. You can invite them again later.
      </Confirm>
    </Card>
  );
}

function untilLabel(iso: string): string {
  const days = Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000);
  if (days <= 0) return 'today';
  return days === 1 ? 'in 1 day' : `in ${days} days`;
}
