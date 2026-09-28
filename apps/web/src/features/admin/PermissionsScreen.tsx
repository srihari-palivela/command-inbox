/**
 * What staff and team leads may do in this workspace. The product baseline applies unless the tenant
 * overrides a delegable capability; separation-of-duties rules are locked and shown with the reason.
 * Admins always keep every capability. Needs `rbac.manage`.
 */
import type { PermissionMatrixDTO, PolicyEffect, Role } from '@ci/contracts';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { ago } from '../../lib/format';
import { TEAM_ROLE_LABEL } from '../../lib/presentation';
import { keys, usePermissions } from '../../lib/queries';
import { toast } from '../../lib/toast';
import { Card, Loadable, Page, PageHeader, Skeleton, Toggle } from '../../ui';
import { isForbidden, LockIcon, NoAccess, useInlineAction, ProblemAlert } from './bits';
import { lockReason } from './model';
import s from './admin.module.css';

const TITLE = 'Permissions';
const SUB =
  'What staff and team leads may do here. You can widen or narrow the delegable capabilities; separation-of-duties rules are fixed. Admins always keep every capability.';
const EDITABLE: Role[] = ['staff', 'lead'];

type Row = PermissionMatrixDTO['rows'][number];

export default function PermissionsScreen() {
  const q = usePermissions();
  return (
    <Page>
      <div className="rise">
        <PageHeader title={TITLE} subtitle={SUB} />
      </div>
      {isForbidden(q.error) ? (
        <NoAccess error={q.error} what="permissions" who="Role permissions are managed by admins." />
      ) : (
        <Loadable query={q} skeleton={<Skeleton h={420} />}>
          {(m) => <Matrix m={m} />}
        </Loadable>
      )}
    </Page>
  );
}

function Matrix({ m }: { m: PermissionMatrixDTO }) {
  const qc = useQueryClient();
  const set = useInlineAction(
    (v: { role: Role; row: Row; effect: PolicyEffect | null; allowed: boolean }) =>
      api.put<PermissionMatrixDTO>('/v1/permissions', {
        overrides: [{ role: v.role, capability: v.row.capability, effect: v.effect }],
      }),
    { invalidate: [keys.me] },
  );
  const change = (role: Role, row: Row, allowed: boolean) => {
    const cell = row.cells.find((c) => c.role === role)!;
    // Back at the baseline means no override at all, so the product default applies again.
    const effect: PolicyEffect | null = allowed === cell.baseline ? null : allowed ? 'allow' : 'deny';
    set.mutate(
      { role, row, effect, allowed },
      {
        onSuccess: (next) => {
          qc.setQueryData(keys.permissions, next);
          toast.show(
            `${TEAM_ROLE_LABEL[role]}: “${row.label}” ${allowed ? 'allowed' : 'not allowed'}${effect === null ? ' (product default)' : ''}.`,
          );
        },
      },
    );
  };
  const who = (r: Row, role: Role) =>
    m.overrides.find((o) => o.role === role && o.capability === r.capability);

  return (
    <>
      <ProblemAlert error={set.error} />
      <Card className={s.card} flush>
        <div className={s.scroll}>
          <table className={s.table} aria-label="Permission matrix">
            <thead>
              <tr>
                <th scope="col">Capability</th>
                {EDITABLE.map((r) => (
                  <th scope="col" key={r}>
                    {TEAM_ROLE_LABEL[r]}
                  </th>
                ))}
                <th scope="col">Admin</th>
              </tr>
            </thead>
            <tbody>
              {m.rows.map((row) => {
                const editable = EDITABLE.map((r) => row.cells.find((c) => c.role === r)!);
                const locked = editable.every((c) => c.locked);
                return (
                  <tr key={row.capability} aria-label={row.label}>
                    <td>
                      <div className={s.name}>{row.label}</div>
                      <div className={s.sub}>
                        <span className={s.mono}>{row.capability}</span>
                      </div>
                      {locked && (
                        <div className={s.reason}>
                          <LockIcon />
                          <span>Locked — {lockReason(row.capability, row.adminOnly)}</span>
                        </div>
                      )}
                    </td>
                    {editable.map((cell) => {
                      const o = who(row, cell.role);
                      const name = `${TEAM_ROLE_LABEL[cell.role]}: ${row.label}`;
                      return (
                        <td key={cell.role}>
                          {cell.locked ? (
                            <span
                              className={`${s.cell} ${s.locked}`}
                              aria-label={`${name} — ${cell.allowed ? 'allowed' : 'not allowed'}, locked`}
                            >
                              <LockIcon />
                              {cell.allowed ? 'Allowed' : 'Never'}
                            </span>
                          ) : (
                            <span className={s.cell}>
                              <Toggle
                                label={name}
                                checked={cell.allowed}
                                disabled={set.isPending}
                                onChange={(v) => change(cell.role, row, v)}
                              />
                              <span>{cell.allowed ? 'Allowed' : 'Not allowed'}</span>
                              {cell.override && (
                                <span
                                  className={s.changed}
                                  title={
                                    o
                                      ? `Changed ${ago(o.changedAt)}${o.changedBy ? ` by ${o.changedBy.name}` : ''}`
                                      : undefined
                                  }
                                >
                                  changed · default {cell.baseline ? 'allowed' : 'not allowed'}
                                </span>
                              )}
                            </span>
                          )}
                        </td>
                      );
                    })}
                    <td>
                      <span
                        className={`${s.cell} ${s.locked}`}
                        aria-label={`Admin: ${row.label} — always, locked`}
                      >
                        <LockIcon />
                        Always
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
      <p className={s.muted}>
        Changes take effect on each person’s next request and are recorded in the audit log.{' '}
        {m.overrides.length
          ? `${m.overrides.length} override${m.overrides.length === 1 ? '' : 's'} in place.`
          : 'No overrides: the product defaults apply.'}
      </p>
    </>
  );
}
