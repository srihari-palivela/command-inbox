/**
 * Automatic provisioning (SCIM 2.0) from the bank's identity provider: the base URL and a bearer token for it
 * (shown once), and which directory groups give which role. Without a mapping, roles stay managed here.
 */
import type { Role, ScimGroupRolesBody, ScimSettingsDTO, ScimTokenDTO } from '@ci/contracts';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../lib/api';
import { ago } from '../../lib/format';
import { keys } from '../../lib/queries';
import { Button, Card, Input, Skeleton } from '../../ui';
import { Notice, ProblemAlert, Select, useInlineAction } from './bits';
import s from './admin.module.css';

const ROLES: { key: Role; label: string }[] = [
  { key: 'staff', label: 'Staff' },
  { key: 'lead', label: 'Team lead' },
  { key: 'admin', label: 'Admin' },
];

export function ScimCard() {
  const q = useQuery({ queryKey: keys.scim, queryFn: () => api.get<ScimSettingsDTO>('/v1/workspace/scim') });
  const [token, setToken] = useState<string | null>(null);
  const issue = useInlineAction(() => api.post<ScimTokenDTO>('/v1/workspace/scim/token'), {
    invalidate: [keys.scim],
    success: 'New provisioning token issued. Paste it into your identity provider now.',
  });
  const revoke = useInlineAction(() => api.del<ScimSettingsDTO>('/v1/workspace/scim/token'), {
    invalidate: [keys.scim],
    success: 'Automatic provisioning turned off.',
  });
  if (!q.data) return q.isPending ? <Skeleton h={200} /> : <ProblemAlert error={q.error} />;
  const d = q.data;
  return (
    <Card className={s.card} title="Automatic provisioning (SCIM)" meta={d.enabled ? 'On' : 'Off'}>
      <div className={s.stack}>
        <p className={s.muted} style={{ margin: 0 }}>
          Let your identity provider (Microsoft Entra ID, Okta) add people when they are assigned to Command
          Inbox and remove them when they leave. {d.provisionedMembers} member
          {d.provisionedMembers === 1 ? ' was' : 's were'} provisioned this way.
        </p>
        <label className={s.field}>
          <span className={s.fieldLabel}>Tenant URL</span>
          <Input readOnly value={d.baseUrl} className="mono" onFocus={(e) => e.target.select()} />
        </label>
        {token && (
          <Notice tone="warn">
            <div style={{ marginBottom: 6 }}>Secret token — copy it now; it is not shown again:</div>
            <Input readOnly value={token} className="mono" onFocus={(e) => e.target.select()} />
          </Notice>
        )}
        {d.enabled && (
          <p className={s.muted} style={{ margin: 0 }}>
            Token issued {d.tokenCreatedAt ? ago(d.tokenCreatedAt) : ''} · last used{' '}
            {d.lastUsedAt ? ago(d.lastUsedAt) : 'never'}
          </p>
        )}
        {d.canEdit && (
          <div className={s.row}>
            <Button
              variant="dark"
              loading={issue.isPending}
              onClick={() => issue.mutate(undefined, { onSuccess: (r) => setToken(r.token) })}
            >
              {d.enabled ? 'Replace token' : 'Turn on and issue a token'}
            </Button>
            {d.enabled && (
              <Button
                loading={revoke.isPending}
                onClick={() => revoke.mutate(undefined, { onSuccess: () => setToken(null) })}
              >
                Turn off
              </Button>
            )}
          </div>
        )}
        <ProblemAlert error={issue.error ?? revoke.error} />
        <GroupRoles d={d} />
      </div>
    </Card>
  );
}

function GroupRoles({ d }: { d: ScimSettingsDTO }) {
  const [rows, setRows] = useState<{ name: string; role: Role }[]>(
    Object.entries(d.groupRoles).map(([name, role]) => ({ name, role })),
  );
  const save = useInlineAction(
    (body: ScimGroupRolesBody) => api.put<ScimSettingsDTO>('/v1/workspace/scim/roles', body),
    { invalidate: [keys.scim, keys.members], success: 'Group roles saved and applied.' },
  );
  const known = d.groups.map((g) => g.name);
  return (
    <div className={s.stack}>
      <span className={s.fieldLabel}>Roles from directory groups</span>
      <p className={s.muted} style={{ margin: 0, fontSize: 12 }}>
        A person gets the highest role among their mapped groups (Staff if none). Leave empty to manage roles
        here instead. The last admin is never demoted by the directory.
      </p>
      {rows.map((r, i) => (
        <div key={i} className={s.row}>
          <Input
            aria-label="Group name"
            list="scim-groups"
            value={r.name}
            disabled={!d.canEdit}
            onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
          />
          <Select
            aria-label="Role"
            value={r.role}
            disabled={!d.canEdit}
            onChange={(e) =>
              setRows(rows.map((x, j) => (j === i ? { ...x, role: e.target.value as Role } : x)))
            }
          >
            {ROLES.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label}
              </option>
            ))}
          </Select>
          {d.canEdit && (
            <Button size="sm" onClick={() => setRows(rows.filter((_, j) => j !== i))}>
              Remove
            </Button>
          )}
        </div>
      ))}
      <datalist id="scim-groups">
        {known.map((n) => (
          <option key={n} value={n} />
        ))}
      </datalist>
      {d.canEdit && (
        <div className={s.row}>
          <Button size="sm" onClick={() => setRows([...rows, { name: '', role: 'staff' }])}>
            + Map a group
          </Button>
          <Button
            size="sm"
            variant="dark"
            loading={save.isPending}
            disabled={rows.some((r) => !r.name.trim())}
            onClick={() =>
              save.mutate({ groupRoles: Object.fromEntries(rows.map((r) => [r.name.trim(), r.role])) })
            }
          >
            Save mapping
          </Button>
        </div>
      )}
      <ProblemAlert error={save.error} />
    </div>
  );
}
