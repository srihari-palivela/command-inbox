/**
 * Teams, their query types, and reply-time targets — edited by the workspace's admin. The API refuses a
 * change that would orphan live routing (a name a published deployment routes by) or erase history (a team
 * or query type with tickets); the reason comes back as a problem and is shown in place.
 */
import type {
  DepartmentAdminDTO,
  DepartmentBody,
  QueryTypeAdminDTO,
  QueryTypeBody,
  SlaPoliciesBody,
  SlaPoliciesDTO,
  SlaPolicyDTO,
  TaxonomyAdminDTO,
} from '@ci/contracts';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../../lib/api';
import { keys } from '../../../lib/queries';
import { Button, Card, EmptyState, Field, Input, Modal, Pill, Skeleton } from '../../../ui';
import { Check, Confirm, Notice, ProblemAlert, Select, useInlineAction } from '../../admin/bits';
import a from '../../admin/admin.module.css';

const INVALIDATE = [keys.taxonomyAdmin, keys.taxonomy, keys.me];
const LANE_LABEL = { draft: 'Draft for approval', manual: 'A person handles it', auto: 'Automatic' } as const;

export function TeamsAdmin() {
  const q = useQuery({
    queryKey: keys.taxonomyAdmin,
    queryFn: () => api.get<TaxonomyAdminDTO>('/v1/taxonomy/admin'),
  });
  if (!q.data) return q.isPending ? <Skeleton h={320} /> : <ProblemAlert error={q.error} />;
  return <Teams t={q.data} />;
}

function Teams({ t }: { t: TaxonomyAdminDTO }) {
  const [team, setTeam] = useState<DepartmentAdminDTO | 'new' | null>(null);
  const [qt, setQt] = useState<QueryTypeAdminDTO | 'new' | null>(null);
  const [removing, setRemoving] = useState<{ kind: 'team' | 'type'; id: string; name: string } | null>(null);
  const remove = useInlineAction(
    (r: { kind: 'team' | 'type'; id: string }) =>
      api.del(`/v1/taxonomy/${r.kind === 'team' ? 'departments' : 'query-types'}/${r.id}`),
    { invalidate: INVALIDATE, success: (_x, r) => `${r.kind === 'team' ? 'Team' : 'Query type'} deleted.` },
  );
  const teamName = new Map(t.departments.map((d) => [d.id, d.name]));

  return (
    <div style={{ display: 'grid', gap: 14, marginTop: 14 }}>
      <Card
        title="Teams"
        actions={
          t.canEdit && (
            <Button size="sm" onClick={() => setTeam('new')}>
              + Add team
            </Button>
          )
        }
        flush
      >
        {t.departments.length === 0 ? (
          <EmptyState title="No teams yet" text="Add the teams that own customer queries." />
        ) : (
          <div className={a.scroll}>
            <table className={a.table} aria-label="Teams">
              <thead>
                <tr>
                  <th scope="col">Team</th>
                  <th scope="col">Owner</th>
                  <th scope="col" className={a.num}>
                    Query types
                  </th>
                  <th scope="col" className={a.num}>
                    Open tickets
                  </th>
                  <th scope="col" />
                </tr>
              </thead>
              <tbody>
                {t.departments.map((d) => (
                  <tr key={d.id}>
                    <td>
                      <span className={a.name}>{d.name}</span>{' '}
                      {d.risk && (
                        <Pill fg="var(--warn)" bg="var(--warn-bg)">
                          Higher risk
                        </Pill>
                      )}
                    </td>
                    <td>{d.owner?.name ?? <span className={a.muted}>No owner</span>}</td>
                    <td className={a.num}>{d.queryTypes}</td>
                    <td className={a.num}>{d.openTickets}</td>
                    <td className={a.actions}>
                      {t.canEdit && (
                        <>
                          <Button size="sm" onClick={() => setTeam(d)}>
                            Edit
                          </Button>
                          <Button
                            size="sm"
                            disabled={!d.deletable}
                            title={
                              d.deletable ? undefined : 'It has query types, tickets, mailboxes or documents'
                            }
                            onClick={() => setRemoving({ kind: 'team', id: d.id, name: d.name })}
                          >
                            Delete
                          </Button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card
        title="Query types"
        actions={
          t.canEdit && (
            <Button size="sm" onClick={() => setQt('new')} disabled={t.departments.length === 0}>
              + Add query type
            </Button>
          )
        }
        flush
      >
        {t.queryTypes.length === 0 ? (
          <EmptyState title="No query types yet" text="Add the kinds of request each team answers." />
        ) : (
          <div className={a.scroll}>
            <table className={a.table} aria-label="Query types">
              <thead>
                <tr>
                  <th scope="col">Query type</th>
                  <th scope="col">Team</th>
                  <th scope="col">By default</th>
                  <th scope="col" className={a.num}>
                    Tickets
                  </th>
                  <th scope="col" />
                </tr>
              </thead>
              <tbody>
                {t.queryTypes.map((q) => (
                  <tr key={q.id}>
                    <td>
                      <span className={a.name}>{q.name}</span>{' '}
                      {!q.live && (
                        <Pill fg="var(--muted)" bg="var(--surface-3)">
                          Out of service
                        </Pill>
                      )}
                    </td>
                    <td>
                      {q.departmentId ? (
                        teamName.get(q.departmentId)
                      ) : (
                        <span style={{ color: 'var(--warn)' }}>No team — stays manual</span>
                      )}
                    </td>
                    <td>{LANE_LABEL[q.defaultLane]}</td>
                    <td className={a.num}>{q.tickets}</td>
                    <td className={a.actions}>
                      {t.canEdit && (
                        <>
                          <Button size="sm" onClick={() => setQt(q)}>
                            Edit
                          </Button>
                          <Button
                            size="sm"
                            disabled={!q.deletable}
                            title={
                              q.deletable
                                ? undefined
                                : 'Tickets are filed under it; take it out of service instead'
                            }
                            onClick={() => setRemoving({ kind: 'type', id: q.id, name: q.name })}
                          >
                            Delete
                          </Button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <SlaCard />

      {team && <TeamModal team={team === 'new' ? null : team} onClose={() => setTeam(null)} />}
      {qt && (
        <QueryTypeModal qt={qt === 'new' ? null : qt} teams={t.departments} onClose={() => setQt(null)} />
      )}
      <Confirm
        open={!!removing}
        title={`Delete ${removing?.name ?? ''}?`}
        confirmLabel="Delete"
        danger
        pending={remove.isPending}
        error={remove.error}
        onClose={() => {
          setRemoving(null);
          remove.reset();
        }}
        onConfirm={() => removing && remove.mutate(removing, { onSuccess: () => setRemoving(null) })}
      >
        This is recorded in the audit log. It cannot be deleted if a live deployment still routes mail by this
        name.
      </Confirm>
    </div>
  );
}

function TeamModal({ team, onClose }: { team: DepartmentAdminDTO | null; onClose: () => void }) {
  const [name, setName] = useState(team?.name ?? '');
  const [risk, setRisk] = useState(team?.risk ?? false);
  const save = useInlineAction(
    (body: DepartmentBody) =>
      team
        ? api.put(`/v1/taxonomy/departments/${team.id}`, body)
        : api.post('/v1/taxonomy/departments', body),
    {
      invalidate: INVALIDATE,
      success: team ? 'Team saved.' : 'Team added — every member can now be cleared for it.',
    },
  );
  return (
    <Modal
      open
      onClose={onClose}
      width={440}
      title={team ? `Edit ${team.name}` : 'Add a team'}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="dark"
            style={{ marginLeft: 'auto' }}
            disabled={!name.trim()}
            loading={save.isPending}
            onClick={() => save.mutate({ name: name.trim(), risk }, { onSuccess: onClose })}
          >
            Save
          </Button>
        </>
      }
    >
      <div style={{ display: 'grid', gap: 12 }}>
        <Field label="Name">
          <Input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} data-autofocus />
        </Field>
        <Check checked={risk} onChange={setRisk}>
          Higher risk (disputes, fraud, complaints): shown flagged on the responsibility map
        </Check>
        <ProblemAlert error={save.error} />
      </div>
    </Modal>
  );
}

function QueryTypeModal({
  qt,
  teams,
  onClose,
}: {
  qt: QueryTypeAdminDTO | null;
  teams: DepartmentAdminDTO[];
  onClose: () => void;
}) {
  const [v, setV] = useState<QueryTypeBody>({
    name: qt?.name ?? '',
    departmentId: qt?.departmentId ?? teams[0]?.id ?? null,
    defaultLane: qt?.defaultLane === 'manual' ? 'manual' : 'draft',
    live: qt?.live ?? true,
  });
  const save = useInlineAction(
    (body: QueryTypeBody) =>
      qt ? api.put(`/v1/taxonomy/query-types/${qt.id}`, body) : api.post('/v1/taxonomy/query-types', body),
    { invalidate: INVALIDATE, success: qt ? 'Query type saved.' : 'Query type added.' },
  );
  return (
    <Modal
      open
      onClose={onClose}
      width={480}
      title={qt ? `Edit ${qt.name}` : 'Add a query type'}
      subtitle="To have the AI sort mail into it, add it as a category in a deployment draft too."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="dark"
            style={{ marginLeft: 'auto' }}
            disabled={!v.name.trim()}
            loading={save.isPending}
            onClick={() => save.mutate({ ...v, name: v.name.trim() }, { onSuccess: onClose })}
          >
            Save
          </Button>
        </>
      }
    >
      <div style={{ display: 'grid', gap: 12 }}>
        <Field label="Name">
          <Input
            value={v.name}
            maxLength={120}
            onChange={(e) => setV({ ...v, name: e.target.value })}
            data-autofocus
          />
        </Field>
        <Field label="Owning team">
          <Select
            value={v.departmentId ?? ''}
            onChange={(e) => setV({ ...v, departmentId: e.target.value || null })}
          >
            <option value="">No team (stays manual)</option>
            {teams.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label="By default"
          hint="There is no automatic lane in this version: a person approves every reply."
        >
          <Select
            value={v.defaultLane}
            onChange={(e) => setV({ ...v, defaultLane: e.target.value as QueryTypeBody['defaultLane'] })}
          >
            <option value="draft">{LANE_LABEL.draft}</option>
            <option value="manual">{LANE_LABEL.manual}</option>
          </Select>
        </Field>
        <Check checked={v.live} onChange={(live) => setV({ ...v, live })}>
          In service (new mail can be sorted into it)
        </Check>
        <ProblemAlert error={save.error} />
      </div>
    </Modal>
  );
}

type Row = Omit<SlaPolicyDTO, 'id'> & { key: string };

const hours = (m: number) => String(Math.round((m / 60) * 100) / 100);

function SlaCard() {
  const q = useQuery({
    queryKey: keys.slaPolicies,
    queryFn: () => api.get<SlaPoliciesDTO>('/v1/taxonomy/sla-policies'),
  });
  if (!q.data) return q.isPending ? <Skeleton h={220} /> : <ProblemAlert error={q.error} />;
  return <SlaForm key={JSON.stringify(q.data.policies)} data={q.data} />;
}

function SlaForm({ data }: { data: SlaPoliciesDTO }) {
  const [rows, setRows] = useState<Row[]>(data.policies.map((p) => ({ ...p, key: p.id })));
  const [draftHours, setDraftHours] = useState<Record<string, string>>(
    Object.fromEntries(data.policies.map((p) => [p.id, hours(p.minutes)])),
  );
  const save = useInlineAction(
    (body: SlaPoliciesBody) => api.put<SlaPoliciesDTO>('/v1/taxonomy/sla-policies', body),
    {
      invalidate: [keys.slaPolicies],
      success: 'Reply-time targets saved. They apply to newly triaged mail.',
    },
  );
  const set = (key: string, patch: Partial<Row>) =>
    setRows(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  const minutes = (key: string) => Math.round(Number(draftHours[key] ?? '') * 60);
  const invalid = rows.some((r) => !r.name.trim() || !(minutes(r.key) >= 5 && minutes(r.key) <= 43_200));
  const add = () => {
    const key = `new-${Date.now()}`;
    setRows([...rows, { key, name: '', priority: null, segment: null, escalation: false, minutes: 1440 }]);
    setDraftHours({ ...draftHours, [key]: '24' });
  };
  const submit = () =>
    save.mutate({
      policies: rows.map((r) => ({
        name: r.name.trim(),
        priority: r.priority,
        segment: r.segment,
        escalation: r.escalation,
        minutes: minutes(r.key),
      })),
    });

  return (
    <Card
      title="Reply-time targets"
      meta="The most specific match wins"
      actions={
        data.canEdit && (
          <Button size="sm" onClick={add}>
            + Add target
          </Button>
        )
      }
    >
      <div style={{ display: 'grid', gap: 12 }}>
        {data.usingDefaults && <Notice>These are the built-in targets. Saving makes them your own.</Notice>}
        <div className={a.scroll}>
          <table className={a.table} aria-label="Reply-time targets">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Priority</th>
                <th scope="col">Segment</th>
                <th scope="col">Escalations</th>
                <th scope="col">Hours to reply</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <td>
                    <Input
                      aria-label="Target name"
                      value={r.name}
                      maxLength={80}
                      disabled={!data.canEdit}
                      onChange={(e) => set(r.key, { name: e.target.value })}
                    />
                  </td>
                  <td>
                    <Select
                      aria-label="Priority"
                      value={r.priority ?? ''}
                      disabled={!data.canEdit}
                      onChange={(e) => set(r.key, { priority: (e.target.value || null) as Row['priority'] })}
                    >
                      <option value="">Any</option>
                      {(['P1', 'P2', 'P3', 'P4'] as const).map((p) => (
                        <option key={p}>{p}</option>
                      ))}
                    </Select>
                  </td>
                  <td>
                    <Select
                      aria-label="Segment"
                      value={r.segment ?? ''}
                      disabled={!data.canEdit}
                      onChange={(e) => set(r.key, { segment: e.target.value || null })}
                    >
                      <option value="">Any</option>
                      {data.segments.map((sg) => (
                        <option key={sg}>{sg}</option>
                      ))}
                    </Select>
                  </td>
                  <td>
                    <Check
                      checked={r.escalation}
                      disabled={!data.canEdit}
                      onChange={(escalation) => set(r.key, { escalation })}
                    >
                      Only escalations
                    </Check>
                  </td>
                  <td>
                    <Input
                      aria-label="Hours to reply"
                      inputMode="decimal"
                      value={draftHours[r.key] ?? ''}
                      disabled={!data.canEdit}
                      style={{ width: 90 }}
                      onChange={(e) => setDraftHours({ ...draftHours, [r.key]: e.target.value })}
                    />
                  </td>
                  <td className={a.actions}>
                    {data.canEdit && (
                      <Button size="sm" onClick={() => setRows(rows.filter((x) => x.key !== r.key))}>
                        Remove
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className={a.muted} style={{ margin: 0, fontSize: 12 }}>
          One target must match any priority and any segment. New targets apply to mail triaged from now on;
          tickets already in the queue keep their deadline.
        </p>
        <ProblemAlert error={save.error} />
        {data.canEdit && (
          <div>
            <Button
              variant="dark"
              disabled={invalid || rows.length === 0}
              loading={save.isPending}
              onClick={submit}
            >
              Save targets
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}
